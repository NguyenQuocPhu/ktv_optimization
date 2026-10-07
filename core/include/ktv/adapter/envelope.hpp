// ============================================================================
// adapter/envelope — VỎ TƯƠNG QUAN CỦA OUT RESPONSE
// ============================================================================
// Hiểu nhanh:
//   Dùng chung cho mọi transport: file adapter, sau này Kafka worker.
//   Envelope giữ `message_id/trigger/planned_at`; wrap_response gói response nghiệp vụ
//   vào OUT prototype (thêm message_id/run_code/trigger/planned_at/schema_version).
//   7.20.1 (workbook (5) sheet 03): `data` là ARRAY 1 phần tử, có `change_id` ("yes" tính mới / "no" lấy cache)
//   + `trace_id` (mã lần tính đã cache khi "no"). reuse_response dựng OUT "no" từ bản đã cache.
//
// Dùng thế nào:
//   Envelope env{"local-1", "DAY_START", now};
//   ordered_json out = wrap_response(env, plan_response);          // data: [{staff_id, change_id:"yes", ...}]
//   ordered_json again = reuse_response(cached_out, env2, now);    // data[0].change_id = "no", trace_id = run_code cũ
//
// Phụ thuộc: api (json, Minutes, format_datetime).
// ============================================================================
#pragma once

#include <string>

#include <nlohmann/json.hpp>

#include "ktv/api.hpp"

namespace ktv {

// Thông tin tương quan của một lần tính.
struct Envelope {
    std::string message_id;
    std::string trigger;
    Minutes planned_at = 0;
    std::string run_code;  // Mã lần tính; rỗng = message_id. VD replan "m-123-r20261001092000".
};

// Gói response nghiệp vụ vào envelope OUT prototype, giữ thứ tự field. data object → [ {staff_id, change_id "yes",
// trace_id "", ...} ]; lỗi giữ data = null (sheet 07).
nlohmann::ordered_json wrap_response(const Envelope& envelope, const nlohmann::ordered_json& response);

// OUT trả lại từ cache cho IN mới (7.20.1b): vỏ ngoài theo envelope mới (message_id, run_code, trigger, planned_at,
// trace_id, server_time = now); data[0].change_id = "no", data[0].trace_id = run_code của lần TÍNH THẬT (bản cache
// đã là "no" thì giữ trace_id của nó). clusters/metrics giữ nguyên. Bản cache dạng cũ (data object) cũng nhận.
nlohmann::ordered_json reuse_response(const nlohmann::ordered_json& cached, const Envelope& envelope, Minutes now);

// data[0] của OUT đã gói (bản cũ data object cũng nhận); không có → null.
const nlohmann::ordered_json& out_data(const nlohmann::ordered_json& out);

}  // namespace ktv
