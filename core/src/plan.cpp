#include "ktv/plan.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>

#include "ktv/normalization.hpp"

namespace ktv {

namespace {

using ojson = nlohmann::ordered_json;

Minutes start_of_day(Minutes t) { return t - ((t % 1440) + 1440) % 1440; }

Minutes end_of_month(Minutes t) {
    std::string text = format_datetime(t);  // "YYYY-MM-DD ..."
    int year = std::stoi(text.substr(0, 4)), month = std::stoi(text.substr(5, 2));
    char next[32];
    std::snprintf(next, sizeof next, "%04d-%02d-01 00:00:00", month == 12 ? year + 1 : year, month % 12 + 1);
    return *parse_datetime(next) - 1;
}

std::string hhmm(Minutes t) { return format_datetime(t).substr(11, 5); }
std::string latlng(Point p) {
    char text[40];
    std::snprintf(text, sizeof text, "%.4f,%.4f", p.lat, p.lng);
    return text;
}
double round_to(double value, int digits) {
    double scale = std::pow(10, digits);
    return std::round(value * scale) / scale;
}

// Hạn của một việc theo loại việc (sheet 05) và giờ hẹn. Mốc trả về là phút tuyệt đối.
struct Deadlines {
    std::optional<Minutes> opens, due, complete_by;
};
Deadlines deadlines(const Task& task, const TaskKind& kind, Minutes now) {
    Deadlines d;
    if (task.appointment) {
        d.opens = task.appointment;
        if (task.sla_minutes) d.due = *task.appointment + *task.sla_minutes;  // Check-in ≤ B = A + SLA.
        else d.complete_by = start_of_day(*task.appointment) + 1439;          // Xong trong ngày hẹn.
        return d;
    }
    // Không hẹn: "trong ngày tạo phiếu" lấy hôm nay (API chưa có ngày tạo phiếu). [GIẢ ĐỊNH]
    if (kind.on_time == OnTime::DoneSameCreatedDay || kind.on_time == OnTime::DoneSameAppointmentDay)
        d.complete_by = start_of_day(now) + 1439;
    else if (kind.on_time == OnTime::DoneWithinMonth)
        d.complete_by = end_of_month(now);
    return d;  // Loại "check-in trước B" mà không hẹn: chưa có B, không tính trễ.
}

// ON_TIME / AT_RISK / WILL_BREACH / ALREADY_BREACHED (sheet 05) theo hạn check-in, nếu không thì hạn làm xong.
const char* projected_sla(const Visit& v, double due, double complete_by, double service, double now, const Rules& r) {
    double deadline = !std::isnan(due) ? due : complete_by;
    if (std::isnan(deadline)) return "ON_TIME";
    double at = !std::isnan(due) ? v.checkin : v.done;
    if (deadline < now) return "ALREADY_BREACHED";
    if (at > deadline) return "WILL_BREACH";
    double spare = deadline - at;
    if (spare < r.at_risk_minutes || spare < r.at_risk_ratio * service) return "AT_RISK";
    return "ON_TIME";
}

}  // namespace

ojson error_response(const std::string& statuscode, const std::string& text, const std::string& trace_id, Minutes server_now) {
    return {{"success", false}, {"statuscode", statuscode}, {"message", text}, {"trace_id", trace_id},
            {"server_time", format_datetime(server_now)}, {"data", nullptr}};
}

PlanResult plan(const Message& message, const Rules& rules, Minutes server_now, const std::string& osrm_url) {
    auto clock = std::chrono::steady_clock::now();
    PlanResult result;
    const Staff& staff = message.staff;
    const Minutes now = message.planned_at.value_or(server_now);
    const Minutes day = start_of_day(now);
    const NormalizedWorklist worklist = normalize_worklist(message);
    if (worklist.staff_off) {  // KTV off: không sinh tuyến.
        result.response = error_response("422", "KTV đang off, không sinh tuyến", message.message_id, server_now);
        return result;
    }
    const std::vector<const Task*>& tasks = worklist.candidates;
    result.routed = static_cast<int>(tasks.size());
    result.excluded = worklist.stats.excluded_missing_location;

    const Minutes shift_start = day + staff.available.front().first;
    const Minutes shift_end = day + staff.available.back().second;  // Nhiều khung giờ: xử lý ở bước sau.
    Minutes start = std::max(now, shift_start);
    if (worklist.current_task) start = std::max(start, now + static_cast<Minutes>(rules.current_task_minutes));

    if (tasks.empty() || tasks.size() > 64) {
        result.response = error_response("422", tasks.empty() ? "Không có công việc để dựng tuyến" : "Quá 64 việc cho một KTV",
                                         message.message_id, server_now);
        return result;
    }

    // ---- Bài toán số: mọi mốc giờ đổi về phút kể từ lúc xuất phát.
    Problem p;
    std::vector<Point> points{staff.latlng};
    auto relative = [&](std::optional<Minutes> t) { return t ? static_cast<double>(*t - start) : kNone; };
    for (const Task* task : tasks) {
        const TaskKind& kind = *find_kind(task->task_group_name, task->task_type_name);
        Deadlines d = deadlines(*task, kind, now);
        points.push_back(*task->latlng);

        p.service.push_back(task->handle_minutes.value_or(kind.handle_minutes));
        p.opens.push_back(relative(d.opens));
        p.due.push_back(relative(d.due));
        p.complete_by.push_back(relative(d.complete_by));
        p.weight.push_back(rules.priority_weight[task->priority_in_day]);
    }
    for (size_t i = 0; i < tasks.size(); ++i) {
        uint64_t mask = 0;
        for (size_t j = 0; j < tasks.size(); ++j)
            if (j != i && tasks[j]->task_plots_id == tasks[i]->task_plots_id) mask |= uint64_t{1} << j;
        p.same_area.push_back(mask);
    }
    p.shift_end = static_cast<double>(shift_end - start);
    // Nghỉ trưa: cần khi còn kịp bắt đầu nghỉ (chưa qua giờ chốt) và ca làm phủ khung trưa.
    const Minutes break_open = day + rules.break_start, break_latest = day + rules.break_end - static_cast<Minutes>(rules.break_minutes);
    if (rules.break_minutes > 0 && start <= break_latest && shift_start <= break_open && shift_end >= day + rules.break_end) {
        p.break_open = static_cast<double>(break_open - start);
        p.break_latest = static_cast<double>(break_latest - start);
        p.break_minutes = rules.break_minutes;
    }
    
    std::string travel_error;
    if (osrm_url.empty()) {
        p.travel = haversine_matrix(points, rules.average_speed_kmh);
    } else if (auto road = osrm_matrix(osrm_url, points, rules.average_speed_kmh, travel_error)) {
        p.travel = std::move(*road);
        result.travel = "OSRM";
    } else {
        p.travel = haversine_matrix(points, rules.average_speed_kmh, kRoadFactor);
        result.travel = "ESTIMATED";
    }

    Solution solution = solve(p, rules);
    result.source = solution.source;
    const std::vector<Visit>& steps = solution.steps;

    // ---- Dòng lịch.
    auto at = [&](double minutes) { return start + static_cast<Minutes>(std::llround(minutes)); };
    const double now_rel = static_cast<double>(now - start);
    ojson schedule = ojson::array();
    int seq = 0, at_risk = 0, breach = 0, completed = 0, revisits = 0, total = 0;
    double km = 0, travel = 0, handle = 0, idle = 0, rest = 0, internal_km = 0;
    std::vector<std::string> plot_names;
    Point center{0, 0};
    std::vector<const Visit*> visits;  // Chỉ các bước tới việc thật (bỏ nghỉ trưa).
    for (const Visit& v : steps)
        if (v.task != kBreak) visits.push_back(&v);
    for (const Visit& v : steps) {
        if (at(v.checkin) > at(v.arrive)) {  // Tới sớm hơn mốc hẹn / giờ nghỉ: chờ.
            idle += v.checkin - v.arrive;
            schedule.push_back({{"seq", ++seq}, {"type", "IDLE"}, {"at", hhmm(at(v.arrive))},
                                {"start_at", format_datetime(at(v.arrive))}, {"end_at", format_datetime(at(v.checkin))},
                                {"duration_minutes", at(v.checkin) - at(v.arrive)},
                                {"label", (v.task == kBreak ? "Chờ tới giờ nghỉ trưa " : "Chờ tới khung hẹn ") + hhmm(at(v.checkin))}});
        }
        if (v.task == kBreak) {
            rest += v.done - v.checkin;
            schedule.push_back({{"seq", ++seq}, {"type", "BREAK"}, {"at", hhmm(at(v.checkin))},
                                {"start_at", format_datetime(at(v.checkin))}, {"end_at", format_datetime(at(v.done))},
                                {"duration_minutes", at(v.done) - at(v.checkin)}, {"label", "Nghỉ trưa"}});
            continue;
        }
        const Task& task = *tasks[v.task];
        const char* sla = projected_sla(v, p.due[v.task], p.complete_by[v.task], p.service[v.task], now_rel, rules);
        at_risk += std::string(sla) == "AT_RISK";
        breach += std::string(sla) == "WILL_BREACH" || std::string(sla) == "ALREADY_BREACHED";
        completed += at(v.done) <= shift_end;
        revisits += static_cast<int>(v.cost[AREA_REENTRY]);
        km += v.km;
        travel += v.travel;
        handle += p.service[v.task];
        if (total++ > 0) internal_km += v.km;  // Chặng đầu là inbound.
        center.lat += task.latlng->lat / visits.size();
        center.lng += task.latlng->lng / visits.size();
        std::string plot = "Lô " + std::to_string(task.task_plots_id);
        for (const Plot& own : staff.plots)
            if (own.id == task.task_plots_id && !own.name.empty()) plot = own.name;
        if (std::find(plot_names.begin(), plot_names.end(), plot) == plot_names.end()) plot_names.push_back(plot);
        schedule.push_back({{"seq", ++seq}, {"type", "TASK"}, {"at", hhmm(at(v.checkin))},
                            {"start_at", format_datetime(at(v.checkin))}, {"end_at", format_datetime(at(v.done))},
                            {"task_id", task.task_id}, {"task_group_id", task.task_group_id},
                            {"task_group_name", task.task_group_name}, {"task_type_id", task.task_type_id},
                            {"task_type_name", task.task_type_name}, {"task_sub_id", task.task_sub_id},
                            {"task_sub_name", task.task_sub_name}, {"checkindate", ""}, {"checkoutdate", ""},
                            {"travel_minutes_before", std::llround(v.travel)}, {"travel_km_before", round_to(v.km, 1)},
                            {"handle_minutes", std::llround(p.service[v.task])}, {"projected_sla", sla}});
    }

    // ---- Cụm: bước này gộp cả tuyến vào một cụm; module cluster tách ở bước sau.
    double radius_km = 0;
    for (const Visit* v : visits) radius_km = std::max(radius_km, distance_km(center, *tasks[v->task]->latlng));
    std::string name = "Cluster 1 — ";
    for (size_t i = 0; i < plot_names.size(); ++i) name += (i ? " · " : "") + plot_names[i];
    ojson cluster = {{"cluster_seg", 1}, {"cluster_code", "CL-1"}, {"name", name}, {"center", latlng(center)},
                     {"radius_m", std::llround(radius_km * 1000)}, {"task_count", total},
                     {"travel_km_inbound", round_to(visits.front()->km, 1)}, {"travel_km_internal", round_to(internal_km, 1)},
                     {"handle_minutes", std::llround(handle)}, {"schedule", schedule}};

    const Minutes finish = at(visits.back()->done);  // Xong việc cuối (nghỉ ở cuối tuyến không tính).
    ojson metrics = {
        {"total_distance_km", round_to(km, 1)},
        {"total_travel_minutes", std::llround(travel)},
        {"total_handle_minutes", std::llround(handle)},
        {"start_at", format_datetime(start)},
        {"finish_at", format_datetime(finish)},
        {"on_time_rate_forecast", round_to(100.0 * (total - breach) / total, 1)},
        {"at_risk_count", at_risk},
        {"breach_forecast_count", breach},
        {"cluster_count", 1},
        {"revisit_count", revisits},
        {"idle_minutes", std::llround(idle)},
        {"break_minutes", std::llround(rest)},
        {"tasks_total", total},
        {"tasks_forecast_completed", completed},
        {"shift_end_at", format_datetime(shift_end)},
        {"overload_minutes", std::max<Minutes>(0, finish - shift_end)},
        {"generated_in_ms", 0},
    };
    long long ms = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - clock).count();
    metrics["generated_in_ms"] = ms;

    const bool estimated = !travel_error.empty();  // Sheet 07: bản đồ lỗi vẫn trả tuyến, mã 424.
    result.response = {{"success", true}, {"statuscode", estimated ? "424" : "200"},
                       {"message", estimated ? "Không lấy được dữ liệu bản đồ, khoảng cách là ước lượng đường chim bay (" + travel_error + ")" : ""},
                       {"trace_id", message.message_id},
                       {"server_time", format_datetime(server_now)},
                       {"data", {{"staff_id", staff.staff_id}, {"clusters", ojson::array({cluster})}, {"metrics", metrics}}}};
    return result;
}

}  // namespace ktv
