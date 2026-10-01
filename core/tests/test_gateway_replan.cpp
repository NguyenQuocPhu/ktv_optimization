// Gateway replan (Phase 7.4) trên Redis thật qua HTTP: 404/400/401, MISS → HIT, GPS rung < 11 m vẫn HIT,
// đổi vị trí / IN mới → MISS, envelope replan, route mới hơn của worker thắng, Redis rớt kết nối thì tự nối lại.
// Cần env KTV_TEST_REDIS=host:port; không kết nối được thì SKIP.
#include <cstdlib>
#include <iostream>
#include <memory>
#include <string>
#include <thread>
#include <vector>

#include <unistd.h>

#include <hiredis/hiredis.h>
#include <httplib.h>

#include "ktv/gateway/redis_store.hpp"
#include "ktv/gateway/server.hpp"

static int failures = 0;
#define CHECK(cond)                                                           \
    do {                                                                      \
        if (!(cond)) {                                                        \
            std::cerr << __FILE__ << ":" << __LINE__ << ": FAIL " #cond "\n"; \
            ++failures;                                                       \
        }                                                                     \
    } while (0)

using ktv::json;

namespace {

json message(const std::string& id) {
    const json task = {
        {"task_id", 1}, {"task_group_id", 2}, {"task_group_name", "bao_tri"},
        {"task_type_id", 1}, {"task_type_name", "bao_tri_vat_ly"}, {"task_sub_id", 0}, {"task_sub_name", ""},
        {"task_status_id", 6}, {"task_status_name", ""},
        {"sla", {{"sla_minutes", 60}, {"priority_in_day", 1}}},
        {"appointment", ""}, {"create_date", ""}, {"complete_date", ""},
        {"location", ""}, {"latlng", "21.03,105.81"}, {"handle_minutes", ""},
        {"task_plots_id", 1}, {"staff_plots_id", 1}, {"staff_role", 1}, {"block_id", 1}};
    return json{{"message_id", id}, {"planned_at", "2026-10-01 08:00:00"}, {"trigger", "DAY_START"},
                {"staff", {{"staff_id", "S1"}, {"staff_account", "A"}, {"latlng", "21.02,105.80"},
                           {"available", "08:00-17:30"},
                           {"plots", json::array({{{"id", 1}, {"name", "P"}, {"role", 1}, {"block_id", 1}}})},
                           {"current_task", nullptr}}},
                {"tasks", {{"trien_khai", json::array({task})}, {"bao_tri", json::array()}, {"thu_hoi", json::array()},
                           {"hoa_don", json::array()}, {"onsite", json::array()}}}};
}

struct Running {
    std::unique_ptr<httplib::Server> server;
    std::thread thread;
    httplib::Client client;

    explicit Running(std::unique_ptr<httplib::Server> s) : server(std::move(s)), client("127.0.0.1", 0) {
        const int port = server->bind_to_any_port("127.0.0.1");
        client = httplib::Client("127.0.0.1", port);
        client.set_connection_timeout(2);
        client.set_read_timeout(5);
        thread = std::thread([this] { server->listen_after_bind(); });
        server->wait_until_ready();
    }
    ~Running() {
        server->stop();
        if (thread.joinable()) thread.join();
    }
};

void redis_command(const ktv::RedisStore::Config& config, const std::string& command) {
    redisContext* context = redisConnect(config.host.c_str(), config.port);
    if (context && !context->err) freeReplyObject(redisCommand(context, command.c_str()));
    if (context) redisFree(context);
}

}  // namespace

