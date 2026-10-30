// ============================================================================
// rules — RULE NGHIỆP VỤ: tuyến nào tốt hơn tuyến nào
// ============================================================================
// Hiểu nhanh:
//   "Hàm so sánh nhãn" mình đã thống nhất. Mỗi tuyến được chấm một bảng điểm phạt theo tầng:
//     tầng 1: trễ hẹn check-in       → so tầng này trước
//     tầng 2: làm xong quá hạn, quá ca, bỏ ca chèn → chỉ xét khi tầng 1 bằng nhau
//     tầng 3: đảo ưu tiên trong cùng khung hẹn (8.5a)
//     tầng 4: km, phút đi, quay lại lô… cộng có trọng số → chỉ xét khi các tầng trên bằng nhau
//   (Mode SLA. Mode Kết nối / Tuyến gom một số rule lên tầng 1 có trọng số — xem default_rules.)
//   Giống xếp hạng bảng đấu: so điểm trước, bằng điểm mới so hiệu số, rồi mới so bàn thắng.
//   Muốn đổi nghiệp vụ → đổi tầng / trọng số ở đây, KHÔNG phải sửa thuật toán.
//
// Dùng thế nào:
//   Rules r = default_rules();              // bộ mặc định (viết trong rules.cpp)
//   Rules r = load_rules("rules.json");     // hoặc đọc từ file, field thiếu lấy mặc định
//   ktv_core print-rules > rules.json       // in bộ đang dùng ra file để sửa
//
// Trong file này có:
//   Rule, kRuleCodes – các rule mềm và tên chữ của chúng (dùng trong file JSON)
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

