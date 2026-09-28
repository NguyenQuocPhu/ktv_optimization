// Normalization: lọc task theo status/complete/location và tách current_task khỏi ứng viên.
#include <iostream>

#include "ktv/normalization.hpp"
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

// Một task hợp lệ theo contract: chỉ status/latlng/complete_date thay đổi giữa các ca test.
static json make_task(long long id, const char* group, int gid, const char* type, int tid, json sla_minutes,
                      int priority, int status, const char* latlng, const char* complete_date) {
    return json{
        {"task_id", id},
        {"task_group_id", gid}, {"task_group_name", group},
        {"task_type_id", tid}, {"task_type_name", type},
        {"task_sub_id", 0}, {"task_sub_name", ""},
        {"task_status_id", status}, {"task_status_name", ""},
        {"sla", {{"sla_minutes", sla_minutes}, {"priority_in_day", priority}}},
        {"appointment", ""}, {"create_date", ""}, {"complete_date", complete_date},
        {"location", ""}, {"latlng", latlng}, {"handle_minutes", ""},
        {"task_plots_id", 1}, {"staff_plots_id", 1}, {"staff_role", 1}, {"block_id", 1}};
}

static json make_message(const char* staff_status, json current, json trien, json bao, json thu, json hoa, json onsite) {
    json staff = {
        {"staff_id", "1"}, {"staff_account", "A"}, {"latlng", "21.02,105.80"},
        {"available", "08:00-17:30"},
        {"plots", json::array({{{"id", 1}, {"name", "P"}, {"role", 1}, {"block_id", 1}}})},
        {"current_task", current}};
    if (staff_status) staff["status"] = std::stoi(staff_status);  // nullptr = payload không gửi status.
    return json{{"message_id", "M"}, {"planned_at", "2026-09-10 09:00:00"}, {"trigger", "DAY_START"},
                {"staff", staff},
                {"tasks", {{"trien_khai", trien}, {"bao_tri", bao}, {"thu_hoi", thu}, {"hoa_don", hoa}, {"onsite", onsite}}}};
}

int main() {
    const json empty = json::array();
    const json current = {{"task_id", 106}, {"task_status_id", 10}, {"task_type_id", 1}};
    const json trien = json::array({make_task(101, "trien_khai", 1, "trien_khai_net", 3, 120, 3, 6, "21.03,105.81", "")});
    const json bao = json::array({
        make_task(102, "bao_tri", 2, "bao_tri_vat_ly", 1, 60, 1, 6, "21.04,105.82", "2026-09-01 10:00:00"),  // hoàn tất
        make_task(103, "bao_tri", 2, "bao_tri_vat_ly", 1, 60, 1, 6, "", ""),                                  // thiếu tọa độ
    });
    const json thu = json::array({make_task(104, "thu_hoi", 3, "thu_hoi_thiet_bi", 1, nullptr, 4, 97, "21.05,105.83", "")});
    const json hoa = json::array({make_task(105, "hoa_don", 4, "hoa_don_tra_sau", 2, nullptr, 4, 0, "21.06,105.84", "")});
    const json onsite = json::array({
        make_task(106, "onsite", 5, "phieu_onsite", 1, nullptr, 2, 10, "21.07,105.85", ""),  // trùng current
        make_task(107, "onsite", 5, "phieu_onsite", 1, nullptr, 2, 10, "21.08,105.86", ""),  // status 10 không current
    });

    {
        std::vector<ktv::Error> errors;
        ktv::Message message = ktv::parse_message(make_message("2", current, trien, bao, thu, hoa, onsite), errors);
        for (const auto& e : errors) std::cerr << "  lỗi không mong đợi: " << e.path << " " << e.problem << "\n";
        CHECK(errors.empty());

        ktv::NormalizedWorklist w = ktv::normalize_worklist(message);
        CHECK(!w.staff_off);
        CHECK(w.stats.tasks == 7);
        CHECK(w.candidates.size() == 1 && w.candidates[0]->task_id == 101);
        CHECK(w.stats.candidates == 1);
        CHECK(w.stats.excluded_current == 1);           // 106
        CHECK(w.stats.excluded_status == 3);            // 104 (97), 105 (0), 107 (10 không current)
        CHECK(w.stats.excluded_completed == 1);         // 102
        CHECK(w.stats.excluded_missing_location == 1);  // 103
        CHECK(w.current_task && w.current_task->task_id == 106);

        // Plan: chỉ task status 6 được xếp; task 0/97/10 không xuất hiện trong schedule.
        ktv::PlanResult r = ktv::plan(message, ktv::default_rules(), *ktv::parse_datetime("2026-09-10 09:00:05"));
        CHECK(r.response["success"] == true && r.response["statuscode"] == "200");
        CHECK(r.routed == 1);
        for (const auto& row : r.response["data"]["clusters"][0]["schedule"]) {
            CHECK(row["type"] != "TASK" || row["task_id"] == 101);
            if (row["task_id"] == 101) CHECK(row["handle_minutes"] == 120);  // "" → định mức trien_khai_net
        }
    }

    {  // current_task không có row khớp: dựng bản tối thiểu, ứng viên vẫn giữ.
        std::vector<ktv::Error> errors;
        const json orphan = {{"task_id", 999}, {"task_status_id", 10}, {"task_type_id", 1}};
        ktv::Message message = ktv::parse_message(make_message(nullptr, orphan, trien, empty, empty, empty, empty), errors);
        CHECK(errors.empty());
        ktv::NormalizedWorklist w = ktv::normalize_worklist(message);
        CHECK(w.current_task && w.current_task->task_id == 999);
        CHECK(w.candidates.size() == 1 && w.candidates[0]->task_id == 101);
    }

    {  // staff.status = 3 (off): không sinh tuyến.
        std::vector<ktv::Error> errors;
        ktv::Message message = ktv::parse_message(make_message("3", json(nullptr), trien, empty, empty, empty, empty), errors);
        CHECK(errors.empty());
        ktv::NormalizedWorklist w = ktv::normalize_worklist(message);
        CHECK(w.staff_off && w.candidates.empty());
        ktv::PlanResult r = ktv::plan(message, ktv::default_rules(), *ktv::parse_datetime("2026-09-10 09:00:05"));
        CHECK(r.response["statuscode"] == "422" && r.response["data"].is_null());
    }

    {  // status 1/2 tiếp tục bình thường.
        std::vector<ktv::Error> errors;
        ktv::Message message = ktv::parse_message(make_message("1", json(nullptr), trien, empty, empty, empty, empty), errors);
        CHECK(errors.empty());
        ktv::NormalizedWorklist w = ktv::normalize_worklist(message);
        CHECK(!w.staff_off && w.candidates.size() == 1);
    }

    if (failures) std::cerr << failures << " lỗi\n";
    else std::cout << "test_normalization: OK\n";
    return failures != 0;
}
