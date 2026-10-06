// Một message API đi hết đường: đọc → xếp → output đúng dạng file API (sheet 03, 04).
#include <cmath>
#include <iostream>

#include "ktv/plan.hpp"

static int failures = 0;
#define CHECK(cond)                                                           \
    do {                                                                      \
        if (!(cond)) {                                                        \
            std::cerr << __FILE__ << ":" << __LINE__ << ": FAIL " #cond "\n"; \
            ++failures;                                                       \
        }                                                                     \
    } while (0)

using ktv::json;

// JSON mẫu sheet 08 (rút gọn): KTV ở Trung Kính lúc 09:20; việc triển khai hẹn 14:00, hóa đơn không hẹn.
static json message() {
    return json::parse(R"({
      "message_id": "M1", "planned_at": "2026-09-10 09:20:00", "trigger": "DAY_START",
      "staff": {"staff_id": "00039434", "staff_account": "TIN0101.TUYEN9", "latlng": "21.0248,105.7961",
                "plots": [{"id": 2, "name": "Trung Kính", "role": 1, "block_id": 3434}],
                "available": "08:00-17:30", "current_task": null},
      "tasks": {
        "trien_khai": [{"task_id": 5454541, "task_group_id": 1, "task_group_name": "trien_khai", "task_type_id": 3,
          "task_type_name": "trien_khai_net", "task_sub_id": 0, "task_sub_name": "", "task_status_id": 6,
          "task_status_name": "check_in", "sla": {"sla_minutes": 120, "priority_in_day": 3},
          "appointment": "2026-09-10 14:00:00", "location": "Trần Duy Hưng", "latlng": "21.0122,105.7995",
          "handle_minutes": 90, "task_plots_id": 2, "staff_plots_id": 2, "staff_role": 1, "block_id": 3434}],
        "bao_tri": [], "thu_hoi": [],
        "hoa_don": [{"task_id": 5454544, "task_group_id": 4, "task_group_name": "hoa_don", "task_type_id": 2,
          "task_type_name": "hoa_don_tra_sau", "task_sub_id": 0, "task_sub_name": "", "task_status_id": 6,
          "task_status_name": "check_in", "sla": {"sla_minutes": null, "priority_in_day": 4}, "appointment": "",
          "location": "Trung Hòa", "latlng": "21.0043,105.8021", "handle_minutes": 20, "task_plots_id": 7,
          "staff_plots_id": 2, "staff_role": 2, "block_id": 3434},
          {"task_id": 5454545, "task_group_id": 4, "task_group_name": "hoa_don", "task_type_id": 2,
          "task_type_name": "hoa_don_tra_sau", "task_sub_id": 0, "task_sub_name": "", "task_status_id": 6,
          "task_status_name": "check_in", "sla": {"sla_minutes": null, "priority_in_day": 4}, "appointment": "",
          "location": "Không tọa độ", "latlng": "", "handle_minutes": 20, "task_plots_id": 7,
          "staff_plots_id": 2, "staff_role": 2, "block_id": 3434}],
        "onsite": []}})");
}

