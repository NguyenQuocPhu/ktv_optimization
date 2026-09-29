// ============================================================================
// gateway/server — HTTP chỉ đọc: trả route mới nhất cho Mobix và healthz
// ============================================================================
// Hiểu nhanh:
//   Bọc `RouteStore` bằng vài route HTTP. KHÔNG tính lại: chỉ tra store.
//   - GET /api/v1/worklist/{staff_id}?date=  → 200 JSON / 202 retry_after / 401
//   - GET /healthz                            → 200 {"ok":true,"entries":N}
//
// Dùng thế nào:
//   MemoryRouteStore store;
//   GatewayOptions options; options.port = 8080; options.token = "...";
//   auto server = make_gateway_server(store, options);   // store/options phải sống bằng server
//   server->listen(options.host, options.port);
//
// Phụ thuộc: store, cpp-httplib (third_party).
// ============================================================================
#pragma once

#include <memory>
#include <string>

#include <httplib.h>

#include "ktv/gateway/store.hpp"

namespace ktv {

struct GatewayOptions {
    std::string host = "0.0.0.0";
    int port = 8080;
    std::string token;  // rỗng = không kiểm tra token
};

// Tạo server (chưa listen). Caller giữ `store` và `options` sống trong suốt thời gian server chạy.
std::unique_ptr<httplib::Server> make_gateway_server(RouteStore& store, const GatewayOptions& options);

}  // namespace ktv
