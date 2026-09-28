// ============================================================================
// adapter — VỎ TRUYỀN TẢI LOCAL: đọc input và gói output cho prototype chưa có Kafka
// ============================================================================
// Hiểu nhanh:
//   Không phải nghiệp vụ. Chỉ lo hai việc của "bưu tá":
//     1. Đọc file input: một object pretty-printed, hoặc nhiều dòng JSONL.
//     2. Gói response nghiệp vụ vào envelope tương quan (message_id, run_code, trigger...).
//   Envelope staging còn thiếu thì tự sinh: message_id = "local-<số thứ tự>", trigger = DAY_START,
//   planned_at = giờ server truyền vào (test truyền tường minh, không dựa đồng hồ máy).
//
// Dùng thế nào:
//   std::vector<json> records = read_records(in);
//   Envelope env = local_envelope(records[i], i + 1, server_now);
//   nlohmann::ordered_json out = wrap_response(env, plan_response);
//
// Phụ thuộc: api (json, Minutes, parse_datetime, format_datetime).
// ============================================================================
#pragma once

#include <istream>
#include <string>
#include <vector>

#include "ktv/api.hpp"

namespace ktv {

// Thông tin tương quan suy ra từ record (hoặc sinh nếu thiếu).
struct Envelope {
    std::string message_id;
    std::string trigger;
    Minutes planned_at = 0;
};

// Đọc toàn bộ stream: ưu tiên parse cả file thành một JSON object (pretty-printed);
// không được thì coi là JSONL, mỗi dòng một record (dòng JSON hỏng giữ dạng discarded để báo 400).
std::vector<json> read_records(std::istream& in);

// Suy envelope từ record; index dùng để sinh message_id khi thiếu; default_planned_at khi không có planned_at.
Envelope local_envelope(const json& record, long long index, Minutes default_planned_at);

// Gói response nghiệp vụ vào envelope OUT prototype, giữ thứ tự field.
nlohmann::ordered_json wrap_response(const Envelope& envelope, const nlohmann::ordered_json& response);

}  // namespace ktv
