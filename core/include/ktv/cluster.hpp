// ============================================================================
// cluster — CHIA TUYẾN THÀNH CỤM VÀ TÓM TẮT CỤM
// ============================================================================
// Hiểu nhanh:
//   QHĐ đã trả thứ tự TASK. Module này chỉ:
//     1. CẮT thứ tự đó thành các cụm liên tiếp (chặng giữa hai TASK > ngưỡng thì mở cụm).
//     2. TÓM TẮT mỗi cụm: tâm, bán kính, số việc, km vào/trong cụm, thời gian làm, tên theo lô.
//   Không đổi thứ tự, không thêm/bớt TASK.
//
//   IDLE/BREAK KHÔNG thuộc module này: chúng chỉ là dòng timeline, không quyết định biên cụm.
//   Việc gắn IDLE/BREAK vào cụm nào là chuyện dựng output ở plan.cpp.
//
// Dùng thế nào:
//   std::vector<ClusterSummary> cs = summarize_clusters(stops, staff.plots);
//
// Phụ thuộc: api (Task/Plot/Point), travel (distance_km).
// ============================================================================
#pragma once

#include <string>
#include <vector>

#include "ktv/api.hpp"

namespace ktv {

inline constexpr double kClusterSplitKm = 2.0;  // Chặng giữa hai TASK liên tiếp dài hơn mức này thì mở cụm mới.

// Khoảng TASK liên tiếp thuộc một cụm: first = chỉ số TASK đầu, count = số TASK.
struct ClusterSpan {
    int first = 0;
    int count = 0;
};

// Một TASK đã xếp kèm dữ liệu để tóm tắt cụm. Không có giờ, không IDLE/BREAK.
struct TaskStop {
    const Task* task = nullptr;
    double leg_km = 0;          // chặng tới TASK này
    double service_minutes = 0;
};

// Tóm tắt một cụm. `first_task` để plan.cpp map dòng timeline về cụm.
struct ClusterSummary {
    int seg = 0;                // 1-based
    int first_task = 0;         // chỉ số TASK đầu trong dãy
    int task_count = 0;
    std::string code;           // "CL-<seg>"
    std::string name;           // "Cluster <seg> — <lô…>"
    Point center;
    double radius_km = 0;
    double travel_km_inbound = 0, travel_km_internal = 0, handle_minutes = 0;
};

std::vector<ClusterSpan> split_clusters(const std::vector<double>& legs_km, double threshold_km);

std::vector<ClusterSummary> summarize_clusters(const std::vector<TaskStop>& stops,
                                               const std::vector<Plot>& staff_plots,
                                               double split_km = kClusterSplitKm);

}  // namespace ktv
