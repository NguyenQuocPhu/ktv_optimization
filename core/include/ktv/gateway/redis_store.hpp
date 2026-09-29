// ============================================================================
// gateway/redis_store — RouteStore trên Redis (nhiều replica, sống qua restart)
// ============================================================================
// Hiểu nhanh:
//   Cùng hợp đồng `RouteStore`, nhưng dữ liệu nằm ở Redis thay vì RAM.
//   Khóa:
//     {prefix}route:{staff_id}:{date}  → nguyên JSON response OUT   (TTL)
//     {prefix}latest:{staff_id}        → ngày của bản mới nhất      (TTL)
//
// Dùng thế nào:
//   RedisRouteStore::Config cfg; cfg.host="127.0.0.1"; cfg.port=6379;
//   RedisRouteStore store(cfg);              // ném nếu không kết nối/auth được
//
//   Chỉ build khi tìm thấy hiredis (xem CMake, define KTV_WITH_REDIS).
// Phụ thuộc: hiredis.
// ============================================================================
#pragma once

#include <mutex>
#include <string>

#include "ktv/gateway/store.hpp"

struct redisContext;  // forward declare, không lộ hiredis ra header

namespace ktv {

class RedisRouteStore : public RouteStore {
public:
    struct Config {
        std::string host = "127.0.0.1";
        int port = 6379;
        std::string password;
        int db = 0;
        std::string prefix = "ktv:";     // namespace để không đụng key khác trong Redis dùng chung
        int ttl_seconds = 7 * 24 * 3600; // 7 ngày
    };

    // Ném std::runtime_error nếu không kết nối / AUTH / SELECT được.
    explicit RedisRouteStore(const Config& config);
    ~RedisRouteStore() override;

    RedisRouteStore(const RedisRouteStore&) = delete;
    RedisRouteStore& operator=(const RedisRouteStore&) = delete;

    void put(const std::string& staff_id, const std::string& date, std::string json) override;
    std::optional<std::string> get(const std::string& staff_id, const std::string& date) const override;
    std::optional<std::string> get_latest(const std::string& staff_id) const override;
    std::size_t size() const override;

private:
    std::string route_key(const std::string& staff_id, const std::string& date) const;
    std::string latest_key(const std::string& staff_id) const;
    std::optional<std::string> get_value(const std::string& key) const;      // khóa mutex_ bên trong
    bool set_value(const std::string& key, const std::string& value) const;  // khóa mutex_ bên trong

    Config config_;
    redisContext* context_ = nullptr;
    mutable std::mutex mutex_;  // hiredis context không thread-safe; ponytail: khóa chung, tách pool nếu cần throughput
};

}  // namespace ktv
