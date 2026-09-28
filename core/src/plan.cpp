#include "ktv/plan.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>

#include "ktv/cluster.hpp"
#include "ktv/normalization.hpp"
#include "ktv/sla.hpp"

namespace ktv {

namespace {

using ojson = nlohmann::ordered_json;

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
        Deadlines d = resolve_deadlines(*task, kind, now);
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
            if (j != i && tasks[i]->task_plots_id != 0 && tasks[j]->task_plots_id == tasks[i]->task_plots_id)
                mask |= uint64_t{1} << j;  // Lô 0 = chưa xác định, không tính là cùng khu vực.
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

    // ---- Dòng lịch (chưa gắn seq; seq đánh lại theo từng cụm sau khi chia).
    auto at = [&](double minutes) { return start + static_cast<Minutes>(std::llround(minutes)); };
    const double now_rel = static_cast<double>(now - start);
    int at_risk = 0, breach = 0, completed = 0, revisits = 0;
    double km = 0, travel = 0, handle = 0, idle = 0, rest = 0;
    std::vector<const Visit*> visits;  // Chỉ các bước tới việc thật (bỏ nghỉ trưa).
    for (const Visit& v : steps)
        if (v.task != kBreak) visits.push_back(&v);
    const int total = static_cast<int>(visits.size());

    std::vector<ojson> rows;       // IDLE / BREAK / TASK theo đúng thứ tự thời gian.
    std::vector<int> row_task;     // Với mỗi row: TASK gần nhất tại/trước nó (-1 nếu trước TASK đầu).
    std::vector<TaskStop> stops;   // Chỉ TASK, cho summarize_clusters.
    int task_ordinal = -1;
    for (const Visit& v : steps) {
        if (at(v.checkin) > at(v.arrive)) {  // Tới sớm hơn mốc hẹn / giờ nghỉ: chờ.
            idle += v.checkin - v.arrive;
            rows.push_back({{"entry_type", "IDLE"}, {"at", hhmm(at(v.arrive))},
                            {"start_at", format_datetime(at(v.arrive))}, {"end_at", format_datetime(at(v.checkin))},
                            {"duration_minutes", at(v.checkin) - at(v.arrive)},
                            {"label", (v.task == kBreak ? "Chờ tới giờ nghỉ trưa " : "Chờ tới khung hẹn ") + hhmm(at(v.checkin))}});
            row_task.push_back(task_ordinal);
        }
        if (v.task == kBreak) {
            rest += v.done - v.checkin;
            rows.push_back({{"entry_type", "BREAK"}, {"at", hhmm(at(v.checkin))},
                            {"start_at", format_datetime(at(v.checkin))}, {"end_at", format_datetime(at(v.done))},
                            {"duration_minutes", at(v.done) - at(v.checkin)}, {"label", "Nghỉ trưa"}});
            row_task.push_back(task_ordinal);
            continue;
        }
        const Task& task = *tasks[v.task];
        const char* sla = projected_sla(v.checkin, v.done, p.due[v.task], p.complete_by[v.task], p.service[v.task], now_rel, rules);
        at_risk += std::string(sla) == "AT_RISK";
        breach += std::string(sla) == "WILL_BREACH" || std::string(sla) == "ALREADY_BREACHED";
        completed += at(v.done) <= shift_end;
        revisits += static_cast<int>(v.cost[AREA_REENTRY]);
        km += v.km;
        travel += v.travel;
        handle += p.service[v.task];
        ++task_ordinal;
        stops.push_back({&task, v.km, p.service[v.task]});
        rows.push_back({{"entry_type", "TASK"}, {"at", hhmm(at(v.checkin))},
                        {"start_at", format_datetime(at(v.checkin))}, {"end_at", format_datetime(at(v.done))},
                        {"task_id", task.task_id}, {"task_group_id", task.task_group_id},
                        {"task_group_name", task.task_group_name}, {"task_type_id", task.task_type_id},
                        {"task_type_name", task.task_type_name}, {"task_sub_id", task.task_sub_id},
                        {"task_sub_name", task.task_sub_name}, {"checkindate", ""}, {"checkoutdate", ""},
                        {"travel_minutes_before", std::llround(v.travel)}, {"travel_km_before", round_to(v.km, 1)},
                        {"handle_minutes", std::llround(p.service[v.task])}, {"projected_sla", sla}});
        row_task.push_back(task_ordinal);
    }

    // ---- Cụm: tóm tắt theo TASK, rồi gắn dòng timeline vào cụm (không đổi thứ tự).
    const std::vector<ClusterSummary> summaries = summarize_clusters(stops, staff.plots);
    std::vector<int> cluster_of_task(stops.size());
    for (int c = 0; c < static_cast<int>(summaries.size()); ++c)
        for (int k = summaries[c].first_task; k < summaries[c].first_task + summaries[c].task_count; ++k)
            cluster_of_task[k] = c;

    std::vector<ojson> cluster_schedule(summaries.size(), ojson::array());
    std::vector<int> cluster_seq(summaries.size(), 0);
    for (size_t i = 0; i < rows.size(); ++i) {
        const int c = row_task[i] < 0 ? 0 : cluster_of_task[row_task[i]];
        ojson ordered = ojson::object();
        ordered["seq"] = ++cluster_seq[c];
        for (auto it = rows[i].begin(); it != rows[i].end(); ++it) ordered[it.key()] = it.value();
        cluster_schedule[c].push_back(std::move(ordered));
    }

    ojson clusters = ojson::array();
    for (int c = 0; c < static_cast<int>(summaries.size()); ++c) {
        const ClusterSummary& summary = summaries[c];
        clusters.push_back({{"cluster_seg", summary.seg}, {"cluster_code", summary.code}, {"name", summary.name},
                            {"center", latlng(summary.center)}, {"radius_m", std::llround(summary.radius_km * 1000)},
                            {"task_count", summary.task_count},
                            {"travel_km_inbound", round_to(summary.travel_km_inbound, 1)},
                            {"travel_km_internal", round_to(summary.travel_km_internal, 1)},
                            {"handle_minutes", std::llround(summary.handle_minutes)},
                            {"schedule", cluster_schedule[c]}});
    }

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
        {"cluster_count", static_cast<int>(summaries.size())},
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
                       {"data", {{"staff_id", staff.staff_id}, {"clusters", clusters}, {"metrics", metrics}}}};
    return result;
}

}  // namespace ktv
