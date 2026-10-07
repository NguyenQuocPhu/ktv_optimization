#include "ktv/adapter/publish.hpp"

#include <algorithm>
#include <cstdio>
#include <cstdlib>

#include "ktv/adapter/file.hpp"
#include "ktv/plan.hpp"

namespace ktv {

namespace {

// Vị trí Mobix {"latlng":"21.02,105.79","latlng_at":"2026-10-01 08:40:00"} còn dùng được thì thay vào staff.
// Hỏng / quá cũ → bỏ qua (tuyến xuất phát từ vị trí trong IN). Trả latlng_at đã ghi trong version của loc.
std::optional<std::int64_t> apply_mobix_loc(const Versioned& loc, Minutes now, Staff& staff) {
    const json value = json::parse(loc.json, nullptr, false);
    if (!value.is_object() || !value.contains("latlng") || !value["latlng"].is_string() ||
        !value.contains("latlng_at") || !value["latlng_at"].is_string())
        return std::nullopt;
    const std::optional<Point> point = parse_latlng(value["latlng"].get<std::string>());
    const std::optional<Minutes> at = parse_datetime(value["latlng_at"].get<std::string>());
    if (!point || !at || std::llabs(now - *at) > kLocMaxAgeMinutes) return std::nullopt;
    staff.latlng = *point;
    return loc.version.empty() ? 0 : loc.version.front();
}

// 7.20.1b: OUT cache dùng lại được cho lần chạy này không? Có → OUT "no" theo envelope mới.
std::optional<nlohmann::ordered_json> reuse_cached(RedisStore& store, const std::string& staff_id,
                                                   const std::string& fingerprint, const Envelope& envelope,
                                                   Minutes now, const Rules& rules) {
    if (rules.cache_max_age_minutes <= 0) return std::nullopt;
    const auto& force = rules.force_recompute_triggers;
    if (std::find(force.begin(), force.end(), envelope.trigger) != force.end()) return std::nullopt;
    const std::optional<std::string> meta = store.get_dedup(staff_id);  // "fingerprint|run_code" lần tính cuối
    if (!meta || meta->rfind(fingerprint + "|", 0) != 0) return std::nullopt;
    const std::string run_code = meta->substr(fingerprint.size() + 1);
    const std::optional<std::string> text = store.get(staff_id, format_datetime(envelope.planned_at).substr(0, 10));
    if (!text) return std::nullopt;
    const nlohmann::ordered_json cached = nlohmann::ordered_json::parse(*text, nullptr, false);
    if (!cached.is_object() || cached.value("run_code", "") != run_code) return std::nullopt;  // route đã bị thay
    const std::string code = cached.value("statuscode", "");
    if (code != "200" && code != "422") return std::nullopt;  // 424 (OSRM lỗi): tính lại để thử OSRM
    const std::optional<Minutes> computed = parse_datetime(cached.value("planned_at", ""));
    if (!computed || *computed > envelope.planned_at ||
        envelope.planned_at - *computed > rules.cache_max_age_minutes)  // trần tuổi
        return std::nullopt;
    const nlohmann::ordered_json& data = out_data(cached);
    if (data.is_object() && data.contains("clusters"))  // (b): chưa tới giờ đến ca đầu của bản cache
        for (const auto& cluster : data["clusters"])
            for (const auto& row : cluster["schedule"])
                if (row.value("entry_type", "") == "TASK") {
                    const std::optional<Minutes> first = parse_datetime(row.value("start_at", ""));
                    if (!first || envelope.planned_at >= *first) return std::nullopt;
                    return reuse_response(cached, envelope, now);
                }
    return reuse_response(cached, envelope, now);  // không có TASK (422): chỉ xét trần tuổi
}

}  // namespace

std::string in_fingerprint(const json& in, const Point& staff_latlng, Minutes planned_at) {
    json canonical = in;  // json (không ordered) = khóa sắp theo tên → chuỗi tất định
    for (const char* key : {"message_id", "trigger", "planned_at", "run_code"}) canonical.erase(key);
    canonical["date"] = format_datetime(planned_at).substr(0, 10);
    char point[64];
    std::snprintf(point, sizeof point, "%.4f,%.4f", staff_latlng.lat, staff_latlng.lng);  // ~11 m: GPS rung không đổi
    if (canonical.contains("staff") && canonical["staff"].is_object()) canonical["staff"]["latlng"] = point;
    if (canonical.contains("tasks") && canonical["tasks"].is_object())
        for (auto& [group, items] : canonical["tasks"].items())
            if (items.is_array())
                std::stable_sort(items.begin(), items.end(), [](const json& a, const json& b) {
                    const auto id = [](const json& t) { return t.is_object() ? t.value("task_id", json()).dump() : t.dump(); };
                    return id(a) < id(b);
                });
    std::uint64_t hash = 1469598103934665603ULL;  // FNV-1a 64
    for (unsigned char c : canonical.dump()) hash = (hash ^ c) * 1099511628211ULL;
    char hex[17];
    std::snprintf(hex, sizeof hex, "%016llx", static_cast<unsigned long long>(hash));
    return hex;
}

std::int64_t stamp(Minutes value) {
    std::string digits;
    for (char c : format_datetime(value))
        if (c >= '0' && c <= '9') digits += c;
    return std::stoll(digits);
}

Published plan_and_store(const json& in, const Envelope& envelope, Minutes now, const Version& version,
                         const Rules& rules, const std::string& osrm_url, RedisStore* store,
                         const SendOut& send_out, bool reply_on_cache) {
    Published result;
    result.message_id = envelope.message_id;

    Message message = parse_record(in, envelope, result.errors, &result.warnings);
    result.staff_id = message.staff.staff_id;
    if (!result.errors.empty()) {
        result.status = "400";
        result.out = wrap_response(envelope, bad_request(result.errors, envelope.message_id, now));
        return result;
    }

    const std::string& staff_id = message.staff.staff_id;
    Version based_on = version;
    if (store) {
        if (!store->put_state(staff_id, in.dump(), version)) {
            const std::optional<Versioned> current = store->get_state(staff_id);
            if (!current || current->version != version) {
                result.status = "STALE";
                return result;
            }
        }
        const std::optional<Versioned> loc = store->get_loc(staff_id);
        const std::optional<std::int64_t> loc_at = loc ? apply_mobix_loc(*loc, now, message.staff) : std::nullopt;
        result.used_mobix_loc = loc_at.has_value();
        based_on.push_back(loc_at.value_or(0));
    }
    // 7.20.1b: IN không đổi nội dung → trả OUT cache (change_id "no"), không tính, không ghi route.
    std::string fingerprint;
    if (store) {
        fingerprint = in_fingerprint(in, message.staff.latlng, envelope.planned_at);
        if (std::optional<nlohmann::ordered_json> reused = reuse_cached(*store, staff_id, fingerprint, envelope, now, rules)) {
            result.cached = true;
            result.status = (*reused)["statuscode"].get<std::string>();
            result.out = std::move(*reused);
            if (send_out && reply_on_cache) {
                send_out(staff_id, result.out->dump(-1, ' ', false, nlohmann::json::error_handler_t::replace));
                result.out_sent = true;
            }
            return result;
        }
    }

    nlohmann::ordered_json response;
    try {
        PlanResult planned = plan(message, rules, now, osrm_url);
        response = std::move(planned.response);
        result.stats = planned.stats;
        // Cảnh báo bước lọc (trạng thái task) đi cùng cảnh báo parser: worker log + /healthz.
        result.warnings.insert(result.warnings.end(), planned.warnings.begin(), planned.warnings.end());
    } catch (const std::exception& error) {
        // Lỗi của MỘT message không được làm chết worker (chết → đọc lại → chết tiếp → kẹt partition).
        response = error_response("500", std::string("Lỗi xử lý: ") + error.what(), envelope.message_id, now);
    }
    result.status = response["statuscode"].get<std::string>();
    result.out = wrap_response(envelope, response);

    // 422 (KTV off / hết việc) vẫn ghi + gửi: Mobix/OA không được giữ tuyến cũ còn việc đã gỡ. 500 thì giữ route cũ.
    if (result.status == "500") return result;
    const std::string json_text = result.out->dump(-1, ' ', false, nlohmann::json::error_handler_t::replace);
    bool current = true;  // route này là bản hiện hành (không có bản mới hơn)?
    if (store) {
        const Write written =
            store->put_route(staff_id, format_datetime(envelope.planned_at).substr(0, 10), json_text, based_on);
        result.route_stored = written == Write::Stored;
        current = written != Write::Older;
        // 7.20.1b: nhớ dấu vân tay của bản route vừa thành hiện hành (424 vẫn ghi, reuse_cached tự bỏ qua 424).
        if (current) store->put_dedup(staff_id, fingerprint + "|" + (envelope.run_code.empty() ? envelope.message_id : envelope.run_code));
    }
    if (send_out && current) {
        send_out(staff_id, json_text);
        result.out_sent = true;
    }
    return result;
}

}  // namespace ktv
