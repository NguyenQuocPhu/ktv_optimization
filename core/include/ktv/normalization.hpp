// ============================================================================
// normalization — LỌC ĐIỀU KIỆN XẾP TUYẾN: từ Message thô ra danh sách việc được xếp
// ============================================================================
// Hiểu nhanh:
//   Parser (`api`) chỉ đọc hợp đồng. Module này quyết định việc nào *được* đưa vào thuật toán:
//     - status 6 (check_in)  → ứng viên xếp tuyến
//     - status 10 trùng current_task → việc đang làm, bổ sung dữ liệu, KHÔNG thành stop
//     - status khác 6/10     → bị loại (coi như không cần xếp)
//     - complete_date có giá trị → đã hoàn thành, loại
//     - thiếu tọa độ          → loại (chưa tính được đường)
//     - staff.status = 3      → KTV off, không sinh tuyến
//   Giống "lọc hồ sơ trước khi giao việc": chỉ giữ hồ sơ còn mở, đủ dữ liệu, không trùng.
//
// Dùng thế nào:
//   NormalizedWorklist w = normalize_worklist(message);
//   for (const Task* task : w.candidates) ...   // đưa vào bước dựng Problem/DP
//
// Phụ thuộc: api (kiểu Message/Task). KHÔNG phụ thuộc travel, dp, rules hay transport.
// ============================================================================
#pragma once

#include <optional>
#include <vector>

#include "ktv/api.hpp"

namespace ktv {

// Status nghiệp vụ đang hỗ trợ (theo quyết định prototype): 6 = việc chờ, 10 = việc đang làm.
inline constexpr int kStatusRoutable = 6;
inline constexpr int kStatusCurrent = 10;
inline constexpr int kStaffOff = 3;

// Thống kê để chẩn đoán vì sao task bị loại. Không dùng cho thuật toán.
struct NormalizationStats {
    int tasks = 0;                      // Số task nhận vào.
    int candidates = 0;                 // Số task được xếp tuyến.
    int excluded_status = 0;            // status không phải 6 (10 không khớp current, hoặc mã khác).
    int excluded_completed = 0;         // complete_date có giá trị.
    int excluded_missing_location = 0;  // thiếu tọa độ.
    int excluded_current = 0;           // row trùng current_task (không tính là stop).
};

struct NormalizedWorklist {
    std::vector<const Task*> candidates;  // Con trỏ vào Message, giữ nguyên thứ tự message.tasks.
    std::optional<Task> current_task;     // Việc đang làm; đầy đủ nếu có row khớp, tối thiểu nếu không.
    bool staff_off = false;               // staff.status == 3, hoặc trạng thái không rõ (kStaffStatusUnknown).
    NormalizationStats stats;
};

NormalizedWorklist normalize_worklist(const Message& message);

}  // namespace ktv
