// Adapter local: đọc object/JSONL và gói envelope OUT prototype.
#include <iostream>
#include <sstream>

#include "ktv/adapter/envelope.hpp"
#include "ktv/adapter/file.hpp"
#include "ktv/adapter/log.hpp"

static int failures = 0;
#define CHECK(cond)                                                           \
    do {                                                                      \
        if (!(cond)) {                                                        \
            std::cerr << __FILE__ << ":" << __LINE__ << ": FAIL " #cond "\n"; \
            ++failures;                                                       \
        }                                                                     \
    } while (0)

using namespace ktv;

int main() {
    const Minutes now = *parse_datetime("2026-09-10 09:00:00");

    {  // Một object pretty-printed (nhiều dòng) → một record.
        std::istringstream in("{\n  \"staff\": {},\n  \"tasks\": {}\n}\n");
        std::vector<json> records = read_records(in);
        CHECK(records.size() == 1 && records[0].is_object());
    }
    {  // JSONL nhiều dòng → nhiều record; dòng trắng bị bỏ.
        std::istringstream in("{\"a\":1}\n\n{\"b\":2}\n");
        std::vector<json> records = read_records(in);
        CHECK(records.size() == 2 && records[0]["a"] == 1 && records[1]["b"] == 2);
    }
    {  // JSON hỏng trên một dòng → record discarded để main báo 400, dòng còn lại vẫn đọc.
        std::istringstream in("{bad}\n{\"a\":1}\n");
        std::vector<json> records = read_records(in);
        CHECK(records.size() == 2 && records[0].is_discarded() && records[1]["a"] == 1);
    }
    {  // Object pretty nhiều dòng bị hỏng → đúng MỘT input lỗi, không phải một lỗi mỗi dòng.
        std::istringstream in("{\n  \"staff\": {},\n  \"tasks\": {\n");
        std::vector<json> records = read_records(in);
        CHECK(records.size() == 1 && records[0].is_discarded());
    }
    {  // File rỗng/whitespace → không có input.
        std::istringstream in(" \n\t\n");
        CHECK(read_records(in).empty());
    }
    {  // Envelope: thiếu metadata thì sinh; có thì dùng lại.
        json bare = json::object();
        Envelope e = local_envelope(bare, "local-3", now);
        CHECK(e.message_id == "local-3" && e.trigger == "DAY_START");
        CHECK(format_datetime(e.planned_at) == "2026-09-10 09:00:00");

        json full = {{"message_id", "M1"}, {"trigger", "TASK_NEW"}, {"planned_at", "2026-09-01 08:30:00"}};
        Envelope f = local_envelope(full, "local-9", now);
        CHECK(f.message_id == "M1" && f.trigger == "TASK_NEW" && format_datetime(f.planned_at) == "2026-09-01 08:30:00");
    }
    {  // wrap_response: đúng khóa, run_code = message_id, giữ data.
        Envelope e{"local-1", "DAY_START", now};
        nlohmann::ordered_json resp = {{"success", true}, {"statuscode", "200"}, {"message", ""},
                                       {"trace_id", "local-1"}, {"server_time", "2026-09-10 09:00:01"},
                                       {"data", {{"staff_id", "1"}, {"priority_type", 0}, {"clusters", nlohmann::ordered_json::array()}}}};
        nlohmann::ordered_json out = wrap_response(e, resp);
        CHECK(out["message_id"] == "local-1" && out["run_code"] == "local-1");
        CHECK(out["schema_version"] == "1" && out["statuscode"] == "200");
        // 7.20.1: data là array 1 phần tử; change_id "yes", trace_id "" ngay sau staff_id (sheet 03 bản (5)).
        CHECK(out["data"].is_array() && out["data"].size() == 1);
        CHECK(ktv::out_data(out)["staff_id"] == "1" && ktv::out_data(out)["change_id"] == "yes" && ktv::out_data(out)["trace_id"] == "");
        CHECK(ktv::out_data(out)["route_changed"] == "yes");  // 7.28: mặc định "yes"
        std::vector<std::string> inner;
        for (auto it = out["data"][0].begin(); it != out["data"][0].end(); ++it) inner.push_back(it.key());
        CHECK((inner == std::vector<std::string>{"staff_id", "change_id", "trace_id", "route_changed", "priority_type", "clusters"}));
        // Thứ tự field: message_id đứng trước, data đứng cuối.
        std::vector<std::string> keys;
        for (auto it = out.begin(); it != out.end(); ++it) keys.push_back(it.key());
        CHECK(keys.front() == "message_id" && keys.back() == "data");

        // 7.20.1b reuse_response: vỏ theo envelope mới, data[0] change_id "no" + trace_id = run_code lần tính thật.
        Envelope next{"local-9", "TRAFFIC", now + 10};
        nlohmann::ordered_json again = ktv::reuse_response(out, next, now + 10);
        CHECK(again["message_id"] == "local-9" && again["run_code"] == "local-9" && again["trigger"] == "TRAFFIC");
        CHECK(again["trace_id"] == "local-9" && again["planned_at"] == format_datetime(now + 10));
        CHECK(ktv::out_data(again)["change_id"] == "no" && ktv::out_data(again)["trace_id"] == "local-1");
        CHECK(ktv::out_data(again)["route_changed"] == "no");  // 7.28: cache = cùng tuyến
        CHECK(ktv::out_data(again)["clusters"] == ktv::out_data(out)["clusters"]);
        inner.clear();
        for (auto it = again["data"][0].begin(); it != again["data"][0].end(); ++it) inner.push_back(it.key());
        CHECK((inner == std::vector<std::string>{"staff_id", "change_id", "trace_id", "route_changed", "priority_type", "clusters"}));
        // Cache của cache: vẫn trỏ về lần tính thật (local-1), không phải local-9.
        Envelope third{"local-10", "TRAFFIC", now + 20};
        CHECK(ktv::out_data(ktv::reuse_response(again, third, now + 20))["trace_id"] == "local-1");
        // Bản cache dạng cũ (data object) cũng nhận.
        nlohmann::ordered_json legacy = out;
        legacy["data"] = out["data"][0];
        CHECK(ktv::reuse_response(legacy, next, now + 10)["data"].is_array());
    }
    {  // 7.29 mobix_response (sheet 09): chỉ field sheet 09, đúng thứ tự; IDLE giữ seq … end_at; lỗi → data null.
        nlohmann::ordered_json full = nlohmann::ordered_json::parse(R"({
          "message_id": "m", "run_code": "m", "trigger": "T", "planned_at": "x", "schema_version": "1", "success": true,
          "statuscode": "200", "message": "", "trace_id": "m", "server_time": "x",
          "data": [{"staff_id": "S", "change_id": "yes", "trace_id": "", "route_changed": "yes", "priority_type": 0,
            "clusters": [{"cluster_seg": 1, "cluster_code": "CL-1", "name": "n", "center": "1,1", "radius_m": 1, "task_count": 1,
              "travel_km_inbound": 1.0, "travel_km_internal": 0.0, "handle_minutes": 60,
              "schedule": [{"seq": 1, "entry_type": "IDLE", "at": "08:00", "start_at": "a", "end_at": "b", "duration_minutes": 5, "label": "Chờ"},
                {"seq": 2, "entry_type": "TASK", "at": "08:05", "start_at": "b", "end_at": "c", "task_id": 7, "location": "L",
                 "latlng": "1,1", "task_role": "main", "priority": 50.0, "insert_reason": "", "task_group_id": 2,
                 "task_group_name": "bao_tri", "task_type_id": 1, "task_type_name": "bao_tri_vat_ly", "task_sub_id": 0,
                 "task_sub_name": "", "checkindate": "", "checkoutdate": "", "travel_minutes_before": 3, "travel_km_before": 1.0,
                 "handle_minutes": 60, "projected_sla": "ON_TIME", "sla_buffer_minutes": 20, "contract_id": "", "contract_no": "",
                 "contract_name": "K", "timezone": "+07:00", "appointment": "", "complete_date": ""}]}],
            "metrics": {"total_distance_km": 1.0, "total_travel_minutes": 3, "total_handle_minutes": 60, "tasks_total": 1}}]})");
        const nlohmann::ordered_json view = ktv::mobix_response(full);
        const auto keys = [](const nlohmann::ordered_json& o) {
            std::vector<std::string> k;
            for (auto it = o.begin(); it != o.end(); ++it) k.push_back(it.key());
            return k;
        };
        CHECK((keys(view) == std::vector<std::string>{"success", "statuscode", "message", "data"}));
        const auto& schedule = view["data"][0]["clusters"][0]["schedule"];
        CHECK((keys(schedule[0]) == std::vector<std::string>{"seq", "entry_type", "at", "start_at", "end_at"}));
        CHECK(keys(schedule[1]).back() == "timezone" && !schedule[1].contains("priority") && schedule[1]["contract_name"] == "K");
        CHECK((keys(view["data"][0]["metrics"]) == std::vector<std::string>{"total_distance_km", "total_travel_minutes", "tasks_total"}));
        CHECK(full["data"][0].contains("change_id"));  // bản gốc không đổi
        nlohmann::ordered_json failed = {{"success", false}, {"statuscode", "422"}, {"message", "x"}, {"data", nullptr}};
        CHECK(ktv::mobix_response(failed)["data"].is_null() && ktv::mobix_response(failed)["statuscode"] == "422");
    }
    {  // Response lỗi không có data → data = null, không vỡ.
        Envelope e{"local-2", "DAY_START", now};
        nlohmann::ordered_json resp = {{"success", false}, {"statuscode", "400"}, {"message", "x"},
                                       {"trace_id", "local-2"}, {"server_time", "2026-09-10 09:00:01"}, {"data", nullptr}};
        nlohmann::ordered_json out = wrap_response(e, resp);
        CHECK(out["success"] == false && out["data"].is_null());
    }

    {  // 7.14 log: chế độ payload, payload JSON / chuỗi cắt 64 KB, errors đầy đủ, giờ có giây.
        using ktv::PayloadLog;
        CHECK(ktv::payload_log_from("none") == PayloadLog::None && ktv::payload_log_from("error") == PayloadLog::Error &&
              ktv::payload_log_from("all") == PayloadLog::All && !ktv::payload_log_from("ALL"));
        for (const char* code : {"400", "500"}) CHECK(ktv::payload_wanted(PayloadLog::Error, code));
        for (const char* code : {"200", "424", "422", "STALE"}) CHECK(!ktv::payload_wanted(PayloadLog::Error, code));
        CHECK(ktv::payload_wanted(PayloadLog::All, "200") && !ktv::payload_wanted(PayloadLog::None, "400"));

        const json parsed = json::parse(R"({"staff":{"staff_id":""},"tasks":{}})");
        CHECK(ktv::payload_json(parsed, "bỏ qua")["staff"]["staff_id"] == "");  // đọc được → JSON gốc, jq đọc thẳng
        const std::string broken = "{hỏng";
        CHECK(ktv::payload_json(json::parse(broken, nullptr, false), broken) == broken);
        const std::string huge(70 * 1024, 'x');
        const std::string cut = ktv::payload_json(json::parse(huge, nullptr, false), huge).get<std::string>();
        CHECK(cut.size() < huge.size() && cut.rfind(std::string(ktv::kMaxLoggedPayload, 'x'), 0) == 0 &&
              cut.find("tổng 71680 byte") != std::string::npos);

        std::vector<ktv::Error> errors;
        for (int i = 0; i < 5; ++i) errors.push_back({"staff.f" + std::to_string(i), "sai"});
        const auto listed = ktv::errors_json(errors);
        CHECK(listed.size() == 5 && listed[4]["path"] == "staff.f4" && listed[4]["problem"] == "sai");

        const std::string ts = ktv::log_now();
        CHECK(ts.size() == 19 && ts[4] == '-' && ts[10] == ' ' && ts[16] == ':' && ktv::parse_datetime(ts));
        const auto line = ktv::log_event("message");
        CHECK(line.begin().key() == "ts" && line["event"] == "message");
    }

    {  // 8.11: JSON lồng quá sâu → bỏ phần sâu (trước: tràn stack khi sao chép / dump, process chết), phần còn lại giữ.
        const int depth = 200000;
        const std::string deep = "{\"a\":1,\"deep\":" + std::string(depth, '[') + std::string(depth, ']') + ",\"b\":[[1]]}";
        bool trimmed = false;
        const json value = parse_in(deep, &trimmed);
        CHECK(trimmed && value["a"] == 1 && value["b"] == json::parse("[[1]]"));
        int levels = 0;  // "deep" còn đúng kMaxJsonDepth − 1 tầng mảng (gốc object là tầng 0, mảng tầng 1..63)
        for (const json* at = &value["deep"]; ++levels, !at->empty(); at = &(*at)[0]) {
        }
        CHECK(levels == kMaxJsonDepth - 1);
        CHECK(!value.dump().empty() && json(value).size() == 3);  // sao chép / dump được
        const json inner = parse_in(R"({"x":{"y":[{"deep":[[{"k":"v"}]]}]}})", &trimmed);
        CHECK(!trimmed && inner["x"]["y"][0]["deep"][0][0]["k"] == "v");
        CHECK(parse_in("{bad", &trimmed).is_discarded() && !trimmed);
        std::string chain = "{}";  // object lồng object qua key: object quá sâu → key của nó bị gỡ, phần tử bên cạnh giữ
        for (int k = 0; k < 100; ++k) chain = "{\"k\":" + chain + "}";
        const json mixed = parse_in("[1," + chain + ",2]", &trimmed);
        CHECK(trimmed && mixed.size() == 3 && mixed[0] == 1 && mixed[2] == 2 && mixed[1].contains("k"));
        std::vector<char> marks;  // JSONL: đánh dấu đúng dòng bị cắt
        std::istringstream in("{\"a\":1}\n" + deep + "\n");
        CHECK(read_records(in, &marks).size() == 2 && marks == std::vector<char>({0, 1}));
        CHECK(json_too_deep().code == "JSON_TOO_DEEP");
    }

    if (failures) std::cerr << failures << " lỗi\n";
    else std::cout << "test_adapter: OK\n";
    return failures != 0;
}
