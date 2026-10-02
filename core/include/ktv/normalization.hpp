// ============================================================================
// normalization — LỌC ĐIỀU KIỆN XẾP TUYẾN: từ Message thô ra danh sách việc được xếp
// ============================================================================
// Hiểu nhanh:
//   Parser (`api`) chỉ đọc hợp đồng. Module này quyết định việc nào *được* đưa vào thuật toán, theo thứ tự:
//     1. staff.status = 3 hoặc không rõ   → không xếp gì (422)
//     2. task_id trùng staff.current_task → việc đang làm: bổ sung dữ liệu, KHÔNG thành stop
//     3. (nhóm, task_status_id) tra bảng sheet 05 (`find_status`, cùng mã khác nghĩa theo nhóm):
//          xếp → tiếp bước 4 · không xếp (xong/hủy/theo dõi) → bỏ
//          chưa phân công → bỏ + cảnh báo TASK_STATUS_UNASSIGNED
//          "đang làm" mà không trùng current_task → bỏ + cảnh báo CURRENT_NOT_MATCHED
//          KHÔNG có trong bảng (hoa_don/onsite, mã lạ) → đọc task_status_name: tên mang nghĩa xong/hủy
//            ("Đã hủy", "Hoàn tất", "Đóng checklist"...) → bỏ + TASK_STATUS_UNKNOWN_CLOSED; còn lại (kể cả
//            tên rỗng) coi là còn mở → xếp + TASK_STATUS_UNKNOWN
//     4. thiếu tọa độ          → loại (chưa tính được đường)
//   complete_date KHÔNG loại task: workbook (3) đổi nghĩa thành "ngày hoàn tất ca vụ trước đó (ngày thu bill trước)".
//   Giống "lọc hồ sơ trước khi giao việc": chỉ giữ hồ sơ còn mở, đủ dữ liệu, không trùng.
//
// Dùng thế nào:
//   NormalizedWorklist w = normalize_worklist(message);
//   for (const Task* task : w.candidates) ...   // đưa vào bước dựng Problem/DP
//   w.warnings                                  // cảnh báo trạng thái (log + /healthz, không vào OUT)
//
// Trong file này có:
//   NormalizationStats, NormalizedWorklist – kết quả lọc + đếm lý do bị loại
//   normalize_worklist                     – HÀM CHÍNH
//   status_name_closed                     – tên trạng thái có mang nghĩa đã xong / đã hủy không
//
// Phụ thuộc: api (kiểu Message/Task, bảng trạng thái). KHÔNG phụ thuộc travel, dp, rules hay transport.
// ============================================================================
#pragma once

#include <optional>
#include <vector>

#include "ktv/api.hpp"

namespace ktv {

inline constexpr int kStaffOff = 3;

// Thống kê để chẩn đoán vì sao task bị loại. Không dùng cho thuật toán.
struct NormalizationStats {
    int tasks = 0;                      // Số task nhận vào.
    int candidates = 0;                 // Số task được xếp tuyến.
    int excluded_status = 0;            // bị bỏ theo trạng thái (không xếp, chưa phân công, đang làm không khớp, tên đã xong).
    int excluded_missing_location = 0;  // thiếu tọa độ.
    int excluded_current = 0;           // row trùng current_task (không tính là stop).
};

struct NormalizedWorklist {
    std::vector<const Task*> candidates;  // Con trỏ vào Message, giữ nguyên thứ tự message.tasks.
    std::optional<Task> current_task;     // Việc đang làm; đầy đủ nếu có row khớp, tối thiểu nếu không.
    bool staff_off = false;               // staff.status == 3, hoặc trạng thái không rõ (kStaffStatusUnknown).
    NormalizationStats stats;
    std::vector<Error> warnings;          // Mã TASK_STATUS_* / CURRENT_NOT_MATCHED, xem docs/DATA_QUESTIONS.md.
};

NormalizedWorklist normalize_worklist(const Message& message);

// "Đã hủy", "Huỷ thi công", "Đã xử lý hoàn tất", "Đóng checklist", "da_huy" → true. "Chưa hoàn tất",
// "Đang di chuyển", "" → false. Bỏ dấu + chữ thường trước khi so; có "chưa" thì không tính là xong. [GIẢ ĐỊNH]
bool status_name_closed(const std::string& name);

}  // namespace ktv
