#include "ktv/travel.hpp"

#include <httplib.h>

#include <cmath>
#include <chrono>
#include <algorithm>
#include <cstdio>
#include <unordered_map>

namespace ktv {

double distance_km(Point a, Point b) {
    constexpr double rad = M_PI / 180;
    double lat1 = a.lat * rad, lat2 = b.lat * rad;
    double h = std::pow(std::sin((lat2 - lat1) / 2), 2) +
               std::cos(lat1) * std::cos(lat2) * std::pow(std::sin((b.lng - a.lng) * rad / 2), 2);
    return 6371.0088 * 2 * std::asin(std::sqrt(h));
}

Matrix haversine_matrix(const std::vector<Point>& points, double speed_kmh, double factor) {
    size_t n = points.size();
    Matrix m{std::vector(n, std::vector<double>(n)), std::vector(n, std::vector<double>(n))};
    for (size_t i = 0; i < n; ++i)
        for (size_t j = 0; j < n; ++j) {
            m.km[i][j] = distance_km(points[i], points[j]) * factor;
            m.minutes[i][j] = m.km[i][j] / speed_kmh * 60;
        }
    return m;
}

std::string point_key(Point p) {
    char text[64];
    std::snprintf(text, sizeof text, "%.6f,%.6f", p.lat, p.lng);
    return text;
}

namespace {

std::string index_list(const std::vector<int>& items) {
    std::string text;
    for (size_t k = 0; k < items.size(); ++k) text += (k ? ";" : "") + std::to_string(items[k]);
    return text;
}

// Một lần gọi OSRM /table: hàng = sources (rỗng = mọi điểm), cột = destinations (rỗng = mọi điểm).
std::optional<json> osrm_table(const std::string& url, const std::vector<Point>& points, const std::vector<int>& sources,
                               const std::vector<int>& destinations, std::string& error, double timeout_seconds) {
    // url có thể kèm đường dẫn (OSRM sau proxy): "http://host:5000/osrm" → host + tiền tố "/osrm".
    size_t scheme = url.find("://");
    size_t slash = url.find('/', scheme == std::string::npos ? 0 : scheme + 3);
    std::string host = url.substr(0, slash), path = slash == std::string::npos ? "" : url.substr(slash);
    if (!path.empty() && path.back() == '/') path.pop_back();
    path += "/table/v1/driving/";
    for (size_t i = 0; i < points.size(); ++i) {
        char pair[64];
        std::snprintf(pair, sizeof pair, "%s%.6f,%.6f", i ? ";" : "", points[i].lng, points[i].lat);  // OSRM: lng,lat.
        path += pair;
    }
    path += "?annotations=duration,distance";
    if (!sources.empty()) path += "&sources=" + index_list(sources);
    if (!destinations.empty()) path += "&destinations=" + index_list(destinations);

    httplib::Client client(host);
    client.set_connection_timeout(1);
    client.set_read_timeout(std::chrono::milliseconds(static_cast<long long>(timeout_seconds * 1000)));
    auto response = client.Get(path);
    if (!response) {
        error = "OSRM không phản hồi: " + httplib::to_string(response.error());
        return std::nullopt;
    }
    json body = json::parse(response->body, nullptr, false);
    if (response->status != 200 || body.is_discarded() || body.value("code", "") != "Ok") {
        error = "OSRM trả lỗi HTTP " + std::to_string(response->status) +
                (body.is_object() ? " " + body.value("message", body.value("code", "")) : "");
        return std::nullopt;
    }
    const size_t rows = sources.empty() ? points.size() : sources.size();
    const size_t cols = destinations.empty() ? points.size() : destinations.size();
    auto shaped = [&](const json& table) {  // Đủ rows hàng, mỗi hàng cols ô. Méo → lùi về chim bay, không ném.
        if (!table.is_array() || table.size() != rows) return false;
        for (const json& row : table)
            if (!row.is_array() || row.size() != cols) return false;
        return true;
    };
    if (!body.contains("distances") || !body.contains("durations") || !shaped(body["distances"]) ||
        !shaped(body["durations"])) {
        error = "OSRM trả ma trận sai kích thước";
        return std::nullopt;
    }
    return body;
}

}  // namespace

std::optional<Matrix> osrm_matrix(const std::string& url, const std::vector<Point>& points, double speed_kmh,
                                  std::string& error, double timeout_seconds, TravelCache* cache) {
    const int n = static_cast<int>(points.size());
    Matrix m = haversine_matrix(points, speed_kmh, kRoadFactor);  // Ô OSRM không tới được (null) thì giữ ước lượng.
    // Bảng làm việc: điểm việc của lần này đứng đầu, rồi điểm cũ khác của cache. slot[i] = vị trí của points[i] (i ≥ 1).
    TravelCache next;
    std::vector<int> slot(n, -1);
    std::unordered_map<std::string, int> where;
    for (int i = 1; i < n; ++i) {
        auto [it, fresh] = where.emplace(point_key(points[i]), static_cast<int>(next.points.size()));
        if (fresh) next.points.push_back(it->first);
        slot[i] = it->second;
    }
    std::vector<int> old_slot;  // vị trí trong next của từng điểm cache cũ
    if (cache) {
        for (const std::string& key : cache->points) {
            auto [it, fresh] = where.emplace(key, static_cast<int>(next.points.size()));
            if (fresh) next.points.push_back(key);
            old_slot.push_back(it->second);
        }
    }
    const size_t k = next.points.size();
    next.meters.assign(k, std::vector<double>(k, kUnknown));
    next.seconds = next.meters;
    for (size_t a = 0; a < k; ++a) next.meters[a][a] = next.seconds[a][a] = 0;
    if (cache) {
        const size_t old = std::min({old_slot.size(), cache->meters.size(), cache->seconds.size()});
        for (size_t a = 0; a < old; ++a)
            for (size_t b = 0; b < old && b < cache->meters[a].size() && b < cache->seconds[a].size(); ++b)
                if (cache->meters[a][b] != kUnknown && cache->seconds[a][b] != kUnknown) {
                    next.meters[old_slot[a]][old_slot[b]] = cache->meters[a][b];
                    next.seconds[old_slot[a]][old_slot[b]] = cache->seconds[a][b];
                }
    }
    auto known = [&](int i, int j) { return next.meters[slot[i]][slot[j]] != kUnknown; };
    // Điểm cần hỏi hàng + cột: phủ mọi cặp chưa biết (một trong hai chiều) bằng ít điểm nhất — tham lam theo số cặp
    // chưa biết (điểm mới chưa có trong cache thiếu với mọi điểm → được chọn trước, điểm cũ không bị hỏi lại).
    std::vector<int> missing;
    std::vector<std::vector<char>> open(n, std::vector<char>(n, 0));
    std::vector<int> degree(n, 0);
    for (int i = 1; i < n; ++i)
        for (int j = i + 1; j < n; ++j)
            if (!known(i, j) || !known(j, i)) open[i][j] = open[j][i] = 1, ++degree[i], ++degree[j];
    for (;;) {
        const int best = static_cast<int>(std::max_element(degree.begin(), degree.end()) - degree.begin());
        if (degree[best] == 0) break;
        missing.push_back(best);
        for (int j = 1; j < n; ++j)
            if (open[best][j]) open[best][j] = open[j][best] = 0, --degree[j];
        degree[best] = 0;
    }
    std::sort(missing.begin(), missing.end());

    int fetched = 0, added = 0;
    auto take = [&](const json& body, const std::vector<int>& rows, const std::vector<int>& cols) {
        const json& distances = body["distances"];
        const json& durations = body["durations"];
        for (size_t r = 0; r < rows.size(); ++r)
            for (size_t c = 0; c < cols.size(); ++c) {
                const int i = rows[r], j = cols[c];
                const bool road = distances[r][c].is_number() && durations[r][c].is_number();
                if (road) {
                    m.km[i][j] = distances[r][c].get<double>() / 1000;
                    m.minutes[i][j] = durations[r][c].get<double>() / 60;
                }
                ++fetched;
                if (i >= 1 && j >= 1 && next.meters[slot[i]][slot[j]] == kUnknown) {
                    next.meters[slot[i]][slot[j]] = road ? distances[r][c].get<double>() : kNoRoute;
                    next.seconds[slot[i]][slot[j]] = road ? durations[r][c].get<double>() : kNoRoute;
                    ++added;
                }
            }
    };
    std::vector<int> all(n);
    for (int i = 0; i < n; ++i) all[i] = i;
    if (!cache || 2 * static_cast<int>(missing.size()) >= n - 1) {  // không cache / thiếu nhiều: cả bảng một lần
        auto body = osrm_table(url, points, {}, {}, error, timeout_seconds);
        if (!body) return std::nullopt;
        take(*body, all, all);
    } else {
        for (int i = 1; i < n; ++i)  // ô đã biết lấy từ cache (kNoRoute: giữ chim bay)
            for (int j = 1; j < n; ++j)
                if (known(i, j) && next.meters[slot[i]][slot[j]] >= 0) {
                    m.km[i][j] = next.meters[slot[i]][slot[j]] / 1000;
                    m.minutes[i][j] = next.seconds[slot[i]][slot[j]] / 60;
                }
        std::vector<int> rows{0};  // hàng điểm 0 (KTV → các việc) + hàng các điểm thiếu
        rows.insert(rows.end(), missing.begin(), missing.end());
        auto body = osrm_table(url, points, rows, all, error, timeout_seconds);
        if (!body) return std::nullopt;
        take(*body, rows, all);
        if (!missing.empty()) {  // cột các điểm thiếu (từ mọi điểm việc tới chúng)
            std::vector<int> from(all.begin() + 1, all.end());
            auto cols = osrm_table(url, points, from, missing, error, timeout_seconds);
            if (!cols) return std::nullopt;
            take(*cols, from, missing);
        }
    }
    if (cache) {
        next.fetched_cells = cache->fetched_cells + fetched;  // cộng dồn: plan() gọi lại chính nó (lưới an toàn 8.27)
        next.added_cells = cache->added_cells + added;
        *cache = std::move(next);
    }
    return m;
}

}  // namespace ktv
