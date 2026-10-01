// RedisStore trên Redis thật: route put/get/get_latest/size + state/route/loc "chỉ ghi khi mới hơn", dedup, TTL.
// Cần env KTV_TEST_REDIS=host:port; không có hoặc không kết nối được thì bỏ qua (pass).
#include <cstdlib>
#include <iostream>
#include <memory>
#include <vector>
#include <string>
#include <thread>

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

// Lệnh Redis trả số nguyên (TTL, EXISTS…), -999 nếu lỗi.
long long redis_int(const std::string& host, int port, std::vector<const char*> argv) {
    redisContext* context = redisConnectWithTimeout(host.c_str(), port, {1, 500000});
    long long out = -999;
    if (context && !context->err) {
        auto* reply = static_cast<redisReply*>(redisCommandArgv(context, argv.size(), argv.data(), nullptr));
        if (reply && reply->type == REDIS_REPLY_INTEGER) out = reply->integer;
        if (reply) freeReplyObject(reply);
    }
    if (context) redisFree(context);
    return out;
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

    ktv::RedisStore::Config config;
    config.host = addr.substr(0, colon);
    config.port = std::stoi(addr.substr(colon + 1));
    config.prefix = "ktvtest:" + std::to_string(getpid()) + ":";

    std::unique_ptr<ktv::RedisStore> holder;
    try {
        holder = std::make_unique<ktv::RedisStore>(config);
    } catch (const std::exception& error) {
        std::cout << "test_gateway_redis: SKIP (không kết nối Redis: " << error.what() << ")\n";
        return 0;
    }
    try {
        ktv::RedisStore& store = *holder;
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

        // --- State IN: chỉ ghi khi version mới hơn hẳn ---
        CHECK(!store.get_state("S"));
        CHECK(store.put_state("S", "in-0805", {20261001080500, 7}));
        CHECK(!store.put_state("S", "in-0800", {20261001080000, 99}));  // IN đến trễ không kéo lùi
        CHECK(!store.put_state("S", "in-0805-lai", {20261001080500, 7}));  // bằng → không ghi
        CHECK(store.get_state("S") && store.get_state("S")->json == "in-0805");
        CHECK(store.put_state("S", "in-0805-o8", {20261001080500, 8}));  // cùng giờ, offset lớn hơn
        CHECK(store.put_state("S", "in-0810", {20261001081000}));        // thiếu số thứ hai = 0, vẫn mới hơn
        CHECK(store.get_state("S") && store.get_state("S")->json == "in-0810");
        CHECK(store.get_state("S") && store.get_state("S")->version == ktv::Version({20261001081000}));

        // --- Route có based_on: gateway tính chậm trên state cũ không đè route worker vừa ghi ---
        CHECK(store.put_route("S", "2026-10-01", "route-tu-in-0810", {20261001081000, 0, 0}));
        CHECK(!store.put_route("S", "2026-10-01", "route-cham-in-0805", {20261001080500, 7, 20261001092000}));
        CHECK(store.get("S", "2026-10-01") == std::optional<std::string>("route-tu-in-0810"));
        CHECK(store.put_route("S", "2026-10-01", "route-replan-0920", {20261001081000, 0, 20261001092000}));
        CHECK(store.get_latest("S") == std::optional<std::string>("route-replan-0920"));
        CHECK(store.put_route("S", "2026-09-30", "route-hom-qua", {20260930080000}));  // ngày khác: khóa khác
        CHECK(store.get_latest("S") == std::optional<std::string>("route-replan-0920"));  // latest không lùi

        // --- Khóa route dạng chuỗi (trước 7.2) bị thay bằng hash, không lỗi WRONGTYPE ---
        const std::string legacy = config.prefix + "route:L:2026-10-01";
        redis_int(config.host, config.port, {"APPEND", legacy.c_str(), "chuoi-cu"});
        CHECK(!store.get("L", "2026-10-01"));
        CHECK(store.put_route("L", "2026-10-01", "route-moi", {1}));
        CHECK(store.get("L", "2026-10-01") == std::optional<std::string>("route-moi"));

        // --- Vị trí Mobix + dedup ---
        CHECK(store.put_loc("S", R"({"latlng":"21.02,105.79"})", {20261001092000}));
        CHECK(!store.put_loc("S", R"({"latlng":"cu"})", {20261001091500}));
        CHECK(store.get_loc("S") && store.get_loc("S")->json == R"({"latlng":"21.02,105.79"})");
        CHECK(!store.get_dedup("S"));
        store.put_dedup("S", "20261001081000|21.0200,105.7900");
        CHECK(store.get_dedup("S") == std::optional<std::string>("20261001081000|21.0200,105.7900"));

        // --- TTL theo loại khóa ---
        const auto ttl = [&](const std::string& key) {
            return redis_int(config.host, config.port, {"TTL", key.c_str()});
        };
        const auto near = [](long long value, long long want) { return value > want - 60 && value <= want; };
        CHECK(near(ttl(config.prefix + "state:S"), 2 * 24 * 3600));
        CHECK(near(ttl(config.prefix + "route:S:2026-10-01"), 2 * 24 * 3600));
        CHECK(near(ttl(config.prefix + "latest:S"), 2 * 24 * 3600));
        CHECK(near(ttl(config.prefix + "loc:S"), 24 * 3600));
        CHECK(near(ttl(config.prefix + "dedup:S"), 24 * 3600));

        // --- Hai replica ghi song song (hai kết nối riêng): cuối cùng luôn là version lớn nhất ---
        {
            ktv::RedisStore other(config);
            const auto writer = [](ktv::RedisStore& target, int parity) {
                for (int i = parity; i < 400; i += 2) target.put_state("R", "v" + std::to_string(i), {i});
            };
            std::thread first(writer, std::ref(store), 0);
            std::thread second(writer, std::ref(other), 1);
            first.join();
            second.join();
            CHECK(store.get_state("R") && store.get_state("R")->json == "v399");
        }

    } catch (const std::exception& error) {
        std::cerr << "FAIL: ngoại lệ " << error.what() << "\n";
        ++failures;
    }
    cleanup(config.host, config.port, config.prefix);

    if (failures) std::cerr << failures << " lỗi\n";
    else std::cout << "test_gateway_redis: OK\n";
    return failures != 0;
}
