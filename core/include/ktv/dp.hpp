// ============================================================================
// dp — QUY HOẠCH ĐỘNG: chọn thứ tự làm việc cho MỘT KTV
// ============================================================================
// Hiểu nhanh:
//   Đưa vào "bài toán số" (Problem), nhận lại thứ tự làm việc (Solution).
//   Module này KHÔNG biết JSON, tên việc hay giờ thật: chỉ có số, giờ tính bằng phút kể từ
//   lúc KTV xuất phát (0). plan.cpp lo đổi message ↔ số.
//
//   Cách làm: xét dần các tuyến dở dang. Trạng thái = (tập việc đã làm + bit "đã nghỉ trưa", việc làm cuối).
//   Mỗi trạng thái giữ vài "nhãn" = tuyến dở dang tốt nhất tới đó (giờ xong + điểm phạt theo tầng).
//   Nhãn thua nhãn khác ở MỌI mặt thì bỏ. Hết việc thì chọn nhãn nhỏ nhất theo tầng (rules).
//   ≤ max_exact_tasks việc: ra thứ tự tốt nhất. Nhiều hơn: tham lam rồi cải thiện cục bộ (or-opt dời đoạn 1–3 việc
//   + 2-opt đảo đoạn) từ nhiều điểm xuất phát, lấy tốt nhất; tất định, 64 việc chậm nhất ~0,5 s.
//
//   Nghỉ trưa = "việc ảo" kBreak: không di chuyển, bắt đầu = max(giờ xong, break_open), dài break_minutes.
//   Luật chặn: CHƯA nghỉ thì mọi việc phải xong trước break_latest (giờ muộn nhất bắt đầu nghỉ).
//   Kết thúc hợp lệ khi đã nghỉ, hoặc chưa nghỉ nhưng mọi việc đã xong trước break_latest.
//
// Dùng thế nào:
//   Problem p = ...;                  // plan.cpp dựng từ message
//   Solution s = solve(p, rules);     // s.order = {2, kBreak, 0, 1}: làm việc 2, nghỉ trưa, rồi 0, rồi 1
//                                     // s.steps[k]: giờ tới, check-in, xong, km của việc thứ k
//
// Trong file này có:
//   kNone     – "không có" (không hẹn, không hạn)
//   kBreak    – ký hiệu "nghỉ trưa" trong thứ tự và trong Visit.task
//   Problem   – bài toán của 1 KTV đã quy ra số
//   Visit     – kết quả của MỘT bước đi tới một việc (giờ tới, check-in, xong, km, điểm phạt)
//   Source, Solution – thứ tự + từng bước + nó do đâu mà ra (tốt nhất / gần đúng / tham lam)
//   solve     – HÀM CHÍNH: vào bài toán, ra kết quả đầy đủ (7.24: ca tuỳ chọn được làm hay bỏ tùy khóa)
//   solve_from – 7.24: cải thiện cục bộ (kể cả chèn / gỡ ca tuỳ chọn) từ một tuyến có sẵn, không tệ hơn nó
//   objective – chấm điểm một thứ tự theo tầng (test dùng để so với vét cạn)
//
// Ẩn trong dp.cpp: visit() = bước chuyển của QHĐ, ĐÂY là chỗ áp rule nghiệp vụ cho từng bước;
//   nhãn (Label), giữ/bỏ nhãn (keep), vòng QHĐ (exact), tham lam + cải thiện cục bộ (heuristic, improve).
// Phụ thuộc: rules (tầng, trọng số), travel (Matrix).
// ============================================================================
#pragma once

#include <cmath>
#include <cstdint>
#include <vector>

#include "ktv/rules.hpp"
#include "ktv/travel.hpp"

