// Pipeline end-to-end: nhiều kịch bản ETA/SLA, ca làm, current task, create_date, heuristic, biên.
#include <cmath>
#include <iostream>
#include <map>
#include <string>

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
using ojson = nlohmann::ordered_json;

// Một task hợp lệ; appointment/create_date/handle để trống thì truyền "".
static json task(long long id, const char* group, int gid, const char* type, int tid, json sla_minutes, int prio,
                 const char* appointment, const char* latlng, json handle, const char* create_date = "") {
    return json{
        {"task_id", id}, {"task_group_id", gid}, {"task_group_name", group},
        {"task_type_id", tid}, {"task_type_name", type}, {"task_sub_id", 0}, {"task_sub_name", ""},
        {"task_status_id", 6}, {"task_status_name", ""},
        {"sla", {{"sla_minutes", sla_minutes}, {"priority_in_day", prio}}},
        {"appointment", appointment}, {"create_date", create_date}, {"complete_date", ""},
        {"location", ""}, {"latlng", latlng}, {"handle_minutes", handle},
        {"task_plots_id", 1}, {"staff_plots_id", 1}, {"staff_role", 1}, {"block_id", 1}};
}

static json message(const char* planned, json trien, json bao, json thu, json hoa, json onsite, json current = nullptr) {
    return json{{"message_id", "M"}, {"planned_at", planned}, {"trigger", "DAY_START"},
                {"staff", {{"staff_id", "1"}, {"staff_account", "A"}, {"latlng", "21.02,105.80"},
                           {"available", "08:00-17:30"},
                           {"plots", json::array({{{"id", 1}, {"name", "P"}, {"role", 1}, {"block_id", 1}}})},
                           {"current_task", current}}},
                {"tasks", {{"trien_khai", trien}, {"bao_tri", bao}, {"thu_hoi", thu}, {"hoa_don", hoa}, {"onsite", onsite}}}};
}

static const json none = json::array();

static std::optional<ktv::PlanResult> run(const json& msg, const ktv::Rules& rules = ktv::default_rules()) {
    std::vector<ktv::Error> errors;
    ktv::Message parsed = ktv::parse_message(msg, errors);
    if (!errors.empty()) {
        for (const auto& e : errors) std::cerr << "  lỗi không mong đợi: " << e.path << " " << e.problem << "\n";
        ++failures;
        return std::nullopt;
    }
    return ktv::plan(parsed, rules, *ktv::parse_datetime("2026-09-10 09:00:00"));
}

static std::string first_sla(const ojson& response) {
    for (const auto& cluster : response["data"]["clusters"])
        for (const auto& row : cluster["schedule"])
            if (row["entry_type"] == "TASK") return row["projected_sla"].get<std::string>();
    return "";
}

static const ojson& first_task(const ojson& response) {
    for (const auto& cluster : response["data"]["clusters"])
        for (const auto& row : cluster["schedule"])
            if (row["entry_type"] == "TASK") return row;
    static const ojson empty = nullptr;
    return empty;
}

