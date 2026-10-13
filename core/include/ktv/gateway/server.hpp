// ============================================================================
// gateway/server — HTTP cho Mobix: đọc route đã tính + /suggest theo vị trí mới
// ============================================================================
// Hiểu nhanh:
//   - GET /api/v1/staff/{staff_id}/route           bản route mới nhất của KTV (= OUT đầy đủ), không tính
//       → 200 route / 202 {"retry_after":5} chưa có / 401
//   - POST /api/v1/staff/{staff_id}/suggest        (cần Redis; 7.29, POST body JSON từ 2026-10-13)
//       body {"latlng":"21.02,105.79","action_type":0|1|2,"latlng_at":"2026-10-01 09:20:30"?}
//       state IN mới nhất + vị trí Mobix, mode = action_type → tính lại (adapter/publish) → 200 bản rút gọn sheet 09,
//       header X-Cache: MISS. IN không đổi nội dung + vị trí lệch ≤ rules.location_change_m (mặc định 50 m) + bản
//       cache còn hạn (chưa tới ca đầu, ≤ 30′) → 200 bản đang cache, X-Cache: HIT, không gửi OUT (7.20.1b)
//       → 400 body / tham số sai, 404 chưa có IN của KTV, 401, 503 không có Redis hoặc Redis lỗi
//       Route: message_id của IN, run_code "<message_id>-s<latlng_at yyyymmddHHMMSS>", trigger MOBIX_SUGGEST,
//       planned_at = giờ gọi. latlng_at bỏ trống = giờ gọi (kèm giây).
//       Có options.send_out: route vừa ghi thì đẩy Kafka OUT (bản đầy đủ), không chờ; lỗi chỉ log + đếm.
//   - GET /healthz  (process còn sống)             → 200 {"ok":true,"entries":N[,"out_failed":M]}
//   - GET /readyz   (Redis trả lời PING)            → 200 {"ready":true} / 503 {"ready":false}; không Redis → 200
//
// Dùng thế nào:
//   RedisStore store(config);
//   GatewayOptions options; options.port = 8080; options.token = "...";
//   auto server = make_gateway_server(store, options, &store);   // store/options phải sống bằng server
//   server->listen(options.host, options.port);
//
// Trong file này có:
//   GatewayOptions        cổng, token, rules, OSRM, giờ cố định (test)
//   make_gateway_server   HÀM CHÍNH
//
// Ẩn trong server.cpp: kiểm token, envelope /suggest (cache IN không đổi: plan_and_store, 7.20.1b).
// Phụ thuộc: store, redis_store + adapter/publish (khi có hiredis), rules, cpp-httplib (third_party).
// ============================================================================
#pragma once

#include <functional>
#include <memory>
#include <optional>
#include <string>

#include <httplib.h>

#include "ktv/api.hpp"
#include "ktv/gateway/store.hpp"
#include "ktv/rules.hpp"

namespace ktv {

class RedisStore;

struct GatewayOptions {
    std::string host = "0.0.0.0";
    int port = 8080;
    std::string token;                  // rỗng = không kiểm tra token
    Rules rules;                        // rule cho /suggest (main: default_rules() hoặc --rules)
    std::string osrm_url;               // rỗng = chim bay
    std::optional<Minutes> fixed_now;   // test: cố định giờ (--at); không có = giờ VN hiện tại
    // Đẩy route /suggest ra Kafka OUT (key, value), không chờ xác nhận; trống = không đẩy (Phase 7.5).
    std::function<void(const std::string&, const std::string&)> send_out;
    std::function<long long()> out_failed;  // số OUT giao nhận lỗi, hiện ở /healthz; trống = không hiện
};

// Tạo server (chưa listen). redis = nullptr: /suggest trả 503 (gateway đọc store RAM/seed).
// Caller giữ `store`, `options`, `redis` sống trong suốt thời gian server chạy.
std::unique_ptr<httplib::Server> make_gateway_server(RouteStore& store, const GatewayOptions& options,
                                                     RedisStore* redis = nullptr);

}  // namespace ktv
