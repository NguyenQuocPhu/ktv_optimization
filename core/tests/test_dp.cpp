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
        p.urgency.push_back(unit(rng) < 0.5 ? 0 : unit(rng) * 5);  // 7.16.2: nửa số bài có urgency
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
    for (int round = 0; round < 600; ++round) {
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

    {  // Bài rỗng: không việc thì không bước, vẫn là Optimal.
        Problem empty;
        empty.travel = haversine_matrix({{21.0, 105.8}}, 30);
        Solution s = solve(empty, default_rules());
        CHECK(s.order.empty() && s.steps.empty() && s.source == Source::Optimal);
    }
    {  // Một việc, không hạn/không ca: tới = thời gian đi, xong = tới + thời gian làm.
        Problem one;
        one.travel = haversine_matrix({{21.0, 105.8}, {21.03, 105.81}}, 30);
        one.service = {30};
        one.opens = {kNone};
        one.due = {kNone};
        one.complete_by = {kNone};
        one.weight = {4};
        one.same_area = {0};
        one.shift_end = kNone;
        Solution s = solve(one, default_rules());
        CHECK(s.steps.size() == 1);
        CHECK(std::abs(s.steps[0].checkin - s.steps[0].travel) < 1e-9);
        CHECK(std::abs(s.steps[0].done - (s.steps[0].checkin + 30)) < 1e-9);
    }
    {  // Heuristic với số việc lớn vẫn là hoán vị đủ.
        for (int n : {20, 40}) {
            Problem big = random_problem(rng, n);
            Solution s = solve(big, default_rules());
            CHECK(s.source == Source::Heuristic);
            std::vector<int> sorted = s.order;
            std::sort(sorted.begin(), sorted.end());
            std::vector<int> all(n);
            std::iota(all.begin(), all.end(), 0);
            CHECK(sorted == all);
        }
    }

    {  // Heuristic (> max_exact_tasks) là cực tiểu cục bộ: không nước or-opt (nhấc đoạn 1–3 việc đặt chỗ khác) hay
       // 2-opt (đảo đoạn) nào làm điểm tốt hơn. Tham lam + 2-opt 2 vòng cũ không đạt điều này.
        std::mt19937 local_rng(21);
        const Rules rules = default_rules();
        for (int n : {13, 20, 30}) {
            for (int k = 0; k < 5; ++k) {
                Problem p = random_problem(local_rng, n);
                p.break_open = 150;
                p.break_latest = 195;
                p.break_minutes = 45;
                const std::vector<int> order = solve(p, rules).order;
                const std::vector<double> best = objective(p, rules, order);
                const int m = static_cast<int>(order.size());
                bool local_min = true;
                for (int len = 1; len <= 3 && local_min; ++len)
                    for (int a = 0; a + len <= m && local_min; ++a)
                        for (int b = 0; b <= m - len && local_min; ++b) {
                            std::vector<int> moved = order;
                            std::vector<int> segment(moved.begin() + a, moved.begin() + a + len);
                            moved.erase(moved.begin() + a, moved.begin() + a + len);
                            moved.insert(moved.begin() + b, segment.begin(), segment.end());
                            local_min = !(objective(p, rules, moved) < best);
                        }
                for (int a = 0; a < m - 1 && local_min; ++a)
                    for (int b = a + 1; b < m && local_min; ++b) {
                        std::vector<int> reversed = order;
                        std::reverse(reversed.begin() + a, reversed.begin() + b + 1);
                        local_min = !(objective(p, rules, reversed) < best);
                    }
                CHECK(local_min);
            }
        }
    }
    {  // So với tối ưu thật (QHĐ chính xác chạy tới 14 việc): lệch tầng 1 (trễ check-in có trọng số) trung bình
       // phải nhỏ. Đo 2026-10-02: tham lam + 2-opt cũ lệch ~3,3–4,6; bản mới (4 lần improve) ~0,4–1,5.
        std::mt19937 gap_rng(5);
        Rules exact_rules = default_rules();
        exact_rules.max_exact_tasks = 14;
        Rules heuristic_rules = default_rules();
        heuristic_rules.max_exact_tasks = 0;
        double gap = 0;
        const int samples = 12;
        for (int k = 0; k < samples; ++k) {
            Problem p = random_problem(gap_rng, 13);
            const double optimum = objective(p, exact_rules, solve(p, exact_rules).order)[0];
            const double found = objective(p, exact_rules, solve(p, heuristic_rules).order)[0];
            CHECK(found >= optimum - 1e-9);  // heuristic không thể tốt hơn tối ưu
            gap += found - optimum;
        }
        CHECK(gap / samples < 1.5);
    }

    {  // 7.16.2: trọng số DEADLINE_URGENCY = 0 → cùng thứ tự như khi không có urgency.
        Rules zero = default_rules();
        for (auto& tier : zero.tiers)
            for (auto& [rule, weight] : tier)
                if (rule == DEADLINE_URGENCY) weight = 0;
        std::mt19937 urgent_rng(11);
        for (int k = 0; k < 20; ++k) {
            Problem p = random_problem(urgent_rng, 6);
            Problem q = p;
            for (double& value : q.urgency) value = 0;
            CHECK(solve(p, zero).order == solve(q, default_rules()).order);
        }
    }

    {  // 7.24: ca tuỳ chọn — QHĐ chọn tập con + thứ tự tốt nhất, khớp vét cạn mọi tập con ⊇ bắt buộc × mọi hoán vị × chỗ nghỉ.
        std::mt19937 opt_rng(17);
        int compared = 0;
        for (int round = 0; round < 300; ++round) {
            const int n = 2 + round % 5;
            Problem p = random_problem(opt_rng, n);
            p.optional.assign(n, 0);
            for (int j = 0; j < n; ++j) p.optional[j] = unit(opt_rng) < 0.4;
            if (round % 2) {
                p.break_open = 60 + unit(opt_rng) * 200;
                p.break_latest = p.break_open + 45;
                p.break_minutes = 45;
            }
            Solution s = solve(p, rules);
            std::vector<double> best;
            for (uint64_t subset = 0; subset < (uint64_t{1} << n); ++subset) {
                bool ok = true;
                std::vector<int> order;
                for (int j = 0; j < n; ++j) {
                    if (subset >> j & 1) order.push_back(j);
                    else if (!p.optional[j]) ok = false;
                }
                if (!ok) continue;
                do {
                    for (int at = -1; at <= static_cast<int>(order.size()); ++at) {
                        std::vector<int> candidate = order;
                        if (at >= 0) candidate.insert(candidate.begin() + at, kBreak);
                        auto k = objective(p, rules, candidate);
                        if (best.empty() || k < best) best = k;
                    }
                } while (std::next_permutation(order.begin(), order.end()));
            }
            auto got = objective(p, rules, s.order);
            bool same = got.size() == best.size();
            for (size_t t = 0; same && t < got.size(); ++t) same = std::abs(got[t] - best[t]) < 1e-9;
            CHECK(same);
            for (const Visit& v : s.steps)  // ca tuỳ chọn được làm luôn xong trong ca (ràng buộc cứng)
                if (v.task != kBreak && p.optional[v.task]) CHECK(!(v.done > p.shift_end));
            ++compared;
        }
        CHECK(compared == 300);

        // Tham lam + chèn/gỡ so với tối ưu (QHĐ chính xác) trên 8–12 việc có ca tuỳ chọn: không bao giờ tốt hơn tối ưu,
        // lệch tầng 1 trung bình nhỏ. In số đo để theo dõi.
        Rules exact_rules = default_rules();
        Rules heuristic_rules = default_rules();
        heuristic_rules.max_exact_tasks = 0;
        double gap1 = 0, gap3 = 0;
        int same_tier12 = 0, samples = 0;
        for (int k = 0; k < 40; ++k) {
            const int n = 8 + k % 5;
            Problem p = random_problem(opt_rng, n);
            p.optional.assign(n, 0);
            for (int j = 0; j < n; ++j) p.optional[j] = unit(opt_rng) < 0.35;
            const auto optimum = objective(p, exact_rules, solve(p, exact_rules).order);
            const auto found = objective(p, exact_rules, solve(p, heuristic_rules).order);
            CHECK(!(found < optimum));
            gap1 += found[0] - optimum[0];
            gap3 += found[2] - optimum[2];
            same_tier12 += std::abs(found[0] - optimum[0]) < 1e-9 && std::abs(found[1] - optimum[1]) < 1e-9;
            ++samples;
        }
        std::cout << "  7.24 tham lam vs tối ưu (" << samples << " bài 8–12 việc, ~35% tuỳ chọn): lệch tầng 1 TB "
                  << gap1 / samples << ", tầng 3 TB " << gap3 / samples << ", trùng tầng 1+2 " << same_tier12 << "/"
                  << samples << "\n";
        CHECK(gap1 / samples < 1.5);

        // solve_from: không bao giờ tệ hơn tuyến xuất phát.
        for (int k = 0; k < 20; ++k) {
            Problem p = random_problem(opt_rng, 9);
            p.optional.assign(9, 0);
            for (int j = 6; j < 9; ++j) p.optional[j] = 1;
            Problem mandatory_only = p;
            for (auto* v : {&mandatory_only.service, &mandatory_only.opens, &mandatory_only.due, &mandatory_only.complete_by,
                            &mandatory_only.weight, &mandatory_only.urgency})
                v->resize(6);
            mandatory_only.same_area.resize(6);
            mandatory_only.optional.resize(6);
            const std::vector<int> start = solve(mandatory_only, rules).order;
            const Solution better = solve_from(p, rules, start);
            CHECK(!(objective(p, rules, start) < objective(p, rules, better.order)));
        }
    }

    if (failures) std::cerr << failures << " lỗi\n";
    else std::cout << "test_dp: OK (" << checked << " bài so với vét cạn)\n";
    return failures != 0;
}
