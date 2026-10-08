#include "ktv/rules.hpp"

#include <cstdio>
#include <fstream>
#include <stdexcept>

namespace ktv {

namespace {
std::string hhmm(int minutes) {
    char text[8];
    std::snprintf(text, sizeof text, "%02d:%02d", minutes / 60 % 100, minutes % 60);
    return text;
}
}  // namespace

Rules default_rules() {
    Rules rules;
    rules.tiers = {
        {{LATE_CHECKIN, 1}},                       // Tầng 1: đúng hẹn khách hàng.
        {{LATE_COMPLETION, 1}, {AFTER_SHIFT, 1}},  // Tầng 2: hoàn tất đúng hạn, trong ca.
        {{KM, 1},                                  // Tầng 3: quy về "km tương đương".
         {LATE_MINUTES, 0.1},                      //   10 phút trễ ≈ 1 km
         {TRAVEL_MINUTES, 0.05},                   //   20 phút đi ≈ 1 km
         {AREA_REENTRY, 2},                        //   quay lại lô 1 lần ≈ 2 km
         {PRIORITY_DELAY, 0.5},                    //   việc P1 làm muộn 1 giờ ≈ 2 km
         {DEADLINE_URGENCY, 0.5},                  //   việc gấp để muộn 1 giờ ≈ 0,5 km (7.16.2)
         {FINISH, 0.01},                           //   xong muộn 100 phút ≈ 1 km
         {SKIP_OPTIONAL, 2}},                      //   7.24: bỏ 1 ca tuỳ chọn ≈ 2 km — chèn khi tốn thêm < 2 [GIẢ ĐỊNH, BR-16]
    };
    // Mode Tuyến 100% (7.26, yêu cầu data — thay R2 của 7.23): quãng đường lên tầng 1, kèm trễ hẹn trọng số nhỏ
    // (1 ca trễ hẹn ≈ 1 km × trọng số ưu tiên: P4 1 km … P1 4 km) để phân xử các tuyến dài gần bằng nhau.
    // Đo 400 biến thể sample_in (chim bay): km −23% so SLA, trễ hẹn +30% (trễ hẹn ở tầng 2: km −25%, trễ hẹn +69%).
    rules.route_tiers = {
        {{KM, 1}, {TRAVEL_MINUTES, 0.05}, {AREA_REENTRY, 2}, {SKIP_OPTIONAL, 2}, {LATE_CHECKIN, 1}},
        {{LATE_COMPLETION, 1}, {AFTER_SHIFT, 1}},
        {{LATE_MINUTES, 0.1}, {PRIORITY_DELAY, 0.5}, {DEADLINE_URGENCY, 0.5}, {FINISH, 0.01}},
    };
    rules.mix_tiers = mix_tiers_from(rules.mix_sla_share, rules.mix_breach_km);
    return rules;
}

Tiers mix_tiers_from(double sla_share, double breach_km) {
    // Tầng 1 = share × SLA + (1 − share) × Tuyến, quy về km: mỗi ca trễ hẹn (× trọng số ưu tiên) / trễ hạn / quá giờ
    // ≈ share/(1 − share) × breach_km km. Không còn tầng "đúng hẹn tuyệt đối": đủ km thì chịu trễ (yêu cầu data).
    const double breach = sla_share / (1 - sla_share) * breach_km;
    return {
        {{KM, 1}, {TRAVEL_MINUTES, 0.05}, {AREA_REENTRY, 2}, {SKIP_OPTIONAL, 2},
         {LATE_CHECKIN, breach}, {LATE_COMPLETION, breach}, {AFTER_SHIFT, breach}},
        {{LATE_MINUTES, 0.1}, {PRIORITY_DELAY, 0.5}, {DEADLINE_URGENCY, 0.5}, {FINISH, 0.01}},
    };
}

Rules rules_from_json(const nlohmann::json& data) {
    Rules rules = default_rules();
    auto fail = [](const std::string& text) { throw std::runtime_error("rules: " + text); };
    if (!data.is_object()) fail("cần object JSON");
    auto number = [&](const char* key, double& target, double low) {
        if (!data.contains(key)) return;
        if (!data[key].is_number() || data[key].get<double>() < low) fail(std::string(key) + " cần số ≥ " + std::to_string(low));
        target = data[key].get<double>();
    };
    auto read_tiers = [&](const char* key, Tiers& target) {
        if (!data.contains(key)) return;
        const auto& tiers = data[key];
        if (!tiers.is_array() || tiers.empty() || tiers.size() > kMaxTiers) fail(std::string(key) + " cần 1–4 tầng");
        target.clear();
        bool seen[RULE_COUNT] = {};
        for (const auto& tier : tiers) {
            if (!tier.is_object() || tier.empty()) fail("mỗi tầng cần object {mã rule: trọng số}");
            auto& out = target.emplace_back();
            for (auto it = tier.begin(); it != tier.end(); ++it) {
                int rule = 0;
                while (rule < RULE_COUNT && it.key() != kRuleCodes[rule]) ++rule;
                if (rule == RULE_COUNT) fail("rule không có: " + it.key());
                if (seen[rule]) fail("rule nằm ở hai tầng: " + it.key());
                if (!it->is_number() || it->get<double>() < 0) fail("trọng số cần số ≥ 0: " + it.key());
                seen[rule] = true;
                out.emplace_back(static_cast<Rule>(rule), it->get<double>());
            }
        }
    };
    read_tiers("tiers", rules.tiers);
    read_tiers("route_tiers", rules.route_tiers);  // 7.23 mode Tuyến
    number("mix_sla_share", rules.mix_sla_share, 0);  // 7.26 mode Kết nối
    if (rules.mix_sla_share >= 1) fail("mix_sla_share cần trong [0, 1) — 100% SLA là priority_type 1");
    number("mix_breach_km", rules.mix_breach_km, 0);
    rules.mix_tiers = mix_tiers_from(rules.mix_sla_share, rules.mix_breach_km);
    read_tiers("mix_tiers", rules.mix_tiers);  // ghi rõ trong rules.json thì thắng 2 tham số trên
    if (data.contains("priority_weights")) {
        const auto& weights = data["priority_weights"];
        if (!weights.is_object()) fail("priority_weights cần object {\"1\": 4, ...}");
        for (auto it = weights.begin(); it != weights.end(); ++it) {
            if (it.key().size() != 1 || it.key()[0] < '1' || it.key()[0] > '4' || !it->is_number())
                fail("priority_weights cần khóa \"1\"–\"4\", giá trị số");
            rules.priority_weight[it.key()[0] - '0'] = it->get<double>();
        }
    }
    number("default_priority_weight", rules.priority_weight[0], 0);
    double exact = rules.max_exact_tasks, labels = rules.max_labels;
    number("max_exact_tasks", exact, 0);
    number("max_labels_per_state", labels, 1);
    if (exact > 16) fail("max_exact_tasks tối đa 16");  // Bộ nhớ QHĐ tăng theo 2^n.
    rules.max_exact_tasks = static_cast<int>(exact);
    rules.max_labels = static_cast<int>(labels);
    if (data.contains("large_method")) {  // Phase 8
        const auto& method = data["large_method"];
        if (!method.is_string() || (method != "improve" && method != "lns" && method != "lns_window" && method != "alns_dp"))
            fail("large_method cần \"improve\", \"lns\", \"lns_window\" hoặc \"alns_dp\"");
        rules.large_method = method.get<std::string>();
    }
    double iterations = rules.lns_iterations, window = rules.window_size;
    number("lns_iterations", iterations, 0);
    number("window_size", window, 2);
    if (window > 12) fail("window_size tối đa 12");  // QHĐ cửa sổ 2^w ô
    rules.lns_iterations = static_cast<int>(iterations);
    rules.window_size = static_cast<int>(window);
    double k_month = rules.k_month_days;
    number("k_month_days", k_month, 0);
    rules.k_month_days = static_cast<int>(k_month);
    number("stop_group_radius_m", rules.stop_group_radius_m, 0);
    double group_wait = rules.stop_group_max_wait_minutes;
    number("stop_group_max_wait_minutes", group_wait, 0);
    rules.stop_group_max_wait_minutes = static_cast<int>(group_wait);
    number("current_task_minutes", rules.current_task_minutes, 0);
    number("average_speed_kmh", rules.average_speed_kmh, 1);
    number("at_risk_minutes", rules.at_risk_minutes, 0);
    number("at_risk_ratio", rules.at_risk_ratio, 0);
    number("lunch_break_minutes", rules.break_minutes, 0);
    if (data.contains("lunch_break")) {  // "11:30-13:30"
        int h1, m1, h2, m2;
        const auto& text = data["lunch_break"];
        if (!text.is_string() || std::sscanf(text.get<std::string>().c_str(), "%d:%d-%d:%d", &h1, &m1, &h2, &m2) != 4)
            fail("lunch_break cần dạng \"HH:mm-HH:mm\"");
        rules.break_start = h1 * 60 + m1;
        rules.break_end = h2 * 60 + m2;
    }
    double cache_age = rules.cache_max_age_minutes;
    number("cache_max_age_minutes", cache_age, 0);
    rules.cache_max_age_minutes = static_cast<int>(cache_age);
    if (data.contains("force_recompute_triggers")) {
        const auto& triggers = data["force_recompute_triggers"];
        if (!triggers.is_array()) fail("force_recompute_triggers cần mảng chuỗi");
        rules.force_recompute_triggers.clear();
        for (const auto& trigger : triggers) {
            if (!trigger.is_string()) fail("force_recompute_triggers cần mảng chuỗi");
            rules.force_recompute_triggers.push_back(trigger.get<std::string>());
        }
    }
    if (rules.break_minutes > 0 && rules.break_end - rules.break_start < rules.break_minutes)
        fail("khung lunch_break ngắn hơn lunch_break_minutes");
    return rules;
}

Rules load_rules(const std::string& path) {
    std::ifstream in(path);
    if (!in) throw std::runtime_error("rules: không mở được " + path);
    nlohmann::json data = nlohmann::json::parse(in, nullptr, false);
    if (data.is_discarded()) throw std::runtime_error("rules: JSON hỏng " + path);
    return rules_from_json(data);
}

nlohmann::json rules_to_json(const Rules& rules) {
    auto tiers_json = [](const Tiers& source) {
        nlohmann::json tiers = nlohmann::json::array();
        for (const auto& tier : source) {
            nlohmann::json out = nlohmann::json::object();
            for (auto [rule, weight] : tier) out[kRuleCodes[rule]] = weight;
            tiers.push_back(out);
        }
        return tiers;
    };
    return {
        {"tiers", tiers_json(rules.tiers)},
        {"route_tiers", tiers_json(rules.route_tiers)},
        {"mix_sla_share", rules.mix_sla_share},
        {"mix_breach_km", rules.mix_breach_km},
        {"mix_tiers", tiers_json(rules.mix_tiers)},
        {"priority_weights", {{"1", rules.priority_weight[1]}, {"2", rules.priority_weight[2]},
                              {"3", rules.priority_weight[3]}, {"4", rules.priority_weight[4]}}},
        {"default_priority_weight", rules.priority_weight[0]},
        {"max_exact_tasks", rules.max_exact_tasks},
        {"max_labels_per_state", rules.max_labels},
        {"large_method", rules.large_method},
        {"lns_iterations", rules.lns_iterations},
        {"window_size", rules.window_size},
        {"k_month_days", rules.k_month_days},
        {"stop_group_radius_m", rules.stop_group_radius_m},
        {"stop_group_max_wait_minutes", rules.stop_group_max_wait_minutes},
        {"current_task_minutes", rules.current_task_minutes},
        {"average_speed_kmh", rules.average_speed_kmh},
        {"at_risk_minutes", rules.at_risk_minutes},
        {"at_risk_ratio", rules.at_risk_ratio},
        {"lunch_break", hhmm(rules.break_start) + "-" + hhmm(rules.break_end)},
        {"lunch_break_minutes", rules.break_minutes},
        {"cache_max_age_minutes", rules.cache_max_age_minutes},
        {"force_recompute_triggers", rules.force_recompute_triggers},
    };
}

}  // namespace ktv
