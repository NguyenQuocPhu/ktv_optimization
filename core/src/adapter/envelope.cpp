#include "ktv/adapter/envelope.hpp"

#include <utility>

namespace ktv {

nlohmann::ordered_json wrap_response(const Envelope& envelope, const nlohmann::ordered_json& response) {
    auto field = [&](const char* key, nlohmann::ordered_json fallback) -> nlohmann::ordered_json {
        return response.contains(key) ? response[key] : std::move(fallback);
    };
    nlohmann::ordered_json out;
    out["message_id"] = envelope.message_id;
    out["run_code"] = envelope.run_code.empty() ? envelope.message_id : envelope.run_code;
    out["trigger"] = envelope.trigger;
    out["planned_at"] = format_datetime(envelope.planned_at);
    out["schema_version"] = "1";
    out["success"] = field("success", false);
    out["statuscode"] = field("statuscode", "400");
    out["message"] = field("message", "");
    out["trace_id"] = field("trace_id", envelope.message_id);
    out["server_time"] = field("server_time", format_datetime(envelope.planned_at));
    const nlohmann::ordered_json data = field("data", nlohmann::ordered_json(nullptr));
    if (!data.is_object()) {  // lỗi (null) hoặc đã là array: giữ nguyên
        out["data"] = data;
        return out;
    }
    nlohmann::ordered_json item = nlohmann::ordered_json::object();  // sheet 03: staff_id, change_id, trace_id, ...
    for (auto it = data.begin(); it != data.end(); ++it) {
        item[it.key()] = it.value();
        if (it.key() == "staff_id") {
            item["change_id"] = "yes";
            item["trace_id"] = "";
            item["route_changed"] = "yes";  // 7.28: plan_and_store hạ "no" khi tuyến giống bản mới nhất trong cache
        }
    }
    if (!item.contains("change_id")) item["change_id"] = "yes", item["trace_id"] = "", item["route_changed"] = "yes";
    out["data"] = nlohmann::ordered_json::array({item});
    return out;
}

const nlohmann::ordered_json& out_data(const nlohmann::ordered_json& out) {
    static const nlohmann::ordered_json none(nullptr);
    if (!out.is_object() || !out.contains("data")) return none;
    const nlohmann::ordered_json& data = out["data"];
    if (data.is_array()) return data.empty() ? none : data[0];
    return data;
}

nlohmann::ordered_json reuse_response(const nlohmann::ordered_json& cached, const Envelope& envelope, Minutes now) {
    nlohmann::ordered_json out = cached;
    if (out.contains("data") && out["data"].is_object()) out["data"] = nlohmann::ordered_json::array({out["data"]});
    const std::string computed = cached.value("run_code", cached.value("message_id", ""));
    out["message_id"] = envelope.message_id;
    out["run_code"] = envelope.run_code.empty() ? envelope.message_id : envelope.run_code;
    out["trigger"] = envelope.trigger;
    out["planned_at"] = format_datetime(envelope.planned_at);
    out["trace_id"] = envelope.message_id;
    out["server_time"] = format_datetime(now);
    if (out.contains("data") && out["data"].is_array() && !out["data"].empty()) {
        nlohmann::ordered_json& data = out["data"][0];
        const bool chained = data.value("change_id", "") == "no";  // bản cache cũng là "no" → trỏ về lần tính thật
        const std::string origin = chained ? data.value("trace_id", computed) : computed;
        nlohmann::ordered_json item = nlohmann::ordered_json::object();
        for (auto it = data.begin(); it != data.end(); ++it) {
            if (it.key() == "change_id" || it.key() == "trace_id" || it.key() == "route_changed") continue;
            item[it.key()] = it.value();
            // Trả cache = đúng tuyến đang có → route_changed "no" (7.28).
            if (it.key() == "staff_id") item["change_id"] = "no", item["trace_id"] = origin, item["route_changed"] = "no";
        }
        if (!item.contains("change_id")) item["change_id"] = "no", item["trace_id"] = origin, item["route_changed"] = "no";
        data = std::move(item);
    }
    return out;
}

}  // namespace ktv
