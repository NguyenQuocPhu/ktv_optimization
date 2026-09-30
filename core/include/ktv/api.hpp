// ============================================================================
// api — ĐỌC MESSAGE: JSON input của API Gợi ý công việc → struct C++
// ============================================================================
// Hiểu nhanh:
//   Cửa vào của lõi. Nhận chuỗi JSON (đúng file API-Goi-y-cong-viec.xlsx, sheet 01/02),
//   kiểm tra từng field, trả về struct `Message` để các module sau dùng. Sai thì ghi lỗi.
//   Giống "form nhập liệu": nhận giấy tờ, soát từng ô, ô nào sai thì khoanh đỏ.
//
//   Hai chế độ:
//     strict   (không truyền warnings) – lệch hợp đồng ở đâu cũng là lỗi → 400. Dùng cho `validate`, test.
//     nới lỏng (truyền warnings)      – chỉ lỗi khi KHÔNG xếp được tuyến (staff hỏng, JSON sai kiểu gốc).
//              Lệch mà vẫn xếp được → cảnh báo có mã (field lạ, role lạ, lệch/ngoài danh mục...);
//              một task hỏng → bỏ riêng task đó (TASK_DROPPED). Dùng cho worker/gateway/`plan`.
//              Mã cảnh báo + câu hỏi cho team data: docs/DATA_QUESTIONS.md.
//
// Dùng thế nào:
//   std::vector<Error> errors, warnings;
//   Message m = parse_message(json::parse(line), errors, &warnings);   // bỏ &warnings = strict
//   if (!errors.empty()) → trả 400;   else → m.staff, m.tasks dùng tiếp ở plan (warnings ghi log)
//
// Trong file này có:
//   Minutes              – kiểu giờ: số phút kể từ 1970 (giờ VN). Mọi mốc giờ đổi về đây
//   Point, Plot, CurrentTask, Staff, Task, Message
//                        – ánh xạ 1-1 với JSON: Message = {staff, tasks}; Staff = khối "staff"...
//   Error                – một lỗi / cảnh báo dữ liệu: đường dẫn field + mô tả (+ mã nếu là cảnh báo)
//   OnTime, TaskKind     – một dòng bảng loại việc (sheet 05): SLA, ưu tiên, định mức thời gian
//   task_kinds, find_kind – lấy bảng loại việc / tìm một loại theo (nhóm, tên)
//   kind_or_default      – như find_kind, nhưng loại ngoài danh mục thì trả loại mặc định
//   parse_message        – HÀM CHÍNH: JSON → Message (+ danh sách lỗi, + cảnh báo nếu nới lỏng)
//   parse_datetime, format_datetime – "2026-09-10 14:00:00" ↔ Minutes
//
// Ẩn trong api.cpp: bảng loại việc cụ thể, cách đọc tọa độ / khung giờ, đổi ngày ↔ số ngày.
// Phụ thuộc: không module nào (chỉ thư viện JSON).
// ============================================================================
#pragma once

#include <optional>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

