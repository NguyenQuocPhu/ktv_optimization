// ============================================================================
// plan — MỘT LẦN GỌI AI: message API → response API (nối các module lại)
// ============================================================================
// Hiểu nhanh:
//   Người "điều phối" của lõi. Không tự tính thứ tự, chỉ gọi đúng module theo đúng thứ tự:
//
//     Message ─► bỏ việc thiếu tọa độ, tính hạn từng việc (A, B, hạn hoàn tất)
//             ─► travel: bảng km/phút ─► dp: Problem → solve() (thứ tự + từng bước, có nghỉ trưa)
//             ─► dòng lịch TASK / IDLE / BREAK, projected_sla, cụm, metrics ─► response JSON
//
//   Giống quản đốc: nhận phiếu (api), hỏi đường (travel), nhờ người xếp lịch (dp),
//   rồi viết lại thành bảng lịch cho KTV (response đúng sheet 03, 04 file API).
//
// Dùng thế nào:
//   PlanResult r = plan(message, rules, now, "http://127.0.0.1:5000");   // "" = chim bay
//   r.response  → JSON trả về (success, statuscode, data.clusters, data.metrics)
//
// Trong file này có:
//   PlanResult     – response + vài con số thống kê
//   plan           – HÀM CHÍNH của cả lõi
//   error_response – response lỗi (400 sai dữ liệu, 422 không có việc)
//
// Ẩn trong plan.cpp: tính hạn theo loại việc, projected_sla, dựng dòng lịch / cụm / metrics.
// Phụ thuộc: api, rules, travel, dp — tất cả.
// ============================================================================
#pragma once

#include "ktv/api.hpp"
#include "ktv/dp.hpp"
#include "ktv/normalization.hpp"
#include "ktv/rules.hpp"

namespace ktv {

struct PlanResult {
    nlohmann::ordered_json response;    // Response JSON, giữ đúng thứ tự field của file API.
    Source source = Source::Optimal;    // Thứ tự do QHĐ tốt nhất / gần đúng / tham lam.
    int routed = 0;                     // Số việc được xếp.
    int excluded = 0;                   // Số việc bị loại (thiếu tọa độ).
    const char* travel = "HAVERSINE";   // OSRM, HAVERSINE, hoặc ESTIMATED (OSRM lỗi → chim bay × 1,3).
    std::vector<Error> warnings;        // Cảnh báo của bước lọc (trạng thái task). Log + /healthz, không vào OUT.
    NormalizationStats stats;           // Đếm task theo lý do bị loại (log: vì sao 422 "không có việc").
};

// HÀM CHÍNH. server_now: giờ VN hiện tại, dùng khi message không có planned_at và cho server_time.
// osrm_url rỗng: chim bay. Có: gọi OSRM; lỗi thì chim bay × 1,3 và trả mã 424 (vẫn có tuyến).
PlanResult plan(const Message& message, const Rules& rules, Minutes server_now, const std::string& osrm_url = "");

// Response lỗi (sheet 07): success = false, data = null.
nlohmann::ordered_json error_response(const std::string& statuscode, const std::string& text,
                                      const std::string& trace_id, Minutes server_now);

}  // namespace ktv
