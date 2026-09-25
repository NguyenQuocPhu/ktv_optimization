// ============================================================================
// travel — KHOẢNG CÁCH: từ điểm i tới điểm j mất bao nhiêu km, bao nhiêu phút
// ============================================================================
// Hiểu nhanh:
//   Đưa vào danh sách điểm [nơi KTV đứng, việc 1, việc 2, …], nhận lại 2 bảng:
//     km[i][j]      – quãng đường từ điểm i tới điểm j
//     minutes[i][j] – thời gian đi tương ứng
//   Giống bảng tra khoảng cách giữa các thành phố trên bản đồ giấy.
//   Nguồn: OSRM (đường bộ thật, một lần gọi cho cả bảng) → lỗi thì chim bay × 1,3.
//
// Dùng thế nào:
//   std::string error;
//   if (auto m = osrm_matrix("http://127.0.0.1:5000", points, 30, error)) dùng *m;
//   else Matrix m = haversine_matrix(points, 30, kRoadFactor);   // ước lượng, error ghi lý do
//
// Trong file này có:
//   Matrix           – 2 bảng km và phút
//   kRoadFactor      – 1,3: đường bộ ≈ chim bay × 1,3 khi phải ước lượng
//   distance_km      – khoảng cách chim bay giữa 2 điểm
//   haversine_matrix – bảng chim bay (nhân thêm hệ số nếu muốn)
//   osrm_matrix      – bảng đường bộ từ OSRM; lỗi thì trả "không có" + lý do
//
// Ẩn trong travel.cpp: dựng URL, gọi HTTP, đọc JSON của OSRM.
// Phụ thuộc: api (kiểu Point).
// ============================================================================
#pragma once

#include <optional>
#include <string>
#include <vector>

#include "ktv/api.hpp"

namespace ktv {

// km[i][j], minutes[i][j]: từ điểm i tới điểm j. Đường bộ có thể khác chiều (đường một chiều).
struct Matrix {
    std::vector<std::vector<double>> km, minutes;
};

inline constexpr double kRoadFactor = 1.3;  // Đường bộ ≈ chim bay × 1,3 khi phải ước lượng.

double distance_km(Point a, Point b);                                                   // Chim bay (Haversine).
Matrix haversine_matrix(const std::vector<Point>& points, double speed_kmh, double factor = 1);  // Phút = km / tốc độ.

// Gọi OSRM /table một lần cho mọi điểm. Lỗi mạng / HTTP / JSON → không có + lý do trong error.
// Cặp điểm OSRM không tìm được đường → ô đó dùng chim bay × 1,3.
std::optional<Matrix> osrm_matrix(const std::string& url, const std::vector<Point>& points, double speed_kmh,
                                  std::string& error);

}  // namespace ktv
