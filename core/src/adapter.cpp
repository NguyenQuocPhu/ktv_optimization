#include "ktv/adapter.hpp"

#include <iterator>
#include <optional>
#include <sstream>

namespace ktv {

std::vector<json> read_records(std::istream& in) {
    std::string content((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    if (content.find_first_not_of(" \t\r\n") == std::string::npos) return {};  // file rỗng: không có input

    // Ưu tiên một object (có thể pretty nhiều dòng). Hợp lệ → đúng một record.
    json whole = json::parse(content, nullptr, false);
    if (!whole.is_discarded()) return {std::move(whole)};

    // Không phải một JSON hợp lệ: thử đọc như JSONL. Chỉ coi là JSONL khi có ít nhất một dòng tự parse được;
    // dòng hỏng vẫn giữ dạng discarded để main trả 400 cho đúng dòng đó.
    std::vector<json> records;
    std::istringstream stream(content);
    std::string line;
    bool any_valid = false;
    while (std::getline(stream, line)) {
        if (line.find_first_not_of(" \t\r\n") == std::string::npos) continue;
        json value = json::parse(line, nullptr, false);
        any_valid |= !value.is_discarded();
        records.push_back(std::move(value));
    }
    // Không dòng nào parse được: cả nội dung là MỘT input lỗi (thường là object pretty bị hỏng),
    // phải cho đúng một error response thay vì một lỗi mỗi dòng.
    if (!any_valid) return {std::move(whole)};
    return records;
}

Envelope local_envelope(const json& record, long long index, Minutes default_planned_at) {
    Envelope envelope;
    auto text_field = [&](const char* key) -> std::optional<std::string> {
        if (!record.is_object() || !record.contains(key) || !record[key].is_string()) return std::nullopt;
        std::string value = record[key].get<std::string>();
        if (value.empty()) return std::nullopt;
        return value;
    };
    envelope.message_id = text_field("message_id").value_or("local-" + std::to_string(index));
    envelope.trigger = text_field("trigger").value_or("DAY_START");
    std::optional<Minutes> planned;
    if (record.is_object() && record.contains("planned_at") && record["planned_at"].is_string())
        planned = parse_datetime(record["planned_at"].get<std::string>());
    envelope.planned_at = planned.value_or(default_planned_at);
    return envelope;
}

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