int main() {
    std::vector<ktv::Error> errors;
    ktv::Message parsed = ktv::parse_message(message(), errors);
    CHECK(errors.empty());
    ktv::Minutes server_now = *ktv::parse_datetime("2026-09-10 09:20:05");
    ktv::PlanResult result = ktv::plan(parsed, ktv::default_rules(), server_now);
    const auto& r = result.response;
    CHECK(r["success"] == true && r["statuscode"] == "200" && r["trace_id"] == "M1");
    CHECK(result.routed == 2 && result.excluded == 1 && result.source == ktv::Source::Optimal);

    const auto& schedule = r["data"]["clusters"][0]["schedule"];
    // Hóa đơn làm trước (không hẹn) → chờ tới 11:30 nghỉ trưa 45 phút → sang việc triển khai, chờ tới hẹn 14:00.
    CHECK(schedule.size() == 5);
    CHECK(schedule[0]["entry_type"] == "TASK" && schedule[0]["task_id"] == 5454544);
    CHECK(schedule[1]["entry_type"] == "IDLE" && schedule[1]["end_at"] == "2026-09-10 11:30:00");
    CHECK(schedule[2]["entry_type"] == "BREAK" && schedule[2]["start_at"] == "2026-09-10 11:30:00" &&
          schedule[2]["end_at"] == "2026-09-10 12:15:00" && schedule[2]["label"] == "Nghỉ trưa");
    CHECK(schedule[3]["entry_type"] == "IDLE" && schedule[3]["end_at"] == "2026-09-10 14:00:00");
    CHECK(schedule[4]["task_id"] == 5454541 && schedule[4]["start_at"] == "2026-09-10 14:00:00");
    CHECK(schedule[4]["end_at"] == "2026-09-10 15:30:00" && schedule[4]["projected_sla"] == "ON_TIME");
    for (int i = 0; i < 5; ++i) CHECK(schedule[i]["seq"] == i + 1);
    CHECK(r["data"]["clusters"][0]["task_count"] == 2);

    const auto& m = r["data"]["metrics"];
    CHECK(m["tasks_total"] == 2 && m["tasks_forecast_completed"] == 2 && m["overload_minutes"] == 0);
    CHECK(m["on_time_rate_forecast"] == 100.0 && m["breach_forecast_count"] == 0);
    CHECK(m["start_at"] == "2026-09-10 09:20:00" && m["finish_at"] == "2026-09-10 15:30:00");
    CHECK(m["shift_end_at"] == "2026-09-10 17:30:00" && m["break_minutes"] == 45);

    // Tính lại lúc 14:10 (đã qua giờ chốt nghỉ 12:45): không còn dòng nghỉ.
    json late = message();
    late["planned_at"] = "2026-09-10 14:10:00";
    errors.clear();
    ktv::PlanResult after = ktv::plan(ktv::parse_message(late, errors), ktv::default_rules(), server_now);
    CHECK(after.response["data"]["metrics"]["break_minutes"] == 0);
    for (const auto& row : after.response["data"]["clusters"][0]["schedule"]) CHECK(row["entry_type"] != "BREAK");

    // Chỉ còn việc thiếu tọa độ → 422.
    json empty = message();
    empty["tasks"]["trien_khai"] = json::array();
    empty["tasks"]["hoa_don"].erase(0);
    errors.clear();
    ktv::PlanResult none = ktv::plan(ktv::parse_message(empty, errors), ktv::default_rules(), server_now);
    CHECK(none.response["statuscode"] == "422" && none.response["data"].is_null());

    // --explain: thêm data.score (chi phí từng tầng/rule + phương án so sánh); tuyến QHĐ không thua phương án nào.
    ktv::PlanResult explained = ktv::plan(parsed, ktv::default_rules(), server_now, "", true);
    const auto& score = explained.response["data"]["score"];
    CHECK(score.is_object() && score["tiers"].size() == 3 && score["alternatives"].size() == 3);
    CHECK(score["sequence_source"] == "OPTIMAL");
    CHECK(score["raw"]["km"].get<double>() == explained.response["data"]["metrics"]["total_distance_km"].get<double>());
    for (const auto& tier : score["tiers"]) {  // tổng tầng = tổng các rule trong tầng (chi tiết phải khớp số đã chấm)
        double sum = 0;
        for (auto it = tier["rules"].begin(); it != tier["rules"].end(); ++it) sum += it.value().get<double>();
        CHECK(std::abs(sum - tier["total"].get<double>()) < 0.02);
    }
    const double chosen[3] = {score["tiers"][0]["total"].get<double>(), score["tiers"][1]["total"].get<double>(),
                              score["tiers"][2]["total"].get<double>()};
    bool never_worse = true;
    for (const auto& alt : score["alternatives"]) {
        if (!alt["feasible"].get<bool>()) continue;
        for (int t = 0; t < 3; ++t) {
            const double value = alt["tiers"][t].get<double>();
            if (value > chosen[t] + 1e-9) break;               // tệ hơn ở tầng này → QHĐ thắng, đúng
            if (value < chosen[t] - 1e-9) never_worse = false;  // tốt hơn mà các tầng trước bằng → sai
        }
    }
    CHECK(never_worse);
    CHECK(!r["data"].contains("score"));  // mặc định không explain: OUT giữ nguyên contract

    if (failures) std::cerr << failures << " lỗi\n" << r.dump(2) << "\n";
    else std::cout << "test_plan: OK\n";
    return failures != 0;
}
