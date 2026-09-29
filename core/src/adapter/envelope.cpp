#include "ktv/adapter/envelope.hpp"

#include <utility>

namespace ktv {

nlohmann::ordered_json wrap_response(const Envelope& envelope, const nlohmann::ordered_json& response) {
    auto field = [&](const char* key, nlohmann::ordered_json fallback) -> nlohmann::ordered_json {
        return response.contains(key) ? response[key] : std::move(fallback);
    };
    nlohmann::ordered_json out;
    out["message_id"] = envelope.message_id;
    out["run_code"] = envelope.message_id;  // prototype: mỗi lần tính một run_code riêng.
    out["trigger"] = envelope.trigger;
    out["planned_at"] = format_datetime(envelope.planned_at);
    out["schema_version"] = "1";
    out["success"] = field("success", false);
    out["statuscode"] = field("statuscode", "400");
    out["message"] = field("message", "");
    out["trace_id"] = field("trace_id", envelope.message_id);
    out["server_time"] = field("server_time", format_datetime(envelope.planned_at));
    out["data"] = field("data", nlohmann::ordered_json(nullptr));
    return out;
}

}  // namespace ktv
