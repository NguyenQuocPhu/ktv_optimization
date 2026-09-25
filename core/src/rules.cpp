#include "ktv/rules.hpp"

#include <fstream>
#include <stdexcept>

namespace ktv {

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
         {FINISH, 0.01}},                          //   xong muộn 100 phút ≈ 1 km
    };
    return rules;
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
    if (data.contains("tiers")) {
        const auto& tiers = data["tiers"];
        if (!tiers.is_array() || tiers.empty() || tiers.size() > kMaxTiers) fail("tiers cần 1–4 tầng");
        rules.tiers.clear();
        bool seen[RULE_COUNT] = {};
        for (const auto& tier : tiers) {
            if (!tier.is_object() || tier.empty()) fail("mỗi tầng cần object {mã rule: trọng số}");
            auto& out = rules.tiers.emplace_back();
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
    }
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
    number("current_task_minutes", rules.current_task_minutes, 0);
    number("average_speed_kmh", rules.average_speed_kmh, 1);
    number("at_risk_minutes", rules.at_risk_minutes, 0);
    number("at_risk_ratio", rules.at_risk_ratio, 0);
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
    nlohmann::json tiers = nlohmann::json::array();
    for (const auto& tier : rules.tiers) {
        nlohmann::json out = nlohmann::json::object();
        for (auto [rule, weight] : tier) out[kRuleCodes[rule]] = weight;
        tiers.push_back(out);
    }
    return {
        {"tiers", tiers},
        {"priority_weights", {{"1", rules.priority_weight[1]}, {"2", rules.priority_weight[2]},
                              {"3", rules.priority_weight[3]}, {"4", rules.priority_weight[4]}}},
        {"default_priority_weight", rules.priority_weight[0]},
        {"max_exact_tasks", rules.max_exact_tasks},
        {"max_labels_per_state", rules.max_labels},
        {"current_task_minutes", rules.current_task_minutes},
        {"average_speed_kmh", rules.average_speed_kmh},
        {"at_risk_minutes", rules.at_risk_minutes},
        {"at_risk_ratio", rules.at_risk_ratio},
    };
}

}  // namespace ktv
