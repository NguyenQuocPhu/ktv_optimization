// RedisRouteStore: put/get/get_latest/size trên Redis thật.
// Cần env KTV_TEST_REDIS=host:port; không có hoặc không kết nối được thì bỏ qua (pass).
#include <cstdlib>
#include <iostream>
#include <string>

#include <unistd.h>

#include <hiredis/hiredis.h>

#include "ktv/gateway/redis_store.hpp"

static int failures = 0;
#define CHECK(cond)                                                           \
    do {                                                                      \
        if (!(cond)) {                                                        \
            std::cerr << __FILE__ << ":" << __LINE__ << ": FAIL " #cond "\n"; \
            ++failures;                                                       \
        }                                                                     \
    } while (0)

namespace {

// Xóa mọi key của bài test theo prefix qua hiredis, không để rác lại.
void cleanup(const std::string& host, int port, const std::string& prefix) {
    redisContext* context = redisConnectWithTimeout(host.c_str(), port, {1, 500000});
    if (!context || context->err) {
        if (context) redisFree(context);
        return;
    }
    const std::string pattern = prefix + "*";
    std::string cursor = "0";
    do {
        auto* scan = static_cast<redisReply*>(
            redisCommand(context, "SCAN %s MATCH %s COUNT 1000", cursor.c_str(), pattern.c_str()));
        if (!scan || scan->type != REDIS_REPLY_ARRAY || scan->elements != 2) {
            if (scan) freeReplyObject(scan);
            break;
        }
        cursor.assign(scan->element[0]->str, scan->element[0]->len);
        redisReply* keys = scan->element[1];
        for (size_t i = 0; keys->type == REDIS_REPLY_ARRAY && i < keys->elements; ++i) {
            auto* deleted = static_cast<redisReply*>(redisCommand(context, "DEL %s", keys->element[i]->str));
            if (deleted) freeReplyObject(deleted);
        }
        freeReplyObject(scan);
    } while (cursor != "0");
    redisFree(context);
}

}  // namespace

int main() {
    const char* address = std::getenv("KTV_TEST_REDIS");
    if (!address || !*address) {
        std::cout << "test_gateway_redis: SKIP (không có KTV_TEST_REDIS)\n";
        return 0;
    }
    const std::string addr = address;
    const auto colon = addr.rfind(':');
    if (colon == std::string::npos) {
        std::cout << "test_gateway_redis: SKIP (KTV_TEST_REDIS phải là host:port)\n";
        return 0;
    }

    ktv::RedisRouteStore::Config config;
    config.host = addr.substr(0, colon);
    config.port = std::stoi(addr.substr(colon + 1));
    config.prefix = "ktvtest:" + std::to_string(getpid()) + ":";

    try {
        ktv::RedisRouteStore store(config);
        const std::string route_a = "{\"data\":{\"staff_id\":\"A\"}}";
        const std::string route_a_old = "{\"data\":{\"staff_id\":\"A\"},\"v\":1}";

        CHECK(!store.get("A", "2026-09-10"));
        CHECK(!store.get_latest("A"));
        CHECK(store.size() == 0);

        store.put("A", "2026-09-10", route_a_old);
        store.put("A", "2026-09-11", route_a);
        store.put("B", "2026-09-10", route_a);
        CHECK(store.size() == 3);
        CHECK(store.get("A", "2026-09-10") && store.get("A", "2026-09-10").value() == route_a_old);
        CHECK(store.get_latest("A") && store.get_latest("A").value() == route_a);
        CHECK(store.get_latest("B") && store.get_latest("B").value() == route_a);
        CHECK(!store.get("Z", "2026-09-10"));

        store.put("A", "2026-09-11", "{\"data\":{\"staff_id\":\"A\"},\"v\":9}");  // ghi đè
        CHECK(store.size() == 3);
        CHECK(store.get_latest("A") && store.get_latest("A").value().find("\"v\":9") != std::string::npos);

        // ghi ngày cũ hơn không được kéo latest lùi
        store.put("A", "2026-09-09", route_a_old);
        CHECK(store.get_latest("A") && store.get_latest("A").value().find("\"v\":9") != std::string::npos);

        cleanup(config.host, config.port, config.prefix);
    } catch (const std::exception& error) {
        std::cout << "test_gateway_redis: SKIP (không kết nối Redis: " << error.what() << ")\n";
        return 0;
    }

    if (failures) std::cerr << failures << " lỗi\n";
    else std::cout << "test_gateway_redis: OK\n";
    return failures != 0;
}
