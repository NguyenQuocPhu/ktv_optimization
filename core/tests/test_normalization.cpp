// Normalization: lọc task theo trạng thái (theo nhóm, sheet 05) / complete / location, tách current_task khỏi ứng viên.
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <sstream>

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

// Một task hợp lệ theo contract: chỉ status/tên status/latlng/complete_date thay đổi giữa các ca test.
static json make_task(long long id, const char* group, int gid, const char* type, int tid, json sla_minutes,
                      int priority, int status, const char* latlng, const char* complete_date,
                      const char* status_name = "") {
    return json{
        {"task_id", id},
        {"task_group_id", gid}, {"task_group_name", group},
        {"task_type_id", tid}, {"task_type_name", type},
        {"task_sub_id", 0}, {"task_sub_name", ""},
        {"task_status_id", status}, {"task_status_name", status_name},
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

static bool warned(const ktv::NormalizedWorklist& w, const std::string& code) {
    for (const auto& warning : w.warnings)
        if (warning.code == code) return true;
    return false;
}

// Một task (đủ loại hợp lệ của từng nhóm) với status + tên cho trước, không current_task.
static ktv::NormalizedWorklist one(const char* group, int status, const char* status_name = "") {
    struct Kind { const char* group; int gid; const char* type; int tid; json sla; int priority; };
    const Kind kinds[] = {{"trien_khai", 1, "trien_khai_net", 3, 120, 3}, {"bao_tri", 2, "bao_tri_vat_ly", 1, 60, 1},
                          {"thu_hoi", 3, "thu_hoi_thiet_bi", 1, nullptr, 4}, {"hoa_don", 4, "hoa_don_tra_sau", 2, nullptr, 4},
                          {"onsite", 5, "phieu_onsite", 1, 60, 2}, {"cscd", 6, "ngung_ket_noi_4h", 2, nullptr, 2}};
    json groups[6] = {json::array(), json::array(), json::array(), json::array(), json::array(), json::array()};
    for (int g = 0; g < 6; ++g)
        if (std::string(kinds[g].group) == group)
            groups[g].push_back(make_task(1, kinds[g].group, kinds[g].gid, kinds[g].type, kinds[g].tid, kinds[g].sla,
                                          kinds[g].priority, status, "21.03,105.81", "", status_name));
    std::vector<ktv::Error> errors;
    static ktv::Message message;  // worklist giữ con trỏ vào message: sống tới lần gọi sau
    json data = make_message(nullptr, json(nullptr), groups[0], groups[1], groups[2], groups[3], groups[4]);
    data["tasks"]["cscd"] = groups[5];
    message = ktv::parse_message(data, errors);
    for (const auto& e : errors) std::cerr << "  lỗi không mong đợi: " << e.path << " " << e.problem << "\n";
    return ktv::normalize_worklist(message);
}

int main() {
    const json empty = json::array();

    {  // Bảng (nhóm, status) theo sheet 05 workbook (3): cùng mã khác nghĩa theo nhóm.
        struct Case {
            const char* group;
            int status;
            const char* name;
            bool routed;
            const char* warning;  // "" = không cảnh báo
        };
        const Case cases[] = {
            {"trien_khai", 96, "", true, ""},  {"trien_khai", 97, "", true, ""},   // 97: Đã nhận tuyến → xếp
            {"trien_khai", 98, "", true, ""},
            {"trien_khai", 0, "", false, "CURRENT_NOT_MATCHED"},                    // 0: check_in nhưng không current
            {"trien_khai", 99, "", false, "TASK_STATUS_UNASSIGNED"},
            {"trien_khai", 5, "", false, ""},  {"trien_khai", 1, "", false, ""},
            {"trien_khai", -2, "", false, ""}, {"trien_khai", -1, "", false, ""},
            {"bao_tri", 0, "", true, ""},                                            // 0: Đã phân công → xếp
            {"bao_tri", 6, "", true, ""},      {"bao_tri", 7, "", true, ""},
            {"bao_tri", 10, "", false, "CURRENT_NOT_MATCHED"},
            {"bao_tri", 2, "", false, "TASK_STATUS_UNASSIGNED"},
            {"bao_tri", 97, "", false, ""},                                          // 97: Đã hủy → bỏ
            {"bao_tri", 5, "", false, ""},     {"bao_tri", 1, "", false, ""},
            {"bao_tri", 3, "", false, ""},     {"bao_tri", 100, "", false, ""},
            {"thu_hoi", 0, "", true, ""},      {"thu_hoi", 1, "", false, ""},        // 1: Đã thu hồi → bỏ (duyệt)
            {"thu_hoi", 2, "", false, ""},     {"thu_hoi", -1, "", false, ""},
            // Không có trong bảng: tên mang nghĩa xong/hủy → bỏ; còn lại (kể cả tên rỗng) → xếp. Đều cảnh báo.
            // Workbook (4): bảng hoa_don, onsite. Đã thanh toán / đã hoàn tất tên rỗng trước đây bị xếp (đoán theo tên).
            {"hoa_don", 0, "", true, ""},      {"hoa_don", 1, "", false, ""},
            {"onsite", 0, "", true, ""},       {"onsite", 1, "", false, ""},
            {"onsite", 10, "", false, "CURRENT_NOT_MATCHED"},
            {"hoa_don", 6, "", true, "TASK_STATUS_UNKNOWN"},
            {"hoa_don", 4, "Chờ thu", true, "TASK_STATUS_UNKNOWN"},
            {"hoa_don", 9, "Đã thu tiền", false, "TASK_STATUS_UNKNOWN_CLOSED"},
            {"onsite", 3, "Đã hủy", false, "TASK_STATUS_UNKNOWN_CLOSED"},
            {"onsite", 8, "Chưa hoàn tất", true, "TASK_STATUS_UNKNOWN"},
            {"bao_tri", 42, "Đóng checklist", false, "TASK_STATUS_UNKNOWN_CLOSED"},  // mã lạ trong nhóm có bảng
            {"bao_tri", 43, "", true, "TASK_STATUS_UNKNOWN"},
            {"trien_khai", 6, "", true, "TASK_STATUS_UNKNOWN"},                      // 6 chỉ có nghĩa ở bao_tri
            {"cscd", 0, "", true, "TASK_STATUS_UNKNOWN"},                             // 7.12: cscd chưa có bảng trạng thái
            {"cscd", 1, "Đã hoàn tất", false, "TASK_STATUS_UNKNOWN_CLOSED"},
        };
        for (const Case& c : cases) {
            const ktv::NormalizedWorklist w = one(c.group, c.status, c.name);
            const bool ok_route = (w.candidates.size() == 1) == c.routed;
            const bool ok_warn = std::string(c.warning).empty() ? w.warnings.empty() : (w.warnings.size() == 1 && warned(w, c.warning));
            if (!ok_route || !ok_warn)
                std::cerr << "  ca " << c.group << " " << c.status << " \"" << c.name << "\": xếp=" << w.candidates.size()
                          << " cảnh báo=" << (w.warnings.empty() ? "-" : w.warnings[0].code) << "\n";
            CHECK(ok_route);
            CHECK(ok_warn);
            CHECK(w.stats.excluded_status == (c.routed ? 0 : 1));
        }
    }

    {  // Tên trạng thái: bỏ dấu, chữ hoa, gạch dưới; "chưa" thì không phải đã xong.
        for (const char* closed : {"Đã hủy", "ĐÃ HỦY", "Huỷ Thi công", "Đã xử lý hoàn tất qua phone", "Đóng checklist",
                                   "Đã thu hồi", "Đã nhập kho", "da_huy", "hoan_tat", "CANCELLED", "Đã xử lý đang theo dõi"})
            CHECK(ktv::status_name_closed(closed));
        for (const char* open : {"", "Đang di chuyển", "Đã phân công", "Chưa hoàn tất", "Chưa thu hồi", "Đã nhận ca",
                                 "check_in", "Chờ xác minh", "Huyện Đông Anh"})
            CHECK(!ktv::status_name_closed(open));
    }

    {  // Cả message: current trùng row, đang làm không trùng, hủy, mã lạ, hoàn tất, thiếu tọa độ.
        const json current = {{"task_id", 106}, {"task_status_id", 10}, {"task_type_id", 1}};
        const json trien = json::array({
            make_task(101, "trien_khai", 1, "trien_khai_net", 3, 120, 3, 97, "21.03,105.81", ""),  // Đã nhận tuyến
            make_task(107, "trien_khai", 1, "trien_khai_net", 3, 120, 3, 0, "21.08,105.86", ""),   // check_in, không current
        });
        const json bao = json::array({
            make_task(102, "bao_tri", 2, "bao_tri_vat_ly", 1, 60, 1, 6, "21.04,105.82", "2026-09-01 10:00:00"),  // có complete_date (kỳ trước) → vẫn xếp
            make_task(103, "bao_tri", 2, "bao_tri_vat_ly", 1, 60, 1, 6, "", ""),                                  // thiếu tọa độ
            make_task(106, "bao_tri", 2, "bao_tri_vat_ly", 1, 60, 1, 10, "21.07,105.85", ""),                     // trùng current
        });
        const json thu = json::array({make_task(104, "thu_hoi", 3, "thu_hoi_thiet_bi", 1, nullptr, 4, -1, "21.05,105.83", "")});
        const json hoa = json::array({make_task(105, "hoa_don", 4, "hoa_don_tra_sau", 2, nullptr, 4, 4, "21.06,105.84", "")});  // mã 4: không có trong bảng

        std::vector<ktv::Error> errors;
        ktv::Message message = ktv::parse_message(make_message("2", current, trien, bao, thu, hoa, empty), errors);
        for (const auto& e : errors) std::cerr << "  lỗi không mong đợi: " << e.path << " " << e.problem << "\n";
        CHECK(errors.empty());

        ktv::NormalizedWorklist w = ktv::normalize_worklist(message);
        CHECK(!w.staff_off);
        CHECK(w.stats.tasks == 7);
        CHECK(w.candidates.size() == 3 && w.candidates[0]->task_id == 101 && w.candidates[1]->task_id == 102 &&
              w.candidates[2]->task_id == 105);
        CHECK(w.stats.excluded_current == 1);           // 106
        CHECK(w.stats.excluded_status == 2);            // 107 (check_in không current), 104 (thu_hoi đã hủy)
        CHECK(w.stats.excluded_missing_location == 1);  // 103
        CHECK(w.current_task && w.current_task->task_id == 106);
        CHECK(w.warnings.size() == 2 && warned(w, "CURRENT_NOT_MATCHED") && warned(w, "TASK_STATUS_UNKNOWN"));

        // Plan: 101, 102, 105 thành TASK; cảnh báo đi theo PlanResult (worker log + /healthz).
        message.planned_at = *ktv::parse_datetime("2026-09-28 09:00:05");  // 7.16.1: gần cuối tháng để ca tháng không bị lọc K
        ktv::PlanResult r = ktv::plan(message, ktv::default_rules(), *ktv::parse_datetime("2026-09-28 09:00:05"));
        CHECK(r.response["success"] == true && r.response["statuscode"] == "200");
        CHECK(r.routed == 3);
        CHECK(r.warnings.size() == 2);
        for (const auto& cluster : r.response["data"]["clusters"])
            for (const auto& row : cluster["schedule"]) {
                CHECK(row["entry_type"] != "TASK" || row["task_id"] == 101 || row["task_id"] == 102 || row["task_id"] == 105);
                if (row["task_id"] == 101) CHECK(row["handle_minutes"] == 120);  // "" → định mức trien_khai_net
            }
    }

    {  // current_task không có row khớp: dựng bản tối thiểu, ứng viên vẫn giữ.
        const json trien = json::array({make_task(101, "trien_khai", 1, "trien_khai_net", 3, 120, 3, 97, "21.03,105.81", "")});
        std::vector<ktv::Error> errors;
        const json orphan = {{"task_id", 999}, {"task_status_id", 10}, {"task_type_id", 1}};
        ktv::Message message = ktv::parse_message(make_message(nullptr, orphan, trien, empty, empty, empty, empty), errors);
        CHECK(errors.empty());
        ktv::NormalizedWorklist w = ktv::normalize_worklist(message);
        CHECK(w.current_task && w.current_task->task_id == 999);
        CHECK(w.candidates.size() == 1 && w.candidates[0]->task_id == 101);
    }

    {  // staff.status = 3 (off): không sinh tuyến; status 1/2 tiếp tục bình thường.
        const json trien = json::array({make_task(101, "trien_khai", 1, "trien_khai_net", 3, 120, 3, 97, "21.03,105.81", "")});
        std::vector<ktv::Error> errors;
        ktv::Message off = ktv::parse_message(make_message("3", json(nullptr), trien, empty, empty, empty, empty), errors);
        CHECK(errors.empty());
        ktv::NormalizedWorklist w = ktv::normalize_worklist(off);
        CHECK(w.staff_off && w.candidates.empty());
        ktv::PlanResult r = ktv::plan(off, ktv::default_rules(), *ktv::parse_datetime("2026-09-10 09:00:05"));
        CHECK(r.response["statuscode"] == "422" && r.response["data"].is_null());

        ktv::Message on = ktv::parse_message(make_message("1", json(nullptr), trien, empty, empty, empty, empty), errors);
        CHECK(errors.empty() && !ktv::normalize_worklist(on).staff_off && ktv::normalize_worklist(on).candidates.size() == 1);
    }

    {  // Thứ tự loại: trạng thái → tọa độ (bao_tri 6 = Đã nhận ca). complete_date không loại (7.8: là ngày kỳ trước).
        struct Case { int status; bool location, complete; size_t routable; int status_excluded, location_excluded; };
        const Case cases[] = {
            {6, true, false, 1, 0, 0},   {6, false, false, 0, 0, 1},  {6, true, true, 1, 0, 0},  // complete_date vẫn xếp
            {6, false, true, 0, 0, 1},   {97, false, true, 0, 1, 0},  // đã hủy: loại theo trạng thái trước tọa độ
        };
        for (const Case& c : cases) {
            const json tasks = json::array({make_task(1, "bao_tri", 2, "bao_tri_vat_ly", 1, 60, 1, c.status,
                                                      c.location ? "21.03,105.81" : "", c.complete ? "2026-09-01 10:00:00" : "")});
            std::vector<ktv::Error> errors;
            ktv::Message message = ktv::parse_message(make_message(nullptr, json(nullptr), empty, tasks, empty, empty, empty), errors);
            CHECK(errors.empty());
            ktv::NormalizedWorklist w = ktv::normalize_worklist(message);
            CHECK(w.candidates.size() == c.routable);
            CHECK(w.stats.excluded_status == c.status_excluded);
            CHECK(w.stats.excluded_missing_location == c.location_excluded);
        }
    }

    // File staging thật của team data: trien_khai 97 + 2 × bao_tri 0 phải xếp; bao_tri 10 trùng current_task.
    // Trước 7.7 (chỉ status 6) cả 4 task bị bỏ → 422. staff_role = 0 → dùng parse nới lỏng như worker.
    if (const char* path = std::getenv("KTV_STAGING_FILE")) {
        std::ifstream in(path);
        if (!in) {
            std::cout << "  (bỏ qua file staging: không mở được " << path << ")\n";
        } else {
            std::stringstream text;
            text << in.rdbuf();
            std::vector<ktv::Error> errors, warnings;
            const ktv::Message message = ktv::parse_message(json::parse(text.str()), errors, &warnings);
            CHECK(errors.empty());
            const ktv::NormalizedWorklist w = ktv::normalize_worklist(message);
            CHECK(w.candidates.size() == 3 && w.current_task && w.current_task->task_id == 1195760056);
            CHECK(w.warnings.empty());
            ktv::PlanResult r = ktv::plan(message, ktv::default_rules(), *ktv::parse_datetime("2026-08-19 07:30:00"));
            CHECK(r.response["statuscode"] == "200" && r.routed == 3);
        }
    }

    if (failures) std::cerr << failures << " lỗi\n";
    else std::cout << "test_normalization: OK\n";
    return failures != 0;
}
