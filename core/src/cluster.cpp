#include "ktv/cluster.hpp"

namespace ktv {

std::vector<ClusterSpan> split_clusters(const std::vector<double>& legs_km, double threshold_km) {
    std::vector<ClusterSpan> spans;
    for (size_t i = 0; i < legs_km.size(); ++i) {
        // TASK đầu luôn mở cụm (chặng vào cụm không phải tiêu chí cắt); các TASK sau cắt khi chặng quá xa.
        if (i == 0 || legs_km[i] > threshold_km) spans.push_back({static_cast<int>(i), 0});
        ++spans.back().count;
    }
    return spans;
}

}  // namespace ktv
