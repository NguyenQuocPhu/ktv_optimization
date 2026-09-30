// ============================================================================
// adapter/http — TÙY CHỌN CHUNG CHO HTTP SERVER (cpp-httplib)
// ============================================================================
// Hiểu nhanh:
//   httplib mặc định bật SO_REUSEPORT: hai tiến trình cùng cổng đều mở được và chia request ngẫu nhiên
//   (VD hai worker cùng --health-port 8081 → /healthz trả lời của một trong hai). exclusive_port() chỉ
//   giữ SO_REUSEADDR (restart nhanh không bị "cổng đang dùng"), nên trùng cổng là mở cổng lỗi, dễ thấy.
//
// Dùng thế nào:
//   httplib::Server server;
//   exclusive_port(server);          // trước bind/listen
//
// Phụ thuộc: cpp-httplib (header-only). Dùng ở ktv_worker (/healthz) và ktv_gateway.
// ============================================================================
#pragma once

#include <httplib.h>

namespace ktv {

inline void exclusive_port(httplib::Server& server) {
    server.set_socket_options([](socket_t sock) {
        int yes = 1;
        setsockopt(sock, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof yes);
    });
}

}  // namespace ktv
