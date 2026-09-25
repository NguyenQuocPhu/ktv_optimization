// ============================================================================
// dp — QUY HOẠCH ĐỘNG: chọn thứ tự làm việc cho MỘT KTV
// ============================================================================
// Hiểu nhanh:
//   Đưa vào "bài toán số" (Problem), nhận lại thứ tự làm việc (Solution).
//   Module này KHÔNG biết JSON, tên việc hay giờ thật: chỉ có số, giờ tính bằng phút kể từ
//   lúc KTV xuất phát (0). plan.cpp lo đổi message ↔ số.
//
//   Cách làm: xét dần các tuyến dở dang. Trạng thái = (tập việc đã làm, việc làm cuối).
//   Mỗi trạng thái giữ vài "nhãn" = tuyến dở dang tốt nhất tới đó (giờ xong + điểm phạt theo tầng).
//   Nhãn thua nhãn khác ở MỌI mặt thì bỏ. Hết việc thì chọn nhãn nhỏ nhất theo tầng (rules).
//   ≤ max_exact_tasks việc: ra thứ tự tốt nhất. Nhiều hơn: tham lam + 2-opt (đảo đoạn).
//
// Dùng thế nào:
//   Problem p = ...;                  // plan.cpp dựng từ message
//   Solution s = solve(p, rules);     // s.order = {2, 0, 1}: làm việc 2, rồi 0, rồi 1
//                                     // s.steps[k]: giờ tới, check-in, xong, km của việc thứ k
//
// Trong file này có:
//   kNone     – "không có" (không hẹn, không hạn)
//   Problem   – bài toán của 1 KTV đã quy ra số
//   Visit     – kết quả của MỘT bước đi tới một việc (giờ tới, check-in, xong, km, điểm phạt)
//   Source, Solution – thứ tự + từng bước + nó do đâu mà ra (tốt nhất / gần đúng / tham lam)
//   solve     – HÀM CHÍNH: vào bài toán, ra kết quả đầy đủ
//   objective – chấm điểm một thứ tự theo tầng (test dùng để so với vét cạn)
//
// Ẩn trong dp.cpp: visit() = bước chuyển của QHĐ, ĐÂY là chỗ áp rule nghiệp vụ cho từng bước;
//   nhãn (Label), giữ/bỏ nhãn (keep), vòng QHĐ (exact), tham lam + 2-opt (heuristic).
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

// Bài toán của 1 KTV. Việc đánh số 0..n-1; giờ tính bằng phút kể từ lúc xuất phát.
// VD việc 0: làm 90 phút, hẹn lúc 280 (A), phải check-in trước 400 (B), không hạn hoàn tất.
struct Problem {
    Matrix travel;                    // Điểm 0 = nơi xuất phát; việc i là điểm i + 1.
    std::vector<double> service;      // service[i]: phút làm việc i.
    std::vector<double> opens;        // Mốc hẹn A: tới sớm thì chờ tới A. kNone = không hẹn.
    std::vector<double> due;          // Hạn check-in B. kNone = không có.
    std::vector<double> complete_by;  // Hạn làm xong. kNone = không có.
    std::vector<double> weight;       // Trọng số ưu tiên: P1 = 4 … P4 = 1.
    std::vector<uint64_t> same_area;  // same_area[i]: các việc khác cùng lô với việc i (bit j = 1 nếu việc j cùng lô).
    double shift_end = kNone;         // Giờ hết ca.
    int size() const { return static_cast<int>(service.size()); }  // Số việc.
};

// Một bước: đang ở việc `here` (-1 = nơi xuất phát), rời đi lúc `clock`, tới việc `task`.
struct Visit {
    int task;
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
    Heuristic,    // Quá max_exact_tasks việc: tham lam + 2-opt.
};
inline constexpr const char* kSourceNames[] = {"OPTIMAL", "APPROXIMATE", "HEURISTIC"};

struct Solution {
    std::vector<int> order;    // Chỉ số việc theo thứ tự làm.
    std::vector<Visit> steps;  // steps[k]: bước tới việc order[k].
    Source source;
};

// HÀM CHÍNH: chọn thứ tự làm việc.
Solution solve(const Problem& p, const Rules& rules);

// Điểm phạt của một thứ tự, từng tầng (tầng 1 trước). Nhỏ hơn = tốt hơn; so như so từ điển.
std::vector<double> objective(const Problem& p, const Rules& rules, const std::vector<int>& order);

}  // namespace ktv
