// Test đọc/kiểm tra message API. Không dùng framework: lỗi thì in và trả mã khác 0.
#include <iostream>
#include <set>

#include "ktv/api.hpp"

using ktv::json;

static int failures = 0;
#define CHECK(cond)                                                              \
    do {                                                                         \
        if (!(cond)) {                                                           \
            std::cerr << __FILE__ << ":" << __LINE__ << ": FAIL " #cond "\n";    \
            ++failures;                                                          \
        }                                                                        \
    } while (0)

// JSON mẫu của file API (sheet 08), rút còn 2 việc.
static json sample() {
    return json::parse(R"({
      "staff": {"staff_id": "00039434", "staff_account": "TIN0101.TUYEN9", "latlng": "21.0248,105.7961",
                "plots": [{"id": 2, "name": "Trung Kính", "role": 1, "block_id": 3434},
                          {"id": 3, "name": "Nguyễn Khánh Toàn", "role": 2, "block_id": 3434}],
                "available": "08:00-17:30,17:30-21:00", "current_task": null},
      "tasks": {
        "trien_khai": [{"task_id": 5454541, "task_group_id": 1, "task_group_name": "trien_khai", "task_type_id": 3,
          "task_type_name": "trien_khai_net", "task_sub_id": 12, "task_sub_name": "gsafe", "task_status_id": 6,
          "task_status_name": "check_in", "sla": {"sla_minutes": 120, "priority_in_day": 3},
          "appointment": "2026-09-10 14:00:00", "location": "Số 12, ngõ 45 Trần Duy Hưng, Cầu Giấy",
          "latlng": "21.0122,105.7995", "handle_minutes": 90, "task_plots_id": 4, "staff_plots_id": 2,
          "staff_role": 1, "block_id": 3434, "location_id": 4}],
        "bao_tri": [], "thu_hoi": [],
        "hoa_don": [{"task_id": 5454544, "task_group_id": 4, "task_group_name": "hoa_don", "task_type_id": 2,
          "task_type_name": "hoa_don_tra_sau", "task_sub_id": 0, "task_sub_name": "", "task_status_id": 6,
          "task_status_name": "check_in", "sla": {"sla_minutes": null, "priority_in_day": 4}, "appointment": "",
          "location": "P1203 CT2 Trung Hòa Nhân Chính, Thanh Xuân", "latlng": "21.0043,105.8021",
          "handle_minutes": 20, "task_plots_id": 7, "staff_plots_id": 3, "staff_role": 2, "block_id": 3434,
          "location_id": 4}],
        "onsite": []}})");
}

int main() {
    {
        std::vector<ktv::Error> errors;
        auto message = ktv::parse_message(sample(), errors);
        for (const auto& e : errors) std::cerr << "  lỗi không mong đợi: " << e.path << " " << e.problem << "\n";
        CHECK(errors.empty());
        CHECK(message.tasks.size() == 2);
        CHECK(message.staff.available.size() == 2 && message.staff.available[1] == std::make_pair(17 * 60 + 30, 21 * 60));
        CHECK(message.tasks[0].appointment && ktv::format_datetime(*message.tasks[0].appointment) == "2026-09-10 14:00:00");
        CHECK(!message.tasks[1].appointment && !message.tasks[1].sla_minutes && message.tasks[1].staff_role == 2);
        CHECK(!message.staff.current_task);
    }
    {
        // Phase 1: field contract mới (status, create/complete_date, contract, handle 0/null, role 3).
        json data = sample();
        data["staff"]["status"] = 2;
        data["tasks"]["trien_khai"][0]["create_date"] = "2026-05-12 19:29:16";
        data["tasks"]["trien_khai"][0]["complete_date"] = "";
        data["tasks"]["trien_khai"][0]["contract_id"] = 1126569863;
        data["tasks"]["trien_khai"][0]["contract_no"] = "SGABP0236";
        data["tasks"]["trien_khai"][0]["staff_role"] = 3;
        data["tasks"]["trien_khai"][0]["handle_minutes"] = 0;
        std::vector<ktv::Error> errors;
        auto message = ktv::parse_message(data, errors);
        for (const auto& e : errors) std::cerr << "  lỗi không mong đợi: " << e.path << " " << e.problem << "\n";
        CHECK(errors.empty());
        CHECK(message.staff.status == 2);
        CHECK(message.tasks[0].create_date && ktv::format_datetime(*message.tasks[0].create_date) == "2026-05-12 19:29:00");
        CHECK(!message.tasks[0].complete_date);
        CHECK(message.tasks[0].contract_id && *message.tasks[0].contract_id == 1126569863);
        CHECK(message.tasks[0].contract_no == "SGABP0236");
        CHECK(message.tasks[0].staff_role == 3);
        CHECK(!message.tasks[0].handle_minutes);  // 0 = dùng định mức.

        data["tasks"]["trien_khai"][0]["handle_minutes"] = nullptr;  // null cũng dùng định mức.
        errors.clear();
        ktv::parse_message(data, errors);
        CHECK(errors.empty());

        data["staff"]["status"] = 9;  // Ngoài miền 1–3.
        errors.clear();
        ktv::parse_message(data, errors);
        bool status_failed = false;
        for (const auto& e : errors) status_failed |= e.path == "staff.status";
        CHECK(status_failed);

        data = sample();
        data["tasks"]["trien_khai"][0]["staff_role"] = 0;  // Role 0 chưa hỗ trợ (deferred).
        errors.clear();
        ktv::parse_message(data, errors);
        bool role_failed = false;
        for (const auto& e : errors) role_failed |= e.path == "tasks.trien_khai[0].staff_role";
        CHECK(role_failed);

        // current_task trùng row task trong tasks: hợp lệ, chỉ để bổ sung dữ liệu.
        data = sample();
        data["staff"]["current_task"] = json{{"task_id", 5454541}, {"task_status_id", 10}, {"task_type_id", 3}};
        errors.clear();
        ktv::parse_message(data, errors);
        for (const auto& e : errors) std::cerr << "  lỗi không mong đợi: " << e.path << " " << e.problem << "\n";
        CHECK(errors.empty());
    }
    {
        json data = sample();
        data["staff"]["staff_id"] = 324668;
        data["tasks"]["trien_khai"][0]["latlng"] = "21.03;105.80";
        data["tasks"]["trien_khai"][0]["sla"]["priority_in_day"] = 9;
        data["tasks"].erase("onsite");
        std::vector<ktv::Error> errors;
        ktv::parse_message(data, errors);
        std::set<std::string> paths;
        for (const auto& e : errors) paths.insert(e.path);
        CHECK((paths == std::set<std::string>{"staff.staff_id", "tasks.trien_khai[0].latlng",
                                              "tasks.trien_khai[0].sla.priority_in_day", "tasks.trien_khai[0].sla", "tasks"}));
    }
    CHECK(!ktv::parse_datetime("2026-02-29 08:00:00"));
    CHECK(ktv::parse_datetime("2028-02-29 08:00:00"));
    CHECK(ktv::format_datetime(*ktv::parse_datetime("1969-12-31 23:59:00")) == "1969-12-31 23:59:00");

    if (failures) std::cerr << failures << " lỗi\n";
    else std::cout << "test_api: OK\n";
    return failures != 0;
}
