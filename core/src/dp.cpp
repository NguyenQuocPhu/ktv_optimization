#include "ktv/dp.hpp"

#include <algorithm>
#include <array>

namespace ktv {

namespace {

// Bước chuyển của QHĐ: dp[mask ∪ {j}][j] ← dp[mask][i] + visit(mask, i, j, giờ xong của nhãn).
// Giống cost(i, j) của TSP, nhưng thêm `clock` (giờ rời i: quyết định chờ hẹn, trễ hay không)
// và `done_mask` (việc đã làm: quyết định có "quay lại lô đã rời" không).
// i = -1: đang ở chỗ xuất phát.
Visit visit(const Problem& p, uint64_t done_mask, int i, int j, double clock) {
    Visit v{j, 0, 0, 0, 0, 0, {}};
    int origin = i < 0 ? 0 : i + 1;  // Điểm trong bảng travel: 0 = xuất phát, việc k = k + 1.
    v.km = p.travel.km[origin][j + 1];
    v.travel = p.travel.minutes[origin][j + 1]; // thời gian đi lấy từ matrix orsm (update có kẹt xe các thứ sau), orsm profile oto
    v.arrive = clock + v.travel;
    v.checkin = !std::isnan(p.opens[j]) && v.arrive < p.opens[j] ? p.opens[j] : v.arrive;
    v.done = v.checkin + p.service[j]; 
    if (v.checkin > p.due[j]) {  // So sánh với NaN luôn sai: không có hạn thì không trễ.
        v.cost[LATE_CHECKIN] = p.weight[j];
        v.cost[LATE_MINUTES] = v.checkin - p.due[j];
    }
    // complete_by là hạn của những task có sla_minutes = NaN
    if (v.done > p.complete_by[j]) v.cost[LATE_COMPLETION] = 1;
    if (v.done > p.shift_end) v.cost[AFTER_SHIFT] = 1;
    v.cost[KM] = v.km;
    v.cost[TRAVEL_MINUTES] = v.travel;
    // Rời lô của i để vào lô của j, mà lô của j đã từng làm → quay lại khu vực.
    uint64_t area = p.same_area[j];
    if (i >= 0 && !(area >> i & 1) && (done_mask & area)) v.cost[AREA_REENTRY] = 1;
    v.cost[PRIORITY_DELAY] = p.weight[j] * v.checkin / 60;
    return v;
}

std::vector<Visit> walk(const Problem& p, const std::vector<int>& order) {
    std::vector<Visit> steps;
    uint64_t mask = 0;
    int i = -1;
    double clock = 0;
    for (int j : order) {
        steps.push_back(visit(p, mask, i, j, clock));
        mask |= uint64_t{1} << j;
        i = j;
        clock = steps.back().done;
    }
    return steps;
}


using Key = std::array<double, kMaxTiers>;  // Tầng không dùng để 0.

struct Label {
    double finish;
    Key costs;
    int parent;  // Chỉ số trong pool; -1 = gốc.
    int task;
};

// Gom những gì QHĐ và heuristic dùng chung: rule theo tầng + kho nhãn.
struct Search {
    const Problem& p;
    const Rules& rules;
    int tiers;
    int finish_tier = -1;
    double finish_weight = 0;
    std::vector<Label> pool{{0, {}, -1, -1}};  // pool[0] = gốc.

    Search(const Problem& problem, const Rules& r) : p(problem), rules(r), tiers(static_cast<int>(r.tiers.size())) {
        for (int t = 0; t < tiers; ++t)
            for (auto [rule, weight] : rules.tiers[t])
                if (rule == FINISH && weight) finish_tier = t, finish_weight = weight;
    }

    Label extend(int parent, uint64_t mask, int i, int j) const {
        const Label& from = pool[parent];
        Visit v = visit(p, mask, i, j, from.finish);
        Label label{v.done, from.costs, parent, j};
        for (int t = 0; t < tiers; ++t)
            for (auto [rule, weight] : rules.tiers[t])
                if (rule != FINISH) label.costs[t] += weight * v.cost[rule];
        return label;
    }

    Key key(const Label& label) const {
        Key k = label.costs;
        if (finish_tier >= 0) k[finish_tier] += finish_weight * label.finish;
        return k;
    }

    // Thêm nhãn vào một ô dp nếu không nhãn nào trong ô hơn nó ở mọi mặt. Trả true nếu phải cắt bớt nhãn.
    bool keep(std::vector<int>& cell, const Label& label) {
        auto no_worse = [&](const Label& a, const Label& b) {  // a không tệ hơn b ở mặt nào.
            if (a.finish > b.finish) return false;
            for (int t = 0; t < tiers; ++t)
                if (a.costs[t] > b.costs[t]) return false;
            return true;
        };
        for (int old : cell)
            if (no_worse(pool[old], label)) return false;
        cell.erase(std::remove_if(cell.begin(), cell.end(), [&](int old) { return no_worse(label, pool[old]); }),
                     cell.end());
        pool.push_back(label);
        cell.push_back(static_cast<int>(pool.size()) - 1);
        if (static_cast<int>(cell.size()) <= rules.max_labels) return false;
        std::stable_sort(cell.begin(), cell.end(), [&](int a, int b) { return key(pool[a]) < key(pool[b]); });
        cell.resize(rules.max_labels);
        return true;
    }

