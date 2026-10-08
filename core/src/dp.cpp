#include "ktv/dp.hpp"

#include <algorithm>
#include <array>
#include <limits>
#include <optional>
#include <random>
#include <utility>

namespace ktv {

namespace {

// Bước chuyển của QHĐ: dp[mask ∪ {j}][j] ← dp[mask][i] + visit(mask, i, j, giờ xong của nhãn).
// Giống cost(i, j) của TSP, nhưng thêm `clock` (giờ rời i: quyết định chờ hẹn, trễ hay không)
// và `done_mask` (việc đã làm: quyết định có "quay lại lô đã rời" không).
// i = -1: đang ở chỗ xuất phát. j = kBreak: đi nghỉ trưa, KTV đứng yên ở i.
Visit visit(const Problem& p, uint64_t done_mask, int i, int j, double clock) {
    Visit v{j, 0, 0, clock, clock, clock, {}};
    if (j == kBreak) {  // Nghỉ: 0 km, không phạt; chưa tới giờ nghỉ thì chờ.
        v.checkin = std::max(clock, p.break_open);
        v.done = v.checkin + p.break_minutes;
        return v;
    }
    int origin = i < 0 ? 0 : i + 1;  // Điểm trong bảng travel: 0 = xuất phát, việc k = k + 1.
    v.km = p.travel.km[origin][j + 1];
    v.travel = p.travel.minutes[origin][j + 1];
    v.arrive = clock + v.travel;
    v.checkin = !std::isnan(p.opens[j]) && v.arrive < p.opens[j] ? p.opens[j] : v.arrive;
    v.done = v.checkin + p.service[j];
    if (v.checkin > p.due[j]) {  // So sánh với NaN luôn sai: không có hạn thì không trễ.
        v.cost[LATE_CHECKIN] = p.weight[j];
        v.cost[LATE_MINUTES] = v.checkin - p.due[j];
    }
    if (v.done > p.complete_by[j]) v.cost[LATE_COMPLETION] = 1;
    if (v.done > p.shift_end) v.cost[AFTER_SHIFT] = 1;
    v.cost[KM] = v.km;
    v.cost[TRAVEL_MINUTES] = v.travel;
    // Rời lô của i để vào lô của j, mà lô của j đã từng làm → quay lại khu vực.
    uint64_t area = p.same_area[j];
    if (i >= 0 && !(area >> i & 1) && (done_mask & area)) v.cost[AREA_REENTRY] = 1;
    // 7.24: ca tuỳ chọn không bị phạt vì CHÍNH NÓ làm muộn (không thì chỉ chèn được buổi sáng); ca khác bị đẩy muộn
    // vẫn tính PRIORITY_DELAY của ca đó như thường.
    const bool optional = p.is_optional(j);
    v.cost[PRIORITY_DELAY] = optional ? 0 : p.weight[j] * v.checkin / 60;
    // 7.16.2: việc gấp (gần hạn) bị để muộn thì phạt theo giờ check-in; ca chính urgency = 0.
    const double urgency = !optional && j < static_cast<int>(p.urgency.size()) ? p.urgency[j] : 0;
    v.cost[DEADLINE_URGENCY] = urgency * v.checkin / 60;
    return v;
}

// Bit "đã nghỉ trưa" nằm ngay sau bit các việc: bit n.
uint64_t rest_bit(const Problem& p) { return uint64_t{1} << p.size(); }
uint64_t bit_of(const Problem& p, int j) { return j == kBreak ? rest_bit(p) : uint64_t{1} << j; }

// Luật chặn nghỉ trưa. Trả false nếu bước này làm tuyến không hợp lệ.
bool allowed(const Problem& p, uint64_t mask, const Visit& v) {
    const bool rested = mask & rest_bit(p);
    if (v.task == kBreak) return p.needs_break() && !rested;        // Chỉ nghỉ một lần, khi tuyến cần nghỉ.
    if (p.is_optional(v.task) && v.done > p.shift_end) return false;  // 7.24: ca tuỳ chọn phải xong trong ca (cứng)
    return rested || !p.needs_break() || v.done <= p.break_latest;  // Chưa nghỉ: phải xong trước giờ chốt.
}

std::vector<Visit> walk(const Problem& p, const std::vector<int>& order) {
    std::vector<Visit> steps;
    uint64_t mask = 0;
    int i = -1;
    double clock = 0;
    for (int j : order) {
        steps.push_back(visit(p, mask, i, j, clock));
        mask |= bit_of(p, j);
        if (j != kBreak) i = j;  // Nghỉ không di chuyển.
        clock = steps.back().done;
    }
    return steps;
}

using Key = std::array<double, kMaxTiers>;  // Tầng không dùng để 0.
constexpr Key kInfeasible = {std::numeric_limits<double>::infinity()};

struct Label {
    double finish;
    Key costs;
    int parent;  // Nhãn trước, chỉ số trong pool. Riêng pool[0] (gốc: chưa làm gì, giờ 0) có parent = -1.
    int task;    // Việc vừa thêm, hoặc kBreak.
};

// Gom những gì QHĐ và heuristic dùng chung: rule theo tầng + kho nhãn.
struct Search {
    const Problem& p;
    const Rules& rules;
    int tiers;
    int finish_tier = -1;
    double finish_weight = 0;
    int skip_tier = -1;              // 7.24: tầng + trọng số của SKIP_OPTIONAL
    double skip_weight = 0;
    uint64_t mandatory = 0;          // bit các ca bắt buộc (phải có trong tuyến)
    uint64_t optional_tasks = 0;     // bit các ca tuỳ chọn
    std::vector<Label> pool{{0, {}, -1, -1}};  // pool[0] = gốc.