int main() {
    const char* address = std::getenv("KTV_TEST_REDIS");
    std::optional<ktv::RedisStore::Config> config = address ? ktv::redis_config(address) : std::nullopt;
    if (!config) {
        std::cout << "test_gateway_replan: SKIP (không có KTV_TEST_REDIS=host:port)\n";
        return 0;
    }
    config->prefix = "ktvtest-replan:" + std::to_string(getpid()) + ":";
    std::unique_ptr<ktv::RedisStore> holder;
    try {
        holder = std::make_unique<ktv::RedisStore>(*config);
    } catch (const std::exception& error) {
        std::cout << "test_gateway_replan: SKIP (" << error.what() << ")\n";
        return 0;
    }
    ktv::RedisStore& store = *holder;

    ktv::GatewayOptions options;
    options.token = "secret";
    options.rules = ktv::default_rules();
    options.fixed_now = ktv::parse_datetime("2026-10-01 09:20:00");
    std::vector<std::string> sent;  // OUT đã gửi (value); request tuần tự nên không cần khóa
    options.send_out = [&sent](const std::string& key, const std::string& value) {
        CHECK(key == "S1");
        sent.push_back(value);
    };
    options.out_failed = [] { return 7LL; };
    const httplib::Headers auth = {{"Authorization", "Bearer secret"}};
    const std::string replan = "/api/v1/staff/S1/replan?latlng=";

    try {
        Running running(ktv::make_gateway_server(store, options, &store));
        auto& client = running.client;

        CHECK(client.Get(replan + "21.02,105.79")->status == 401);
        CHECK(client.Get(replan + "21.02,105.79", auth)->status == 404);  // chưa có IN
        CHECK(store.put_state("S1", message("m1").dump(), {100, 1}));

        CHECK(client.Get("/api/v1/staff/S1/replan", auth)->status == 400);           // thiếu latlng
        CHECK(client.Get(replan + "abc", auth)->status == 400);                       // latlng sai
        CHECK(client.Get(replan + "21.02,105.79&latlng_at=09:20", auth)->status == 400);  // giờ sai dạng

        // Lần đầu: tính (MISS). latlng_at bỏ trống = giờ gọi.
        auto first = client.Get(replan + "21.0500,105.8500", auth);
        CHECK(first && first->status == 200 && first->get_header_value("X-Cache") == "MISS");
        CHECK(first && first->get_header_value("Cache-Control") == "no-store");
        const json body = json::parse(first ? first->body : "{}", nullptr, false);
        CHECK(body.value("statuscode", "") == "200");
        CHECK(body.value("message_id", "") == "m1");
        CHECK(body.value("run_code", "") == "m1-r20261001092000");
        CHECK(body.value("trigger", "") == "MOBIX_REPLAN");
        CHECK(body.value("planned_at", "") == "2026-10-01 09:20:00");  // giờ gọi, không phải 08:00 của IN
        CHECK(sent.size() == 1 && first && sent[0] == first->body);    // OA nhận đúng bản Mobix nhận

        // Gọi lại cùng vị trí, rồi GPS rung ~1 m (cùng làm tròn 4 số): HIT, đúng bản đó; GET route cũng ra bản đó.
        auto again = client.Get(replan + "21.0500,105.8500", auth);
        CHECK(again && again->get_header_value("X-Cache") == "HIT" && again->body == first->body);
        // Mobix gọi định kỳ: cùng chỗ (rung ~1 m), latlng_at mới hơn → dedup chặn, không tính lại.
        auto jitter = client.Get(replan + "21.05001,105.85001&latlng_at=2026-10-01 09:21:00", auth);
        CHECK(jitter && jitter->get_header_value("X-Cache") == "HIT" && jitter->body == first->body);
        auto read = client.Get("/api/v1/staff/S1/route", auth);
        CHECK(read && read->status == 200 && read->body == first->body);
        CHECK(sent.size() == 1);  // HIT / đọc route: không gửi thêm OUT

        // Đổi vị trí → MISS, run_code theo latlng_at mới.
        auto moved = client.Get(replan + "21.0600,105.8600&latlng_at=2026-10-01 09:25:00", auth);
        CHECK(moved && moved->get_header_value("X-Cache") == "MISS");
        CHECK(json::parse(moved->body).value("run_code", "") == "m1-r20261001092500");

        // IN mới (worker ghi state mới) → cùng vị trí vẫn MISS, message_id theo IN mới.
        CHECK(store.put_state("S1", message("m2").dump(), {200, 1}));
        auto fresh = client.Get(replan + "21.0600,105.8600&latlng_at=2026-10-01 09:25:00", auth);
        CHECK(fresh && fresh->get_header_value("X-Cache") == "MISS");
        CHECK(json::parse(fresh->body).value("message_id", "") == "m2");
        CHECK(sent.size() == 3);  // moved + fresh

        // Worker vừa ghi route mới hơn (state mới hơn) → route replan bị từ chối, trả bản của worker.
        CHECK(store.put_route("S1", "2026-10-01", R"({"run_code":"worker-moi"})", {300, 1, 0}) == ktv::Write::Stored);
        auto lost = client.Get(replan + "21.0700,105.8700&latlng_at=2026-10-01 09:26:00", auth);
        CHECK(lost && lost->status == 200 && lost->get_header_value("X-Cache") == "HIT");
        CHECK(lost && json::parse(lost->body).value("run_code", "") == "worker-moi");
        CHECK(sent.size() == 3);  // route bị từ chối → không gửi bản cũ

        auto health = client.Get("/healthz");
        CHECK(health && json::parse(health->body).value("out_failed", -1) == 7);

        // Redis cắt kết nối của gateway: request gặp lúc rớt → 503, request sau tự nối lại → 200.
        redis_command(*config, "CLIENT KILL TYPE normal");
        auto during = client.Get(replan + "21.0800,105.8800&latlng_at=2026-10-01 09:27:00", auth);
        CHECK(during && (during->status == 503 || during->status == 200));
        auto after = client.Get(replan + "21.0800,105.8800&latlng_at=2026-10-01 09:27:00", auth);
        CHECK(after && after->status == 200);

        // /readyz: PING Redis; bị cắt kết nối thì tự nối lại → vẫn sẵn sàng.
        redis_command(*config, "CLIENT KILL TYPE normal");
        auto ready = client.Get("/readyz");
        CHECK(ready && ready->status == 200 && json::parse(ready->body).value("ready", false));
    } catch (const std::exception& error) {
        std::cerr << "FAIL: ngoại lệ " << error.what() << "\n";
        ++failures;
    }

    for (const char* kind : {"state:S1", "route:S1:2026-10-01", "latest:S1", "loc:S1", "dedup:S1"})
        redis_command(*config, "DEL " + config->prefix + kind);

    if (failures) std::cerr << failures << " lỗi\n";
    else std::cout << "test_gateway_replan: OK\n";
    return failures != 0;
}
