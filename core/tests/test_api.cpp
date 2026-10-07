// Test đọc/kiểm tra message API. Không dùng framework: lỗi thì in và trả mã khác 0.
#include <iostream>
#include <set>

#include "ktv/api.hpp"
#include "ktv/plan.hpp"

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
        data["tasks"]["trien_khai"][0]["staff_role"] = 0;  // Role 0 = default (workbook (3)): hợp lệ cả ở strict.
        errors.clear();
        ktv::parse_message(data, errors);
        CHECK(errors.empty());
        data["tasks"]["trien_khai"][0]["staff_role"] = 4;  // ngoài 0–3: strict vẫn lỗi.
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
    {
        // Lỗi dữ liệu lõi: trùng task_id, sai định dạng hẹn, field lạ trong task.
        json data = sample();
        json dup = data["tasks"]["hoa_don"][0];
        data["tasks"]["hoa_don"].push_back(dup);
        data["tasks"]["trien_khai"][0]["appointment"] = "not-a-date";
        data["tasks"]["trien_khai"][0]["foo"] = 1;
        std::vector<ktv::Error> errors;
        ktv::parse_message(data, errors);
        std::set<std::string> paths;
        for (const auto& e : errors) paths.insert(e.path);
        CHECK(paths.count("tasks.hoa_don[1].task_id") == 1);
        CHECK(paths.count("tasks.trien_khai[0].appointment") == 1);
        CHECK(paths.count("tasks.trien_khai[0].foo") == 1);
    }
    {
        // Task đặt sai nhóm so với khóa tasks.
        json data = sample();
        data["tasks"]["trien_khai"][0]["task_group_name"] = "bao_tri";
        data["tasks"]["trien_khai"][0]["task_group_id"] = 2;
        std::vector<ktv::Error> errors;
        ktv::parse_message(data, errors);
        bool group_failed = false;
        for (const auto& e : errors) group_failed |= e.path == "tasks.trien_khai[0]";
        CHECK(group_failed);
    }

    {  // Nhiều biến thể dữ liệu sai: mỗi cái phải báo đúng đường dẫn field.
        auto bad = [](json data, const std::string& path) {
            std::vector<ktv::Error> errors;
            ktv::parse_message(data, errors);
            bool found = false;
            for (const auto& e : errors) found |= e.path == path;
            CHECK(found);
        };
        { json d = sample(); d["staff"].erase("plots"); bad(d, "staff.plots"); }
        { json d = sample(); d["staff"]["plots"] = json::array(); bad(d, "staff.plots"); }
        { json d = sample(); d["staff"]["plots"][0]["role"] = 2; bad(d, "staff.plots"); }
        { json d = sample(); d["staff"]["latlng"] = "40.0,105.0"; bad(d, "staff.latlng"); }
        { json d = sample(); d["staff"]["available"] = "17:30-08:00"; bad(d, "staff.available"); }
        { json d = sample(); d["staff"]["status"] = 0; bad(d, "staff.status"); }
        { json d = sample(); d["staff"].erase("current_task"); bad(d, "staff.current_task"); }
        { json d = sample(); d["staff"]["foo"] = 1; bad(d, "staff.foo"); }
        { json d = sample(); d["tasks"].erase("onsite"); bad(d, "tasks"); }
        { json d = sample(); d["tasks"]["trien_khai"][0]["sla"]["sla_minutes"] = 0; bad(d, "tasks.trien_khai[0].sla.sla_minutes"); }
        { json d = sample(); d["tasks"]["trien_khai"][0]["sla"]["priority_in_day"] = 0; bad(d, "tasks.trien_khai[0].sla.priority_in_day"); }
        { json d = sample(); d["tasks"]["trien_khai"][0]["handle_minutes"] = -1; bad(d, "tasks.trien_khai[0].handle_minutes"); }
        { json d = sample(); d["tasks"]["trien_khai"][0]["contract_id"] = "x"; bad(d, "tasks.trien_khai[0].contract_id"); }
        { json d = sample(); d["tasks"]["trien_khai"][0]["create_date"] = "bad"; bad(d, "tasks.trien_khai[0].create_date"); }
        { json d = sample(); d["tasks"]["trien_khai"][0]["complete_date"] = "bad"; bad(d, "tasks.trien_khai[0].complete_date"); }
        { json d = sample(); d["tasks"]["trien_khai"][0]["task_type_name"] = "khong_ton_tai"; bad(d, "tasks.trien_khai[0].task_type_name"); }
        { json d = sample(); d["tasks"]["trien_khai"][0]["task_type_id"] = 99; bad(d, "tasks.trien_khai[0].task_type_id"); }
        { json d = sample(); d["tasks"]["trien_khai"][0]["latlng"] = "21.03;105.80"; bad(d, "tasks.trien_khai[0].latlng"); }
    }
    {  // staff_role 3 và contract dùng được.
        json data = sample();
        data["tasks"]["trien_khai"][0]["staff_role"] = 3;
        data["tasks"]["trien_khai"][0]["contract_id"] = 5;
        data["tasks"]["trien_khai"][0]["contract_no"] = "S1";
        std::vector<ktv::Error> errors;
        ktv::parse_message(data, errors);
        for (const auto& e : errors) std::cerr << "  lỗi không mong đợi: " << e.path << " " << e.problem << "\n";
        CHECK(errors.empty());
        // 7.9: contract_no null = không có (trước đây lỗi → bỏ cả task); kiểu khác vẫn lỗi.
        data["tasks"]["trien_khai"][0]["contract_no"] = nullptr;
        errors.clear();
        auto with_null = ktv::parse_message(data, errors);
        CHECK(errors.empty() && !with_null.tasks[0].contract_no);
        data["tasks"]["trien_khai"][0]["contract_no"] = 5;
        errors.clear();
        ktv::parse_message(data, errors);
        CHECK(!errors.empty());
    }
    {  // Mẫu workbook mới: trien_khai_box + subtype gsafe.
        json data = sample();
        json& t = data["tasks"]["trien_khai"][0];
        t["task_type_id"] = 4;
        t["task_type_name"] = "trien_khai_box";
        t["task_sub_id"] = 12;
        t["task_sub_name"] = "gsafe";
        std::vector<ktv::Error> errors;
        ktv::parse_message(data, errors);
        for (const auto& e : errors) std::cerr << "  lỗi không mong đợi: " << e.path << " " << e.problem << "\n";
        CHECK(errors.empty());
    }

    constexpr int kUnknownStatus = -1;  // = ktv::kStaffStatusUnknown (viết số để test chạy được cả trên bản cũ khi soát hồi quy)
    // ---- Chế độ nới lỏng (worker / `plan`): mỗi mã trong docs/DATA_QUESTIONS.md. Strict vẫn lỗi như cũ.
    std::vector<ktv::Error> errors, warnings;
    auto lenient = [&](const json& data) {
        errors.clear();
        warnings.clear();
        return ktv::parse_message(data, errors, &warnings);
    };
    auto strict_fails = [](const json& data) {
        std::vector<ktv::Error> strict_errors;
        ktv::parse_message(data, strict_errors);
        return !strict_errors.empty();
    };
    auto warned = [&](const std::string& code, const std::string& path) {
        for (const auto& w : warnings)
            if (w.code == code && w.path == path) return true;
        return false;
    };
    {  // Dữ liệu chuẩn: không cảnh báo.
        auto m = lenient(sample());
        CHECK(errors.empty() && warnings.empty() && m.tasks.size() == 2);
    }
    {  // Field lạ ở mọi cấp, nhóm lạ, nhóm thiếu → bỏ qua, vẫn xếp.
        json data = sample();
        data["oa_version"] = 2;
        data["staff"]["team"] = "x";
        data["tasks"]["trien_khai"][0]["priority_score"] = 0.5;
        data["tasks"]["kiem_dinh"] = json::array();
        data["tasks"].erase("onsite");
        auto m = lenient(data);
        CHECK(errors.empty() && m.tasks.size() == 2);
        CHECK(warned("UNKNOWN_FIELD", ".oa_version") && warned("UNKNOWN_FIELD", "staff.team"));
        CHECK(warned("UNKNOWN_FIELD", "tasks.trien_khai[0].priority_score") && warned("UNKNOWN_FIELD", "tasks.kiem_dinh"));
        CHECK(warned("TASK_GROUPS", "tasks.onsite"));
        CHECK(strict_fails(data));
    }
    {  // staff_role = 0 (staging thật có, workbook (3) "0 default") → hợp lệ, không cảnh báo. Số lạ (7) → cảnh báo.
        json data = sample();
        data["tasks"]["trien_khai"][0]["staff_role"] = 0;
        auto m = lenient(data);
        CHECK(errors.empty() && warnings.empty() && m.tasks.size() == 2 && m.tasks[0].staff_role == 0);
        CHECK(!strict_fails(data));
        data["tasks"]["trien_khai"][0]["staff_role"] = 7;
        m = lenient(data);
        CHECK(errors.empty() && m.tasks.size() == 2 && m.tasks[0].staff_role == 7);
        CHECK(warned("STAFF_ROLE", "tasks.trien_khai[0].staff_role"));
        CHECK(strict_fails(data));
    }
    {  // 7.15: onsite/phieu_onsite theo bao_tri — SLA 60 / P2 khớp danh mục; SLA null → lệch danh mục (vẫn xếp).
        json data = sample();
        json t = data["tasks"]["hoa_don"][0];
        t["task_group_id"] = 5;
        t["task_group_name"] = "onsite";
        t["task_type_id"] = 1;
        t["task_type_name"] = "phieu_onsite";
        t["sla"] = {{"sla_minutes", 60}, {"priority_in_day", 2}};
        data["tasks"]["hoa_don"] = json::array();
        data["tasks"]["onsite"] = json::array({t});
        auto m = lenient(data);
        CHECK(errors.empty() && warnings.empty() && !strict_fails(data));
        data["tasks"]["onsite"][0]["sla"]["sla_minutes"] = nullptr;
        m = lenient(data);
        CHECK(errors.empty() && m.tasks.size() == 2 && warned("CATALOG_MISMATCH", "tasks.onsite[0].sla"));
    }
    {  // 7.12: nhóm cscd không bắt buộc. 5 khóa: không cảnh báo. 6 khóa: đọc task cscd ở cả strict lẫn nới lỏng.
        json data = sample();
        auto m = lenient(data);
        CHECK(errors.empty() && warnings.empty() && !strict_fails(data));
        json cscd = data["tasks"]["hoa_don"][0];
        cscd["task_id"] = 777;
        cscd["task_group_id"] = 6;
        cscd["task_group_name"] = "cscd";
        cscd["task_type_id"] = 2;
        cscd["task_type_name"] = "ngung_ket_noi_4h";
        cscd["sla"] = {{"sla_minutes", nullptr}, {"priority_in_day", 2}};
        data["tasks"]["cscd"] = json::array({cscd});
        m = lenient(data);
        CHECK(errors.empty() && warnings.empty() && m.tasks.size() == 3);
        CHECK(m.tasks[2].task_id == 777 && m.tasks[2].task_group_name == "cscd" && m.tasks[2].task_group_id == 6);
        CHECK(!strict_fails(data));
        const ktv::TaskKind* kind = ktv::find_kind("cscd", "ngung_ket_noi_4h");
        CHECK(kind && kind->on_time == ktv::OnTime::DoneSameCreatedDay && kind->handle_minutes == 30 && kind->priority == 2);
        data["tasks"]["cscd"][0]["task_type_name"] = "cscd_moi";  // loại lạ dưới cscd → loại mặc định + cảnh báo
        data["tasks"]["cscd"][0]["task_type_id"] = 9;
        m = lenient(data);
        CHECK(errors.empty() && m.tasks.size() == 3 && warned("UNKNOWN_TASK_TYPE", "tasks.cscd[0].task_type_name"));
        data["tasks"].erase("onsite");  // nhóm bắt buộc thiếu vẫn cảnh báo / strict lỗi
        m = lenient(data);
        CHECK(warned("TASK_GROUPS", "tasks.onsite") && !warned("TASK_GROUPS", "tasks.cscd") && strict_fails(data));
        // Phương án B: OA còn gửi ngung_ket_noi_4h dưới onsite (hợp đồng cũ) → luật của cscd + TASK_TYPE_OTHER_GROUP.
        data = sample();
        json old_style = cscd;
        old_style["task_group_id"] = 5;
        old_style["task_group_name"] = "onsite";
        data["tasks"]["onsite"] = json::array({old_style});
        m = lenient(data);
        CHECK(errors.empty() && m.tasks.size() == 3 && warned("TASK_TYPE_OTHER_GROUP", "tasks.onsite[0].task_type_name"));
        CHECK(!warned("UNKNOWN_TASK_TYPE", "tasks.onsite[0].task_type_name") && !warned("CATALOG_MISMATCH", "tasks.onsite[0].sla"));
        CHECK(strict_fails(data));
        const ktv::TaskKind& moved = ktv::kind_or_default("onsite", "ngung_ket_noi_4h");
        CHECK(std::string(moved.group) == "cscd" && moved.on_time == ktv::OnTime::DoneSameCreatedDay && moved.handle_minutes == 30);
        CHECK(!ktv::find_kind("onsite", "ngung_ket_noi_4h") && !ktv::find_kind("onsite", "chap_chon_suy_hao"));
        data = sample();
        data["tasks"]["kiem_dinh"] = json::array();  // nhóm lạ vẫn là field lạ
        lenient(data);
        CHECK(warned("UNKNOWN_FIELD", "tasks.kiem_dinh"));
    }
    {  // create_date / CreateDate là một field: tên nào cũng nhận; cả hai mà khác → dùng create_date + cảnh báo.
        json data = sample();
        json& t = data["tasks"]["trien_khai"][0];
        t["CreateDate"] = "2026-09-01 08:00:00";
        auto m = lenient(data);
        CHECK(errors.empty() && warnings.empty() && m.tasks[0].create_date &&
              ktv::format_datetime(*m.tasks[0].create_date) == "2026-09-01 08:00:00");
        CHECK(!strict_fails(data));
        t["create_date"] = "2026-09-01 08:00:00";  // cả hai, cùng giá trị: không cảnh báo
        m = lenient(data);
        CHECK(errors.empty() && warnings.empty());
        t["create_date"] = "2026-09-02 09:30:00";  // cả hai, khác: create_date thắng + cảnh báo
        m = lenient(data);
        CHECK(errors.empty() && ktv::format_datetime(*m.tasks[0].create_date) == "2026-09-02 09:30:00");
        CHECK(warned("CREATE_DATE_CONFLICT", "tasks.trien_khai[0].CreateDate"));
        CHECK(strict_fails(data));
        t.erase("create_date");
        t["CreateDate"] = "01/09/2026";  // sai định dạng: lỗi của task như create_date
        lenient(data);
        CHECK(errors.empty() && warned("TASK_DROPPED", "tasks.trien_khai[0]"));
    }
    {  // Lệch danh mục → dùng giá trị input.
        json data = sample();
        data["tasks"]["trien_khai"][0]["task_type_id"] = 9;
        data["tasks"]["trien_khai"][0]["sla"]["sla_minutes"] = 90;
        auto m = lenient(data);
        CHECK(errors.empty() && m.tasks.size() == 2 && m.tasks[0].sla_minutes == 90 && m.tasks[0].task_type_id == 9);
        CHECK(warned("CATALOG_MISMATCH", "tasks.trien_khai[0].task_type_id") && warned("CATALOG_MISMATCH", "tasks.trien_khai[0].sla"));
        CHECK(strict_fails(data));
    }
    {  // Loại ngoài danh mục → vẫn xếp bằng loại mặc định (60 phút, không hạn theo loại).
        json data = sample();
        data["tasks"]["hoa_don"][0]["task_type_name"] = "hoa_don_moi";
        data["tasks"]["hoa_don"][0]["handle_minutes"] = 0;
        auto m = lenient(data);
        CHECK(errors.empty() && m.tasks.size() == 2);
        CHECK(warned("UNKNOWN_TASK_TYPE", "tasks.hoa_don[0].task_type_name"));
        CHECK(warnings.size() == 1 && ktv::issue_key(warnings[0]).find("hoa_don/hoa_don_moi") != std::string::npos);
        CHECK(ktv::kind_or_default("hoa_don", "hoa_don_moi").handle_minutes == 60);
        ktv::PlanResult r = ktv::plan(m, ktv::default_rules(), *ktv::parse_datetime("2026-09-10 09:00:00"));
        CHECK(r.response["statuscode"] == "200" && r.response["data"]["metrics"]["tasks_total"] == 2);
        CHECK(strict_fails(data));
    }
    {  // Một task hỏng → chỉ bỏ task đó; task trùng ID → bỏ bản sau; phần tử không phải object → bỏ.
        json data = sample();
        data["tasks"]["hoa_don"][0]["latlng"] = "abc";
        auto m = lenient(data);
        CHECK(errors.empty() && m.tasks.size() == 1 && m.tasks[0].task_id == 5454541);
        CHECK(warned("TASK_DROPPED", "tasks.hoa_don[0]") && warnings.back().problem.rfind("latlng: ", 0) == 0);

        data = sample();
        data["tasks"]["hoa_don"][0]["task_id"] = 5454541;
        data["tasks"]["bao_tri"] = json::array({"x"});
        m = lenient(data);
        CHECK(errors.empty() && m.tasks.size() == 1);
        CHECK(warned("TASK_DROPPED", "tasks.hoa_don[0]") && warned("TASK_DROPPED", "tasks.bao_tri[0]"));
        CHECK(strict_fails(data));
    }
    {  // Phần phụ của staff hỏng → vẫn xếp: status lạ = không rõ, lô hỏng bỏ, current_task hỏng = không có.
        json data = sample();
        data["planned_at"] = "30/09/2026";
        data["staff"]["status"] = 9;
        data["staff"]["plots"] = json::array({{{"id", 2}, {"role", 5}, {"block_id", 1}}});
        data["staff"]["current_task"] = "đang làm";
        auto m = lenient(data);
        CHECK(errors.empty() && m.tasks.size() == 2);
        CHECK(!m.planned_at && m.staff.plots.empty() && !m.staff.current_task);
        CHECK(warned("PLANNED_AT", ".planned_at") && warned("STAFF_STATUS", "staff.status"));
        CHECK(warned("STAFF_PLOTS", "staff.plots[0]") && warned("STAFF_PLOTS", "staff.plots") && warned("CURRENT_TASK", "staff.current_task"));
        CHECK(strict_fails(data));
    }
    {  // staff.status: "3"/3.0 hiểu là số (vẫn ghi sổ); 7.21: off và giá trị lạ đều VẪN xếp tuyến (200) + cảnh báo.
        const ktv::Minutes at = *ktv::parse_datetime("2026-09-10 09:00:00");
        for (const json& off : {json("3"), json(3.0)}) {
            json data = sample();
            data["staff"]["status"] = off;
            auto m = lenient(data);
            CHECK(errors.empty() && m.staff.status == 3 && warned("STAFF_STATUS", "staff.status"));
            CHECK(ktv::plan(m, ktv::default_rules(), at).response["statuscode"] == "200");
            CHECK(strict_fails(data));
        }
        for (const json& odd : {json(9), json("off"), json(2.5), json(nullptr)}) {
            json data = sample();
            data["staff"]["status"] = odd;
            auto m = lenient(data);
            CHECK(errors.empty() && m.staff.status == kUnknownStatus && warned("STAFF_STATUS", "staff.status"));
            ktv::PlanResult r = ktv::plan(m, ktv::default_rules(), at);
            CHECK(r.response["statuscode"] == "200");
        }
        {  // 7.21: KTV off thiếu available (không gửi / null / "") → khung mặc định 08:00-17:30 + AVAILABLE_DEFAULT.
            for (const json& missing : {json(nullptr), json(""), json("__erase__")}) {
                json data = sample();
                data["staff"]["status"] = 3;
                if (missing == "__erase__") data["staff"].erase("available");
                else data["staff"]["available"] = missing;
                auto m = lenient(data);
                CHECK(errors.empty() && warned("AVAILABLE_DEFAULT", "staff.available"));
                CHECK(m.staff.available.size() == 1 && m.staff.available[0] == std::make_pair(8 * 60, 17 * 60 + 30));
                CHECK(ktv::plan(m, ktv::default_rules(), at).response["data"]["metrics"]["shift_end_at"] == "2026-09-10 17:30:00");
                CHECK(strict_fails(data));  // strict (validate) vẫn báo thiếu
            }
            json not_off = sample();  // KTV không off mà thiếu available → vẫn là lỗi như cũ
            not_off["staff"]["status"] = 1;
            not_off["staff"]["available"] = "";
            lenient(not_off);
            CHECK(!errors.empty());
            json bad = sample();  // off nhưng available SAI dạng (không phải thiếu) → vẫn lỗi
            bad["staff"]["status"] = 3;
            bad["staff"]["available"] = "abc";
            lenient(bad);
            CHECK(!errors.empty());
        }
        json data = sample();
        data["staff"]["status"] = "1";
        auto m = lenient(data);
        CHECK(m.staff.status == 1 && ktv::plan(m, ktv::default_rules(), at).response["statuscode"] == "200");
    }
    {  // Nhóm ghi lệch → chuẩn hóa về nhóm chứa task, để plan() tra đúng loại (không rơi về mặc định 60 phút).
        json data = sample();
        json& t = data["tasks"]["hoa_don"][0];
        t["task_group_name"] = "";
        t["handle_minutes"] = 0;  // dùng định mức của loại: hoa_don_tra_sau
        auto m = lenient(data);
        CHECK(errors.empty() && warned("TASK_GROUP", "tasks.hoa_don[0]") && !warned("UNKNOWN_TASK_TYPE", "tasks.hoa_don[0].task_type_name"));
        CHECK(m.tasks[1].task_group_name == "hoa_don" && m.tasks[1].task_group_id == 4);
        const int norm = ktv::find_kind("hoa_don", "hoa_don_tra_sau")->handle_minutes;
        CHECK(norm != 60);  // nếu bằng 60 thì test không phân biệt được với loại mặc định
        m.planned_at = *ktv::parse_datetime("2026-09-28 09:00:00");  // 7.16.1: chạy gần cuối tháng, hóa đơn không bị lọc K
        ktv::PlanResult r = ktv::plan(m, ktv::default_rules(), *ktv::parse_datetime("2026-09-28 09:00:00"));
        bool found = false;
        for (const auto& cluster : r.response["data"]["clusters"])
            for (const auto& row : cluster["schedule"])
                if (row["entry_type"] == "TASK" && row["task_id"] == 5454544) found = row["handle_minutes"] == norm;
        CHECK(found);
    }
    {  // Task bị bỏ chỉ để lại TASK_DROPPED, không đếm thêm cảnh báo khác của chính nó.
        json data = sample();
        json& t = data["tasks"]["hoa_don"][0];
        t["staff_role"] = 0;
        t["extra"] = 1;
        t["latlng"] = "abc";
        auto m = lenient(data);
        CHECK(errors.empty() && m.tasks.size() == 1);
        CHECK(warnings.size() == 1 && warned("TASK_DROPPED", "tasks.hoa_don[0]"));
    }
    {  // Không xếp được tuyến → vẫn 400 ở cả chế độ nới lỏng.
        json data = sample();
        data["staff"]["latlng"] = "abc";
        lenient(data);
        CHECK(!errors.empty());
        data = sample();
        data["staff"]["available"] = "sáng";
        lenient(data);
        CHECK(!errors.empty());
        data = sample();
        data["staff"].erase("staff_id");
        lenient(data);
        CHECK(!errors.empty());
        lenient(json::array());
        CHECK(!errors.empty());
    }
    CHECK(ktv::issue_key({"tasks.bao_tri[12].x", "p", "C"}) == "C tasks.bao_tri[].x — p");

    CHECK(!ktv::parse_datetime("2026-02-29 08:00:00"));
    CHECK(ktv::parse_datetime("2028-02-29 08:00:00"));
    CHECK(ktv::format_datetime(*ktv::parse_datetime("1969-12-31 23:59:00")) == "1969-12-31 23:59:00");

    {  // 7.25: định mức (input bỏ trống handle_minutes) = TGXL chuẩn catalogue ISC sheet 5.
        const std::pair<const char*, const char*> kinds[] = {
            {"trien_khai", "trien_khai_net"}, {"trien_khai", "trien_khai_box"}, {"trien_khai", "box_cam_only"},
            {"trien_khai", "swap"}, {"trien_khai", "giao_thiet_bi_cam"}, {"bao_tri", "bao_tri_vat_ly"},
            {"bao_tri", "bao_tri_logic"}, {"thu_hoi", "thu_hoi_thiet_bi"}, {"hoa_don", "hoa_don_tra_truoc"},
            {"hoa_don", "hoa_don_tra_sau"}, {"onsite", "phieu_onsite"}};
        const int isc[] = {120, 120, 120, 60, 60, 60, 60, 15, 15, 15, 60};
        for (size_t i = 0; i < std::size(kinds); ++i) {
            const ktv::TaskKind* kind = ktv::find_kind(kinds[i].first, kinds[i].second);
            CHECK(kind && kind->handle_minutes == isc[i]);
        }
    }

    if (failures) std::cerr << failures << " lỗi\n";
    else std::cout << "test_api: OK\n";
    return failures != 0;
}
