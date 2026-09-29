// ============================================================================
// gateway/seed — NẠP STORE TỪ FILE OUT (thay Kafka trước khi có broker)
// ============================================================================
// Hiểu nhanh:
//   Đọc luồng JSONL, mỗi dòng là một response OUT (đúng cái `ktv_core plan --out` sinh ra).
//   Lấy `data.staff_id` và ngày từ `planned_at`, nạp vào store.
//   Dòng hỏng / thiếu field bị bỏ qua, không ném lỗi.
//
// Dùng thế nào:
//   std::ifstream in("out.jsonl");
//   std::size_t loaded = load_routes(in, store);
//
// Phụ thuộc: store, thư viện JSON.
// ============================================================================
#pragma once

#include <cstddef>
#include <istream>

#include "ktv/gateway/store.hpp"

namespace ktv {

// Trả về số bản đã nạp. Dòng không phải object, thiếu `data.staff_id`, hoặc thiếu
// `planned_at` hợp lệ đều bị bỏ qua.
std::size_t load_routes(std::istream& in, RouteStore& store);

}  // namespace ktv