    std::vector<int> order_of(int label) const {
        std::vector<int> order;
        for (; label > 0; label = pool[label].parent) order.push_back(pool[label].task);
        std::reverse(order.begin(), order.end());
        return order;
    }

    Key evaluate(const std::vector<int>& order) {
        pool.resize(1);
        int label = 0;
        uint64_t mask = 0;
        int i = -1;
        for (int j : order) {
            pool.push_back(extend(label, mask, i, j));
            label = static_cast<int>(pool.size()) - 1;
            mask |= uint64_t{1} << j;
            i = j;
        }
        return key(pool[label]);
    }
};

Solution exact(Search& s) {
    const int n = s.p.size();
    const uint64_t full = (uint64_t{1} << n) - 1;
    // Bảng QHĐ: dp(mask, last) = các nhãn (tuyến dở dang) đã làm đúng tập `mask`, việc cuối là `last`.
    // Trải phẳng 2 chiều thành 1 chiều: ô [mask][last] nằm ở vị trí mask * n + last.
    std::vector<std::vector<int>> table(static_cast<size_t>(full + 1) * n);
    auto dp = [&](uint64_t mask, int last) -> std::vector<int>& { return table[mask * n + last]; };
    bool capped = false;
    for (int j = 0; j < n; ++j) capped |= s.keep(dp(uint64_t{1} << j, j), s.extend(0, 0, -1, j));
    for (uint64_t mask = 1; mask < full; ++mask)
        for (int i = 0; i < n; ++i) {
            if (!(mask >> i & 1)) continue;
            std::vector<int> labels = dp(mask, i);  // Bản sao: pool có thể đổi địa chỉ khi thêm nhãn.
            for (int label : labels)
                for (int j = 0; j < n; ++j)
                    if (!(mask >> j & 1))
                        capped |= s.keep(dp(mask | uint64_t{1} << j, j), s.extend(label, mask, i, j));
        }
    int best = -1;
    Key best_key{};
    for (int last = 0; last < n; ++last)
        for (int label : dp(full, last))
            if (Key k = s.key(s.pool[label]); best < 0 || k < best_key) best = label, best_key = k;
    return {s.order_of(best), {}, capped ? Source::Approximate : Source::Optimal};
}

constexpr int kMaxImproveTasks = 40;  // 2-opt tốn O(n³) mỗi vòng; nhiều việc hơn chỉ dùng tham lam.

// Tham lam: mỗi bước chọn việc làm khóa tăng ít nhất; sau đó 2-opt tối đa 2 vòng.
std::vector<int> heuristic(Search& s) {
    const int n = s.p.size();
    std::vector<int> order;
    int label = 0, i = -1;
    uint64_t mask = 0;
    while (static_cast<int>(order.size()) < n) {
        int best_j = -1;
        Label best{};
        Key best_key{};
        for (int j = 0; j < n; ++j) {
            if (mask >> j & 1) continue;
            Label candidate = s.extend(label, mask, i, j);
            if (Key k = s.key(candidate); best_j < 0 || k < best_key) best_j = j, best = candidate, best_key = k;
        }
        s.pool.push_back(best);
        label = static_cast<int>(s.pool.size()) - 1;
        order.push_back(best_j);
        mask |= uint64_t{1} << best_j;
        i = best_j;
    }
    if (n < 3 || n > kMaxImproveTasks) return order;
    Key best_key = s.evaluate(order);
    for (int round = 0; round < 2; ++round) {
        bool improved = false;
        for (int a = 0; a < n - 1; ++a)  // Đảo đoạn order[a..b].
            for (int b = a + 1; b < n; ++b) {
                std::vector<int> candidate = order;
                std::reverse(candidate.begin() + a, candidate.begin() + b + 1);
                if (Key k = s.evaluate(candidate); k < best_key) order = candidate, best_key = k, improved = true;
            }
        if (!improved) break;
    }
    return order;
}

}  // namespace

Solution solve(const Problem& p, const Rules& rules) {
    Search s(p, rules);
    Solution result{{}, {}, Source::Optimal};
    if (p.size() > rules.max_exact_tasks) result = {heuristic(s), {}, Source::Heuristic};
    else if (p.size() > 0) result = exact(s);
    result.steps = walk(p, result.order);
    return result;
}

std::vector<double> objective(const Problem& p, const Rules& rules, const std::vector<int>& order) {
    Search s(p, rules);
    Key k = s.evaluate(order);
    return {k.begin(), k.begin() + s.tiers};
}

}  // namespace ktv
