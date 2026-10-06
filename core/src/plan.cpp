#include "ktv/plan.hpp"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cmath>
#include <numeric>
#include <string>

#include "ktv/cluster.hpp"
#include "ktv/normalization.hpp"
#include "ktv/sla.hpp"

namespace ktv {

namespace {

using ojson = nlohmann::ordered_json;

std::string hhmm(Minutes t) { return format_datetime(t).substr(11, 5); }

// "lat,lng". Tâm cụm: 4 chữ số (~11 m). Tọa độ task: 6 chữ số (~0,1 m, marker trên map Mobix — người dùng chốt 7.9).
std::string latlng(Point p, int digits = 4) {
    char text[64];
    std::snprintf(text, sizeof text, "%.*f,%.*f", digits, p.lat, digits, p.lng);
    return text;
}
ojson or_null(const std::optional<long long>& value) { return value ? ojson(*value) : ojson(nullptr); }
ojson or_null(const std::optional<std::string>& value) { return value ? ojson(*value) : ojson(nullptr); }
double round_to(double value, int digits) {
    double scale = std::pow(10, digits);
    return std::round(value * scale) / scale;
}

// ---- Giải thích tuyến (--explain): chi phí theo tầng/rule + so vài phương án khác. ----
// Không đổi thứ tự; chỉ đọc lại chi phí mà QHĐ đã dùng (objective/simulate).
struct Explained {
    std::vector<double> tiers;       // tổng có trọng số từng tầng — đúng khóa mà QHĐ so sánh
    std::vector<ojson> tier_rules;   // chi tiết từng rule trong tầng (đã nhân trọng số)
    ojson raw;                       // số nguyên bản cho người đọc (km, phút, số việc trễ…)
    bool feasible = false;
};

Explained explain_order(const Problem& p, const Rules& rules, const std::vector<int>& order) {
    Explained e;
    e.tiers = objective(p, rules, order);
    e.feasible = !e.tiers.empty() && !std::isinf(e.tiers[0]);
    if (!e.feasible) return e;
    const std::vector<Visit> steps = simulate(p, order);
    double km = 0, travel = 0, late_minutes = 0;
    int late_checkins = 0, late_completions = 0, after_shift = 0, reentries = 0;
    for (const Visit& v : steps) {
        if (v.task == kBreak) continue;
        km += v.km;
        travel += v.travel;
        late_minutes += v.cost[LATE_MINUTES];
        late_checkins += v.cost[LATE_CHECKIN] > 0;
        late_completions += v.cost[LATE_COMPLETION] > 0;
        after_shift += v.cost[AFTER_SHIFT] > 0;
        reentries += v.cost[AREA_REENTRY] > 0;
    }
    const double finish = steps.empty() ? 0 : steps.back().done;  // khớp Search::key: giờ xong của bước cuối
    e.raw = {{"km", round_to(km, 1)}, {"travel_minutes", std::llround(travel)}, {"late_checkins", late_checkins},
             {"late_minutes", std::llround(late_minutes)}, {"late_completions", late_completions},
             {"after_shift", after_shift}, {"area_reentries", reentries}, {"finish_minutes", std::llround(finish)}};
    for (const auto& tier : rules.tiers) {
        ojson detail = ojson::object();
        for (auto [rule, weight] : tier) {
            double total = 0;
            if (rule == FINISH) total = weight * finish;
            else
                for (const Visit& v : steps) total += weight * v.cost[rule];
            detail[kRuleCodes[rule]] = round_to(total, 2);
        }
        e.tier_rules.push_back(std::move(detail));
    }
    return e;
}

// Tham lam gần nhất theo km — chiến lược người hay nghĩ tới ("đi chỗ gần trước").
std::vector<int> nearest_order(const Problem& p) {
    std::vector<int> order;
    std::vector<bool> used(p.size(), false);
    int prev = 0;
    for (int k = 0; k < p.size(); ++k) {
        int best = -1;
        for (int j = 0; j < p.size(); ++j)
            if (!used[j] && (best < 0 || p.travel.km[prev][j + 1] < p.travel.km[prev][best + 1])) best = j;
        order.push_back(best);
        used[best] = true;
        prev = best + 1;
    }
    return order;
}

// Thứ tự theo khóa: mode 0 = hạn sớm trước, mode 1 = ưu tiên cao trước.
std::vector<int> sorted_order(const Problem& p, int mode) {
    std::vector<int> order(p.size());
    std::iota(order.begin(), order.end(), 0);
    std::sort(order.begin(), order.end(), [&](int a, int b) {
        if (mode == 1 && p.weight[a] != p.weight[b]) return p.weight[a] > p.weight[b];
        const bool na = std::isnan(p.due[a]), nb = std::isnan(p.due[b]);
        if (na != nb) return !na;             // có hạn xếp trước
        if (!na && p.due[a] != p.due[b]) return p.due[a] < p.due[b];
        if (p.weight[a] != p.weight[b]) return p.weight[a] > p.weight[b];
        return a < b;
    });
    return order;
}

struct Alternative {
    std::string name;
    Explained explained;
};

// Phương án so sánh: chèn chỗ nghỉ trưa tốt nhất (QHĐ cũng được chọn chỗ nghỉ) rồi chấm điểm.
Alternative best_break(const Problem& p, const Rules& rules, const std::string& name,
                       const std::vector<int>& task_order) {
    Alternative best{name, {}};
    bool found = false;
    auto consider = [&](const std::vector<int>& candidate) {
        Explained e = explain_order(p, rules, candidate);
        if (!e.feasible) return;
        if (!found || e.tiers < best.explained.tiers) best.explained = std::move(e), found = true;
    };
    if (p.needs_break())
        for (int at = 0; at <= static_cast<int>(task_order.size()); ++at) {
            std::vector<int> candidate = task_order;
            candidate.insert(candidate.begin() + at, kBreak);
            consider(candidate);
        }
    consider(task_order);  // không nghỉ (chỉ hợp lệ khi mọi việc xong trước giờ chốt)
    if (!found) best.explained = explain_order(p, rules, task_order);
    return best;
}

std::string one_decimal(double value) {
    char text[32];
    std::snprintf(text, sizeof text, "%.1f", value);
    return text;
}

// ---- 7.16.3: gom ca cùng địa chỉ thành một điểm dừng (chạy trước QHĐ) ----
// Một điểm dừng: 1 ca lẻ, hoặc nhóm ca cùng địa chỉ (workbook không có mã nhóm → AI tự gom theo địa chỉ).
struct Stop {
    std::vector<const Task*> tasks;  // đã sắp thứ tự nội bộ (ưu tiên asc → TGXL asc → task_id)
};

int stop_service(const Task& task) {
    const TaskKind& kind = kind_or_default(task.task_group_name, task.task_type_name);
    return task.handle_minutes.value_or(kind.handle_minutes);
}

// Địa chỉ chuẩn hoá để so khi hai ca trùng toạ độ (catalogue: "toạ độ trùng nhau hoặc thiếu thì đối chiếu địa chỉ").
std::string normalized_address(const std::string& text) {
    std::string out;
    bool space = false;
    for (char ch : text) {
        if (std::isspace(static_cast<unsigned char>(ch))) {
            space = !out.empty();
            continue;
        }
        if (space) {
            out += ' ';
            space = false;
        }
        out += static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
    }
    return out;
}

// Cùng một địa chỉ khách? ≤ bán kính là cùng (catalogue 50 m); riêng trùng toạ độ thì phải khớp địa chỉ
// (dữ liệu giả hay dùng chung tâm phường — không gom hai khách khác nhau chỉ vì chung toạ độ).
bool same_address(const Task& a, const Task& b, double radius_km) {
    const double km = distance_km(*a.latlng, *b.latlng);
    if (km > radius_km) return false;
    if (km > 0.001) return true;
    return normalized_address(a.location) == normalized_address(b.location);
}

// Thứ tự nội bộ nhóm: ưu tiên trong ngày (1 cao nhất) → TGXL ngắn trước → task_id (tất định).
void sort_stop_tasks(std::vector<const Task*>& tasks) {
    std::sort(tasks.begin(), tasks.end(), [](const Task* a, const Task* b) {
        if (a->priority_in_day != b->priority_in_day) return a->priority_in_day < b->priority_in_day;
        const int sa = stop_service(*a), sb = stop_service(*b);
        if (sa != sb) return sa < sb;
        return a->task_id < b->task_id;
    });
}

// Gom nhóm theo địa chỉ (workbook không có mã nhóm — AI tự gom, `rules.stop_group_radius_m`); còn lại là ca lẻ.
std::vector<Stop> build_stops(const std::vector<const Task*>& tasks, const Rules& rules) {
    std::vector<Stop> stops;
    std::vector<bool> used(tasks.size(), false);
    const double radius_km = rules.stop_group_radius_m / 1000.0;
    for (size_t i = 0; i < tasks.size(); ++i) {
        if (used[i]) continue;
        Stop stop{{tasks[i]}};
        used[i] = true;
        for (size_t j = i + 1; j < tasks.size(); ++j)
            if (!used[j] && same_address(*tasks[i], *tasks[j], radius_km)) {
                stop.tasks.push_back(tasks[j]);
                used[j] = true;
            }
        stops.push_back(std::move(stop));
    }
    for (Stop& stop : stops)
        if (stop.tasks.size() > 1) sort_stop_tasks(stop.tasks);
    return stops;
}

// Nhóm có phục vụ liền nhau được không: bắt đầu tại mốc A sớm nhất (đến sớm nhất có thể), ca thứ k check-in
// tại giờ bắt đầu + Σ TGXL trước nó; phải chờ ≤ stop_group_max_wait_minutes và không quá mốc B của chính nó.
// Không đạt → tách từng ca (ngoại lệ catalogue "2 hẹn không thể làm liền nhau"). [GIẢ ĐỊNH: ngưỡng chờ của repo]
bool group_servable(const Stop& stop, const Rules& rules, Minutes now) {
    const Task* anchor = nullptr;
    for (const Task* task : stop.tasks)
        if (task->appointment && (!anchor || *task->appointment < *anchor->appointment)) anchor = task;
    if (!anchor) return true;  // không ca nào có hẹn → phục vụ liền nhau luôn được
    Minutes clock = *anchor->appointment;
    for (const Task* task : stop.tasks) {
        const TaskKind& kind = kind_or_default(task->task_group_name, task->task_type_name);
        const Deadlines d = resolve_deadlines(*task, kind, now);
        Minutes checkin = clock;
        if (d.opens && *d.opens > checkin) checkin = *d.opens;
        if (checkin - clock > rules.stop_group_max_wait_minutes) return false;
        if (d.due && checkin > *d.due) return false;
        clock = checkin + stop_service(*task);
    }
    return true;
}

// Ca neo của điểm dừng: ca có mốc hẹn A sớm nhất (catalogue: "neo theo khung giờ hẹn sớm nhất"); không ai có hẹn → ca đầu.
const Task& stop_anchor(const Stop& stop) {
    const Task* anchor = stop.tasks.front();
    for (const Task* task : stop.tasks)
        if (task->appointment && (!anchor->appointment || *task->appointment < *anchor->appointment)) anchor = task;
    return *anchor;
}

// Ca "hoàn tất trong tháng" còn hơn K ngày làm việc → không xếp (7.16.1; nhóm cùng địa chỉ được xét ở ngoài).
bool beyond_k(const Task& task, Minutes now, const Rules& rules) {
    const TaskKind& kind = kind_or_default(task.task_group_name, task.task_type_name);
    if (kind.on_time != OnTime::DoneWithinMonth) return false;
    const Deadlines d = resolve_deadlines(task, kind, now);
    return d.complete_by && workdays_until(now, *d.complete_by) > rules.k_month_days;
}

// Một câu tiếng Việt ngắn vì sao phương án này không được chọn (so với tuyến đang chạy).
std::string verdict_of(const Explained& chosen, const Alternative& alt) {
    if (!alt.explained.feasible) return "không xếp được: vi phạm luật nghỉ trưa";
    std::string text;
    const double d1 = alt.explained.tiers[0] - chosen.tiers[0];
    const double d2 = alt.explained.tiers.size() > 1 ? alt.explained.tiers[1] - chosen.tiers[1] : 0;
    if (d1 > 0.005) text = "trễ hẹn nhiều hơn (+" + one_decimal(d1) + " điểm tầng 1)";
    else if (d2 > 0.005) text = "xong quá hạn/quá ca nhiều hơn (+" + one_decimal(d2) + " điểm tầng 2)";
    else text = "cùng đúng hạn";
    const double alt_km = alt.explained.raw["km"].get<double>(), chosen_km = chosen.raw["km"].get<double>();
    if (std::abs(alt_km - chosen_km) >= 0.05)
        text += alt_km > chosen_km ? ", km nhiều hơn " + one_decimal(alt_km - chosen_km) + " km"
                                   : ", km ít hơn " + one_decimal(chosen_km - alt_km) + " km";
    return text;
}

}  // namespace

ojson error_response(const std::string& statuscode, const std::string& text, const std::string& trace_id, Minutes server_now) {
    return {{"success", false}, {"statuscode", statuscode}, {"message", text}, {"trace_id", trace_id},
            {"server_time", format_datetime(server_now)}, {"data", nullptr}};
}

PlanResult plan(const Message& message, const Rules& rules, Minutes server_now, const std::string& osrm_url,
                bool explain) {
    auto clock = std::chrono::steady_clock::now();
    PlanResult result;
    const Staff& staff = message.staff;
    const Minutes now = message.planned_at.value_or(server_now);
    const Minutes day = start_of_day(now);
    const NormalizedWorklist worklist = normalize_worklist(message);
    result.warnings = worklist.warnings;
    result.stats = worklist.stats;
    if (worklist.staff_off) {  // KTV off (hoặc trạng thái không rõ): không sinh tuyến.
        const char* why = message.staff.status == kStaffStatusUnknown ? "Trạng thái KTV không rõ, không sinh tuyến"
                                                                       : "KTV đang off, không sinh tuyến";
        result.response = error_response("422", why, message.message_id, server_now);
        return result;
    }
    // 7.18: ca hẹn ngày SAU ngày chạy không xếp tuyến hôm nay (catalogue mục D); hẹn ngày đã qua vẫn xếp (làm bù).
    // Lọc trước khi gom: chỉ gom các ca được tính tuyến. Không vào OUT (người dùng chốt) — chỉ ghi unplaced.
    std::vector<const Task*> today;
    for (const Task* task : worklist.candidates) {
        if (task->appointment && start_of_day(*task->appointment) > day) result.unplaced.push_back(task->task_id);
        else today.push_back(task);
    }
    // 7.16.3: gom ca cùng địa chỉ thành điểm dừng TRƯỚC khi lọc K (catalogue mục C: K không áp cho ca cùng địa chỉ
    // với ca khác đang được làm). Nhóm không phục vụ liền nhau được → tách từng ca + cảnh báo.
    std::vector<Stop> stops = build_stops(today, rules);
    std::vector<Stop> grouped;
    for (Stop& stop : stops) {
        if (stop.tasks.size() > 1 && !group_servable(stop, rules, now)) {
            result.warnings.push_back({"", "nhóm cùng địa chỉ không phục vụ liền nhau được, tách từng ca", "STOP_GROUP_SPLIT"});
            for (const Task* task : stop.tasks) grouped.push_back({{task}});
            continue;
        }
        grouped.push_back(std::move(stop));
    }
    // 7.16.1 + mục C: ca tháng > K bị lọc chỉ khi nhóm của nó không còn ca nào khác được xếp.
    std::vector<Stop> routed_stops;
    for (Stop& stop : grouped) {
        bool any_kept = false;
        for (const Task* task : stop.tasks) any_kept |= !beyond_k(*task, now, rules);
        if (!any_kept) {
            for (const Task* task : stop.tasks) result.unplaced.push_back(task->task_id);
            continue;
        }
        routed_stops.push_back(std::move(stop));
    }
    result.routed = 0;
    for (const Stop& stop : routed_stops) result.routed += static_cast<int>(stop.tasks.size());
    result.excluded = worklist.stats.excluded_missing_location;

    const Minutes shift_start = day + staff.available.front().first;
    const Minutes shift_end = day + staff.available.back().second;  // Nhiều khung giờ: xử lý ở bước sau.
    Minutes start = std::max(now, shift_start);
    if (worklist.current_task) start = std::max(start, now + static_cast<Minutes>(rules.current_task_minutes));

    if (routed_stops.empty() || routed_stops.size() > 64) {
        result.response = error_response("422", routed_stops.empty() ? "Không có công việc để dựng tuyến" : "Quá 64 việc cho một KTV",
                                         message.message_id, server_now);
        return result;
    }

    // ---- Bài toán số: mọi mốc giờ đổi về phút kể từ lúc xuất phát. Mỗi điểm dừng = một "việc" của QHĐ.
    Problem p;
    std::vector<Point> points{staff.latlng};
    auto relative = [&](std::optional<Minutes> t) { return t ? static_cast<double>(*t - start) : kNone; };
    for (const Stop& stop : routed_stops) {
        points.push_back(*stop_anchor(stop).latlng);
        int service = 0;
        std::optional<Minutes> opens, due, complete_by;
        double weight = 0, urgency = 0;
        for (const Task* task : stop.tasks) {
            const TaskKind& kind = kind_or_default(task->task_group_name, task->task_type_name);  // Ngoài danh mục: chỉ lọt qua khi nới lỏng.
            const Deadlines d = resolve_deadlines(*task, kind, now);
            service += task->handle_minutes.value_or(kind.handle_minutes);
            if (d.opens && (!opens || *d.opens < *opens)) opens = d.opens;
            // min B (chặt hơn câu chữ catalogue "mốc B của ca neo"): tuyến tới muộn vẫn không vi phạm B của ca nào trong nhóm.
            if (d.due && (!due || *d.due < *due)) due = d.due;
            if (d.complete_by && (!complete_by || *d.complete_by < *complete_by)) complete_by = d.complete_by;
            weight = std::max(weight, rules.priority_weight[task->priority_in_day]);
            // 7.16.2: độ gấp cho ca chèn = K − số ngày làm việc còn lại (ca chính / không có hạn → 0); nhóm lấy max.
            const int days_left = d.complete_by ? workdays_until(now, *d.complete_by) : 0;
            const double own = kind.extra && d.complete_by ? static_cast<double>(std::max(0, rules.k_month_days - days_left)) : 0.0;
            urgency = std::max(urgency, own);
        }
        p.service.push_back(service);
        p.opens.push_back(relative(opens));
        p.due.push_back(relative(due));
        p.complete_by.push_back(relative(complete_by));
        p.weight.push_back(weight);
        p.urgency.push_back(urgency);
    }
    // Khu vực cho rule "quay lại khu vực đã rời" (AREA_REENTRY): biết lô → theo lô; lô 0 → lùi xuống block
    // (workbook (3): "không có lô thì tính ưu tiên xuống block_id"), chỉ so với task lô 0 khác; lô 0 + block 0 → không
    // thuộc khu vực nào. Task biết lô không gộp với task lô 0 cùng block (thận trọng, giữ như trước 2026-10-02).
    // 7.16.3: điểm dừng lấy lô của ca neo (các ca cùng nhóm cùng địa chỉ nên cùng khu vực).
    auto same_area = [](const Task& a, const Task& b) {
        if (a.task_plots_id != 0 || b.task_plots_id != 0) return a.task_plots_id != 0 && a.task_plots_id == b.task_plots_id;
        return a.block_id != 0 && a.block_id == b.block_id;
    };
    for (size_t i = 0; i < routed_stops.size(); ++i) {
        uint64_t mask = 0;
        for (size_t j = 0; j < routed_stops.size(); ++j)
            if (j != i && same_area(stop_anchor(routed_stops[i]), stop_anchor(routed_stops[j])))
                mask |= uint64_t{1} << j;
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
    std::vector<const Visit*> visits;  // Chỉ các bước tới điểm dừng thật (bỏ nghỉ trưa).
    for (const Visit& v : steps)
        if (v.task != kBreak) visits.push_back(&v);
    int total = 0;  // số CA (không phải số điểm dừng) — giữ contract tasks_total
    for (const Stop& stop : routed_stops) total += static_cast<int>(stop.tasks.size());

    std::vector<ojson> rows;           // IDLE / BREAK / TASK theo đúng thứ tự thời gian.
    std::vector<int> row_task;         // Với mỗi row: TASK gần nhất tại/trước nó (-1 nếu trước TASK đầu).
    std::vector<TaskStop> task_stops;  // Chỉ TASK, cho summarize_clusters.
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
        // 7.16.3: một điểm dừng = một hoặc nhiều ca cùng địa chỉ; bung từng ca, giờ nối tiếp, km chỉ ở ca đầu.
        const Stop& stop = routed_stops[v.task];
        km += v.km;
        travel += v.travel;
        revisits += static_cast<int>(v.cost[AREA_REENTRY]);
        double offset = 0;  // phút kể từ lúc check-in điểm dừng
        for (size_t k = 0; k < stop.tasks.size(); ++k) {
            const Task& task = *stop.tasks[k];
            const TaskKind& kind = kind_or_default(task.task_group_name, task.task_type_name);
            const Deadlines d = resolve_deadlines(task, kind, now);
            const double service = task.handle_minutes.value_or(kind.handle_minutes);
            const double checkin = v.checkin + offset;
            const double done = checkin + service;
            const char* sla = projected_sla(checkin, done, relative(d.due), relative(d.complete_by), service, now_rel, rules);
            at_risk += std::string(sla) == "AT_RISK";
            breach += std::string(sla) == "WILL_BREACH" || std::string(sla) == "ALREADY_BREACHED";
            completed += at(done) <= shift_end;
            handle += service;
            ++task_ordinal;
            task_stops.push_back({&task, k == 0 ? v.km : 0.0, service});
            ojson row = {{"entry_type", "TASK"}, {"at", hhmm(at(checkin))},
                         {"start_at", format_datetime(at(checkin))}, {"end_at", format_datetime(at(done))},
                         {"task_id", task.task_id}, {"location", task.location},
                         {"latlng", latlng(*task.latlng, 6)},  // task đã qua lọc nên luôn có tọa độ
                         // Workbook (1): vai trò ca (main = ca chính theo hẹn SLA, inserted = ca chèn) + lý do chèn
                         // (ca chèn cùng địa chỉ với ca khác → "same_address"; chưa có lý do khác → "").
                         {"task_role", kind.extra ? "inserted" : "main"},
                         {"insert_reason", kind.extra && stop.tasks.size() > 1 ? "same_address" : ""},
                         {"task_group_id", task.task_group_id},
                         {"task_group_name", task.task_group_name}, {"task_type_id", task.task_type_id},
                         {"task_type_name", task.task_type_name}, {"task_sub_id", task.task_sub_id},
                         {"task_sub_name", task.task_sub_name}, {"checkindate", ""}, {"checkoutdate", ""},
                         {"travel_minutes_before", k == 0 ? std::llround(v.travel) : 0},
                         {"travel_km_before", k == 0 ? round_to(v.km, 1) : 0.0},
                         {"handle_minutes", std::llround(service)}, {"projected_sla", sla},
                         // Như input (người dùng chốt 7.9): số vẫn là số, chuỗi giữ nguyên; không gửi / null → null.
                         {"contract_id", or_null(task.contract_id)}, {"contract_no", or_null(task.contract_no)}};
            rows.push_back(std::move(row));
            row_task.push_back(task_ordinal);
            offset += service;
        }
    }

    // ---- Cụm: tóm tắt theo TASK, rồi gắn dòng timeline vào cụm (không đổi thứ tự).
    const std::vector<ClusterSummary> summaries = summarize_clusters(task_stops, staff.plots);
    std::vector<int> cluster_of_task(task_stops.size());
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
    ojson data = {{"staff_id", staff.staff_id}, {"priority_type", 0}, {"clusters", clusters}, {"metrics", metrics}};
    if (explain) {  // --explain: chi phí từng tầng/rule + vài phương án so sánh (không đổi thứ tự).
        const Explained chosen = explain_order(p, rules, solution.order);
        ojson tiers = ojson::array();
        for (size_t t = 0; t < chosen.tiers.size(); ++t)
            tiers.push_back({{"tier", static_cast<int>(t) + 1}, {"total", round_to(chosen.tiers[t], 2)},
                             {"rules", t < chosen.tier_rules.size() ? chosen.tier_rules[t] : ojson::object()}});
        ojson alternatives = ojson::array();
        const std::vector<Alternative> others = {best_break(p, rules, "gần nhất trước", nearest_order(p)),
                                                 best_break(p, rules, "hạn sớm trước", sorted_order(p, 0)),
                                                 best_break(p, rules, "ưu tiên cao trước", sorted_order(p, 1))};
        for (const Alternative& alt : others) {
            ojson alt_tiers = ojson::array();
            for (double value : alt.explained.tiers) alt_tiers.push_back(round_to(value, 2));
            alternatives.push_back({{"name", alt.name}, {"feasible", alt.explained.feasible}, {"tiers", alt_tiers},
                                    {"raw", alt.explained.feasible ? alt.explained.raw : ojson(nullptr)},
                                    {"verdict", verdict_of(chosen, alt)}});
        }
        data["score"] = {{"sequence_source", kSourceNames[static_cast<int>(solution.source)]},
                         {"tiers", tiers}, {"raw", chosen.raw}, {"alternatives", alternatives}};
    }
    result.response = {{"success", true}, {"statuscode", estimated ? "424" : "200"},
                       {"message", estimated ? "Không lấy được dữ liệu bản đồ, khoảng cách là ước lượng đường chim bay (" + travel_error + ")" : ""},
                       {"trace_id", message.message_id},
                       {"server_time", format_datetime(server_now)},
                       // priority_type (workbook (4)): 0 default · 1 SLA · 2 tuyến. Chưa có mode → luôn 0 (7.13).
                       {"data", std::move(data)}};
    return result;
}

}  // namespace ktv
