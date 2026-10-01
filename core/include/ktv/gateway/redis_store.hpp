// ============================================================================
// gateway/redis_store — Redis chung của worker + gateway: state IN, route, vị trí Mobix, dedup
// ============================================================================
// Hiểu nhanh:
//   Vào: chuỗi JSON (message IN, route đã gói, vị trí Mobix) + version. Ra: đọc lại đúng chuỗi đó.
//   Nhiều replica worker/gateway cùng ghi một KTV → ghi state/route/vị trí là "chỉ ghi khi MỚI HƠN"
//   (một Lua script, không khóa). VD worker vừa ghi route từ IN 08:05, gateway tính chậm trên IN 08:00
//   ghi sau → bị từ chối, route 08:05 giữ nguyên.
//
//   Khóa ({prefix} mặc định "ktv:"):
//     {prefix}state:{staff}          hash {json = message IN mới nhất, v = version}      TTL 2 ngày
//     {prefix}route:{staff}:{date}   hash {json = response đã gói (= OUT), v = based_on} TTL 2 ngày
//     {prefix}latest:{staff}         ngày của route mới nhất, VD "2026-10-01"            TTL 2 ngày
//     {prefix}loc:{staff}            hash {json = {"latlng","latlng_at"}, v}             TTL 1 ngày
//     {prefix}dedup:{staff}          fingerprint lần tính cuối (chuỗi)                   TTL 1 ngày
//
//   Version = dãy số nguyên, so từ trái sang (thiếu coi là 0). Chỉ ghi khi version MỚI HƠN HẲN;
//   bằng nhau → không ghi (tính lại cùng state cho cùng kết quả). Mỗi số phải < 2^53 (Lua dùng double).
//   VD state {20261001080500, 123} = planned_at 2026-10-01 08:05:00, offset 123;
//      route based_on {20261001080500, 123, 20261001092000} = state đó + latlng_at 09:20:00.
//
// Dùng thế nào:
//   RedisStore store(RedisStore::Config{});                    // ném nếu không kết nối/auth được
//   store.put_state("00201964", in_json, {20261001080500, 123});  // false = có state mới hơn rồi
//   auto state = store.get_state("00201964");                     // state->json, state->version
//   store.put_route("00201964", "2026-10-01", out_json, {20261001080500, 123, 0});
//
//   Chỉ build khi tìm thấy hiredis (xem CMake, define KTV_WITH_REDIS).
//
// Trong file này có:
//   Version            dãy số so thứ tự (type alias)
//   Versioned          {json, version} đọc ra từ state/vị trí
//   Write              kết quả put_route: Stored / Same / Older
//   RedisStore         HÀM CHÍNH: put_state/get_state, put_route (+ RouteStore put/get/get_latest/size),
//                      put_loc/get_loc, put_dedup/get_dedup
//   redis_config       "HOST:PORT" → Config (CLI worker + gateway)
//
// Ẩn trong redis_store.cpp: Lua script "ghi khi mới hơn", RAII cho reply hiredis.
// Phụ thuộc: hiredis.
// ============================================================================
#pragma once

#include <cstdint>
#include <mutex>
#include <optional>
#include <string>
#include <vector>

#include "ktv/gateway/store.hpp"

struct redisContext;  // forward declare, không lộ hiredis ra header

namespace ktv {

// So từ trái sang, thiếu coi là 0. VD {20261001080500, 123} < {20261001080500, 124} < {20261001080501}.
using Version = std::vector<std::int64_t>;

// Kết quả ghi route: Stored = đã ghi; Same = đúng bản này đã có (VD Kafka giao lại cùng IN); Older = có bản mới hơn.
enum class Write { Stored, Same, Older };

// Một giá trị đọc ra kèm version đã ghi. VD json = message IN, version = {20261001080500, 123}.
struct Versioned {
    std::string json;
    Version version;
};

class RedisStore : public RouteStore {
public:
    struct Config {
        std::string host = "127.0.0.1";
        int port = 6379;
        std::string password;
        int db = 0;
        std::string prefix = "ktv:";              // namespace để không đụng key khác trong Redis dùng chung
        int route_ttl_seconds = 2 * 24 * 3600;    // route + latest: 2 ngày
        int state_ttl_seconds = 2 * 24 * 3600;    // state IN: 2 ngày (hết ngày là vô nghĩa)
        int loc_ttl_seconds = 24 * 3600;          // vị trí Mobix: 1 ngày
        int dedup_ttl_seconds = 24 * 3600;        // fingerprint: 1 ngày
    };

    // Ném std::runtime_error nếu không kết nối / AUTH / SELECT được. Kết nối rớt sau đó: tự nối lại ở lệnh kế
    // tiếp; lệnh gặp lúc rớt thì ném (đọc/ghi đều ném khi lỗi kết nối, không coi là "không có").
    explicit RedisStore(const Config& config);
    ~RedisStore() override;

    RedisStore(const RedisStore&) = delete;
    RedisStore& operator=(const RedisStore&) = delete;

    // State IN của KTV. true = đã ghi; false = đang có state version >= (không ghi). Lỗi Redis → ném.
    bool put_state(const std::string& staff_id, const std::string& json, const Version& version);
    std::optional<Versioned> get_state(const std::string& staff_id) const;

    // Route có based_on: chỉ ghi khi based_on mới hơn; ghi xong cập nhật latest nếu date >= latest.
    Write put_route(const std::string& staff_id, const std::string& date, const std::string& json,
                   const Version& based_on);

    // RouteStore (seed file OUT, GET của gateway). put() không version: luôn ghi đè (đồ nghề dev).
    void put(const std::string& staff_id, const std::string& date, std::string json) override;
    std::optional<std::string> get(const std::string& staff_id, const std::string& date) const override;
    std::optional<std::string> get_latest(const std::string& staff_id) const override;
    std::size_t size() const override;

    // Vị trí Mobix gửi gần nhất. version thường = {latlng_at dạng yyyymmddHHMMSS}.
    bool put_loc(const std::string& staff_id, const std::string& json, const Version& version);
    std::optional<Versioned> get_loc(const std::string& staff_id) const;

    // Fingerprint lần tính cuối (VD "20261001080500,123|21.0285,105.8542"). Ghi đè thẳng.
    void put_dedup(const std::string& staff_id, const std::string& fingerprint);
    std::optional<std::string> get_dedup(const std::string& staff_id) const;

private:
    std::string key(const std::string& kind, const std::string& staff_id) const;  // {prefix}{kind}:{staff}
    // Lua "ghi khi mới hơn" lên hash `hash_key`; latest_key rỗng = không cập nhật latest.
    int set_if_newer(const std::string& hash_key, int ttl_seconds, const std::string& json,
                      const std::optional<Version>& version, const std::string& latest_key = "",
                      const std::string& date = "");
    std::optional<Versioned> get_hash(const std::string& hash_key) const;  // khóa mutex_ bên trong
    std::optional<std::string> get_value(const std::string& key) const;   // khóa mutex_ bên trong
    void connect() const;                    // (nối lại) + PING/AUTH/SELECT; lỗi → ném
    redisContext* live() const;              // context đang dùng được; hỏng thì connect() lại (gọi khi giữ mutex_)

    Config config_;
    mutable redisContext* context_ = nullptr;
    mutable std::mutex mutex_;  // hiredis context không thread-safe; khóa chung, tách pool nếu cần throughput
};

// "127.0.0.1:6379" → Config (host, port; còn lại mặc định). Sai dạng / cổng ngoài 1–65535 → không có.
std::optional<RedisStore::Config> redis_config(const std::string& host_port);

}  // namespace ktv
