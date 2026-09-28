// ============================================================================
// rules — RULE NGHIỆP VỤ: tuyến nào tốt hơn tuyến nào
// ============================================================================
// Hiểu nhanh:
//   "Hàm so sánh nhãn" mình đã thống nhất. Mỗi tuyến được chấm một bảng điểm phạt theo tầng:
//     tầng 1: trễ hẹn check-in       → so tầng này trước
//     tầng 2: làm xong quá hạn, quá ca → chỉ xét khi tầng 1 bằng nhau
//     tầng 3: km, phút đi, quay lại lô… cộng có trọng số → chỉ xét khi tầng 1, 2 bằng nhau
//   Giống xếp hạng bảng đấu: so điểm trước, bằng điểm mới so hiệu số, rồi mới so bàn thắng.
//   Muốn đổi nghiệp vụ → đổi tầng / trọng số ở đây, KHÔNG phải sửa thuật toán.
//
// Dùng thế nào:
//   Rules r = default_rules();              // bộ mặc định (viết trong rules.cpp)
//   Rules r = load_rules("rules.json");     // hoặc đọc từ file, field thiếu lấy mặc định
//   ktv_core print-rules > rules.json       // in bộ đang dùng ra file để sửa
//
// Trong file này có:
//   Rule, kRuleCodes – 9 rule mềm và tên chữ của chúng (dùng trong file JSON)
//   Rules            – toàn bộ cấu hình: các tầng, trọng số ưu tiên P1–P4, ngưỡng, giả định
//   default_rules, load_rules, rules_from_json, rules_to_json – tạo / đọc / ghi Rules
//
// Ẩn trong rules.cpp: giá trị mặc định cụ thể, kiểm tra file JSON hợp lệ.
// Phụ thuộc: không module nào.
// ============================================================================
#pragma once

#include <string>
#include <utility>
#include <vector>

#include <nlohmann/json.hpp>

namespace ktv {

// 9 rule mềm. Với mỗi bước đi tới một việc, dp::visit() tính ra một con số cho từng rule.
enum Rule {
    LATE_CHECKIN,     // Check-in sau hạn B: cộng trọng số ưu tiên của việc (P1 = 4 … P4 = 1).
    LATE_COMPLETION,  // Làm xong sau hạn hoàn tất: cộng 1.
    AFTER_SHIFT,      // Làm xong sau giờ hết ca: cộng 1.
    LATE_MINUTES,     // Số phút check-in trễ.
    KM,               // Quãng đường.
    TRAVEL_MINUTES,   // Phút di chuyển.
    AREA_REENTRY,     // Quay lại một lô đã rời: cộng 1.
    PRIORITY_DELAY,   // Trọng số ưu tiên × giờ check-in: việc gấp nên làm sớm.
    FINISH,           // Giờ xong việc cuối (phút).
    RULE_COUNT        // Không phải rule: số lượng rule.
};
inline constexpr const char* kRuleCodes[RULE_COUNT] = {
    "LATE_CHECKIN", "LATE_COMPLETION", "AFTER_SHIFT", "LATE_MINUTES", "KM",
    "TRAVEL_MINUTES", "AREA_REENTRY", "PRIORITY_DELAY", "FINISH"};

inline constexpr int kMaxTiers = 4;  // Tối đa 4 tầng.

// Toàn bộ cấu hình routing. VD tiers = { {LATE_CHECKIN:1}, {LATE_COMPLETION:1, AFTER_SHIFT:1}, {KM:1, ...} }.
struct Rules {
    std::vector<std::vector<std::pair<Rule, double>>> tiers;  // Mỗi tầng: danh sách (rule, trọng số). Tầng 1 trước.
    double priority_weight[5] = {1, 4, 3, 2, 1};  // [0] = việc không có ưu tiên; [1..4] = P1..P4.
    int max_exact_tasks = 12;      // Tới ngần này việc thì QHĐ ra thứ tự tốt nhất; nhiều hơn dùng tham lam (12 việc: chậm nhất ~0,1 s).
    int max_labels = 32;           // Mỗi trạng thái QHĐ giữ tối đa ngần này nhãn; vượt thì kết quả gần đúng.
    double current_task_minutes = 30;  // Việc đang làm còn bao lâu nữa xong. [GIẢ ĐỊNH]
    double average_speed_kmh = 30;     // Đổi km chim bay ra phút khi không dùng OSRM.
    double at_risk_minutes = 20;       // projected_sla = AT_RISK khi còn dư dưới ngần này phút...
    double at_risk_ratio = 0.2;        // ...hoặc dưới tỷ lệ này của thời gian xử lý.
    // Nghỉ trưa (bắt buộc): phải BẮT ĐẦU nghỉ trong [break_start, break_end − break_minutes]. [GIẢ ĐỊNH]
    int break_start = 11 * 60 + 30;    // 11:30, phút trong ngày.
    int break_end = 13 * 60 + 30;      // 13:30.
    double break_minutes = 45;         // 0 = không có nghỉ trưa.
};

Rules default_rules();                              // Bộ mặc định.
Rules rules_from_json(const nlohmann::json& data);  // Field thiếu lấy mặc định; sai thì ném std::runtime_error.
Rules load_rules(const std::string& path);          // Đọc file JSON.
nlohmann::json rules_to_json(const Rules& rules);   // Ghi ra JSON (lệnh print-rules).

}  // namespace ktv
