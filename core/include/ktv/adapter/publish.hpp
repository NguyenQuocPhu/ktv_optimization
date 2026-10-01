// ============================================================================
// adapter/publish — TÍNH TUYẾN + GHI REDIS cho một message IN (dùng chung worker T1, gateway T2)
// ============================================================================
// Hiểu nhanh:
//   Vào: message IN (JSON) + envelope do bên gọi dựng + version của IN. Ra: response đã gói (= OUT), đồng thời
//   state và route nằm trong Redis để Mobix đọc. Worker: envelope lấy từ IN (local_envelope). Gateway replan:
//   cùng message_id, run_code riêng, trigger MOBIX_REPLAN, planned_at = giờ gọi.
//     1. parse nới lỏng; 400 (staff hỏng, không biết KTV nào) → trả luôn, không đụng Redis
//     2. put_state; IN cũ hơn cái đang có → bỏ qua, không tính (status "STALE")
//        bằng version = Kafka giao lại cùng IN (lần trước chết giữa chừng) → vẫn tính, ghi route lại
//     3. vị trí Mobix trong Redis còn trong 60 phút → thay vị trí trong IN
//     4. plan(); lỗi bất ngờ → 500 (không ghi route, giữ route cũ)
//     5. put_route với based_on = version state + latlng_at (0 = vị trí trong IN)
//     6. gửi OUT (send_out) khi route này là bản hiện hành: vừa ghi, hoặc đúng bản này đã có (Kafka giao lại
//        cùng IN sau khi lần trước ghi route xong nhưng chưa gửi được OUT). Không gửi khi 400/500/STALE hoặc đã có
//        route mới hơn. Không Redis: gửi mọi 200/424/422.
//   VD: IN 08:05 của KTV 00201964 đến, Mobix báo vị trí lúc 08:40, worker xử lý lúc 09:00
//       → tuyến xuất phát từ vị trí 08:40, route ghi với based_on {ts IN, offset, 20261001084000}.
//   Lỗi Redis → ném ra ngoài (worker thoát, không commit, đọc lại message sau khi restart).
//
// Dùng thế nào:
//   json in = json::parse(record.payload, nullptr, false);
//   Published r = plan_and_store(in, local_envelope(in, "topic-0-123", now), now,
//                                {record.timestamp_ms, record.offset}, rules, osrm_url, &store,   // nullptr: không Redis
//                                [&](auto& key, auto& value) { producer.send(key, value); });  // bỏ trống: không OUT
//   if (r.out) write(r.out->dump());                            // không có = IN cũ, đã bỏ qua
//
// Trong file này có:
//   Published        kết quả một lần tính
//   plan_and_store   HÀM CHÍNH
//   stamp            Minutes → số yyyymmddHHMMSS (dùng trong version)
//
// Phụ thuộc: adapter/file (envelope, parse_record), plan, gateway/redis_store (chỉ build khi có hiredis).
// ============================================================================
#pragma once

#include <functional>
#include <optional>
#include <string>
#include <vector>

#include "ktv/adapter/envelope.hpp"
#include "ktv/api.hpp"
#include "ktv/gateway/redis_store.hpp"
#include "ktv/rules.hpp"

namespace ktv {

// Vị trí Mobix cũ hơn chừng này (so với giờ tính) thì không dùng, quay về vị trí trong IN. [GIẢ ĐỊNH]
constexpr Minutes kLocMaxAgeMinutes = 60;

struct Published {
    std::optional<nlohmann::ordered_json> out;  // Response đã gói (= OUT). Không có = IN cũ, đã bỏ qua.
    std::string status;                         // "200" / "424" / "422" / "400" / "500", hoặc "STALE".
    std::string message_id;                     // VD "m-123" hoặc fallback "topic-0-123".
    std::vector<Error> warnings;                // Cảnh báo parse nới lỏng (log + /healthz, không vào OUT).
    bool route_stored = false;                  // false khi không Redis, 400/500, hoặc đã có route mới hơn.
    bool used_mobix_loc = false;                // true = đã thay vị trí IN bằng vị trí Mobix.
    bool out_sent = false;                      // true = đã gọi send_out (key = staff_id, value = route).
};

// HÀM CHÍNH. in: message IN (JSON hỏng = discarded → 400). version: version của state, VD {timestamp Kafka ms,
// offset}. now: giờ VN lúc tính. State lưu lại = in.dump().
// Gửi route ra Kafka OUT: key = staff_id, value = route đã gói (đúng chuỗi ghi trong Redis).
using SendOut = std::function<void(const std::string& key, const std::string& value)>;

Published plan_and_store(const json& in, const Envelope& envelope, Minutes now, const Version& version,
                         const Rules& rules, const std::string& osrm_url, RedisStore* store,
                         const SendOut& send_out = {});

// 2026-10-01 09:20 → 20261001092000.
std::int64_t stamp(Minutes value);

}  // namespace ktv
