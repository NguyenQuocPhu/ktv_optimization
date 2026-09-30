#include "ktv/travel.hpp"

#include <httplib.h>

#include <cmath>
#include <cstdio>

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

std::optional<Matrix> osrm_matrix(const std::string& url, const std::vector<Point>& points, double speed_kmh,
                                  std::string& error) {
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

    httplib::Client client(host);
    client.set_connection_timeout(1);
    client.set_read_timeout(3);
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
    const auto& distances = body["distances"];
    const auto& durations = body["durations"];
    size_t n = points.size();
    auto square = [n](const json& rows) {  // Đủ n hàng, mỗi hàng là mảng n ô. Méo → lùi về chim bay, không ném.
        if (!rows.is_array() || rows.size() != n) return false;
        for (const json& row : rows)
            if (!row.is_array() || row.size() != n) return false;
        return true;
    };
    if (!square(distances) || !square(durations)) {
        error = "OSRM trả ma trận sai kích thước";
        return std::nullopt;
    }
    Matrix m = haversine_matrix(points, speed_kmh, kRoadFactor);  // Ô nào OSRM không tới được (null) thì giữ ước lượng.
    for (size_t i = 0; i < n; ++i)
        for (size_t j = 0; j < n; ++j)
            if (distances[i][j].is_number() && durations[i][j].is_number()) {
                m.km[i][j] = distances[i][j].get<double>() / 1000;
                m.minutes[i][j] = durations[i][j].get<double>() / 60;
            }
    return m;
}

}  // namespace ktv
