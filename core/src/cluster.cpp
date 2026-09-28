#include "ktv/cluster.hpp"

#include <algorithm>

#include "ktv/travel.hpp"

namespace ktv {

namespace {

// Nhãn khu vực của một việc: tên lô nếu KTV có, ngược lại "Lô <id>"; lô 0 = chưa xác định.
std::string plot_label(const std::vector<Plot>& staff_plots, int plot_id) {
    if (plot_id == 0) return "Khu vực chưa xác định";
    for (const Plot& own : staff_plots)
        if (own.id == plot_id && !own.name.empty()) return own.name;
    return "Lô " + std::to_string(plot_id);
}

}  // namespace

std::vector<ClusterSpan> split_clusters(const std::vector<double>& legs_km, double threshold_km) {
    std::vector<ClusterSpan> spans;
    for (size_t i = 0; i < legs_km.size(); ++i) {
        // TASK đầu luôn mở cụm (chặng vào cụm không phải tiêu chí cắt); các TASK sau cắt khi chặng quá xa.
        if (i == 0 || legs_km[i] > threshold_km) spans.push_back({static_cast<int>(i), 0});
        ++spans.back().count;
    }
    return spans;
}

std::vector<ClusterSummary> summarize_clusters(const std::vector<TaskStop>& stops, const std::vector<Plot>& staff_plots,
                                               double split_km) {
    std::vector<double> legs_km;
    legs_km.reserve(stops.size());
    for (const TaskStop& stop : stops) legs_km.push_back(stop.leg_km);
    const std::vector<ClusterSpan> spans = split_clusters(legs_km, split_km);

    std::vector<ClusterSummary> summaries;
    summaries.reserve(spans.size());
    for (int c = 0; c < static_cast<int>(spans.size()); ++c) {
        ClusterSummary summary;
        summary.seg = c + 1;
        summary.first_task = spans[c].first;
        summary.task_count = spans[c].count;
        summary.code = "CL-" + std::to_string(c + 1);

        double lat = 0, lng = 0;
        std::vector<std::string> labels;
        for (int k = spans[c].first; k < spans[c].first + spans[c].count; ++k) {
            const TaskStop& stop = stops[k];
            const Task& task = *stop.task;
            lat += task.latlng->lat;
            lng += task.latlng->lng;
            if (k == spans[c].first) summary.travel_km_inbound = stop.leg_km;  // chặng vào cụm
            else summary.travel_km_internal += stop.leg_km;
            summary.handle_minutes += stop.service_minutes;
            const std::string label = plot_label(staff_plots, task.task_plots_id);
            if (std::find(labels.begin(), labels.end(), label) == labels.end()) labels.push_back(label);
        }
        summary.center = {lat / summary.task_count, lng / summary.task_count};

        double radius_km = 0;
        for (int k = spans[c].first; k < spans[c].first + spans[c].count; ++k)
            radius_km = std::max(radius_km, distance_km(summary.center, *stops[k].task->latlng));
        summary.radius_km = radius_km;

        std::string name = "Cluster " + std::to_string(c + 1) + " — ";
        for (size_t i = 0; i < labels.size(); ++i) name += (i ? " · " : "") + labels[i];
        summary.name = std::move(name);
        summaries.push_back(std::move(summary));
    }
    return summaries;
}

}  // namespace ktv
