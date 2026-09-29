#include "ktv/gateway/redis_store.hpp"

#include <hiredis/hiredis.h>

#include <stdexcept>
#include <utility>
#include <vector>

namespace ktv {

namespace {

struct Reply {
    redisReply* value = nullptr;
    Reply(redisContext* context, const std::vector<std::string>& arguments) {
        std::vector<const char*> argv;
        std::vector<size_t> sizes;
        argv.reserve(arguments.size());
        sizes.reserve(arguments.size());
        for (const std::string& argument : arguments) {
            argv.push_back(argument.c_str());
            sizes.push_back(argument.size());
        }
        value = static_cast<redisReply*>(redisCommandArgv(context, argv.size(), argv.data(), sizes.data()));
    }
    ~Reply() {
        if (value) freeReplyObject(value);
    }
    Reply(const Reply&) = delete;
    Reply& operator=(const Reply&) = delete;
    bool ok() const { return value && value->type != REDIS_REPLY_ERROR; }
};

}  // namespace

RedisRouteStore::RedisRouteStore(const Config& config) : config_(config) {
    const timeval timeout{1, 500000};  // 1.5 giây
    context_ = redisConnectWithTimeout(config_.host.c_str(), config_.port, timeout);
    const auto fail = [&](const std::string& reason) -> void {
        if (context_) redisFree(context_);
        context_ = nullptr;
        throw std::runtime_error("redis: không kết nối được " + config_.host + ":" + std::to_string(config_.port) +
                                 " (" + reason + ")");
    };
    if (!context_ || context_->err) {
        fail(context_ ? context_->errstr : "không cấp được context");
        return;
    }
    {
        Reply reply(context_, {"PING"});
        if (!reply.ok()) {
            fail(context_->err ? context_->errstr : "PING thất bại");
            return;
        }
    }
    if (!config_.password.empty()) {
        Reply reply(context_, {"AUTH", config_.password});
        if (!reply.ok()) {
            fail("AUTH thất bại");
            return;
        }
    }
    if (config_.db != 0) {
        Reply reply(context_, {"SELECT", std::to_string(config_.db)});
        if (!reply.ok()) {
            fail("SELECT thất bại");
            return;
        }
    }
}

RedisRouteStore::~RedisRouteStore() {
    if (context_) redisFree(context_);
}

std::string RedisRouteStore::route_key(const std::string& staff_id, const std::string& date) const {
    return config_.prefix + "route:" + staff_id + ":" + date;
}

std::string RedisRouteStore::latest_key(const std::string& staff_id) const {
    return config_.prefix + "latest:" + staff_id;
}

std::optional<std::string> RedisRouteStore::get_value(const std::string& key) const {
    std::lock_guard<std::mutex> lock(mutex_);
    Reply reply(context_, {"GET", key});
    if (reply.value && reply.value->type == REDIS_REPLY_STRING)
        return std::string(reply.value->str, reply.value->len);
    return std::nullopt;
}

bool RedisRouteStore::set_value(const std::string& key, const std::string& value) const {
    std::lock_guard<std::mutex> lock(mutex_);
    Reply reply(context_, {"SET", key, value, "EX", std::to_string(config_.ttl_seconds)});
    return reply.ok();
}

void RedisRouteStore::put(const std::string& staff_id, const std::string& date, std::string json) {
    if (!set_value(route_key(staff_id, date), json)) throw std::runtime_error("redis: SET route thất bại");
    const std::optional<std::string> latest = get_value(latest_key(staff_id));
    if (!latest || date >= *latest) set_value(latest_key(staff_id), date);
}

std::optional<std::string> RedisRouteStore::get(const std::string& staff_id, const std::string& date) const {
    return get_value(route_key(staff_id, date));
}

std::optional<std::string> RedisRouteStore::get_latest(const std::string& staff_id) const {
    const std::optional<std::string> date = get_value(latest_key(staff_id));
    if (!date) return std::nullopt;
    return get(staff_id, *date);
}

std::size_t RedisRouteStore::size() const {
    std::lock_guard<std::mutex> lock(mutex_);
    std::size_t count = 0;
    std::string cursor = "0";
    const std::string pattern = config_.prefix + "route:*";
    do {
        Reply reply(context_, {"SCAN", cursor, "MATCH", pattern, "COUNT", "1000"});
        if (!reply.ok() || reply.value->type != REDIS_REPLY_ARRAY || reply.value->elements != 2) break;
        cursor.assign(reply.value->element[0]->str, reply.value->element[0]->len);
        const redisReply* keys = reply.value->element[1];
        if (keys->type == REDIS_REPLY_ARRAY) count += keys->elements;
    } while (cursor != "0");
    return count;
}

}  // namespace ktv
