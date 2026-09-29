// Seed: đọc OUT JSONL, index (staff, date), bỏ dòng hỏng/thiếu field.
#include <iostream>
#include <sstream>

#include "ktv/gateway/seed.hpp"

static int failures = 0;
#define CHECK(cond)                                                           \
    do {                                                                      \
        if (!(cond)) {                                                        \
            std::cerr << __FILE__ << ":" << __LINE__ << ": FAIL " #cond "\n"; \
            ++failures;                                                       \
        }                                                                     \
    } while (0)

int main() {
    const std::string a10 = "{\"planned_at\":\"2026-09-10 09:00:00\",\"data\":{\"staff_id\":\"A\"}}";
    const std::string a11 = "{\"planned_at\":\"2026-09-11 09:00:00\",\"data\":{\"staff_id\":\"A\"}}";
    const std::string b10 = "{\"planned_at\":\"2026-09-10 10:00:00\",\"data\":{\"staff_id\":\"B\"}}";

    {  // dòng hợp lệ + dòng hỏng/thiếu field; dòng trắng bị bỏ
        std::istringstream in(a10 + "\n" +
                              "not json\n" +
                              a11 + "\n" +
                              b10 + "\n" +
                              "{\"data\":{\"staff_id\":\"C\"}}\n" +                    // thiếu planned_at
                              "{\"planned_at\":\"2026-09-10 09:00:00\"}\n" +            // thiếu data
                              "{\"planned_at\":\"bad\",\"data\":{\"staff_id\":\"D\"}}\n" +  // planned_at sai dạng
                              "{\"planned_at\":\"2026-09-10 09:00:00\",\"data\":{\"staff_id\":\"\"}}\n" +  // staff rỗng
                              "\n");
        ktv::MemoryRouteStore store;
        CHECK(ktv::load_routes(in, store) == 3);
        CHECK(store.size() == 3);
        CHECK(store.get("A", "2026-09-10") && store.get("A", "2026-09-10").value() == a10);
        CHECK(store.get("B", "2026-09-10") && store.get("B", "2026-09-10").value() == b10);
        CHECK(store.get_latest("A") && store.get_latest("A").value() == a11);
        CHECK(!store.get("C", "2026-09-10") && !store.get("D", "2026-09-10"));
    }
    {  // file rỗng
        std::istringstream in("\n \n");
        ktv::MemoryRouteStore store;
        CHECK(ktv::load_routes(in, store) == 0);
        CHECK(store.size() == 0);
    }
    {  // cùng (staff, date) nhiều dòng: bản sau thắng (nạp tuần tự)
        std::istringstream in(a10 + "\n" +
                              "{\"planned_at\":\"2026-09-10 18:00:00\",\"data\":{\"staff_id\":\"A\"}}\n");
        ktv::MemoryRouteStore store;
        CHECK(ktv::load_routes(in, store) == 2);
        CHECK(store.size() == 1);
        CHECK(store.get("A", "2026-09-10"));
    }

    if (failures) std::cerr << failures << " lỗi\n";
    else std::cout << "test_gateway_seed: OK\n";
    return failures != 0;
}
