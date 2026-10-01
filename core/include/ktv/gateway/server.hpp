// ============================================================================
// gateway/server — HTTP cho Mobix: đọc route đã tính + replan theo vị trí mới
// ============================================================================
// Hiểu nhanh:
//   - GET /api/v1/staff/{staff_id}/route?date=     chỉ đọc store, không tính
//       → 200 route (= OUT) / 202 {"retry_after":5} chưa có / 401
//   - GET /api/v1/staff/{staff_id}/replan?latlng=21.02,105.79&latlng_at=2026-10-01 09:20:00   (cần Redis)
//       state IN mới nhất + vị trí Mobix → tính lại (adapter/publish) → 200 route, header X-Cache: MISS
//       cùng state + cùng vị trí (làm tròn 4 số ≈ 11 m) như lần trước → 200 route đang cache, X-Cache: HIT
//       → 400 sai tham số / 404 chưa có IN của KTV / 401 / 503 không có Redis hoặc Redis lỗi
//       Route replan: message_id của IN, run_code "<message_id>-r<latlng_at yyyymmddHHMMSS>",
//       trigger MOBIX_REPLAN, planned_at = giờ gọi. latlng_at bỏ trống = giờ gọi.
//       Có options.send_out: route vừa ghi thì đẩy Kafka OUT, không chờ; lỗi chỉ log + đếm (Mobix vẫn nhận route).
//   - GET /healthz                                  → 200 {"ok":true,"entries":N[,"out_failed":M]}
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
// Ẩn trong server.cpp: kiểm token, fingerprint dedup, envelope replan.
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
    Rules rules;                        // rule cho replan (main: default_rules() hoặc --rules)
    std::string osrm_url;               // rỗng = chim bay
    std::optional<Minutes> fixed_now;   // test: cố định giờ (--at); không có = giờ VN hiện tại
    // Đẩy route replan ra Kafka OUT (key, value), không chờ xác nhận; trống = không đẩy (Phase 7.5).
    std::function<void(const std::string&, const std::string&)> send_out;
    std::function<long long()> out_failed;  // số OUT giao nhận lỗi, hiện ở /healthz; trống = không hiện
};

// Tạo server (chưa listen). redis = nullptr: replan trả 503 (gateway đọc store RAM/seed).
// Caller giữ `store`, `options`, `redis` sống trong suốt thời gian server chạy.
std::unique_ptr<httplib::Server> make_gateway_server(RouteStore& store, const GatewayOptions& options,
                                                     RedisStore* redis = nullptr);

}  // namespace ktv
