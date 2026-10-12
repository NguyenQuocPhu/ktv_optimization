// ============================================================================
// adapter/metrics — SỐ LIỆU cho GET /metrics (định dạng text Prometheus) — Phase 8.3
// ============================================================================
// Hiểu nhanh:
//   Worker và gateway đếm / đo tại chỗ; /metrics in ra dạng Prometheus đọc được:
//     # TYPE ktv_messages_total counter
//     ktv_messages_total{status="200"} 2085
//   Prometheus (của công ty) định kỳ lấy số từ mọi replica, lưu lịch sử; Grafana vẽ + cảnh báo.
//   counter = chỉ tăng · gauge = lên xuống · histogram = đếm theo khoảng (suy ra p50 / p95).
//
// Dùng thế nào (an toàn nhiều luồng):
//   Metrics m;
//   m.add("ktv_messages_total", "status=\"200\"");          // counter += 1
//   m.set("ktv_queue_depth", "thread=\"2\"", 7);            // gauge = 7
//   m.observe("ktv_plan_seconds", "", 0.012);               // histogram
//   std::string text = m.render();                          // body của GET /metrics
//
// Phụ thuộc: không.
// ============================================================================
#pragma once

#include <cstdio>
#include <map>
#include <mutex>
#include <string>
#include <utility>
#include <vector>

namespace ktv {

class Metrics {
public:
    void add(const std::string& name, const std::string& labels = "", double value = 1) {
        std::lock_guard<std::mutex> lock(mutex_);
        counters_[name][labels] += value;
    }
    void set(const std::string& name, const std::string& labels, double value) {
        std::lock_guard<std::mutex> lock(mutex_);
        gauges_[name][labels] = value;
    }
    // Xóa mọi giá trị của một gauge (VD partition đã bị thu hồi) trước khi set lại.
    void clear(const std::string& name) {
        std::lock_guard<std::mutex> lock(mutex_);
        gauges_.erase(name);
    }
    void observe(const std::string& name, const std::string& labels, double seconds) {
        std::lock_guard<std::mutex> lock(mutex_);
        Histogram& h = histograms_[name][labels];
        if (h.buckets.empty()) h.buckets.assign(kBounds.size(), 0);
        for (size_t i = 0; i < kBounds.size(); ++i)
            if (seconds <= kBounds[i]) ++h.buckets[i];
        h.sum += seconds;
        ++h.count;
    }

    std::string render() const {
        std::lock_guard<std::mutex> lock(mutex_);
        std::string out;
        const auto line = [&](const std::string& name, const std::string& labels, double value) {
            char number[64];
            std::snprintf(number, sizeof number, "%.6g", value);
            out += name + (labels.empty() ? "" : "{" + labels + "}") + " " + number + "\n";
        };
        for (const auto& [name, series] : counters_) {
            out += "# TYPE " + name + " counter\n";
            for (const auto& [labels, value] : series) line(name, labels, value);
        }
        for (const auto& [name, series] : gauges_) {
            out += "# TYPE " + name + " gauge\n";
            for (const auto& [labels, value] : series) line(name, labels, value);
        }
        for (const auto& [name, series] : histograms_) {
            out += "# TYPE " + name + " histogram\n";
            for (const auto& [labels, h] : series) {
                const std::string sep = labels.empty() ? "" : labels + ",";
                for (size_t i = 0; i < kBounds.size(); ++i) {
                    char bound[32];
                    std::snprintf(bound, sizeof bound, "%g", kBounds[i]);
                    line(name + "_bucket", sep + "le=\"" + bound + "\"", static_cast<double>(h.buckets[i]));
                }
                line(name + "_bucket", sep + "le=\"+Inf\"", static_cast<double>(h.count));
                line(name + "_sum", labels, h.sum);
                line(name + "_count", labels, static_cast<double>(h.count));
            }
        }
        return out;
    }

private:
    struct Histogram {
        std::vector<long long> buckets;  // buckets[i] = số lần ≤ kBounds[i] (cộng dồn như Prometheus)
        double sum = 0;
        long long count = 0;
    };
    // Giây: 5 ms … 10 s (tính tuyến thường 5–50 ms, LNS / OSRM chậm tới vài giây).
    inline static const std::vector<double> kBounds = {0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10};
    mutable std::mutex mutex_;
    std::map<std::string, std::map<std::string, double>> counters_, gauges_;
    std::map<std::string, std::map<std::string, Histogram>> histograms_;
};

}  // namespace ktv