namespace ktv {

inline constexpr double kNone = NAN;  // "Không có". So sánh với NaN luôn sai → không hạn thì không bao giờ trễ.
inline constexpr int kBreak = -1;     // "Nghỉ trưa": dùng thay chỉ số việc trong Solution.order và Visit.task.

// Bài toán của 1 KTV. Việc đánh số 0..n-1; giờ tính bằng phút kể từ lúc xuất phát.
// VD việc 0: làm 90 phút, hẹn lúc 280 (A), phải check-in trước 400 (B), không hạn hoàn tất.
struct Problem {
    Matrix travel;                    // Điểm 0 = nơi xuất phát; việc i là điểm i + 1.
    std::vector<double> service;      // service[i]: phút làm việc i.
    std::vector<double> opens;        // Mốc hẹn A: tới sớm thì chờ tới A. kNone = không hẹn.
    std::vector<double> due;          // Hạn check-in B. kNone = không có.
    std::vector<double> complete_by;  // Hạn làm xong. kNone = không có.
    std::vector<double> weight;       // Trọng số ưu tiên: P1 = 4 … P4 = 1.
    std::vector<double> urgency;      // 7.16.2: độ gấp của ca chèn (0..K; ca chính = 0). Rỗng = không dùng (test cũ).
    std::vector<uint64_t> same_area;  // same_area[i]: các việc khác cùng lô với việc i (bit j = 1 nếu việc j cùng lô).
    double shift_end = kNone;         // Giờ hết ca.
    double break_open = kNone;        // Sớm nhất được bắt đầu nghỉ trưa. kNone = tuyến này không cần nghỉ.
    double break_latest = kNone;      // Muộn nhất phải bắt đầu nghỉ (= hết khung trưa − break_minutes).
    double break_minutes = 0;         // Thời lượng nghỉ.
    // 7.24: ca TUỲ CHỌN (ca ngoài K…): được bỏ, mỗi ca bỏ cộng rule SKIP_OPTIONAL; làm thì phải xong ≤ shift_end
    // (ràng buộc cứng) và không tính PRIORITY_DELAY / DEADLINE_URGENCY của chính nó. Rỗng = mọi ca bắt buộc.
    std::vector<char> optional;
    bool is_optional(int j) const { return j >= 0 && j < static_cast<int>(optional.size()) && optional[j]; }
    bool needs_break() const { return !std::isnan(break_latest); }
    int size() const { return static_cast<int>(service.size()); }  // Số việc.
};

// Một bước: tới việc `task`, hoặc đi nghỉ trưa (task = kBreak: km 0, checkin = giờ bắt đầu nghỉ).
struct Visit {
    int task;                // Chỉ số việc, hoặc kBreak.
    double km, travel;       // Chặng vừa đi.
    double arrive;           // Giờ tới nơi.
    double checkin;          // = max(arrive, mốc hẹn A): tới sớm thì chờ.
    double done;             // = checkin + thời gian làm.
    double cost[RULE_COUNT];  // Điểm phạt từng rule của riêng bước này, VD cost[KM] = km.
};

// Thứ tự do đâu mà ra.
enum class Source {
    Optimal,      // QHĐ, tốt nhất theo rules.
    Approximate,  // QHĐ nhưng phải bỏ bớt nhãn (vượt max_labels): gần tốt nhất.
    Heuristic,    // Quá max_exact_tasks việc: tham lam + or-opt/2-opt (gần đúng).
};
inline constexpr const char* kSourceNames[] = {"OPTIMAL", "APPROXIMATE", "HEURISTIC"};

struct Solution {
    std::vector<int> order;    // Chỉ số việc theo thứ tự làm; kBreak = chỗ nghỉ trưa.
    std::vector<Visit> steps;  // steps[k]: bước tới việc order[k].
    Source source;
};

// HÀM CHÍNH: chọn thứ tự làm việc. Có ca tuỳ chọn (7.24): chọn luôn tập ca tuỳ chọn nên làm; order chỉ chứa ca được làm.
Solution solve(const Problem& p, const Rules& rules);

// 7.24: cải thiện cục bộ (dời / đảo đoạn / chèn – gỡ ca tuỳ chọn) xuất phát từ `start`, chỉ nhận nước làm khóa giảm
// → không bao giờ tệ hơn start. Dùng khi đã có tuyến tốt (VD QHĐ chính xác) mà còn thêm ca tuỳ chọn ngoài giới hạn.
Solution solve_from(const Problem& p, const Rules& rules, const std::vector<int>& start);

// Mô phỏng một thứ tự có sẵn (kể cả phương án so sánh): từng bước + giờ, dùng đúng luật của solve().
// Thứ tự phạm luật nghỉ trưa vẫn ra số; chỉ objective() mới biết hợp lệ hay không (trả vô cực).
std::vector<Visit> simulate(const Problem& p, const std::vector<int>& order);

// Điểm phạt của một thứ tự, từng tầng (tầng 1 trước). Nhỏ hơn = tốt hơn; so như so từ điển.
std::vector<double> objective(const Problem& p, const Rules& rules, const std::vector<int>& order);

}  // namespace ktv
