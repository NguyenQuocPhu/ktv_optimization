// Phase 8.3: /metrics định dạng Prometheus — counter, gauge, histogram cộng dồn, nhiều luồng.
#include <iostream>
#include <thread>
#include <vector>

#include "ktv/adapter/metrics.hpp"

static int failures = 0;
#define CHECK(cond)                                                           \
    do {                                                                      \
        if (!(cond)) {                                                        \
            std::cerr << __FILE__ << ":" << __LINE__ << ": FAIL " #cond "\n"; \
            ++failures;                                                       \
        }                                                                     \
    } while (0)

static bool has(const std::string& text, const std::string& line) { return text.find(line + "\n") != std::string::npos; }

int main() {
    ktv::Metrics m;
    std::vector<std::thread> pool;
    for (int t = 0; t < 4; ++t)
        pool.emplace_back([&] {
            for (int i = 0; i < 1000; ++i) m.add("ktv_messages_total", "status=\"200\"");
        });
    for (auto& t : pool) t.join();
    m.add("ktv_messages_total", "status=\"422\"", 2);
    m.set("ktv_queue_depth", "thread=\"0\"", 7);
    m.observe("ktv_plan_seconds", "", 0.004);
    m.observe("ktv_plan_seconds", "", 0.03);
    m.observe("ktv_plan_seconds", "", 20);
    const std::string text = m.render();
    CHECK(has(text, "# TYPE ktv_messages_total counter"));
    CHECK(has(text, "ktv_messages_total{status=\"200\"} 4000"));  // 4 luồng × 1000, không mất lượt cộng
    CHECK(has(text, "ktv_messages_total{status=\"422\"} 2"));
    CHECK(has(text, "# TYPE ktv_queue_depth gauge") && has(text, "ktv_queue_depth{thread=\"0\"} 7"));
    CHECK(has(text, "ktv_plan_seconds_bucket{le=\"0.005\"} 1"));   // cộng dồn: ≤ 5 ms
    CHECK(has(text, "ktv_plan_seconds_bucket{le=\"0.05\"} 2"));
    CHECK(has(text, "ktv_plan_seconds_bucket{le=\"10\"} 2"));      // 20 s vượt khoảng cuối
    CHECK(has(text, "ktv_plan_seconds_bucket{le=\"+Inf\"} 3") && has(text, "ktv_plan_seconds_count 3"));
    m.clear("ktv_queue_depth");
    CHECK(m.render().find("ktv_queue_depth") == std::string::npos);

    if (failures) std::cerr << failures << " lỗi\n";
    else std::cout << "test_metrics: OK\n";
    return failures != 0;
}