int main() {
    const char* here = "21.03,105.81";  // cách điểm xuất phát ~1,4 km / ~3 phút

    {  // ON_TIME: hẹn chiều, tới sớm thì chờ tới hẹn (nghỉ trưa xếp trước).
        json m = message("2026-09-10 09:00:00", none,
                         json::array({task(1, "bao_tri", 2, "bao_tri_vat_ly", 1, 60, 1, "2026-09-10 15:00:00", here, "")}),
                         none, none, none);
        auto r = run(m);
        CHECK(r && first_sla(r->response) == "ON_TIME");
        CHECK(r && first_task(r->response)["start_at"] == "2026-09-10 15:00:00");  // chờ tới hẹn
    }
    {  // AT_RISK: hẹn 08:11 + SLA 60 → hạn 09:11, check-in ~09:03, còn dư < 20 phút.
        json m = message("2026-09-10 09:00:00", none,
                         json::array({task(1, "bao_tri", 2, "bao_tri_vat_ly", 1, 60, 1, "2026-09-10 08:11:00", here, "")}),
                         none, none, none);
        auto r = run(m);
        CHECK(r && first_sla(r->response) == "AT_RISK");
    }
    {  // WILL_BREACH: hạn đã tới ngay lúc check-in (check-in sau hạn, chưa quá hạn khi lập tuyến).
        json m = message("2026-09-10 09:00:00", none,
                         json::array({task(1, "bao_tri", 2, "bao_tri_vat_ly", 1, 60, 1, "2026-09-10 08:00:00", here, "")}),
                         none, none, none);
        auto r = run(m);
        CHECK(r && first_sla(r->response) == "WILL_BREACH");
    }
    {  // ALREADY_BREACHED: hạn đã qua trước cả khi lập tuyến.
        json m = message("2026-09-10 09:00:00", none,
                         json::array({task(1, "bao_tri", 2, "bao_tri_vat_ly", 1, 60, 1, "2026-09-10 07:00:00", here, "")}),
                         none, none, none);
        auto r = run(m);
        CHECK(r && first_sla(r->response) == "ALREADY_BREACHED");
        CHECK(r && r->response["data"]["metrics"]["breach_forecast_count"] == 1);
    }
    {  // Xong ngoài ca: việc dài 1000 phút vượt 17:30 → overload_minutes > 0, finish sau shift_end.
        json m = message("2026-09-10 09:00:00", none,
                         json::array({task(1, "bao_tri", 2, "bao_tri_vat_ly", 1, 60, 1, "", here, 1000)}),
                         none, none, none);
        auto r = run(m);
        const auto& metrics = r->response["data"]["metrics"];
        CHECK(r && metrics["overload_minutes"].get<long long>() > 0);
        CHECK(r && metrics["finish_at"].get<std::string>() > metrics["shift_end_at"].get<std::string>());
    }
    {  // Việc đang làm: cộng current_task_minutes vào giờ xuất phát, không thành stop.
        json current = {{"task_id", 99}, {"task_status_id", 10}, {"task_type_id", 1}};
        json m = message("2026-09-10 09:00:00", none,
                         json::array({task(1, "bao_tri", 2, "bao_tri_vat_ly", 1, 60, 1, "", here, "")}),
                         none, none, none, current);
        auto r = run(m);
        CHECK(r && r->response["data"]["metrics"]["start_at"] == "2026-09-10 09:30:00");  // 09:00 + 30 phút
        CHECK(r && r->response["data"]["metrics"]["tasks_total"] == 1);
        for (const auto& cluster : r->response["data"]["clusters"])
            for (const auto& row : cluster["schedule"])
                CHECK(row["entry_type"] != "TASK" || row["task_id"] != 99);
    }
    {  // create_date quyết định hạn "trong tháng": tháng 6 đã qua → trễ hoàn tất; thiếu create_date lấy planned_at.
       // (Chạy ngày 28/09 — 2 ngày làm việc tới hạn tháng ≤ K, nếu chạy 10/09 thì bị lọc K ở 7.16.1.)
        json with = message("2026-09-28 09:00:00", none, none,
                            json::array({task(1, "thu_hoi", 3, "thu_hoi_thiet_bi", 1, nullptr, 4, "", here, "", "2026-06-03 08:00:00")}),
                            none, none);
        auto r1 = run(with);
        CHECK(r1 && first_sla(r1->response) == "ALREADY_BREACHED");
        json without = message("2026-09-28 09:00:00", none, none,
                               json::array({task(1, "thu_hoi", 3, "thu_hoi_thiet_bi", 1, nullptr, 4, "", here, "")}),
                               none, none);
        auto r2 = run(without);
        CHECK(r2 && first_sla(r2->response) == "ON_TIME");
    }
    {  // Hơn max_exact_tasks → heuristic, vẫn phủ đủ mọi việc. (Toạ độ cách ~1,1 km để không bị gom điểm dừng 7.16.3.)
        json tasks = json::array();
        for (int i = 0; i < 13; ++i)
            tasks.push_back(task(100 + i, "bao_tri", 2, "bao_tri_vat_ly", 1, 60, 1, "",
                                 ("21." + std::to_string(30 + i) + ",105.81").c_str(), ""));
        auto r = run(message("2026-09-10 09:00:00", none, tasks, none, none, none));
        CHECK(r && r->source == ktv::Source::Heuristic);
        CHECK(r && r->response["data"]["metrics"]["tasks_total"] == 13);
    }
    {  // Quá 64 điểm dừng → 422.
        json tasks = json::array();
        for (int i = 0; i < 65; ++i)
            tasks.push_back(task(200 + i, "bao_tri", 2, "bao_tri_vat_ly", 1, 60, 1, "",
                                 ("21." + std::to_string(30 + i) + ",105.82").c_str(), ""));
        auto r = run(message("2026-09-10 09:00:00", none, tasks, none, none, none));
        CHECK(r && r->response["statuscode"] == "422" && r->response["data"].is_null());
    }
    {  // Không có việc nào → 422.
        auto r = run(message("2026-09-10 09:00:00", none, none, none, none, none));
        CHECK(r && r->response["statuscode"] == "422" && r->response["data"].is_null());
    }
    {  // Việc thiếu tọa độ → 422.
        auto r = run(message("2026-09-10 09:00:00", none,
                             json::array({task(1, "bao_tri", 2, "bao_tri_vat_ly", 1, 60, 1, "", "", "")}),
                             none, none, none));
        CHECK(r && r->response["statuscode"] == "422");
    }
    {  // staff.status=3 (off) → 422 dù có việc.
        json m = message("2026-09-10 09:00:00", none,
                         json::array({task(1, "bao_tri", 2, "bao_tri_vat_ly", 1, 60, 1, "", here, "")}),
                         none, none, none);
        m["staff"]["status"] = 3;
        CHECK(run(m, ktv::default_rules())->response["statuscode"] == "422");
    }
    {  // Lô 0 lùi xuống block cho AREA_REENTRY. Thẳng hàng: xuất phát — X1 (1 km) — Y (2 km) — X2 (3 km).
       // X1, X2 cùng block 5, Y block 6. Không tính block: X1→Y→X2 (3 km). Có: quay lại block 5 phạt 2 (≈ 2 km)
       // → X1→X2→Y (4 km). Block 0 = không thuộc khu vực nào → giữ thứ tự ngắn nhất như trước.
        auto order = [](const ojson& response) {
            std::vector<long long> ids;
            for (const auto& cluster : response["data"]["clusters"])
                for (const auto& row : cluster["schedule"])
                    if (row["entry_type"] == "TASK") ids.push_back(row["task_id"].get<long long>());
            return ids;
        };
        auto line = [](long long id, const char* latlng, int block) {
            json t = task(id, "bao_tri", 2, "bao_tri_vat_ly", 1, 60, 1, "", latlng, 30);  // không hẹn → không hạn
            t["task_plots_id"] = 0;
            t["staff_plots_id"] = 0;
            t["block_id"] = block;
            return t;
        };
        auto tasks = [&](int x, int y) {
            return json::array({line(1, "21.029,105.80", x), line(2, "21.038,105.80", y), line(3, "21.047,105.80", x)});
        };
        auto with_block = run(message("2026-09-10 09:00:00", none, tasks(5, 6), none, none, none));
        auto no_block = run(message("2026-09-10 09:00:00", none, tasks(0, 0), none, none, none));
        CHECK(with_block && order(with_block->response) == (std::vector<long long>{1, 3, 2}));
        CHECK(with_block && with_block->response["data"]["metrics"]["revisit_count"] == 0);
        CHECK(no_block && order(no_block->response) == (std::vector<long long>{1, 2, 3}));
    }
    {  // 7.9: dòng TASK có location, latlng (6 chữ số), contract_id / contract_no như input, đúng thứ tự sheet 03.
        json a = task(1, "bao_tri", 2, "bao_tri_vat_ly", 1, 60, 1, "", "21.0291234,105.8012345", "");
        a["location"] = "Số 12 ngõ 45 Trần Duy Hưng";
        a["contract_id"] = 1126569863;
        a["contract_no"] = "SGABP0236";
        json b = task(2, "bao_tri", 2, "bao_tri_vat_ly", 1, 60, 1, "", "21.0301,105.8013", "");
        b["contract_id"] = nullptr;  // null → null; contract_no không gửi → null
        json c = task(3, "bao_tri", 2, "bao_tri_vat_ly", 1, 60, 1, "", "21.0311,105.8014", "");
        c["contract_no"] = "";       // "" giữ nguyên; contract_id không gửi → null
        auto r = run(message("2026-09-10 09:00:00", none, json::array({a, b, c}), none, none, none));
        CHECK(r && r->response["statuscode"] == "200");
        std::map<long long, ojson> rows;
        if (r)
            for (const auto& cluster : r->response["data"]["clusters"])
                for (const auto& row : cluster["schedule"])
                    if (row["entry_type"] == "TASK") rows[row["task_id"].get<long long>()] = row;
        CHECK(rows.size() == 3);
        if (rows.size() == 3) {
            const ojson& ra = rows[1];
            CHECK(ra["location"] == "Số 12 ngõ 45 Trần Duy Hưng" && ra["latlng"] == "21.029123,105.801235");
            CHECK(ra["contract_id"].is_number_integer() && ra["contract_id"] == 1126569863 && ra["contract_no"] == "SGABP0236");
            CHECK(rows[2]["contract_id"].is_null() && rows[2]["contract_no"].is_null() && rows[2]["location"] == "");
            CHECK(rows[3]["contract_id"].is_null() && rows[3]["contract_no"] == "");
            std::vector<std::string> keys;  // thứ tự field theo sheet 03
            for (auto it = ra.begin(); it != ra.end(); ++it) keys.push_back(it.key());
            const std::vector<std::string> expected = {
                "seq", "entry_type", "at", "start_at", "end_at", "task_id", "location", "latlng", "task_role",
                "insert_reason", "task_group_id", "task_group_name", "task_type_id", "task_type_name", "task_sub_id",
                "task_sub_name", "checkindate", "checkoutdate", "travel_minutes_before", "travel_km_before",
                "handle_minutes", "projected_sla", "contract_id", "contract_no"};
            if (keys != expected) {
                std::cerr << "  thứ tự field TASK:";
                for (const auto& k : keys) std::cerr << " " << k;
                std::cerr << "\n";
            }
            CHECK(keys == expected);
        }
        // 7.13: data = {staff_id, priority_type (luôn 0 = default), clusters, metrics}.
        if (r) {
            const ojson& data = r->response["data"];
            std::vector<std::string> data_keys;
            for (auto it = data.begin(); it != data.end(); ++it) data_keys.push_back(it.key());
            CHECK(data["priority_type"] == 0);
            CHECK(data_keys == (std::vector<std::string>{"staff_id", "priority_type", "clusters", "metrics"}));
        }
        // Tâm cụm vẫn 4 chữ số.
        if (r) CHECK(r->response["data"]["clusters"][0]["center"].get<std::string>().size() == std::string("21.0301,105.8013").size());
    }
    {  // complete_date = ngày hoàn tất kỳ trước (workbook (3)): KHÔNG loại việc, không đổi giờ/định mức.
        json previous = task(2, "bao_tri", 2, "bao_tri_vat_ly", 1, 60, 1, "", here, "");
        previous["complete_date"] = "2026-09-01 10:00:00";
        json same = task(2, "bao_tri", 2, "bao_tri_vat_ly", 1, 60, 1, "", here, "");
        auto with = run(message("2026-09-10 09:00:00", none,
                                json::array({task(1, "bao_tri", 2, "bao_tri_vat_ly", 1, 60, 1, "", here, ""), previous}),
                                none, none, none));
        auto without = run(message("2026-09-10 09:00:00", none,
                                   json::array({task(1, "bao_tri", 2, "bao_tri_vat_ly", 1, 60, 1, "", here, ""), same}),
                                   none, none, none));
        CHECK(with && with->response["data"]["metrics"]["tasks_total"] == 2);
        CHECK(with && without && with->response["data"]["clusters"] == without->response["data"]["clusters"]);
    }
    {  // Nhiều cụm: tổng task_count = tasks_total; inbound+internal ≈ tổng khoảng cách.
        auto r = run(message("2026-09-10 09:00:00", none,
                             json::array({task(1, "bao_tri", 2, "bao_tri_vat_ly", 1, 60, 1, "", "21.00,105.80", ""),
                                          task(2, "bao_tri", 2, "bao_tri_vat_ly", 1, 60, 1, "", "21.10,105.80", ""),
                                          task(3, "bao_tri", 2, "bao_tri_vat_ly", 1, 60, 1, "", "21.1005,105.80", "")}),
                             none, none, none));
        CHECK(r && r->response["data"]["metrics"]["cluster_count"] == 2);
        if (r) {
            long long sum = 0;
            double legs = 0;
            for (const auto& c : r->response["data"]["clusters"]) {
                sum += c["task_count"].get<long long>();
                legs += c["travel_km_inbound"].get<double>() + c["travel_km_internal"].get<double>();
            }
            CHECK(sum == r->response["data"]["metrics"]["tasks_total"].get<long long>());
            CHECK(std::abs(legs - r->response["data"]["metrics"]["total_distance_km"].get<double>()) < 0.3);
        }
    }
    {  // Tới sớm hơn hẹn → có dòng IDLE trước TASK.
        auto r = run(message("2026-09-10 09:00:00", none,
                             json::array({task(1, "bao_tri", 2, "bao_tri_vat_ly", 1, 60, 1, "2026-09-10 15:00:00", here, "")}),
                             none, none, none));
        bool idle = false;
        if (r)
            for (const auto& c : r->response["data"]["clusters"])
                for (const auto& row : c["schedule"]) idle |= row["entry_type"] == "IDLE";
        CHECK(r && idle);
    }
    {  // Tất định: cùng input cho cùng output.
        json m = message("2026-09-10 09:00:00", none,
                         json::array({task(1, "bao_tri", 2, "bao_tri_vat_ly", 1, 60, 1, "", here, ""),
                                      task(2, "bao_tri", 2, "bao_tri_logic", 2, 60, 2, "2026-09-10 11:00:00", "21.04,105.83", "")}),
                         none, none, none);
        auto a = run(m);
        auto b = run(m);
        CHECK(a && b && a->response == b->response);
    }

    if (failures) std::cerr << failures << " lỗi\n";
    else std::cout << "test_pipeline: OK\n";
    return failures != 0;
}