// Các rule mềm. Với mỗi bước đi tới một việc, dp::visit() tính ra một con số cho từng rule.
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
    SKIP_OPTIONAL,    // 7.24: bỏ 1 ca TUỲ CHỌN. 8.7: trọng số = km tương đương / phút được phép đi thêm (plan nhân với
                      // số phút được phép của KTV: min(optional_max_detour_minutes, optional_spare_ratio × phút trống)).
    PRIORITY_ORDER,   // 8.5a: mỗi cặp ca có khung hẹn chồng nhau mà ca ưu tiên THẤP làm trước ca ưu tiên cao: cộng 1.
    SKIP_INSERTED,    // 8.5b: bỏ một ca CHÈN trong K (hết chỗ): cộng K + 1 − ngày làm việc còn lại (phần "gấp").
    SKIP_INSERTED_PRIORITY,  // 8.5b: bỏ ca chèn: cộng trọng số ưu tiên của ca (P2 3 … P4 1) — tách để mỗi mode chỉnh riêng.
    OVERTIME_MINUTES, // 8.18: phút tuyến xong muộn hơn mốc max(hết ca, giờ xong tuyến chỉ ca chính) — chỉ khi có ca chèn.
    OLD_WINDOW_MINUTES,  // 8.22: ca CHÍNH đã trễ (hoặc có hạn B hôm nay): số phút check-in hôm nay lệch ra ngoài giờ khung cũ (cùng giờ, ngày hôm nay).
    OVERDUE_ORDER,       // 8.22: mỗi cặp ca ĐÃ TRỄ có khung cũ chồng nhau (hoặc cùng không hẹn) mà ca trễ ít làm trước ca trễ nhiều: cộng 1.
    RULE_COUNT        // Không phải rule: số lượng rule.
};
inline constexpr const char* kRuleCodes[RULE_COUNT] = {
    "LATE_CHECKIN", "LATE_COMPLETION", "AFTER_SHIFT", "LATE_MINUTES", "KM",
    "TRAVEL_MINUTES", "AREA_REENTRY", "PRIORITY_DELAY", "FINISH", "DEADLINE_URGENCY", "SKIP_OPTIONAL",
    "PRIORITY_ORDER", "SKIP_INSERTED", "SKIP_INSERTED_PRIORITY", "OVERTIME_MINUTES", "OLD_WINDOW_MINUTES", "OVERDUE_ORDER"};

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
    // Phase 8 — bài > max_exact_tasks: "improve" = tham lam + or-opt/2-opt (như trước); "lns" = thêm phá – dựng lại;
    // "lns_window" = LNS + QHĐ cửa sổ trượt đánh bóng mỗi tuyến tốt nhất mới; "alns_dp" (8.1, thử nghiệm) = thêm QHĐ
    // làm bước đi trong vòng LNS + chọn cách thích nghi. Giới hạn theo vòng (không theo đồng hồ)
    // → cùng input cùng tuyến trên mọi máy.
    std::string large_method = "lns_window";  // Người dùng chốt 2026-10-08 theo bảng quét Phase 8.
    int lns_iterations = 1000;  // Số vòng phá – dựng lại (điểm gãy: 13–20 việc gần tối ưu, ≤ ~0,11 s).
    int window_size = 8;       // Số phần tử (việc + nghỉ trưa) QHĐ sắp lại mỗi cửa sổ.
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
    // 7.30 (PRD v1.4 BR-10): KTV dịch chuyển ≤ ngần này mét so với vị trí của lần tính đang cache → coi là input KHÔNG đổi
    // (dùng cache). Thay cho làm tròn tọa độ 4 chữ số (~11 m) của 7.20.1b. [GIẢ ĐỊNH: 50 m = bán kính gom cùng địa chỉ]
    double location_change_m = 50;
    // 8.7 (UAT case 14) ca tuỳ chọn: phút được phép đi thêm = min(optional_max_detour_minutes,
    // optional_spare_ratio × phút còn trống của tuyến ca bắt buộc) — rảnh nhiều chịu đi xa hơn, nhưng không quá trần
    // (case 15: rảnh mà ca xa 90′ thì không đi). Gate 1 gạt ca có phút đi thêm (chèn rẻ nhất) ≥ mức đó; gate 2 = QHĐ với
    // SKIP_OPTIONAL × mức đó. 0 = gạt hết. Tuyến có ca hẹn trễ → không chèn ca tuỳ chọn. [GIẢ ĐỊNH 20′ / 0,5]
    double optional_max_detour_minutes = 20;
    double optional_spare_ratio = 0.5;
    // 8.18: ca chèn / tuỳ chọn được làm thì CHÍNH NÓ phải xong ≤ giờ hết ca + ngần này phút (ràng buộc cứng; người dùng:
    // "chấp nhận quá giờ, nhưng chỉ một mức nhỏ" — 8.5b 15′, nâng 30′ 2026-10-27). Kéo ca khác lùi thì mềm (OVERTIME_MINUTES).
    // Không chèn được → DEFERRED NO_CAPACITY.
    double inserted_overtime_minutes = 30;
    // 8.22 — ca CHÍNH đã trễ coi như ca chèn: hết chỗ thì được bỏ (DEFERRED NO_CAPACITY) thay vì tràn ca, ca chưa trễ
    // không bị đẩy ra sau giờ ca. Phạt bỏ (đơn vị SKIP_INSERTED) = ca chèn hạn hôm nay (K + 1) + overdue_skip_bonus
    // + overdue_skip_per_day × ngày đã trễ (≤ overdue_skip_max_days) + overdue_skip_window × phần khung cũ còn kịp
    // (1 khi khung đặt sang hôm nay chưa qua, giảm về 0 khi đã qua overdue_skip_window_decay phút). Khung > ngày trễ
    // (4 > 0,5 × 7). Phần trễ đã xảy ra không tính lại (không thì bỏ ca trễ "tiết kiệm" được phạt trễ). Mode Tuyến tắt.
    // [GIẢ ĐỊNH, quét 40 bộ quá tải + 516 IN 2026-10-30]
    bool overdue_as_inserted = true;
    double overdue_skip_bonus = 1;
    double overdue_skip_per_day = 0.5;
    double overdue_skip_max_days = 7;
    double overdue_skip_window = 4;
    double overdue_skip_window_decay = 60;
    double old_window_cap_minutes = 240;  // OLD_WINDOW_MINUTES tính tối đa ngần này phút / ca; ca có khung bị bỏ = mức này
    std::vector<std::string> force_recompute_triggers;  // Trigger luôn tính lại dù IN không đổi. Mặc định rỗng.
    // 8.21: giờ trong ngày (phút) của hạn "trong ngày / trong tháng". Không đọc từ rules.json: plan() gán = giờ hết ca
    // của KTV (staff.available) cho từng lần tính.
    int day_end_minute = 1439;
};

Rules default_rules();                              // Bộ mặc định.
// 7.26: bộ tầng Kết nối. Tầng 1 = km + LATE_CHECKIN / LATE_COMPLETION / AFTER_SHIFT trọng số share/(1 − share) × breach_km
// (70/30, Q = 5 → tránh 1 ca trễ nếu đi thêm < 11,7 km); tầng 2 phần mềm còn lại (phút trễ, ưu tiên, độ gấp, giờ xong).
Tiers mix_tiers_from(double sla_share, double breach_km);
Rules rules_from_json(const nlohmann::json& data);  // Field thiếu lấy mặc định; sai thì ném std::runtime_error.
Rules load_rules(const std::string& path);          // Đọc file JSON.
nlohmann::json rules_to_json(const Rules& rules);   // Ghi ra JSON (lệnh print-rules).

}  // namespace ktv
