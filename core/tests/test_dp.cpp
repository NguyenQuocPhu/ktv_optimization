// QHĐ phải ra đúng thứ tự tốt nhất như khi thử mọi hoán vị; heuristic phải ra hoán vị hợp lệ.
#include <algorithm>
#include <iostream>
#include <numeric>
#include <random>

#include "ktv/dp.hpp"

static int failures = 0;
#define CHECK(cond)                                                           \
    do {                                                                      \
        if (!(cond)) {                                                        \
            std::cerr << __FILE__ << ":" << __LINE__ << ": FAIL " #cond "\n"; \
            ++failures;                                                       \
        }                                                                     \
    } while (0)

using namespace ktv;

static std::uniform_real_distribution<double> unit(0, 1);

static Problem random_problem(std::mt19937& rng, int n) {
    std::vector<Point> points;
    for (int i = 0; i <= n; ++i) points.push_back({21 + unit(rng) * 0.08, 105.8 + unit(rng) * 0.08});
    Problem p;
    p.travel = haversine_matrix(points, 30);
    std::vector<int> area(n);
    for (int i = 0; i < n; ++i) {
        p.service.push_back(15 + unit(rng) * 60);
        bool booked = unit(rng) < 0.5;
        double a = unit(rng) * 300;
        p.opens.push_back(booked ? a : kNone);
        p.due.push_back(booked ? a + 60 : (unit(rng) < 0.3 ? unit(rng) * 400 : kNone));
        p.complete_by.push_back(unit(rng) < 0.3 ? unit(rng) * 500 : kNone);
        p.weight.push_back(4 - static_cast<int>(unit(rng) * 4));
        area[i] = static_cast<int>(unit(rng) * 3);
    }
    for (int i = 0; i < n; ++i) {
        uint64_t mask = 0;
        for (int j = 0; j < n; ++j)
            if (j != i && area[j] == area[i]) mask |= uint64_t{1} << j;
        p.same_area.push_back(mask);
    }
    p.shift_end = unit(rng) < 0.5 ? 420 : kNone;
    return p;
}

int main() {
    std::mt19937 rng(7);
    Rules rules = default_rules();
    rules.max_labels = 100000;  // Không cắt nhãn → phải khớp tuyệt đối với vét cạn.
    int checked = 0;
    for (int round = 0; round < 300; ++round) {
        int n = 1 + round % 7;
        Problem p = random_problem(rng, n);
        if (round % 2) {  // Nửa số bài có nghỉ trưa: khung [open, open + 90], nghỉ 45 phút.
            p.break_open = 60 + unit(rng) * 200;
            p.break_latest = p.break_open + 45;
            p.break_minutes = 45;
        }
        Solution s = solve(p, rules);
        CHECK(s.source == Source::Optimal);
        std::vector<int> order(n);
        std::iota(order.begin(), order.end(), 0);
        std::vector<double> best;
        do {  // Vét cạn: mọi hoán vị × mọi chỗ chèn nghỉ (và không nghỉ). Thứ tự sai luật → vô cực.
            for (int at = -1; at <= n; ++at) {
                std::vector<int> candidate = order;
                if (at >= 0) candidate.insert(candidate.begin() + at, kBreak);
                auto k = objective(p, rules, candidate);
                if (best.empty() || k < best) best = k;
            }
        } while (std::next_permutation(order.begin(), order.end()));
        auto got = objective(p, rules, s.order);
        bool same = got.size() == best.size();
        for (size_t t = 0; same && t < got.size(); ++t) same = std::abs(got[t] - best[t]) < 1e-9;
        CHECK(same);
        ++checked;
    }

    // Nhiều việc hơn max_exact_tasks: heuristic, vẫn là hoán vị đủ mọi việc.
    rules = default_rules();
    Problem big = random_problem(rng, 14);
    Solution s = solve(big, rules);
    CHECK(s.source == Source::Heuristic);
    std::vector<int> sorted = s.order;
    std::sort(sorted.begin(), sorted.end());
    std::vector<int> all(14);
    std::iota(all.begin(), all.end(), 0);
    CHECK(sorted == all);

    // Heuristic có nghỉ trưa: đúng một lần nghỉ, thứ tự hợp lệ (điểm hữu hạn).
    big.break_open = 150;
    big.break_latest = 195;
    big.break_minutes = 45;
    Solution rested = solve(big, rules);
    CHECK(std::count(rested.order.begin(), rested.order.end(), kBreak) == 1);
    CHECK(std::isfinite(objective(big, rules, rested.order)[0]));

    // Tới sớm hơn mốc hẹn thì chờ; check-in sau hạn B thì tính trễ theo trọng số ưu tiên.
    Problem one = random_problem(rng, 1);
    one.opens = {500};
    one.due = {510};
    one.weight = {4};
    Visit v = solve(one, rules).steps.at(0);
    CHECK(v.checkin == 500 && v.done == 500 + one.service[0] && v.cost[LATE_CHECKIN] == 0);
    one.opens = {kNone};
    one.due = {-10};  // Hạn đã qua trước lúc xuất phát.
    v = solve(one, rules).steps.at(0);
    CHECK(v.cost[LATE_CHECKIN] == 4 && std::abs(v.cost[LATE_MINUTES] - (v.checkin + 10)) < 1e-9);
    CHECK(solve(big, rules).steps.size() == 15);  // 14 việc + 1 lần nghỉ trưa.

    if (failures) std::cerr << failures << " lỗi\n";
    else std::cout << "test_dp: OK (" << checked << " bài so với vét cạn)\n";
    return failures != 0;
}