namespace ktv {

using json = nlohmann::json;
using Minutes = long long;  // Phút kể từ 1970-01-01 00:00 giờ VN. VD 14:00 ngày 10/09/2026 = một số nguyên lớn.

// Tọa độ "21.0192,105.7938" → {lat 21.0192, lng 105.7938}.
struct Point {
    double lat = 0, lng = 0;
};

// Một địa bàn (lô) của KTV: staff.plots[i].
struct Plot {
    int id = 0;
    std::string name;
    int role = 0;  // 1 = lô chính KTV phụ trách, 2 = lô sang hỗ trợ.
    int block_id = 0;
};

// Việc KTV đang làm dở: staff.current_task (khóa ở đầu tuyến).
struct CurrentTask {
    long long task_id = 0;
    int task_status_id = 0;
    int task_type_id = 0;
};

// Khối "staff": KTV đang ở đâu, làm giờ nào, phụ trách lô nào.
struct Staff {
    std::string staff_id, staff_account, staff_location;
    Point latlng;                                 // Vị trí hiện tại = điểm xuất phát.
    std::vector<std::pair<int, int>> available;   // "08:00-17:30,17:30-21:00" → {(480,1050), (1050,1260)}: phút trong ngày.
    std::vector<Plot> plots;
    std::optional<CurrentTask> current_task;      // Không có = null.
    int status = 0;                               // 1 rảnh, 2 bận, 3 off. 0 = payload không gửi. Chưa dùng để lọc ở phase này.
};

// Một việc trong tasks.<nhóm>[i].
struct Task {
    long long task_id = 0;
    int task_group_id = 0, task_type_id = 0, task_sub_id = 0, task_status_id = 0;
    std::string task_group_name, task_type_name, task_sub_name, task_status_name;
    std::optional<int> sla_minutes;      // Hẹn A → hạn check-in B = A + sla_minutes. Không có = hạn không tính theo phút.
    int priority_in_day = 0;             // 1 gấp nhất … 4.
    std::optional<Minutes> appointment;  // Mốc hẹn A. Không có = khách không hẹn.
    std::optional<Minutes> create_date;  // Ngày phát sinh ca vụ (workbook mới). Không có = chưa gửi.
    std::optional<Minutes> complete_date;// Ngày hoàn thành. Không có = chưa hoàn thành.
    std::string location;                // Địa chỉ, chỉ để hiển thị.
    std::optional<Point> latlng;         // Không có = thiếu tọa độ → bị loại khỏi tuyến.
    std::optional<int> handle_minutes;   // Không có / 0 = dùng định mức theo loại việc.
    int task_plots_id = 0, staff_plots_id = 0, staff_role = 0, block_id = 0;
    std::optional<long long> contract_id;  // ObjID hợp đồng. Chỉ để truy vết.
    std::string contract_no;               // Số hợp đồng. Chỉ để truy vết.
};

// Cả message = input của một lần gọi AI cho một KTV.
struct Message {
    std::string message_id, trigger;  // Vỏ Kafka (đề xuất), không bắt buộc.
    std::optional<Minutes> planned_at;  // Thời điểm tính tuyến; không có thì lấy giờ server.
    Staff staff;
    std::vector<Task> tasks;            // Gộp 5 nhóm, thứ tự trien_khai → bao_tri → thu_hoi → hoa_don → onsite.
};

// Một lỗi dữ liệu. VD {"tasks.bao_tri[0].latlng", "cần \"lat,lng\" trong Việt Nam"}.
// Cảnh báo (chế độ nới lỏng) có thêm mã, VD {"tasks.bao_tri[0].staff_role", "cần 1, 2 hoặc 3", "STAFF_ROLE"}.
struct Error {
    std::string path, problem;
    std::string code{};  // Rỗng với lỗi; mã cảnh báo (docs/DATA_QUESTIONS.md) với cảnh báo.
};

// Cột "Yêu cầu đúng hẹn" của sheet 05: tính trễ theo giờ check-in hay theo ngày làm xong.
enum class OnTime {
    CheckinBeforeB,          // Check-in trước mốc hẹn cuối B.
    DoneSameAppointmentDay,  // Làm xong trong ngày hẹn.
    DoneSameCreatedDay,      // Làm xong trong ngày tạo phiếu.
    DoneWithinMonth,         // Làm xong trong tháng.
};

// Một dòng bảng loại việc (sheet 05). VD {"bao_tri", 1, "bao_tri_vat_ly", 60 phút, CheckinBeforeB, P1, 60 phút}.
struct TaskKind {
    const char* group;
    int type_id;
    const char* name;
    std::optional<int> sla_minutes;
    OnTime on_time;
    int priority;
    int handle_minutes;  // Định mức thời gian xử lý khi input để trống. [GIẢ ĐỊNH]
};
const std::vector<TaskKind>& task_kinds();                                     // Cả bảng (12 loại).
const TaskKind* find_kind(const std::string& group, const std::string& name);  // Không thấy → nullptr.
// Loại ngoài danh mục (chỉ lọt qua ở chế độ nới lỏng) → loại mặc định: không hạn theo loại (hạn chỉ
// theo hẹn + SLA của input), xử lý 60 phút. [GIẢ ĐỊNH, xem docs/DATA_QUESTIONS.md UNKNOWN_TASK_TYPE]
const TaskKind& kind_or_default(const std::string& group, const std::string& name);

inline constexpr const char* kGroups[] = {"trien_khai", "bao_tri", "thu_hoi", "hoa_don", "onsite"};  // 5 khóa của tasks.

// HÀM CHÍNH. Đọc + kiểm tra theo file API; lỗi ghi vào errors (rỗng = xếp được).
// warnings = nullptr: strict. Có: nới lỏng, lệch hợp đồng mà vẫn xếp được thì ghi vào *warnings (có mã).
Message parse_message(const json& data, std::vector<Error>& errors, std::vector<Error>* warnings = nullptr);

// Khóa gom cảnh báo cùng loại để đếm (bỏ chỉ số mảng). VD "STAFF_ROLE tasks.bao_tri[].staff_role — cần 1, 2 hoặc 3 (đang là 0)".
std::string issue_key(const Error& warning);

std::optional<Minutes> parse_datetime(const std::string& text);  // "2026-09-10 14:00:00" → Minutes; sai dạng → không có.
std::string format_datetime(Minutes value);                      // Minutes → "2026-09-10 14:00:00".

}  // namespace ktv
