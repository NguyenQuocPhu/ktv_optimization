// ============================================================================
// adapter/file — ĐỌC INPUT TỪ FILE/STDIN (object pretty hoặc JSONL)
// ============================================================================
// Hiểu nhanh:
//   Transport local trước khi có Kafka. Đọc cả file thành một JSON object; nếu không được
//   thì coi là JSONL. Envelope staging thiếu thì tự sinh:
//   message_id = "local-<số thứ tự>", trigger = DAY_START, planned_at = giờ truyền vào.
//
// Dùng thế nào:
//   std::vector<json> records = read_records(in);
//   Envelope env = local_envelope(records[i], i + 1, server_now);
//
// Phụ thuộc: adapter/envelope (Envelope), api.
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

// Suy envelope từ record; index dùng để sinh message_id khi thiếu; default_planned_at khi không có planned_at.
Envelope local_envelope(const json& record, long long index, Minutes default_planned_at);

}  // namespace ktv
