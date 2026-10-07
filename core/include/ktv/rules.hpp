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
//   mix_tiers_from   – 7.26: sinh bộ tầng mode Kết nối từ tỷ trọng SLA + tỷ giá ca trễ ↔ km
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
    DEADLINE_URGENCY, // 7.16.2: độ gấp (K − ngày làm việc còn lại) × giờ check-in — chỉ ca chèn (ca chính = 0).
    SKIP_OPTIONAL,    // 7.24: mỗi ca TUỲ CHỌN không làm cộng 1 (× trọng số = ngưỡng "đáng đi thêm", km tương đương).
    RULE_COUNT        // Không phải rule: số lượng rule.
};
inline constexpr const char* kRuleCodes[RULE_COUNT] = {
    "LATE_CHECKIN", "LATE_COMPLETION", "AFTER_SHIFT", "LATE_MINUTES", "KM",
    "TRAVEL_MINUTES", "AREA_REENTRY", "PRIORITY_DELAY", "FINISH", "DEADLINE_URGENCY", "SKIP_OPTIONAL"};

inline constexpr int kMaxTiers = 4;  // Tối đa 4 tầng.

// Toàn bộ cấu hình routing. VD tiers = { {LATE_CHECKIN:1}, {LATE_COMPLETION:1, AFTER_SHIFT:1}, {KM:1, ...} }.
using Tiers = std::vector<std::vector<std::pair<Rule, double>>>;  // Mỗi tầng: danh sách (rule, trọng số). Tầng 1 trước.

struct Rules {
    Tiers tiers;        // Mode SLA (priority_type 1).
    Tiers route_tiers;  // Mode Tuyến 100% (priority_type 2): km + trễ hẹn trọng số nhỏ ở tầng 1, SLA còn lại xuống dưới (7.26; 7.23 từng là R2).
    // 7.26 — mode Kết nối (priority_type 0, mặc định): mix_sla_share SLA + phần còn lại Tuyến, gom chung tầng 1.
    // Mặc định sinh từ 2 tham số dưới (mix_tiers_from); rules.json có "mix_tiers" thì dùng nguyên văn.
    Tiers mix_tiers;
    double mix_sla_share = 0.7;  // Tỷ trọng SLA (0 ≤ x < 1): 0,7 = 70% SLA · 30% Tuyến.
    double mix_breach_km = 5;    // Q: 1 ca trễ hạn / quá giờ ≈ Q km khi trộn 50/50. [GIẢ ĐỊNH, chọn theo bảng quét 7.26]
    double priority_weight[5] = {1, 4, 3, 2, 1};  // [0] = việc không có ưu tiên; [1..4] = P1..P4.
    int max_exact_tasks = 12;      // Tới ngần này việc thì QHĐ ra thứ tự tốt nhất; nhiều hơn dùng tham lam (12 việc: chậm nhất ~0,1 s).
    int max_labels = 32;           // Mỗi trạng thái QHĐ giữ tối đa ngần này nhãn; vượt thì kết quả gần đúng.
    int k_month_days = 5;          // 7.16.1: ca "hoàn tất trong tháng" còn hơn ngần này ngày làm việc thì không xếp (K theo catalogue). [GIẢ ĐỊNH]
    // 7.16.3 — gộp điểm dừng cùng địa chỉ (catalogue mục C; workbook không có mã nhóm → AI tự gom theo địa chỉ).
    double stop_group_radius_m = 50;       // Bán kính coi là cùng địa chỉ (tham số cấu hình catalogue, sheet 1).
    int stop_group_max_wait_minutes = 30;  // Ca trong nhóm phải chờ quá ngần này thì tách nhóm. [GIẢ ĐỊNH của repo]
    double current_task_minutes = 30;  // Việc đang làm còn bao lâu nữa xong, khi không tra được định mức (7.22: còn ½ định mức). [GIẢ ĐỊNH]
    double average_speed_kmh = 30;     // Đổi km chim bay ra phút khi không dùng OSRM.
    double at_risk_minutes = 20;       // projected_sla = AT_RISK khi còn dư dưới ngần này phút...
    double at_risk_ratio = 0.2;        // ...hoặc dưới tỷ lệ này của thời gian xử lý.
    // Nghỉ trưa (bắt buộc): phải BẮT ĐẦU nghỉ trong [break_start, break_end − break_minutes]. [GIẢ ĐỊNH]
    int break_start = 11 * 60 + 30;    // 11:30, phút trong ngày.
    int break_end = 13 * 60 + 30;      // 13:30.
    double break_minutes = 45;         // 0 = không có nghỉ trưa.
    // 7.20.1b — worker/gateway trả OUT cache khi IN không đổi nội dung (change_id "no"). Chỉ dùng khi có Redis.
    int cache_max_age_minutes = 30;                  // Trần tuổi bản cache (giờ chạy mới − giờ tính bản cache). 0 = tắt cache.
    std::vector<std::string> force_recompute_triggers;  // Trigger luôn tính lại dù IN không đổi. Mặc định rỗng.
};

Rules default_rules();                              // Bộ mặc định.
// 7.26: bộ tầng Kết nối. Tầng 1 = km + LATE_CHECKIN / LATE_COMPLETION / AFTER_SHIFT trọng số share/(1 − share) × breach_km
// (70/30, Q = 5 → tránh 1 ca trễ nếu đi thêm < 11,7 km); tầng 2 phần mềm còn lại (phút trễ, ưu tiên, độ gấp, giờ xong).
Tiers mix_tiers_from(double sla_share, double breach_km);
Rules rules_from_json(const nlohmann::json& data);  // Field thiếu lấy mặc định; sai thì ném std::runtime_error.
Rules load_rules(const std::string& path);          // Đọc file JSON.
nlohmann::json rules_to_json(const Rules& rules);   // Ghi ra JSON (lệnh print-rules).

}  // namespace ktv
