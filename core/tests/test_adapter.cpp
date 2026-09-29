// Adapter local: đọc object/JSONL và gói envelope OUT prototype.
#include <iostream>
#include <sstream>

#include "ktv/adapter/envelope.hpp"
#include "ktv/adapter/file.hpp"

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
        Envelope e = local_envelope(bare, 3, now);
        CHECK(e.message_id == "local-3" && e.trigger == "DAY_START");
        CHECK(format_datetime(e.planned_at) == "2026-09-10 09:00:00");

        json full = {{"message_id", "M1"}, {"trigger", "TASK_NEW"}, {"planned_at", "2026-09-01 08:30:00"}};
        Envelope f = local_envelope(full, 9, now);
        CHECK(f.message_id == "M1" && f.trigger == "TASK_NEW" && format_datetime(f.planned_at) == "2026-09-01 08:30:00");
    }
    {  // wrap_response: đúng khóa, run_code = message_id, giữ data.
        Envelope e{"local-1", "DAY_START", now};
        nlohmann::ordered_json resp = {{"success", true}, {"statuscode", "200"}, {"message", ""},
                                       {"trace_id", "local-1"}, {"server_time", "2026-09-10 09:00:01"},
                                       {"data", {{"staff_id", "1"}}}};
        nlohmann::ordered_json out = wrap_response(e, resp);
        CHECK(out["message_id"] == "local-1" && out["run_code"] == "local-1");
        CHECK(out["schema_version"] == "1" && out["statuscode"] == "200");
        CHECK(out["data"]["staff_id"] == "1");
        // Thứ tự field: message_id đứng trước, data đứng cuối.
        std::vector<std::string> keys;
        for (auto it = out.begin(); it != out.end(); ++it) keys.push_back(it.key());
        CHECK(keys.front() == "message_id" && keys.back() == "data");
    }
    {  // Response lỗi không có data → data = null, không vỡ.
        Envelope e{"local-2", "DAY_START", now};
        nlohmann::ordered_json resp = {{"success", false}, {"statuscode", "400"}, {"message", "x"},
                                       {"trace_id", "local-2"}, {"server_time", "2026-09-10 09:00:01"}, {"data", nullptr}};
        nlohmann::ordered_json out = wrap_response(e, resp);
        CHECK(out["success"] == false && out["data"].is_null());
    }

    if (failures) std::cerr << failures << " lỗi\n";
    else std::cout << "test_adapter: OK\n";
    return failures != 0;
}
