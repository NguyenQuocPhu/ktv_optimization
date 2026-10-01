// plan_and_store (Phase 7.3, luồng T1 của worker) trên Redis thật: state/route, IN cũ, giao lại, vị trí Mobix,
// 422 vẫn ghi route, 400 không đụng Redis. Cần env KTV_TEST_REDIS=host:port; không kết nối được thì SKIP.
#include <cstdlib>
#include <iostream>
#include <memory>
#include <string>

#include <unistd.h>

#include <hiredis/hiredis.h>

#include "ktv/adapter/publish.hpp"

static int failures = 0;
#define CHECK(cond)                                                           \
    do {                                                                      \
        if (!(cond)) {                                                        \
            std::cerr << __FILE__ << ":" << __LINE__ << ": FAIL " #cond "\n"; \
            ++failures;                                                       \
        }                                                                     \
    } while (0)

using ktv::json;

namespace {

json message(const std::string& id, const std::string& staff_latlng, json staff_status = nullptr) {
    const json task = {
        {"task_id", 1}, {"task_group_id", 2}, {"task_group_name", "bao_tri"},
        {"task_type_id", 1}, {"task_type_name", "bao_tri_vat_ly"}, {"task_sub_id", 0}, {"task_sub_name", ""},
        {"task_status_id", 6}, {"task_status_name", ""},
        {"sla", {{"sla_minutes", 60}, {"priority_in_day", 1}}},
        {"appointment", ""}, {"create_date", ""}, {"complete_date", ""},
        {"location", ""}, {"latlng", "21.03,105.81"}, {"handle_minutes", ""},
        {"task_plots_id", 1}, {"staff_plots_id", 1}, {"staff_role", 1}, {"block_id", 1}};
    json staff = {{"staff_id", "S1"}, {"staff_account", "A"}, {"latlng", staff_latlng}, {"available", "08:00-17:30"},
                  {"plots", json::array({{{"id", 1}, {"name", "P"}, {"role", 1}, {"block_id", 1}}})},
                  {"current_task", nullptr}};
    if (!staff_status.is_null()) staff["status"] = staff_status;
    return json{{"message_id", id}, {"planned_at", "2026-10-01 09:00:00"}, {"trigger", "DAY_START"}, {"staff", staff},
                {"tasks", {{"trien_khai", json::array({task})}, {"bao_tri", json::array()}, {"thu_hoi", json::array()},
                           {"hoa_don", json::array()}, {"onsite", json::array()}}}};
}

// Lệnh Redis trả chuỗi (HGET…); không có → "".
std::string redis_text(redisContext* context, const std::string& command, const std::string& key,
                       const std::string& field = "") {
    auto* reply = static_cast<redisReply*>(field.empty() ? redisCommand(context, "%s %s", command.c_str(), key.c_str())
                                                         : redisCommand(context, "%s %s %s", command.c_str(),
                                                                        key.c_str(), field.c_str()));
    std::string out = reply && reply->type == REDIS_REPLY_STRING ? std::string(reply->str, reply->len) : "";
    if (reply) freeReplyObject(reply);
    return out;
}

double first_leg_km(const nlohmann::ordered_json& out) {
    for (const auto& cluster : out["data"]["clusters"])
        for (const auto& row : cluster["schedule"])
            if (row["entry_type"] == "TASK") return row["travel_km_before"].get<double>();
    return -1;
}

}  // namespace

