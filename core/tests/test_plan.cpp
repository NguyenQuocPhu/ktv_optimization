// Một message API đi hết đường: đọc → xếp → output đúng dạng file API (sheet 03, 04).
#include <cmath>
#include <algorithm>
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
      "message_id": "M1", "planned_at": "2026-09-28 09:20:00", "trigger": "DAY_START",
      "staff": {"staff_id": "00039434", "staff_account": "TIN0101.TUYEN9", "latlng": "21.0248,105.7961",
                "plots": [{"id": 2, "name": "Trung Kính", "role": 1, "block_id": 3434}],
                "available": "08:00-17:30", "current_task": null},
      "tasks": {
        "trien_khai": [{"task_id": 5454541, "task_group_id": 1, "task_group_name": "trien_khai", "task_type_id": 3,
          "task_type_name": "trien_khai_net", "task_sub_id": 0, "task_sub_name": "", "task_status_id": 6,
          "task_status_name": "check_in", "sla": {"sla_minutes": 120, "priority_in_day": 3},
          "appointment": "2026-09-28 14:00:00", "location": "Trần Duy Hưng", "latlng": "21.0122,105.7995",
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
    ktv::Minutes server_now = *ktv::parse_datetime("2026-09-28 09:20:05");
    ktv::PlanResult result = ktv::plan(parsed, ktv::default_rules(), server_now);
    const auto& r = result.response;
    CHECK(r["success"] == true && r["statuscode"] == "200" && r["trace_id"] == "M1");
    CHECK(result.routed == 2 && result.excluded == 1 && result.source == ktv::Source::Optimal);

    const auto& schedule = r["data"]["clusters"][0]["schedule"];
    // Hóa đơn làm trước (không hẹn) → chờ tới 11:30 nghỉ trưa 45 phút → sang việc triển khai, chờ tới hẹn 14:00.
    CHECK(schedule.size() == 5);
    CHECK(schedule[0]["entry_type"] == "TASK" && schedule[0]["task_id"] == 5454544);
    CHECK(schedule[1]["entry_type"] == "IDLE" && schedule[1]["end_at"] == "2026-09-28 11:30:00");
    CHECK(schedule[2]["entry_type"] == "BREAK" && schedule[2]["start_at"] == "2026-09-28 11:30:00" &&
          schedule[2]["end_at"] == "2026-09-28 12:15:00" && schedule[2]["label"] == "Nghỉ trưa");
    CHECK(schedule[3]["entry_type"] == "IDLE" && schedule[3]["end_at"] == "2026-09-28 14:00:00");
    CHECK(schedule[4]["task_id"] == 5454541 && schedule[4]["start_at"] == "2026-09-28 14:00:00");
    CHECK(schedule[4]["end_at"] == "2026-09-28 15:30:00" && schedule[4]["projected_sla"] == "ON_TIME");
    for (int i = 0; i < 5; ++i) CHECK(schedule[i]["seq"] == i + 1);
    CHECK(r["data"]["clusters"][0]["task_count"] == 2);

    const auto& m = r["data"]["metrics"];
    CHECK(m["tasks_total"] == 2 && m["tasks_forecast_completed"] == 2 && m["overload_minutes"] == 0);
    CHECK(m["on_time_rate_forecast"] == 100.0 && m["breach_forecast_count"] == 0);
    CHECK(m["start_at"] == "2026-09-28 09:20:00" && m["finish_at"] == "2026-09-28 15:30:00");
    CHECK(m["shift_end_at"] == "2026-09-28 17:30:00" && m["break_minutes"] == 45);

    // Tính lại lúc 14:10 (đã qua giờ chốt nghỉ 12:45): không còn dòng nghỉ.
    json late = message();
    late["planned_at"] = "2026-09-28 14:10:00";
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
    CHECK(score["tiers"][2]["rules"].contains("DEADLINE_URGENCY"));
    CHECK(score["tiers"][2]["rules"]["DEADLINE_URGENCY"].get<double>() > 0);  // hóa đơn (ca chèn) còn 2 ngày ≤ K → có urgency
    CHECK(never_worse);
    CHECK(!r["data"].contains("score"));  // mặc định không explain: OUT giữ nguyên contract

    // 7.16.1 — K: chạy ngày 10/09 (thứ Năm), hóa đơn hạn cuối tháng còn 14 ngày làm việc > K=5 → không xếp.
    json early = message();
    early["planned_at"] = "2026-09-10 09:20:00";
    early["tasks"]["trien_khai"][0]["appointment"] = "2026-09-10 14:00:00";
    errors.clear();
    ktv::PlanResult k_filtered = ktv::plan(ktv::parse_message(early, errors), ktv::default_rules(), server_now, "", true);
    CHECK(errors.empty());
    CHECK(k_filtered.response["data"]["metrics"]["tasks_total"] == 1);
    CHECK(k_filtered.response["data"]["score"]["tiers"][2]["rules"]["DEADLINE_URGENCY"].get<double>() == 0);  // chỉ còn ca chính → urgency 0
    CHECK(k_filtered.unplaced.size() == 1 && k_filtered.unplaced[0] == 5454544);
    int k_deferred = 0;  // 7.20.2: ca bị lọc K vẫn trả về — dòng DEFERRED BEYOND_K, không phải TASK
    for (const auto& cluster : k_filtered.response["data"]["clusters"])
        for (const auto& row : cluster["schedule"]) {
            CHECK(row["entry_type"] != "TASK" || row["task_id"] != 5454544);
            if (row["entry_type"] == "DEFERRED") {
                ++k_deferred;
                CHECK(row["task_id"] == 5454544 && row["insert_reason"] == "BEYOND_K" && row["seq"] == 0);
                CHECK(row["at"] == "" && row["start_at"] == "" && row["task_role"] == "inserted" && row["projected_sla"] == "");
                CHECK(row["travel_km_before"] == 0.0 && row["handle_minutes"] == 20);
            }
        }
    CHECK(k_deferred == 1);
    {  // 7.17 mục E: cùng ngày 10/09, KH thanh toán kỳ trước ngày 10 → hóa đơn đến hạn hôm nay: không lọc K, urgency max.
        json paid = early;
        paid["tasks"]["hoa_don"][0]["complete_date"] = "2026-08-10 16:00:00";
        errors.clear();
        ktv::PlanResult due_today = ktv::plan(ktv::parse_message(paid, errors), ktv::default_rules(), server_now, "", true);
        CHECK(errors.empty());
        CHECK(due_today.unplaced.empty());
        CHECK(due_today.response["data"]["metrics"]["tasks_total"] == 2);
        CHECK(due_today.response["data"]["score"]["tiers"][2]["rules"]["DEADLINE_URGENCY"].get<double>() > 0);
    }
    // Ngày 28/09 (2 ngày làm việc ≤ K) thì hóa đơn vẫn xếp, không ca nào bị lọc — đã kiểm ở trên.
    CHECK(result.unplaced.empty());

    {  // 7.18 + 7.20.2: ca hẹn ngày sau không xếp tuyến, trả về dòng DEFERRED NEXT_DAY; hẹn ngày đã qua vẫn xếp.
        const auto run = [&](const char* appointment) {
            json data = message();
            data["tasks"]["trien_khai"][0]["appointment"] = appointment;
            errors.clear();
            ktv::PlanResult out = ktv::plan(ktv::parse_message(data, errors), ktv::default_rules(), server_now, "");
            CHECK(errors.empty());
            return out;
        };
        ktv::PlanResult tomorrow = run("2026-09-29 09:00:00");
        CHECK(tomorrow.unplaced.size() == 1 && tomorrow.unplaced[0] == 5454541);
        const json& m = tomorrow.response["data"]["metrics"];
        CHECK(m["tasks_total"] == 1 && m["finish_at"].get<std::string>().substr(0, 10) == "2026-09-28");
        CHECK(m["overload_minutes"] == 0);
        int next_day = 0;
        for (const auto& cluster : tomorrow.response["data"]["clusters"])
            for (const auto& row : cluster["schedule"]) {
                CHECK(row["entry_type"] != "TASK" || row["task_id"] != 5454541);
                if (row["entry_type"] == "DEFERRED") {
                    ++next_day;
                    CHECK(row["task_id"] == 5454541 && row["insert_reason"] == "NEXT_DAY");
                    CHECK(cluster["task_count"] == 1);  // DEFERRED không đếm vào cụm
                    continue;
                }
                CHECK(row["end_at"].get<std::string>().substr(0, 10) == "2026-09-28");  // không IDLE qua đêm
            }
        CHECK(next_day == 1);
        ktv::PlanResult yesterday = run("2026-09-27 14:00:00");
        CHECK(yesterday.unplaced.empty() && yesterday.response["data"]["metrics"]["tasks_total"] == 2);
        ktv::PlanResult late_today = run("2026-09-28 23:30:00");  // cuối ngày chạy vẫn là hôm nay
        CHECK(late_today.unplaced.empty());
        // Chỉ còn ca ngày sau → 7.20.2: 200, một cụm chỉ có DEFERRED, metrics = 0 (trước: 422).
        json only = message();
        only["tasks"]["trien_khai"][0]["appointment"] = "2026-09-30 09:00:00";
        only["tasks"]["hoa_don"] = json::array();
        errors.clear();
        const json lone = ktv::plan(ktv::parse_message(only, errors), ktv::default_rules(), server_now, "").response;
        CHECK(lone["statuscode"] == "200" && lone["data"]["clusters"].size() == 1);
        const json& lone_cluster = lone["data"]["clusters"][0];
        CHECK(lone_cluster["task_count"] == 0 && lone_cluster["handle_minutes"] == 0 && lone_cluster["center"] == "21.0122,105.7995");
        CHECK(lone_cluster["schedule"].size() == 1 && lone_cluster["schedule"][0]["entry_type"] == "DEFERRED");
        CHECK(lone_cluster["schedule"][0]["insert_reason"] == "NEXT_DAY");
        CHECK(lone["data"]["metrics"]["tasks_total"] == 0 && lone["data"]["metrics"]["total_distance_km"] == 0.0);
        CHECK(lone["data"]["metrics"]["cluster_count"] == 1 && lone["data"]["metrics"]["on_time_rate_forecast"] == 100.0);
        // Rỗng thật (không ca nào trả được) → vẫn 422.
        json none = only;
        none["tasks"]["trien_khai"] = json::array();
        errors.clear();
        CHECK(ktv::plan(ktv::parse_message(none, errors), ktv::default_rules(), server_now, "").response["statuscode"] == "422");
    }
    {  // 7.20.2: DEFERRED gắn vào cụm có tâm gần nhất, cuối schedule, sắp theo task_id.
        json data = message();
        data["tasks"]["hoa_don"] = json::array();
        json far = data["tasks"]["trien_khai"][0];  // ca xếp hôm nay ở Trần Duy Hưng (21.0122,105.7995)
        far["task_id"] = 5454600;
        far["latlng"] = "21.0500,105.8500";  // ca xếp hôm nay thứ 2, xa > 2 km → cụm riêng
        far["appointment"] = "2026-09-28 16:00:00";
        data["tasks"]["trien_khai"].push_back(far);
        for (long long id : {5454702LL, 5454701LL}) {  // 2 ca hẹn mai sát ca "far" → vào cụm của "far"
            json next = far;
            next["task_id"] = id;
            next["latlng"] = "21.0505,105.8505";
            next["appointment"] = "2026-09-29 09:00:00";
            data["tasks"]["trien_khai"].push_back(next);
        }
        errors.clear();
        const json r = ktv::plan(ktv::parse_message(data, errors), ktv::default_rules(), server_now, "").response;
        CHECK(errors.empty() && r["data"]["clusters"].size() == 2);
        for (const auto& cluster : r["data"]["clusters"]) {
            const auto& schedule = cluster["schedule"];
            const bool has_far = std::any_of(schedule.begin(), schedule.end(), [](const json& row) { return row["task_id"] == 5454600; });
            std::vector<long long> ids;
            for (const auto& row : schedule)
                if (row["entry_type"] == "DEFERRED") ids.push_back(row["task_id"]);
            CHECK(has_far ? ids == std::vector<long long>({5454701, 5454702}) : ids.empty());
            if (has_far) CHECK(schedule.back()["entry_type"] == "DEFERRED" && schedule[schedule.size() - 3]["entry_type"] != "DEFERRED");
        }
        CHECK(r["data"]["metrics"]["tasks_total"] == 2);
    }

    {  // 7.16.3: gom ca cùng địa chỉ thành một điểm dừng; ca tháng > K vẫn được gom theo ca khác cùng địa chỉ (K không áp).
        json data = message();
        data["planned_at"] = "2026-09-10 09:20:00";
        data["tasks"]["trien_khai"][0]["appointment"] = "2026-09-10 14:00:00";
        json& hoa = data["tasks"]["hoa_don"][0];
        hoa["latlng"] = "21.0122,105.7995";  // trùng toạ độ với việc triển khai
        hoa["location"] = "Trần Duy Hưng";
        errors.clear();
        ktv::PlanResult grouped = ktv::plan(ktv::parse_message(data, errors), ktv::default_rules(), server_now, "", true);
        CHECK(errors.empty());
        CHECK(grouped.response["data"]["metrics"]["tasks_total"] == 2);
        CHECK(grouped.unplaced.empty());  // hóa đơn còn 14 ngày > K nhưng được gom nên không lọc
        std::vector<json> task_rows;
        for (const auto& cluster : grouped.response["data"]["clusters"])
            for (const auto& row : cluster["schedule"])
                if (row["entry_type"] == "TASK") task_rows.push_back(row);
        CHECK(task_rows.size() == 2);
        CHECK(task_rows[0]["task_id"] == 5454541 && task_rows[1]["task_id"] == 5454544);  // trong nhóm: ưu tiên P3 trước P4
        CHECK(task_rows[0]["task_role"] == "main" && task_rows[1]["task_role"] == "inserted");  // workbook (1)
        CHECK(task_rows[0]["insert_reason"] == "" && task_rows[1]["insert_reason"] == "same_address");
        CHECK(task_rows[0]["end_at"] == task_rows[1]["start_at"]);
        CHECK(task_rows[1]["travel_km_before"] == 0.0);  // km chỉ tính ở ca đầu của điểm dừng
    }

    if (failures) std::cerr << failures << " lỗi\n" << r.dump(2) << "\n";
    else std::cout << "test_plan: OK\n";
    return failures != 0;
}
