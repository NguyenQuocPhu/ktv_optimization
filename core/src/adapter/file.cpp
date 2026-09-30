#include "ktv/adapter/file.hpp"

#include "ktv/plan.hpp"

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

Envelope local_envelope(const json& record, const std::string& fallback_id, Minutes default_planned_at) {
    Envelope envelope;
    auto text_field = [&](const char* key) -> std::optional<std::string> {
        if (!record.is_object() || !record.contains(key) || !record[key].is_string()) return std::nullopt;
        std::string value = record[key].get<std::string>();
        if (value.empty()) return std::nullopt;
        return value;
    };
    envelope.message_id = text_field("message_id").value_or(fallback_id);
    envelope.trigger = text_field("trigger").value_or("DAY_START");
    std::optional<Minutes> planned;
    if (record.is_object() && record.contains("planned_at") && record["planned_at"].is_string())
        planned = parse_datetime(record["planned_at"].get<std::string>());
    envelope.planned_at = planned.value_or(default_planned_at);
    return envelope;
}

Message parse_record(const json& record, const Envelope& envelope, std::vector<Error>& errors, std::vector<Error>* warnings) {
    Message message;
    if (record.is_discarded()) errors.push_back({"", "JSON hỏng"});
    else message = parse_message(record, errors, warnings);
    message.message_id = envelope.message_id;  // trace_id = message_id của envelope
    message.planned_at = envelope.planned_at;  // lập tuyến theo đúng planned_at của envelope
    return message;
}

nlohmann::ordered_json bad_request(const std::vector<Error>& errors, const std::string& trace_id, Minutes now) {
    std::string text = "Sai định dạng tham số:";
    for (size_t k = 0; k < errors.size() && k < 3; ++k) text += " " + errors[k].path + " " + errors[k].problem + ";";
    return error_response("400", text, trace_id, now);
}

}  // namespace ktv