int main() {
    const char* address = std::getenv("KTV_TEST_REDIS");
    std::optional<ktv::RedisStore::Config> config = address ? ktv::redis_config(address) : std::nullopt;
    if (!config) {
        std::cout << "test_publish: SKIP (không có KTV_TEST_REDIS=host:port)\n";
        return 0;
    }
    config->prefix = "ktvtest-publish:" + std::to_string(getpid()) + ":";
    std::unique_ptr<ktv::RedisStore> holder;
    try {
        holder = std::make_unique<ktv::RedisStore>(*config);
    } catch (const std::exception& error) {
        std::cout << "test_publish: SKIP (" << error.what() << ")\n";
        return 0;
    }
    ktv::RedisStore& store = *holder;
    redisContext* raw = redisConnect(config->host.c_str(), config->port);
    const std::string route_key = config->prefix + "route:S1:2026-10-01";
    const ktv::Rules rules = ktv::default_rules();
    const ktv::Minutes now = *ktv::parse_datetime("2026-10-01 09:00:00");
    const std::string home = "21.02,105.80";
    const auto run = [&](const json& payload, const ktv::Version& version, ktv::RedisStore* target) {
        return ktv::plan_and_store(payload.dump(), "t-0-1", now, version, rules, "", target);
    };

    try {
        {  // Không Redis: như worker cũ.
            const ktv::Published r = run(message("m0", home), {1, 1}, nullptr);
            CHECK(r.status == "200" && r.out && !r.route_stored && r.message_id == "m0");
        }
        const double km_home = first_leg_km(*run(message("m0", home), {1, 1}, nullptr).out);

        {  // IN đầu tiên → state + route, based_on = version + 0 (vị trí trong IN).
            const ktv::Published r = run(message("m1", home), {100, 1}, &store);
            CHECK(r.status == "200" && r.route_stored && !r.used_mobix_loc);
            CHECK(store.get_state("S1") && store.get_state("S1")->version == ktv::Version({100, 1}));
            CHECK(redis_text(raw, "HGET", route_key, "v") == "100 1 0");
            CHECK(store.get_latest("S1") && json::parse(*store.get_latest("S1"))["run_code"] == "m1");
        }
        {  // IN cũ hơn đến trễ → bỏ qua, không tính, state/route giữ nguyên.
            const ktv::Published r = run(message("m-cu", "21.05,105.85"), {90, 7}, &store);
            CHECK(r.status == "STALE" && !r.out && !r.route_stored);
            CHECK(store.get_state("S1") && store.get_state("S1")->version == ktv::Version({100, 1}));
            CHECK(json::parse(*store.get("S1", "2026-10-01"))["run_code"] == "m1");
        }
        {  // Kafka giao lại cùng IN sau khi chết giữa "ghi state" và "ghi route" → vẫn tính, route được ghi.
            freeReplyObject(redisCommand(raw, "DEL %s", route_key.c_str()));
            const ktv::Published r = run(message("m1", home), {100, 1}, &store);
            CHECK(r.status == "200" && r.route_stored);
            CHECK(store.get("S1", "2026-10-01").has_value());
        }
        {  // Vị trí Mobix 30 phút trước → thay vị trí IN, based_on mang latlng_at.
            CHECK(store.put_loc("S1", R"({"latlng":"21.06,105.86","latlng_at":"2026-10-01 08:30:00"})",
                                {20261001083000}));
            const ktv::Published r = run(message("m2", home), {110, 1}, &store);
            CHECK(r.status == "200" && r.used_mobix_loc && r.route_stored);
            CHECK(r.out && first_leg_km(*r.out) > km_home + 1);  // xuất phát xa hơn → chặng đầu dài hơn
            CHECK(redis_text(raw, "HGET", route_key, "v") == "110 1 20261001083000");
        }
        {  // Vị trí Mobix quá 60 phút → bỏ, quay về vị trí trong IN.
            CHECK(store.put_loc("S1", R"({"latlng":"21.06,105.86","latlng_at":"2026-10-01 09:30:00"})",
                                {20261001093000}));  // mới hơn để ghi đè được; cách now 30 phút: còn dùng
            CHECK(run(message("m3", home), {120, 1}, &store).used_mobix_loc);
            CHECK(store.put_loc("S1", R"({"latlng":"21.06,105.86","latlng_at":"2026-10-01 10:01:00"})",
                                {20261001100100}));  // cách now 61 phút
            const ktv::Published r = run(message("m4", home), {130, 1}, &store);
            CHECK(r.status == "200" && !r.used_mobix_loc);
            CHECK(r.out && std::abs(first_leg_km(*r.out) - km_home) < 1e-9);
        }
        {  // KTV off (422) vẫn ghi route: Mobix không được đọc tuyến cũ còn việc.
            const ktv::Published r = run(message("m5", home, 3), {140, 1}, &store);
            CHECK(r.status == "422" && r.route_stored);
            CHECK(json::parse(*store.get_latest("S1"))["statuscode"] == "422");
        }
        {  // 400 (staff hỏng): không biết KTV nào → không đụng Redis.
            json broken = message("m6", home);
            broken["staff"].erase("staff_id");
            const ktv::Published r = run(broken, {150, 1}, &store);
            CHECK(r.status == "400" && r.out && !r.route_stored);
            CHECK(store.get_state("S1")->version == ktv::Version({140, 1}));
        }
    } catch (const std::exception& error) {
        std::cerr << "FAIL: ngoại lệ " << error.what() << "\n";
        ++failures;
    }

    for (const char* kind : {"state:S1", "route:S1:2026-10-01", "latest:S1", "loc:S1"})
        freeReplyObject(redisCommand(raw, "DEL %s", (config->prefix + kind).c_str()));
    redisFree(raw);

    if (failures) std::cerr << failures << " lỗi\n";
    else std::cout << "test_publish: OK\n";
    return failures != 0;
}
