// ============================================================================
// adapter/file — ĐỌC INPUT TỪ FILE/STDIN (object pretty hoặc JSONL)
// ============================================================================
// Hiểu nhanh:
//   Transport local trước khi có Kafka. Đọc cả file thành một JSON object; nếu không được
//   thì coi là JSONL. Envelope staging thiếu thì tự sinh:
//   message_id = "local-<số thứ tự>", trigger = DAY_START, planned_at = giờ truyền vào.
//
// Dùng thế nào (CLI và Kafka worker đi cùng một đường):
//   std::vector<json> records = read_records(in);
//   Envelope env = local_envelope(records[i], "local-" + std::to_string(i + 1), server_now);
//   Message m = parse_record(records[i], env, errors, &warnings);
//   if (!errors.empty()) response = bad_request(errors, env.message_id, server_now);
//   else                 response = plan(m, rules, server_now, osrm).response;
//
// Phụ thuộc: adapter/envelope (Envelope), api, plan (error_response).
// ============================================================================
#pragma once

#include <istream>
#include <vector>

#include "ktv/adapter/envelope.hpp"
#include "ktv/api.hpp"

namespace ktv {

// Đọc toàn bộ stream: ưu tiên parse cả file thành một JSON object (pretty-printed);
// không được thì coi là JSONL, mỗi dòng một record (dòng JSON hỏng giữ dạng discarded).
std::vector<json> read_records(std::istream& in);

// Suy envelope từ record. Thiếu message_id → fallback_id (CLI: "local-<n>"; Kafka: "topic-partition-offset"
// để không trùng giữa các lần chạy/replica). Thiếu planned_at → default_planned_at.
Envelope local_envelope(const json& record, const std::string& fallback_id, Minutes default_planned_at);

// Record (có thể là JSON hỏng) → Message gắn message_id/planned_at của envelope. Lỗi hợp đồng vào errors;
// warnings = nullptr: strict, có: nới lỏng (xem parse_message).
Message parse_record(const json& record, const Envelope& envelope, std::vector<Error>& errors,
                     std::vector<Error>* warnings = nullptr);

// Response 400 dùng chung: "Sai định dạng tham số:" + tối đa 3 lỗi đầu (đường dẫn field + mô tả).
nlohmann::ordered_json bad_request(const std::vector<Error>& errors, const std::string& trace_id, Minutes now);

}  // namespace ktv