    Search(const Problem& problem, const Rules& r) : p(problem), rules(r), tiers(static_cast<int>(r.tiers.size())) {
        for (int t = 0; t < tiers; ++t)
            for (auto [rule, weight] : rules.tiers[t]) {
                if (rule == FINISH && weight) finish_tier = t, finish_weight = weight;
                if (rule == SKIP_OPTIONAL) skip_tier = t, skip_weight = weight;
            }
        for (int j = 0; j < p.size(); ++j) (p.is_optional(j) ? optional_tasks : mandatory) |= uint64_t{1} << j;
    }

    // Khóa của một tuyến KẾT THÚC ở nhãn này (đã làm `mask`): cộng phạt cho mỗi ca tuỳ chọn không làm.
    Key final_key(const Label& label, uint64_t mask) const {
        Key k = key(label);
        if (skip_tier >= 0) k[skip_tier] += skip_weight * __builtin_popcountll(optional_tasks & ~mask);
        return k;
    }

    // Từ nhãn `parent` (đang ở i, đã làm `mask`) đi tới j. Vi phạm luật nghỉ trưa → không có.
    std::optional<Label> extend(int parent, uint64_t mask, int i, int j) const {
        const Label& from = pool[parent];
        Visit v = visit(p, mask, i, j, from.finish);
        if (!allowed(p, mask, v)) return std::nullopt;
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

    // Chấm một thứ tự có sẵn (có thể chứa kBreak). Vi phạm luật nghỉ, thiếu/thừa việc → kInfeasible.
    // partial = true (Phase 8, LNS đang dựng lại): không đòi đủ ca bắt buộc — chỉ so các tuyến dở cùng tập việc.
    Key evaluate(const std::vector<int>& order, bool partial = false) {
        pool.resize(1);
        int label = 0, i = -1;
        uint64_t mask = 0;
        for (int j : order) {
            if (j != kBreak && (j < 0 || j >= p.size() || (mask >> j & 1))) return kInfeasible;
            auto next = extend(label, mask, i, j);
            if (!next) return kInfeasible;
            pool.push_back(*next);
            label = static_cast<int>(pool.size()) - 1;
            mask |= bit_of(p, j);
            if (j != kBreak) i = j;
        }
        if (!partial && (mask & mandatory) != mandatory) return kInfeasible;  // Thiếu ca bắt buộc (ca tuỳ chọn được thiếu).
        return final_key(pool[label], mask);
    }
};

Solution exact(Search& s) {
    const Problem& p = s.p;
    const int n = p.size();
    const uint64_t rest = rest_bit(p), all_tasks = rest - 1;
    const uint64_t top = p.needs_break() ? (all_tasks | rest) : all_tasks;
    // Bảng QHĐ: dp(mask, last) = các nhãn (tuyến dở dang) đã làm đúng tập `mask` (kể cả bit nghỉ),
    // việc thật làm cuối là `last` (-1 = chưa làm việc nào, vẫn ở chỗ xuất phát).
    // Trải phẳng 2 chiều thành 1 chiều: ô [mask][last] nằm ở vị trí mask * (n + 1) + (last + 1).
    std::vector<std::vector<int>> table(static_cast<size_t>(top + 1) * (n + 1));
    auto dp = [&](uint64_t mask, int last) -> std::vector<int>& { return table[mask * (n + 1) + (last + 1)]; };

    bool capped = false;
    auto step = [&](int label, uint64_t mask, int i, int j) {  // dp[mask ∪ {j}][j] ← nhãn + visit(i → j). Nghỉ: vẫn đứng ở i.
        if (auto next = s.extend(label, mask, i, j))
            capped |= s.keep(dp(mask | bit_of(p, j), j == kBreak ? i : j), *next);
    };
    for (int j = 0; j < n; ++j) step(0, 0, -1, j);
    step(0, 0, -1, kBreak);  // Nghỉ ngay từ đầu (chỉ được khi tuyến cần nghỉ).
    for (uint64_t mask = 1; mask < top; ++mask)
        for (int i = -1; i < n; ++i) {
            if (i < 0 ? (mask & all_tasks) != 0 : !(mask >> i & 1)) continue;  // Ô không thể có.
            for (int label : dp(mask, i)) {  // Nhãn mới luôn vào ô có mask lớn hơn, nên duyệt thẳng được.
                for (int j = 0; j < n; ++j)
                    if (!(mask >> j & 1)) step(label, mask, i, j);
                if (!(mask & rest)) step(label, mask, i, kBreak);
            }
        }

    // Kết thúc hợp lệ: đã làm đủ ca BẮT BUỘC (7.24: ca tuỳ chọn làm hay bỏ tùy khóa nhỏ hơn — mỗi ô của bảng đã là một
    // tập con, chọn tập ở đây không tốn thêm), có nghỉ hoặc không (không nghỉ thì luật chặn bảo đảm xong trước giờ chốt).
    // Không có ca tuỳ chọn → chỉ còn mask ⊇ all_tasks, như trước.
    int best = -1;
    Key best_key{};
    if (s.mandatory == 0) best = 0, best_key = s.final_key(s.pool[0], 0);  // tuyến rỗng: bỏ mọi ca tuỳ chọn
    for (uint64_t mask = 0; mask <= top; ++mask) {
        if ((mask & s.mandatory) != s.mandatory) continue;
        for (int last = -1; last < n; ++last)
            for (int label : dp(mask, last))
                if (Key k = s.final_key(s.pool[label], mask); best < 0 || k < best_key) best = label, best_key = k;
    }
    return {s.order_of(best), {}, capped ? Source::Approximate : Source::Optimal};
}

// Vòng cải thiện tối đa. Giới hạn theo vòng chứ không theo đồng hồ: cùng input luôn ra cùng tuyến trên mọi máy.
// Đo 2026-10-02 (bài ngẫu nhiên kiểu test_dp): 40 việc ~25 ms (chậm nhất ~50 ms), 64 việc ~150 ms (chậm nhất ~210 ms).
constexpr int kMaxImproveRounds = 100;

// Cải thiện cục bộ tới khi không nước nào làm khóa giảm (hoặc hết `rounds` vòng):
//   or-opt: nhấc một đoạn 1–3 phần tử liên tiếp đặt sang chỗ khác (VD A B C D → C A B D: đưa việc có hẹn sớm lên)
//   2-opt:  đảo một đoạn (A B C D → A C B D).
// Có giờ hẹn nên or-opt hiệu quả hơn 2-opt (đảo đoạn làm các việc trong đoạn chạy ngược giờ). Nghỉ trưa (kBreak) cũng
// được dời như một phần tử; nước đi phạm luật nghỉ → evaluate trả kInfeasible, tự bị loại. Mỗi vòng O(n³).
// reverse_first: mỗi vòng thử 2-opt trước or-opt (thứ tự khác → rơi vào cực tiểu khác). relocate = false: chỉ 2-opt.
std::vector<int> improve(Search& s, std::vector<int> order, bool reverse_first, bool relocate = true,
                         int rounds = kMaxImproveRounds) {
    Key best_key = s.evaluate(order);
    auto take = [&](std::vector<int>& candidate) {
        Key k = s.evaluate(candidate);
        if (!(k < best_key)) return false;
        order.swap(candidate);
        best_key = k;
        return true;
    };
    auto or_opt = [&] {
        bool improved = false;
        const int m = static_cast<int>(order.size());
        for (int len = 1; len <= 3; ++len)
            for (int a = 0; a + len <= m; ++a)
                for (int b = 0; b <= m - len; ++b) {
                    if (b == a) continue;
                    std::vector<int> candidate = order;
                    std::vector<int> segment(candidate.begin() + a, candidate.begin() + a + len);
                    candidate.erase(candidate.begin() + a, candidate.begin() + a + len);
                    candidate.insert(candidate.begin() + b, segment.begin(), segment.end());
                    improved |= take(candidate);
                }
        return improved;
    };
    auto two_opt = [&] {
        bool improved = false;
        const int m = static_cast<int>(order.size());
        for (int a = 0; a < m - 1; ++a)
            for (int b = a + 1; b < m; ++b) {
                std::vector<int> candidate = order;
                std::reverse(candidate.begin() + a, candidate.begin() + b + 1);
                improved |= take(candidate);
            }
        return improved;
    };
    // 7.24 chèn rẻ nhất: mỗi ca tuỳ chọn chưa làm thử mọi vị trí (tuyến cần nghỉ mà chưa có nghỉ: thử cả cặp nghỉ + ca),
    // lấy vị trí khóa nhỏ nhất, nhận nếu khóa giảm. Gỡ: bỏ một ca tuỳ chọn đang làm nếu khóa giảm.
    const Problem& p = s.p;
    auto insert_optional = [&] {
        bool improved = false;
        for (int o = 0; o < p.size(); ++o) {
            if (!p.is_optional(o) || std::find(order.begin(), order.end(), o) != order.end()) continue;
            const bool add_break = p.needs_break() && std::find(order.begin(), order.end(), kBreak) == order.end();
            std::vector<int> best_candidate;
            Key best_candidate_key = best_key;
            for (int b = 0; b <= static_cast<int>(order.size()); ++b)
                for (int with_break = 0; with_break <= (add_break ? 1 : 0); ++with_break) {
                    std::vector<int> candidate = order;
                    candidate.insert(candidate.begin() + b, o);
                    if (with_break) candidate.insert(candidate.begin() + b, kBreak);
                    if (Key k = s.evaluate(candidate); k < best_candidate_key) best_candidate.swap(candidate), best_candidate_key = k;
                }
            if (!best_candidate.empty()) order.swap(best_candidate), best_key = best_candidate_key, improved = true;
        }
        return improved;
    };
    auto remove_optional = [&] {
        bool improved = false;
        for (int a = 0; a < static_cast<int>(order.size());) {
            if (p.is_optional(order[a])) {
                std::vector<int> candidate = order;
                candidate.erase(candidate.begin() + a);
                if (take(candidate)) {
                    improved = true;
                    continue;  // phần tử mới ở vị trí a: xét lại
                }
            }
            ++a;
        }
        return improved;
    };
    const bool has_optional = s.optional_tasks != 0;
    for (int round = 0; round < rounds; ++round) {
        bool improved = false;
        if (has_optional) improved |= insert_optional();
        if (reverse_first) improved |= two_opt();
        if (relocate) improved |= or_opt();
        if (!reverse_first) improved |= two_opt();
        if (has_optional) improved |= remove_optional();
        if (!improved) break;
    }
    return order;
}

// Tham lam: mỗi bước chọn việc (hoặc nghỉ) làm khóa tăng ít nhất. Sau đó improve() từ 2 điểm xuất phát (tuyến tham
// lam; tuyến tham lam + 2-opt 2 vòng) × 2 thứ tự nước đi, lấy kết quả tốt nhất. Đo 2026-10-02 trên 13–15 việc: lệch
// tầng 1 so với tối ưu ~0,8–1,0 (một lần improve ~2,2–3,0; tham lam + 2-opt cũ ~3,3–4,6). Tất định: cùng input cùng tuyến.
std::vector<int> heuristic(Search& s) {
    const Problem& p = s.p;
    const int n = p.size();
    std::vector<int> order;
    int label = 0, i = -1, done = 0;
    uint64_t mask = 0;
    const int need = __builtin_popcountll(s.mandatory);  // 7.24: tham lam chỉ xếp ca bắt buộc; ca tuỳ chọn do improve chèn
    while (done < need) {
        int best_j = -2;
        Label best{};
        Key best_key{};
        auto consider = [&](int j) {
            auto candidate = s.extend(label, mask, i, j);
            if (!candidate) return;
            if (Key k = s.key(*candidate); best_j == -2 || k < best_key) best_j = j, best = *candidate, best_key = k;
        };
        for (int j = 0; j < n; ++j)
            if (!(mask >> j & 1) && !p.is_optional(j)) consider(j);
        // Chỉ xét nghỉ khi đã tới giờ nghỉ, hoặc không còn việc nào làm kịp trước giờ chốt:
        // tránh tham lam chọn "nghỉ" từ sáng sớm rồi đứng chờ tới trưa.
        if (best_j == -2 || s.pool[label].finish >= p.break_open) consider(kBreak);
        // Luôn có ứng viên: chưa nghỉ thì nghỉ được; đã nghỉ (hoặc không cần nghỉ) thì việc nào cũng được.
        s.pool.push_back(best);
        label = static_cast<int>(s.pool.size()) - 1;
        order.push_back(best_j);
        mask |= bit_of(p, best_j);
        if (best_j != kBreak) i = best_j, ++done;
    }
    if (n < 3 && s.optional_tasks == 0) return order;
    const std::vector<int> reversed = improve(s, order, true, false, 2);  // điểm xuất phát thứ 2 (2-opt cũ)
    std::vector<int> best;
    Key best_key = kInfeasible;
    for (const std::vector<int>* start : {&std::as_const(order), &reversed})
        for (bool reverse_first : {false, true}) {
            std::vector<int> candidate = improve(s, *start, reverse_first);
            if (Key k = s.evaluate(candidate); best.empty() || k < best_key) best.swap(candidate), best_key = k;
        }
    return best;
}

// ---- Phase 8: bài > max_exact_tasks — LNS (phá – dựng lại) và QHĐ cửa sổ trượt ----

// QHĐ cửa sổ: sắp lại TỐI ƯU w phần tử order[a, a + w) (việc và / hoặc nghỉ trưa), giữ nguyên phần trước và phần sau;
// chấm theo khóa CẢ tuyến (phần sau bị đẩy sớm / muộn được tính). Bảng như exact() nhưng trên w phần tử, xuất phát từ
// nhãn cuối phần trước. Ô cuối giữ mọi nhãn không bị trội (giờ xong + phạt) → nối phần sau vào từng nhãn, lấy khóa nhỏ
// nhất: chính xác vì phạt phần sau không giảm khi xong muộn hơn (cùng việc cuối). Trả true nếu order đổi (khóa giảm).
bool window_dp(Search& s, std::vector<int>& order, int a, int w, Key& best_key) {
    const Problem& p = s.p;
    s.pool.resize(1);
    int start = 0, i = -1;
    uint64_t mask = 0;
    for (int k = 0; k < a; ++k) {
        auto next = s.extend(start, mask, i, order[k]);
        if (!next) return false;
        s.pool.push_back(*next);
        start = static_cast<int>(s.pool.size()) - 1;
        mask |= bit_of(p, order[k]);
        if (order[k] != kBreak) i = order[k];
    }
    const std::vector<int> elems(order.begin() + a, order.begin() + a + w);
    const int full = (1 << w) - 1;
    std::vector<uint64_t> bits(full + 1, 0);  // bit toàn cục của một tập con cửa sổ
    for (int lm = 1; lm <= full; ++lm) bits[lm] = bits[lm & (lm - 1)] | bit_of(p, elems[__builtin_ctz(lm)]);
    // Ô [tập con][phần tử thật làm cuối + 1]; -1 = vẫn ở chỗ cuối phần trước.
    std::vector<std::vector<int>> table(static_cast<size_t>(full + 1) * (w + 1));
    auto cell = [&](int lm, int last) -> std::vector<int>& { return table[lm * (w + 1) + last + 1]; };
    cell(0, -1).push_back(start);
    for (int lm = 0; lm < full; ++lm)
        for (int last = -1; last < w; ++last) {
            if (last >= 0 && !(lm >> last & 1)) continue;
            const int at = last < 0 ? i : elems[last];
            for (int label : cell(lm, last))
                for (int e = 0; e < w; ++e) {
                    if (lm >> e & 1) continue;
                    if (auto next = s.extend(label, mask | bits[lm], at, elems[e]))
                        s.keep(cell(lm | 1 << e, elems[e] == kBreak ? last : e), *next);
                }
        }
    int best = -1;
    for (int last = -1; last < w; ++last)
        for (int label : cell(full, last)) {
            int tail = label, at = last < 0 ? i : elems[last];
            uint64_t done = mask | bits[full];
            for (size_t k = a + w; k < order.size() && tail >= 0; ++k) {
                auto next = s.extend(tail, done, at, order[k]);
                if (!next) {
                    tail = -1;
                    break;
                }
                s.pool.push_back(*next);
                tail = static_cast<int>(s.pool.size()) - 1;
                done |= bit_of(p, order[k]);
                if (order[k] != kBreak) at = order[k];
            }
            if (tail < 0) continue;
            if (Key k = s.final_key(s.pool[tail], done); k < best_key) best_key = k, best = label;
        }
    if (best < 0) return false;
    std::vector<int> middle;
    for (int label = best; label != start; label = s.pool[label].parent) middle.push_back(s.pool[label].task);
    std::reverse(middle.begin(), middle.end());
    std::copy(middle.begin(), middle.end(), order.begin() + a);
    return true;
}

constexpr int kMaxWindowPasses = 10;

// Trượt cửa sổ w phần tử từ đầu tới cuối tuyến (bước 1), lặp lượt tới khi không cửa sổ nào cải thiện.
std::vector<int> window_sweep(Search& s, std::vector<int> order, int w) {
    w = std::min(w, static_cast<int>(order.size()));
    if (w < 2) return order;
    Key best_key = s.evaluate(order);
    if (std::isinf(best_key[0])) return order;
    for (int pass = 0; pass < kMaxWindowPasses; ++pass) {
        bool improved = false;
        for (int a = 0; a + w <= static_cast<int>(order.size()); ++a) improved |= window_dp(s, order, a, w, best_key);
        if (!improved) break;
    }
    return order;
}

// LNS: lặp `iterations` vòng: PHÁ (rút k = 2–5 phần tử theo một trong 5 cách) → DỰNG LẠI (chèn từng phần tử vào chỗ
// khóa nhỏ nhất; ca tuỳ chọn / nghỉ trưa được phép bỏ) → NHẬN nếu khóa không tăng. 60 vòng không có tuyến tốt nhất mới
// thì quay về tuyến tốt nhất. window > 1: mỗi tuyến tốt nhất mới được QHĐ cửa sổ trượt đánh bóng. Bộ sinh ngẫu nhiên
// seed cố định → tất định.
// adaptive (Phase 8.1, "alns_dp"): QHĐ thành bước đi TRONG vòng lặp + chọn cách thích nghi (ALNS). Thêm 2 cách:
//   5 "kéo cụm + QHĐ": rút 2–4 việc liên quan (gần về địa lý hoặc giờ) đang rải rác, đặt liền nhau ở vị trí chèn tốt
//     nhất của việc đầu, rồi QHĐ sắp lại tối ưu cửa sổ quanh đó (các việc vừa kéo + hàng xóm);
//   6 "cửa sổ ngẫu nhiên": QHĐ một cửa sổ ở vị trí ngẫu nhiên của tuyến HIỆN TẠI (kể cả khi chưa phải tốt nhất).
//   Mỗi cách có trọng số, chọn kiểu quay xổ số; được điểm khi ra tuyến tốt nhất mới (5) / tốt hơn hiện tại (2) /
//   được nhận (0,5); cứ 100 vòng trọng số = 0,8 × cũ + 0,2 × điểm TB mỗi lần dùng (sàn 0,05).
constexpr int kInnerWindow = 6;  // cửa sổ trong vòng lặp (64 ô): rẻ hơn cửa sổ đánh bóng

std::vector<int> lns(Search& s, std::vector<int> start, int iterations, int window, bool adaptive = false) {
    const Problem& p = s.p;
    const Rules& rules = s.rules;
    std::mt19937 rng(20261008u + static_cast<unsigned>(p.size()));
    auto pick = [&](int n) { return static_cast<int>(rng() % static_cast<unsigned>(n)); };
    std::vector<int> best = window > 1 ? window_sweep(s, start, window) : start;
    Key best_key = s.evaluate(best);
    if (std::isinf(best_key[0])) return start;
    std::vector<int> current = best;
    Key current_key = best_key;
    int since_best = 0;
    constexpr int kOps = 7;
    double weight[kOps], score[kOps] = {}, uses[kOps] = {};
    std::fill(weight, weight + kOps, 1.0);
    auto roulette = [&] {
        double total = 0;
        for (double w : weight) total += w;
        double r = rng() / 4294967296.0 * total;
        for (int op = 0; op < kOps; ++op)
            if ((r -= weight[op]) < 0) return op;
        return kOps - 1;
    };
    // Rút k việc thật gần `seed` nhất (theo km hoặc theo giờ: hạn B, không có thì giờ check-in hiện tại).
    auto related = [&](const std::vector<int>& route, const std::vector<int>& real, bool geo, int k, std::vector<char>& drop) {
        const std::vector<Visit> steps = walk(p, route);
        const int seed = real[pick(static_cast<int>(real.size()))];
        auto distance = [&](int pos) {
            if (geo) return p.travel.km[route[seed] + 1][route[pos] + 1];
            auto when = [&](int q) { return std::isnan(p.due[route[q]]) ? steps[q].checkin : p.due[route[q]]; };
            return std::abs(when(pos) - when(seed));
        };
        std::vector<int> near = real;
        std::stable_sort(near.begin(), near.end(), [&](int x, int y) { return distance(x) < distance(y); });
        for (int c = 0; c < k; ++c) drop[near[c]] = 1;
    };
    auto hard_first = [&](std::vector<int>& items) {  // hạn B sớm trước, rồi ưu tiên cao
        std::stable_sort(items.begin(), items.end(), [&](int x, int y) {
            auto due = [&](int j) { return j == kBreak || std::isnan(p.due[j]) ? 1e18 : p.due[j]; };
            if (due(x) != due(y)) return due(x) < due(y);
            return (x == kBreak ? 0 : p.weight[x]) > (y == kBreak ? 0 : p.weight[y]);
        });
    };
    for (int it = 0; it < iterations; ++it) {
        const int op = adaptive ? roulette() : it % 5;
        std::vector<int> route = current;
        std::vector<int> real;  // vị trí các việc thật trong route
        for (int k = 0; k < static_cast<int>(route.size()); ++k)
            if (route[k] != kBreak) real.push_back(k);
        if (real.size() < 3) break;
        if (op == 6) {  // cửa sổ ngẫu nhiên trên tuyến hiện tại
            const int w = std::min(kInnerWindow, static_cast<int>(route.size()));
            Key key = current_key;
            window_dp(s, route, pick(static_cast<int>(route.size()) - w + 1), w, key);
        } else if (op == 5) {  // kéo cụm + QHĐ
            const int k = std::min<int>(2 + pick(3), static_cast<int>(real.size()) - 1);
            std::vector<char> drop(route.size(), 0);
            related(route, real, pick(2) == 0, k, drop);
            std::vector<int> removed, kept;
            for (size_t q = 0; q < route.size(); ++q) (drop[q] ? removed : kept).push_back(route[q]);
            hard_first(removed);
            int anchor = 0;
            Key anchor_key = kInfeasible;
            for (int b = 0; b <= static_cast<int>(kept.size()); ++b) {
                std::vector<int> candidate = kept;
                candidate.insert(candidate.begin() + b, removed[0]);
                if (Key key = s.evaluate(candidate, true); b == 0 || key < anchor_key) anchor = b, anchor_key = key;
            }
            route = kept;
            route.insert(route.begin() + anchor, removed.begin(), removed.end());
            const int m = static_cast<int>(route.size());
            const int w = std::min(m, std::max(kInnerWindow, k + 2));
            const int from = std::clamp(anchor - (w - k) / 2, 0, m - w);
            Key key = s.evaluate(route);
            window_dp(s, route, from, w, key);
        } else {
            const int k = std::min<int>(2 + pick(4), static_cast<int>(real.size()) - 1);
            std::vector<char> drop(route.size(), 0);
            switch (op) {
                case 0:  // ngẫu nhiên (có thể trúng nghỉ trưa)
                    for (int c = 0; c < k; ++c) drop[pick(static_cast<int>(route.size()))] = 1;
                    break;
                case 1:  // một đoạn liền (một "buổi")
                    for (int c = 0, from = pick(static_cast<int>(route.size())); c < k && from + c < static_cast<int>(route.size()); ++c)
                        drop[from + c] = 1;
                    break;
                case 2:  // gần nhau về địa lý quanh một việc ngẫu nhiên
                case 3:  // gần nhau về giờ
                    related(route, real, op == 2, k, drop);
                    break;
                default: {  // tệ nhất: bước có phạt (theo tầng) lớn nhất
                    const std::vector<Visit> steps = walk(p, route);
                    std::vector<std::pair<Key, int>> cost;
                    for (int pos : real) {
                        Key c{};
                        for (int t = 0; t < s.tiers; ++t)
                            for (auto [rule, weight] : rules.tiers[t])
                                if (rule != FINISH) c[t] += weight * steps[pos].cost[rule];
                        cost.push_back({c, pos});
                    }
                    std::stable_sort(cost.begin(), cost.end(), [](const auto& x, const auto& y) { return y.first < x.first; });
                    for (int c = 0; c < k; ++c) drop[cost[c].second] = 1;
                }
            }
            std::vector<int> removed, kept;
            for (size_t q = 0; q < route.size(); ++q) (drop[q] ? removed : kept).push_back(route[q]);
            route.swap(kept);
            // Ca tuỳ chọn đang bỏ: 1/4 cơ hội được thử chèn lại ở vòng này.
            for (int j = 0; j < p.size(); ++j)
                if (p.is_optional(j) && std::find(current.begin(), current.end(), j) == current.end() && pick(4) == 0)
                    removed.push_back(j);
            // Thứ tự dựng: nghỉ trưa trước (thiếu nghỉ thì tuyến dở hay phạm luật); rồi xen kẽ "khó trước" (hạn B sớm,
            // ưu tiên cao) và ngẫu nhiên.
            if (pick(2) == 0) {
                hard_first(removed);
            } else {
                for (int q = static_cast<int>(removed.size()) - 1; q > 0; --q) std::swap(removed[q], removed[pick(q + 1)]);
            }
            std::stable_partition(removed.begin(), removed.end(), [](int j) { return j == kBreak; });
            for (int j : removed) {
                const bool may_skip = j == kBreak || p.is_optional(j);
                std::vector<int> chosen = route;
                Key chosen_key = may_skip ? s.evaluate(route, true) : kInfeasible;
                bool have = may_skip;
                for (int b = 0; b <= static_cast<int>(route.size()); ++b) {
                    std::vector<int> candidate = route;
                    candidate.insert(candidate.begin() + b, j);
                    if (Key key = s.evaluate(candidate, true); !have || key < chosen_key)
                        chosen.swap(candidate), chosen_key = key, have = true;
                }
                route.swap(chosen);
            }
        }
        uses[op] += 1;
        const Key key = s.evaluate(route);
        if (!std::isinf(key[0])) {
            const bool new_best = key < best_key, better = key < current_key;
            if (!(current_key < key)) current.swap(route), current_key = key, score[op] += new_best ? 5 : better ? 2 : 0.5;
            if (current_key < best_key) {
                best = window > 1 ? window_sweep(s, current, window) : current;
                best_key = s.evaluate(best);
                current = best, current_key = best_key, since_best = 0;
            } else if (++since_best >= 60) {
                current = best, current_key = best_key, since_best = 0;
            }
        }
        if (adaptive && (it + 1) % 100 == 0) {
            for (int o = 0; o < kOps; ++o) {
                if (uses[o] > 0) weight[o] = std::max(0.05, 0.8 * weight[o] + 0.2 * score[o] / uses[o]);
                score[o] = uses[o] = 0;
            }
        }
    }
    best = improve(s, best, false);
    if (window > 1) best = window_sweep(s, best, window);
    return best;
}

}  // namespace

Solution solve(const Problem& p, const Rules& rules) {
    Search s(p, rules);
    Solution result{{}, {}, Source::Optimal};
    if (p.size() > rules.max_exact_tasks) {
        std::vector<int> order = heuristic(s);
        if (rules.large_method != "improve")  // Phase 8 / 8.1
            order = lns(s, order, rules.lns_iterations, rules.large_method == "lns" ? 0 : rules.window_size,
                        rules.large_method == "alns_dp");
        result = {order, {}, Source::Heuristic};
    } else if (p.size() > 0) {
        result = exact(s);
    }
    result.steps = walk(p, result.order);
    return result;
}

Solution solve_from(const Problem& p, const Rules& rules, const std::vector<int>& start) {
    Search s(p, rules);
    std::vector<int> best = start;
    Key best_key = s.evaluate(start);
    for (bool reverse_first : {false, true}) {
        std::vector<int> candidate = improve(s, start, reverse_first);
        if (Key k = s.evaluate(candidate); k < best_key) best.swap(candidate), best_key = k;
    }
    // Phase 8: tiếp tục bằng LNS (cùng large_method với solve) — rút / chèn lại cả ca tuỳ chọn, chỉ nhận khi khóa giảm.
    if (rules.large_method != "improve" && !std::isinf(best_key[0])) {
        std::vector<int> candidate = lns(s, best, rules.lns_iterations, rules.large_method == "lns" ? 0 : rules.window_size,
                                         rules.large_method == "alns_dp");
        if (Key k = s.evaluate(candidate); k < best_key) best.swap(candidate), best_key = k;
    }
    return {best, walk(p, best), Source::Heuristic};
}

std::vector<Visit> simulate(const Problem& p, const std::vector<int>& order) { return walk(p, order); }

std::vector<double> objective(const Problem& p, const Rules& rules, const std::vector<int>& order) {
    Search s(p, rules);
    Key k = s.evaluate(order);
    return {k.begin(), k.begin() + s.tiers};
}

}  // namespace ktv
