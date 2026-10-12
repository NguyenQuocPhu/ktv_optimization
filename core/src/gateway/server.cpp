#include "ktv/gateway/server.hpp"

#include "ktv/adapter/http.hpp"
#include "ktv/adapter/log.hpp"
#include "ktv/adapter/metrics.hpp"

#include <chrono>
#include <cmath>
#include <cstdio>
#include <ctime>
#include <optional>

#include <nlohmann/json.hpp>

#ifdef KTV_WITH_REDIS
#include "ktv/adapter/file.hpp"
#include "ktv/adapter/publish.hpp"
#include "ktv/gateway/redis_store.hpp"
#endif

namespace ktv {

namespace {

// 8.3: số liệu cho GET /metrics (cả tiến trình gateway).
Metrics g_metrics;

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
    const double seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - t_request_started).count();
    line["total_ms"] = std::round(seconds * 10000) / 10;
    write_log(line);
    // 8.3: nhãn route chỉ nhận tên đã biết (path lạ → "other") để số chuỗi số liệu không phình theo request rác.
    std::string route = line["route"].get<std::string>();
    if (route != "route" && route != "replan" && route != "suggest") route = "other";
    g_metrics.add("ktv_http_requests_total", "route=\"" + route + "\",code=\"" + std::to_string(response.status) + "\"");
    g_metrics.observe("ktv_http_seconds", "route=\"" + route + "\"", seconds);
    if (response.has_header("X-Cache"))
        g_metrics.add("ktv_http_cache_total", "route=\"" + route + "\",result=\"" + response.get_header_value("X-Cache") + "\"");
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
// /replan (Phase 7.4) và /suggest (7.29) dùng chung: lấy IN mới nhất trong cache + vị trí Mobix → plan_and_store (cache
// hoặc tính lại, ghi route, gửi Kafka OUT khi tính lại). suggest: action_type (sheet 09) làm mode cho LẦN TÍNH NÀY
// (ghi đè priority_type của IN đã lưu; state không đổi) và trả bản rút gọn sheet 09 thay cho OUT đầy đủ.
void replan(RedisStore& redis, const GatewayOptions& options, const httplib::Request& request,
            httplib::Response& response, bool suggest = false) {
    const std::string staff_id = request.matches[1];
    std::optional<int> action_type;
    if (suggest) {
        const std::string text = request.has_param("action_type") ? request.get_param_value("action_type") : "";
        if (text != "0" && text != "1" && text != "2")
            return error(response, 400, "action_type cần 0 (mặc định: 70% SLA + 30% tuyến), 1 (SLA) hoặc 2 (tuyến)");
        action_type = text[0] - '0';
    }
    const std::optional<Point> point =
        request.has_param("latlng") ? parse_latlng(request.get_param_value("latlng")) : std::nullopt;
    if (!point) return error(response, 400, "latlng cần dạng \"lat,lng\" trong Việt Nam, VD 21.02,105.79");
    const Minutes now = options.fixed_now.value_or(vietnam_now());
    const std::optional<Minutes> latlng_at =
        request.has_param("latlng_at") ? parse_datetime(request.get_param_value("latlng_at")) : now;
    if (!latlng_at) return error(response, 400, "latlng_at cần dạng \"YYYY-MM-DD HH:mm:ss\"");
    // Minutes bỏ giây; version vị trí + run_code giữ giây để hai vị trí trong cùng phút vẫn phân biệt (mới hơn thắng).
    std::string at_text = request.has_param("latlng_at") ? request.get_param_value("latlng_at") : format_datetime(*latlng_at);
    if (!request.has_param("latlng_at") && !options.fixed_now) {
        char second[3];
        std::snprintf(second, sizeof second, "%02d", static_cast<int>(std::time(nullptr) % 60));
        at_text.replace(17, 2, second);
    }
    const std::int64_t loc_stamp = stamp(*latlng_at) + std::stoi(at_text.substr(17, 2));

    const std::optional<Versioned> state = redis.get_state(staff_id);
    if (!state) return error(response, 404, "chưa có message IN của KTV " + staff_id);

    // Ghi vị trí trước: plan_and_store đọc lại từ Redis (luật 60 phút nằm ở đó). Vị trí cũ hơn bản đang có → giữ bản kia.
    const nlohmann::json loc = {{"latlng", request.get_param_value("latlng")}, {"latlng_at", at_text}};
    redis.put_loc(staff_id, loc.dump(), {loc_stamp});

    json in = json::parse(state->json, nullptr, false);
    if (action_type && in.is_object()) in["priority_type"] = *action_type;
    Envelope envelope = local_envelope(in, staff_id, now);
    envelope.trigger = suggest ? "MOBIX_SUGGEST" : "MOBIX_REPLAN";
    envelope.planned_at = now;
    envelope.run_code = envelope.message_id + (suggest ? "-s" : "-r") + std::to_string(loc_stamp);
    // 7.20.1b: cùng luật cache với worker (dấu vân tay nội dung + vị trí lệch ≤ location_change_m + hạn tuổi). Trả cache →
    // HIT, change_id "no", không gửi OUT (Mobix hỏi lại không phải thay đổi để báo OA).
    const Published published = plan_and_store(in, envelope, now, state->version, options.rules, options.osrm_url,
                                               &redis, options.send_out, /*reply_on_cache=*/false);
    const auto text = [suggest](const nlohmann::ordered_json& out) {
        return (suggest ? mobix_response(out) : out).dump(-1, ' ', false, nlohmann::json::error_handler_t::replace);
    };
    if (published.cached) return route(response, text(*published.out), "HIT");
    if (published.route_stored) return route(response, text(*published.out), "MISS");
    // Không ghi được: đã có route mới hơn (worker vừa tính từ IN mới) hoặc lỗi tính → trả bản mới nhất đang có.
    if (const std::optional<std::string> cached = redis.get_latest(staff_id)) {
        if (!suggest) return route(response, *cached, "HIT");
        return route(response, text(nlohmann::ordered_json::parse(*cached, nullptr, false)), "HIT");
    }
    if (published.out) return route(response, text(*published.out), "MISS");
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

    server->Get("/metrics", [](const httplib::Request&, httplib::Response& response) {  // 8.3: Prometheus, không cần token
        response.set_content(g_metrics.render(), "text/plain; version=0.0.4");
    });
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

    // 7.29: API cho Mobix theo sheet 09 — như replan + action_type, trả bản rút gọn. /route, /replan giữ nguyên.
    server->Get(R"(/api/v1/staff/([^/]+)/suggest)",
                [&options, redis](const httplib::Request& request, httplib::Response& response) {
                    if (!authorized(request, options.token)) return error(response, 401, "unauthorized");
#ifdef KTV_WITH_REDIS
                    if (redis) {
                        try {
                            return replan(*redis, options, request, response, /*suggest=*/true);
                        } catch (const std::exception& failure) {
                            return send_json(response, 503, {{"retry_after", 5}, {"message", failure.what()}});
                        }
                    }
#endif
                    (void)redis;
                    send_json(response, 503, {{"retry_after", 5}, {"message", "suggest cần Redis (--redis)"}});
                });

    return server;
}

}  // namespace ktv
