// ============================================================================
// adapter/envelope — VỎ TƯƠNG QUAN CỦA OUT RESPONSE
// ============================================================================
// Hiểu nhanh:
//   Dùng chung cho mọi transport: file adapter, sau này Kafka worker.
//   Envelope giữ `message_id/trigger/planned_at`; wrap_response gói response nghiệp vụ
//   vào OUT prototype (thêm message_id/run_code/trigger/planned_at/schema_version).
//
// Dùng thế nào:
//   Envelope env{"local-1", "DAY_START", now};
//   ordered_json out = wrap_response(env, plan_response);
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

// Gói response nghiệp vụ vào envelope OUT prototype, giữ thứ tự field.
nlohmann::ordered_json wrap_response(const Envelope& envelope, const nlohmann::ordered_json& response);

}  // namespace ktv
