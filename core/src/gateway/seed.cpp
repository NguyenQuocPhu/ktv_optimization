#include "ktv/gateway/seed.hpp"

#include <string>

#include <nlohmann/json.hpp>

namespace ktv {

namespace {

std::string trim(const std::string& text) {
    const auto first = text.find_first_not_of(" \t\r\n");
    if (first == std::string::npos) return {};
    const auto last = text.find_last_not_of(" \t\r\n");
    return text.substr(first, last - first + 1);
}

// Ngày từ "YYYY-MM-DD HH:mm:ss" → "YYYY-MM-DD". Sai dạng → rỗng.
std::string date_of(const std::string& planned_at) {
    if (planned_at.size() < 10 || planned_at[4] != '-' || planned_at[7] != '-') return {};
    return planned_at.substr(0, 10);
}

}  // namespace

std::size_t load_routes(std::istream& in, RouteStore& store) {
    std::size_t loaded = 0;
    std::string line;
    while (std::getline(in, line)) {
        const std::string text = trim(line);
        if (text.empty()) continue;
        nlohmann::json value = nlohmann::json::parse(text, nullptr, false);
        if (!value.is_object()) continue;  // JSON hỏng hoặc không phải object
        if (!value.contains("planned_at") || !value["planned_at"].is_string()) continue;
        const std::string date = date_of(value["planned_at"].get<std::string>());
        if (date.empty()) continue;
        if (!value.contains("data")) continue;
        // 7.20.1: data là array 1 phần tử; file OUT cũ (data object) vẫn nạp được.
        const auto& raw = value["data"];
        if (raw.is_array() && raw.empty()) continue;
        const auto& data = raw.is_array() ? raw[0] : raw;
        if (!data.is_object()) continue;
        if (!data.contains("staff_id") || !data["staff_id"].is_string()) continue;
        const std::string staff_id = data["staff_id"].get<std::string>();
        if (staff_id.empty()) continue;
        store.put(staff_id, date, text);
        ++loaded;
    }
    return loaded;
}

}  // namespace ktv
