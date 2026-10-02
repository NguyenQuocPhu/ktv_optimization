#include "ktv/gateway/server.hpp"

#include "ktv/adapter/http.hpp"
#include "ktv/adapter/log.hpp"

#include <chrono>
#include <cmath>
#include <cstdio>
#include <optional>

#include <nlohmann/json.hpp>

#ifdef KTV_WITH_REDIS
#include "ktv/adapter/file.hpp"
#include "ktv/adapter/publish.hpp"
#include "ktv/gateway/redis_store.hpp"
#endif

namespace ktv {

namespace {

// Giờ bắt đầu request: pre_routing và logger của httplib chạy cùng một luồng cho cùng một request.
thread_local std::chrono::steady_clock::time_point t_request_started;

// Một dòng JSON / request /api/ (không log /healthz, /readyz; không log query — latlng là vị trí KTV).
// VD {"event":"http","route":"replan","staff_id":"00201964","http_status":200,"cache":"MISS","statuscode":"200",...}
void log_request(const httplib::Request& request, const httplib::Response& response) {
    if (request.path.rfind("/api/", 0) != 0) return;
    nlohmann::ordered_json line = log_event("http");
    line["method"] = request.method;
    const size_t staff = request.path.find("/staff/");  // /api/v1/staff/{staff_id}/{route|replan}
    const size_t slash = staff == std::string::npos ? staff : request.path.find('/', staff + 7);
    line["route"] = slash == std::string::npos ? request.path : request.path.substr(slash + 1);
    line["staff_id"] = slash == std::string::npos ? nlohmann::ordered_json(nullptr)
                                                  : nlohmann::ordered_json(request.path.substr(staff + 7, slash - staff - 7));
    line["http_status"] = response.status;
    line["cache"] = response.has_header("X-Cache") ? nlohmann::ordered_json(response.get_header_value("X-Cache"))
                                                   : nlohmann::ordered_json(nullptr);
    const nlohmann::json body = nlohmann::json::parse(response.body, nullptr, false);  // route hoặc lỗi
    line["statuscode"] = body.is_object() && body.contains("statuscode") ? body["statuscode"] : nlohmann::json(nullptr);
    line["message"] = body.is_object() && body.contains("message") ? body["message"] : nlohmann::json(nullptr);
    line["total_ms"] =
        std::round(std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t_request_started).count() * 10) / 10;
    write_log(line);
}

bool authorized(const httplib::Request& request, const std::string& token) {
    if (token.empty()) return true;
    const std::string value = request.get_header_value("Authorization");
    return value == "Bearer " + token || value == token;
}

void send_json(httplib::Response& response, int status, const nlohmann::json& body) {
    response.status = status;
    response.set_content(body.dump(), "application/json");
}

void error(httplib::Response& response, int status, const std::string& message) {
    send_json(response, status, {{"success", false}, {"statuscode", std::to_string(status)}, {"message", message}});
}

void route(httplib::Response& response, const std::string& body, const char* cache) {
    response.status = 200;
    response.set_header("Cache-Control", "no-store");
    if (cache) response.set_header("X-Cache", cache);
    response.set_content(body, "application/json");
}

#ifdef KTV_WITH_REDIS
// Lần tính trước dùng đúng state + vị trí này chưa? Vị trí làm tròn 4 chữ số (~11 m) để GPS rung không tính lại.
// VD "1790843483054 0|21.0285,105.8542".
std::string fingerprint(const Version& state_version, const Point& point) {
    std::string out;
    for (std::int64_t number : state_version) out += (out.empty() ? "" : " ") + std::to_string(number);
    char text[64];
    std::snprintf(text, sizeof text, "|%.4f,%.4f", point.lat, point.lng);
    return out + text;
}

void replan(RedisStore& redis, const GatewayOptions& options, const httplib::Request& request,
            httplib::Response& response) {
    const std::string staff_id = request.matches[1];
    const std::optional<Point> point =
        request.has_param("latlng") ? parse_latlng(request.get_param_value("latlng")) : std::nullopt;
    if (!point) return error(response, 400, "latlng cần dạng \"lat,lng\" trong Việt Nam, VD 21.02,105.79");
    const Minutes now = options.fixed_now.value_or(vietnam_now());
    const std::optional<Minutes> latlng_at =
        request.has_param("latlng_at") ? parse_datetime(request.get_param_value("latlng_at")) : now;
    if (!latlng_at) return error(response, 400, "latlng_at cần dạng \"YYYY-MM-DD HH:mm:ss\"");

    const std::optional<Versioned> state = redis.get_state(staff_id);
    if (!state) return error(response, 404, "chưa có message IN của KTV " + staff_id);

    // Ghi vị trí trước: plan_and_store đọc lại từ Redis (luật 60 phút nằm ở đó). Vị trí cũ hơn bản đang có → giữ bản kia.
    const nlohmann::json loc = {{"latlng", request.get_param_value("latlng")}, {"latlng_at", format_datetime(*latlng_at)}};
    redis.put_loc(staff_id, loc.dump(), {stamp(*latlng_at)});

    const std::string key = fingerprint(state->version, *point);
    if (redis.get_dedup(staff_id) == key) {
        if (const std::optional<std::string> cached = redis.get_latest(staff_id)) return route(response, *cached, "HIT");
    }

    const json in = json::parse(state->json, nullptr, false);
    Envelope envelope = local_envelope(in, staff_id, now);
    envelope.trigger = "MOBIX_REPLAN";
    envelope.planned_at = now;
    envelope.run_code = envelope.message_id + "-r" + std::to_string(stamp(*latlng_at));
    const Published published =
        plan_and_store(in, envelope, now, state->version, options.rules, options.osrm_url, &redis, options.send_out);
    if (published.route_stored) {
        redis.put_dedup(staff_id, key);
        return route(response, published.out->dump(-1, ' ', false, nlohmann::json::error_handler_t::replace), "MISS");
    }
    // Không ghi được: đã có route mới hơn (worker vừa tính từ IN mới) hoặc lỗi tính → trả bản mới nhất đang có.
    if (const std::optional<std::string> cached = redis.get_latest(staff_id)) return route(response, *cached, "HIT");
    if (published.out)
        return route(response, published.out->dump(-1, ' ', false, nlohmann::json::error_handler_t::replace), "MISS");
    send_json(response, 202, {{"retry_after", 5}});
}
#endif

}  // namespace

