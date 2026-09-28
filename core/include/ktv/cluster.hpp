// ============================================================================
// cluster — CHIA TUYẾN THÀNH CỤM THEO KHOẢNG CÁCH
// ============================================================================
// Hiểu nhanh:
//   QHĐ đã trả thứ tự TASK. Module này chỉ CẮT thứ tự đó thành các cụm liên tiếp:
//   đi từ TASK này sang TASK kế tiếp mà chặng > ngưỡng thì mở cụm mới.
//   Không đổi thứ tự, không thêm/bớt TASK.
//
// Dùng thế nào:
//   std::vector<ClusterSpan> spans = split_clusters(legs_km, kClusterSplitKm);
//   // legs_km[i] = chặng tới TASK thứ i (theo thứ tự đã xếp).
//
// Phụ thuộc: không.
// ============================================================================
#pragma once

#include <vector>

namespace ktv {

inline constexpr double kClusterSplitKm = 2.0;  // Chặng giữa hai TASK liên tiếp dài hơn mức này thì mở cụm mới.

// Khoảng TASK liên tiếp thuộc một cụm: first = chỉ số TASK đầu, count = số TASK.
struct ClusterSpan {
    int first = 0;
    int count = 0;
};

std::vector<ClusterSpan> split_clusters(const std::vector<double>& legs_km, double threshold_km);

}  // namespace ktv
