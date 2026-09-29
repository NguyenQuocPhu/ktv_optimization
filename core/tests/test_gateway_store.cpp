// Store: put/get/get_latest/size, ghi đè, an toàn nhiều luồng.
#include <iostream>
#include <string>
#include <thread>
#include <vector>

#include "ktv/gateway/store.hpp"

static int failures = 0;
#define CHECK(cond)                                                           \
    do {                                                                      \
        if (!(cond)) {                                                        \
            std::cerr << __FILE__ << ":" << __LINE__ << ": FAIL " #cond "\n"; \
            ++failures;                                                       \
        }                                                                     \
    } while (0)

int main() {
    ktv::MemoryRouteStore store;
    CHECK(store.size() == 0);
    CHECK(!store.get("A", "2026-09-10"));
    CHECK(!store.get_latest("A"));

    store.put("A", "2026-09-10", "{\"v\":1}");
    store.put("A", "2026-09-11", "{\"v\":2}");
    store.put("B", "2026-09-10", "{\"v\":3}");
    CHECK(store.size() == 3);
    CHECK(store.get("A", "2026-09-10") && store.get("A", "2026-09-10").value() == "{\"v\":1}");
    CHECK(!store.get("A", "2026-09-09"));
    CHECK(store.get_latest("A") && store.get_latest("A").value() == "{\"v\":2}");
    CHECK(store.get_latest("B") && store.get_latest("B").value() == "{\"v\":3}");
    CHECK(!store.get_latest("C"));

    store.put("A", "2026-09-11", "{\"v\":9}");  // ghi đè cùng key
    CHECK(store.size() == 3);
    CHECK(store.get_latest("A") && store.get_latest("A").value() == "{\"v\":9}");

    {  // nhiều luồng đọc/ghi cùng lúc: không crash, size đúng
        std::vector<std::thread> threads;
        for (int t = 0; t < 4; ++t)
            threads.emplace_back([&store, t] {
                const std::string staff = "T" + std::to_string(t);
                for (int i = 0; i < 200; ++i) {
                    store.put(staff, "2026-09-10", "{\"i\":" + std::to_string(i) + "}");
                    (void)store.get(staff, "2026-09-10");
                    (void)store.get_latest("A");
                }
            });
        for (auto& thread : threads) thread.join();
        CHECK(store.size() == 7);
    }

    if (failures) std::cerr << failures << " lỗi\n";
    else std::cout << "test_gateway_store: OK\n";
    return failures != 0;
}
