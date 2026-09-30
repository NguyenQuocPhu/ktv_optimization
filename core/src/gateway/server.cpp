#include "ktv/gateway/server.hpp"

#include <optional>

#include <nlohmann/json.hpp>

namespace ktv {

namespace {

bool authorized(const httplib::Request& request, const std::string& token) {
    if (token.empty()) return true;
    const std::string value = request.get_header_value("Authorization");
    return value == "Bearer " + token || value == token;
}

void json(httplib::Response& response, int status, const nlohmann::json& body) {
    response.status = status;
    response.set_content(body.dump(), "application/json");
}

}  // namespace

std::unique_ptr<httplib::Server> make_gateway_server(RouteStore& store, const GatewayOptions& options) {
    auto server = std::make_unique<httplib::Server>();
    // httplib mặc định bật SO_REUSEPORT: hai gateway cùng cổng đều listen được và chia request ngẫu nhiên.
    // Chỉ giữ SO_REUSEADDR để trùng cổng là listen lỗi.
    server->set_socket_options([](socket_t sock) {
        int yes = 1;
        setsockopt(sock, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof yes);
    });

    server->Get("/healthz", [&store](const httplib::Request&, httplib::Response& response) {
        json(response, 200, {{"ok", true}, {"entries", store.size()}});
    });

    server->Get(R"(/api/v1/worklist/([^/]+))",
                [&store, &options](const httplib::Request& request, httplib::Response& response) {
                    if (!authorized(request, options.token)) {
                        json(response, 401, {{"success", false}, {"statuscode", "401"}, {"message", "unauthorized"}});
                        return;
                    }
                    const std::string staff_id = request.matches[1];
                    const std::optional<std::string> route =
                        request.has_param("date") ? store.get(staff_id, request.get_param_value("date"))
                                                  : store.get_latest(staff_id);
                    if (!route) {  // chưa có bản nào: Mobix thử lại sau
                        json(response, 202, {{"retry_after", 5}});
                        return;
                    }
                    response.status = 200;
                    response.set_content(*route, "application/json");
                });

    return server;
}

}  // namespace ktv
