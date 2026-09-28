// ============================================================================
// sla — HẠN VÀ DỰ BÁO ĐÚNG HẸN của một việc
// ============================================================================
// Hiểu nhanh:
//   Từ loại việc (sheet 05) + giờ hẹn + ngày tạo phiếu, tính ra các mốc:
//     opens       – mốc hẹn đầu A (tới sớm thì chờ)
//     due         – hạn check-in B = A + sla_minutes
//     complete_by – hạn hoàn tất (cuối ngày hẹn / ngày tạo phiếu / trong tháng)
//   Rồi dự báo projected_sla: ON_TIME / AT_RISK / WILL_BREACH / ALREADY_BREACHED.
//
// Quy tắc "trong ngày tạo phiếu" và "trong tháng" lấy theo create_date nếu có, không thì planned_at.
// Module này KHÔNG đổi cách QHĐ chọn thứ tự; chỉ cung cấp số cho bước dựng Problem và output.
//
// Phụ thuộc: api (Task/TaskKind/OnTime), rules (ngưỡng AT_RISK).
// ============================================================================
#pragma once

#include <optional>

#include "ktv/api.hpp"
#include "ktv/rules.hpp"

namespace ktv {

// Mốc hạn tính bằng phút tuyệt đối (từ 1970, giờ VN). Không có = không tính.
struct Deadlines {
    std::optional<Minutes> opens, due, complete_by;
};

inline Minutes start_of_day(Minutes t) { return t - ((t % 1440) + 1440) % 1440; }

// Hạn của một việc theo loại việc (sheet 05), giờ hẹn và ngày tạo phiếu.
Deadlines resolve_deadlines(const Task& task, const TaskKind& kind, Minutes planned_at);

// Dự báo đúng hẹn của một điểm dừng. `checkin`/`done`/`due`/`complete_by`/`service` tính bằng phút
// tương đối so với lúc xuất phát (như Problem của dp); `now` cũng tương đối. NaN = không có.
const char* projected_sla(double checkin, double done, double due, double complete_by, double service, double now,
                          const Rules& rules);

}  // namespace ktv
