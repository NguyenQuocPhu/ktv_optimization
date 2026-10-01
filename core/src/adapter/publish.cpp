#include "ktv/adapter/publish.hpp"

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

}  // namespace

std::int64_t stamp(Minutes value) {
    std::string digits;
    for (char c : format_datetime(value))
        if (c >= '0' && c <= '9') digits += c;
    return std::stoll(digits);
}

Published plan_and_store(const json& in, const Envelope& envelope, Minutes now, const Version& version,
                         const Rules& rules, const std::string& osrm_url, RedisStore* store,
                         const SendOut& send_out) {
    Published result;
    result.message_id = envelope.message_id;

    std::vector<Error> errors;
    Message message = parse_record(in, envelope, errors, &result.warnings);
    if (!errors.empty()) {
        result.status = "400";
        result.out = wrap_response(envelope, bad_request(errors, envelope.message_id, now));
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

    nlohmann::ordered_json response;
    try {
        response = plan(message, rules, now, osrm_url).response;
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
    }
    if (send_out && current) {
        send_out(staff_id, json_text);
        result.out_sent = true;
    }
    return result;
}

}  // namespace ktv
