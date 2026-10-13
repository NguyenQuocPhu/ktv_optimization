// HTTP gateway: /staff/{id}/route 200/202/401, healthz, có/không token, /replan ẩn → 404, /suggest không Redis → 503. Chạy trên cổng tạm.
#include <iostream>
#include <string>
#include <thread>

#include <httplib.h>
#include <nlohmann/json.hpp>

#include "ktv/gateway/server.hpp"

static int failures = 0;
#define CHECK(cond)                                                           \
    do {                                                                      \
        if (!(cond)) {                                                        \
            std::cerr << __FILE__ << ":" << __LINE__ << ": FAIL " #cond "\n"; \
            ++failures;                                                       \
        }                                                                     \
    } while (0)

namespace {

// Chạy server trên cổng tạm; trả về client đã nối. Caller phải Stop() sau khi xong.
struct Running {
    std::unique_ptr<httplib::Server> server;
    std::thread thread;
    httplib::Client client;

    explicit Running(std::unique_ptr<httplib::Server> s) : server(std::move(s)), client("127.0.0.1", 0) {
        const int port = server->bind_to_any_port("127.0.0.1");
        client = httplib::Client("127.0.0.1", port);
        client.set_connection_timeout(2);
        client.set_read_timeout(2);
        thread = std::thread([this] { server->listen_after_bind(); });
    }
    ~Running() { Stop(); }
    void Stop() {
        server->stop();
        if (thread.joinable()) thread.join();
    }
};

}  // namespace

int main() {
    const std::string route_a = R"({"success":true,"data":{"staff_id":"A"}})";
    const std::string route_a_old = R"({"success":true,"data":{"staff_id":"A"},"v":1})";
    ktv::MemoryRouteStore store;
    store.put("A", "2026-09-10", route_a_old);
    store.put("A", "2026-09-11", route_a);

    {  // không token
        ktv::GatewayOptions options;
        Running running(ktv::make_gateway_server(store, options));

        auto health = running.client.Get("/healthz");
        CHECK(health && health->status == 200);
        if (health) CHECK(nlohmann::json::parse(health->body)["entries"] == 2);

        auto latest = running.client.Get("/api/v1/staff/A/route");
        CHECK(latest && latest->status == 200 && latest->body == route_a);

        // 2026-10-13: /route chỉ trả bản mới nhất; ?date= bị bỏ qua.
        auto by_date = running.client.Get("/api/v1/staff/A/route?date=2026-09-10");
        CHECK(by_date && by_date->status == 200 && by_date->body == route_a);

        auto missing = running.client.Get("/api/v1/staff/Z/route");
        CHECK(missing && missing->status == 202);
        if (missing) CHECK(nlohmann::json::parse(missing->body)["retry_after"] == 5);

        auto old_path = running.client.Get("/api/v1/worklist/A");  // đường cũ đã bỏ (Phase 7.4)
        CHECK(old_path && old_path->status == 404);

        auto replan = running.client.Get("/api/v1/staff/A/replan?latlng=21.02,105.79");  // tạm ẩn (2026-10-13)
        CHECK(replan && replan->status == 404);
        auto suggest = running.client.Post("/api/v1/staff/A/suggest", R"({"latlng":"21.02,105.79","action_type":0})",
                                           "application/json");  // không Redis
        CHECK(suggest && suggest->status == 503);

        auto ready = running.client.Get("/readyz");  // không Redis: store RAM luôn sẵn sàng
        CHECK(ready && ready->status == 200);
    }

    {  // có token
        ktv::GatewayOptions options;
        options.token = "secret";
        Running running(ktv::make_gateway_server(store, options));

        auto open = running.client.Get("/api/v1/staff/A/route");
        CHECK(open && open->status == 401);

        auto wrong = running.client.Get("/api/v1/staff/A/route", {{"Authorization", "Bearer sai"}});
        CHECK(wrong && wrong->status == 401);

        auto ok = running.client.Get("/api/v1/staff/A/route", {{"Authorization", "Bearer secret"}});
        CHECK(ok && ok->status == 200 && ok->body == route_a);

        auto health = running.client.Get("/healthz");  // healthz không cần token
        // 8.3: /metrics không cần token; đếm request /api/ theo route + mã trả về, đo thời gian.
        auto metrics = running.client.Get("/metrics");
        CHECK(metrics && metrics->status == 200 && metrics->get_header_value("Content-Type").rfind("text/plain", 0) == 0);
        CHECK(metrics && metrics->body.find("ktv_http_requests_total{route=\"route\",code=\"") != std::string::npos);
        CHECK(metrics && metrics->body.find("ktv_http_seconds_count{route=\"route\"}") != std::string::npos);
        CHECK(health && health->status == 200);
    }

    if (failures) std::cerr << failures << " lỗi\n";
    else std::cout << "test_gateway_server: OK\n";
    return failures != 0;
}
