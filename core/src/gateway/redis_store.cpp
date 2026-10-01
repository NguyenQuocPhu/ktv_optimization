#include "ktv/gateway/redis_store.hpp"

#include <hiredis/hiredis.h>

#include <sstream>
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

// Ghi hash {json, v} khi version mới hơn hẳn version đang có (so từ trái sang, thiếu = 0).
// KEYS[1] hash, KEYS[2] latest (tùy chọn). ARGV: ttl, json, có version? ("1"/"0"), version "n1 n2 ..", date.
// Khóa cũ không phải hash (dạng chuỗi trước Phase 7.2) → xóa rồi ghi.
const char* const kSetIfNewer = R"lua(
if redis.call('TYPE', KEYS[1]).ok ~= 'hash' then redis.call('DEL', KEYS[1]) end
if ARGV[3] == '1' then
  local old = redis.call('HGET', KEYS[1], 'v')
  if old then
    local a, b = {}, {}
    for x in string.gmatch(ARGV[4], '%S+') do a[#a + 1] = tonumber(x) end
    for x in string.gmatch(old, '%S+') do b[#b + 1] = tonumber(x) end
    local newer = false
    for i = 1, math.max(#a, #b) do
      local x, y = a[i] or 0, b[i] or 0
      if x ~= y then newer = x > y break end
    end
    if not newer then return 0 end
  end
end
redis.call('HSET', KEYS[1], 'json', ARGV[2], 'v', ARGV[4])
redis.call('EXPIRE', KEYS[1], ARGV[1])
if #KEYS > 1 then
  local latest = redis.call('GET', KEYS[2])
  if (not latest) or ARGV[5] >= latest then redis.call('SET', KEYS[2], ARGV[5], 'EX', ARGV[1]) end
end
return 1
)lua";

std::string join(const Version& version) {
    std::string out;
    for (std::int64_t number : version) {
        if (!out.empty()) out += ' ';
        out += std::to_string(number);
    }
    return out;
}

Version split(const std::string& text) {
    Version out;
    std::istringstream in(text);
    for (std::int64_t number; in >> number;) out.push_back(number);
    return out;
}

std::string reply_error(redisContext* context, const Reply& reply) {
    if (reply.value && reply.value->type == REDIS_REPLY_ERROR) return std::string(reply.value->str, reply.value->len);
    return context->err ? context->errstr : "không có reply";
}

}  // namespace

RedisStore::RedisStore(const Config& config) : config_(config) {
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

RedisStore::~RedisStore() {
    if (context_) redisFree(context_);
}

std::string RedisStore::key(const std::string& kind, const std::string& staff_id) const {
    return config_.prefix + kind + ":" + staff_id;
}

bool RedisStore::set_if_newer(const std::string& hash_key, int ttl_seconds, const std::string& json,
                              const std::optional<Version>& version, const std::string& latest_key,
                              const std::string& date) {
    std::vector<std::string> arguments{"EVAL", kSetIfNewer, latest_key.empty() ? "1" : "2", hash_key};
    if (!latest_key.empty()) arguments.push_back(latest_key);
    arguments.insert(arguments.end(), {std::to_string(ttl_seconds), json, version ? "1" : "0",
                                       version ? join(*version) : "", date});
    std::lock_guard<std::mutex> lock(mutex_);
    Reply reply(context_, arguments);
    if (!reply.ok() || reply.value->type != REDIS_REPLY_INTEGER)
        throw std::runtime_error("redis: ghi " + hash_key + " thất bại (" + reply_error(context_, reply) + ")");
    return reply.value->integer == 1;
}

std::optional<Versioned> RedisStore::get_hash(const std::string& hash_key) const {
    std::lock_guard<std::mutex> lock(mutex_);
    Reply reply(context_, {"HMGET", hash_key, "json", "v"});
    if (!reply.ok() || reply.value->type != REDIS_REPLY_ARRAY || reply.value->elements != 2) return std::nullopt;
    const redisReply* json = reply.value->element[0];
    const redisReply* version = reply.value->element[1];
    if (json->type != REDIS_REPLY_STRING) return std::nullopt;
    Versioned out{std::string(json->str, json->len), {}};
    if (version->type == REDIS_REPLY_STRING) out.version = split(std::string(version->str, version->len));
    return out;
}

std::optional<std::string> RedisStore::get_value(const std::string& key) const {
    std::lock_guard<std::mutex> lock(mutex_);
    Reply reply(context_, {"GET", key});
    if (reply.value && reply.value->type == REDIS_REPLY_STRING)
        return std::string(reply.value->str, reply.value->len);
    return std::nullopt;
}

bool RedisStore::put_state(const std::string& staff_id, const std::string& json, const Version& version) {
    return set_if_newer(key("state", staff_id), config_.state_ttl_seconds, json, version);
}

std::optional<Versioned> RedisStore::get_state(const std::string& staff_id) const {
    return get_hash(key("state", staff_id));
}

bool RedisStore::put_route(const std::string& staff_id, const std::string& date, const std::string& json,
                           const Version& based_on) {
    return set_if_newer(key("route", staff_id + ":" + date), config_.route_ttl_seconds, json, based_on,
                        key("latest", staff_id), date);
}

void RedisStore::put(const std::string& staff_id, const std::string& date, std::string json) {
    set_if_newer(key("route", staff_id + ":" + date), config_.route_ttl_seconds, json, std::nullopt,
                 key("latest", staff_id), date);
}

std::optional<std::string> RedisStore::get(const std::string& staff_id, const std::string& date) const {
    const std::optional<Versioned> route = get_hash(key("route", staff_id + ":" + date));
    if (!route) return std::nullopt;
    return route->json;
}

std::optional<std::string> RedisStore::get_latest(const std::string& staff_id) const {
    const std::optional<std::string> date = get_value(key("latest", staff_id));
    if (!date) return std::nullopt;
    return get(staff_id, *date);
}

std::size_t RedisStore::size() const {
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

bool RedisStore::put_loc(const std::string& staff_id, const std::string& json, const Version& version) {
    return set_if_newer(key("loc", staff_id), config_.loc_ttl_seconds, json, version);
}

std::optional<Versioned> RedisStore::get_loc(const std::string& staff_id) const {
    return get_hash(key("loc", staff_id));
}

void RedisStore::put_dedup(const std::string& staff_id, const std::string& fingerprint) {
    std::lock_guard<std::mutex> lock(mutex_);
    Reply reply(context_,
                {"SET", key("dedup", staff_id), fingerprint, "EX", std::to_string(config_.dedup_ttl_seconds)});
    if (!reply.ok()) throw std::runtime_error("redis: ghi dedup thất bại (" + reply_error(context_, reply) + ")");
}

std::optional<RedisStore::Config> redis_config(const std::string& host_port) {
    const auto colon = host_port.rfind(':');
    if (colon == std::string::npos || colon == 0) return std::nullopt;
    const std::string port = host_port.substr(colon + 1);
    if (port.empty() || port.size() > 5 || port.find_first_not_of("0123456789") != std::string::npos) return std::nullopt;
    RedisStore::Config config;
    config.host = host_port.substr(0, colon);
    config.port = std::stoi(port);
    if (config.port < 1 || config.port > 65535) return std::nullopt;
    return config;
}

std::optional<std::string> RedisStore::get_dedup(const std::string& staff_id) const {
    return get_value(key("dedup", staff_id));
}

}  // namespace ktv