std::unique_ptr<httplib::Server> make_gateway_server(RouteStore& store, const GatewayOptions& options,
                                                     RedisStore* redis) {
    auto server = std::make_unique<httplib::Server>();
    exclusive_port(*server);  // trùng cổng là listen lỗi, không chia request ngẫu nhiên với tiến trình khác
    server->set_pre_routing_handler([](const httplib::Request&, httplib::Response&) {
        t_request_started = std::chrono::steady_clock::now();
        return httplib::Server::HandlerResponse::Unhandled;  // chỉ bấm giờ, để route xử lý như thường
    });
    server->set_logger(log_request);

    server->Get("/healthz", [&store, &options](const httplib::Request&, httplib::Response& response) {
        nlohmann::json body = {{"ok", true}, {"entries", store.size()}};
        if (options.out_failed) body["out_failed"] = options.out_failed();
        send_json(response, 200, body);
    });

    // Sẵn sàng phục vụ = Redis trả lời (Redis chết thì restart gateway không giúp gì, nên tách khỏi /healthz).
    server->Get("/readyz", [redis](const httplib::Request&, httplib::Response& response) {
#ifdef KTV_WITH_REDIS
        if (redis) {
            const bool ready = redis->ping();
            return send_json(response, ready ? 200 : 503, {{"ready", ready}, {"redis", ready}});
        }
#endif
        (void)redis;
        send_json(response, 200, {{"ready", true}, {"redis", nullptr}});  // không Redis: store RAM luôn sẵn sàng
    });

    server->Get(R"(/api/v1/staff/([^/]+)/route)",
                [&store, &options](const httplib::Request& request, httplib::Response& response) {
                    if (!authorized(request, options.token)) return error(response, 401, "unauthorized");
                    const std::string staff_id = request.matches[1];
                    const std::optional<std::string> found =
                        request.has_param("date") ? store.get(staff_id, request.get_param_value("date"))
                                                  : store.get_latest(staff_id);
                    if (!found) return send_json(response, 202, {{"retry_after", 5}});  // chưa có bản nào: thử lại sau
                    route(response, *found, nullptr);
                });

    server->Get(R"(/api/v1/staff/([^/]+)/replan)",
                [&options, redis](const httplib::Request& request, httplib::Response& response) {
                    if (!authorized(request, options.token)) return error(response, 401, "unauthorized");
#ifdef KTV_WITH_REDIS
                    if (redis) {
                        try {
                            return replan(*redis, options, request, response);
                        } catch (const std::exception& failure) {
                            // Redis lỗi: gateway vẫn sống, Mobix thử lại (không có offset nào cần giữ như worker).
                            return send_json(response, 503, {{"retry_after", 5}, {"message", failure.what()}});
                        }
                    }
#endif
                    (void)redis;
                    send_json(response, 503, {{"retry_after", 5}, {"message", "replan cần Redis (--redis)"}});
                });

    return server;
}

}  // namespace ktv
