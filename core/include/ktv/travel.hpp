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
//   TravelCache      – bảng OSRM đã biết giữa các điểm việc (service lưu Redis): chỉ hỏi OSRM phần còn thiếu
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

// Phase 8.29 — bảng OSRM đã biết giữa các ĐIỂM VIỆC (không gồm điểm 0 = nơi KTV đứng: đổi mỗi lần). points[k] =
// point_key (đúng chuỗi tọa độ gửi OSRM); meters / seconds[a][b] = từ points[a] tới points[b]: ≥ 0 giá trị OSRM ·
// kNoRoute OSRM không có đường (ô dùng chim bay × 1,3, không hỏi lại) · kUnknown chưa biết. Khoảng cách giữa các việc
// không đổi khi KTV di chuyển → lần sau chỉ hỏi OSRM hàng điểm 0 + hàng / cột điểm chưa biết (đo 07/11: cả bảng 99 điểm
// 2,5–5,6 s, một hàng 0,1–0,2 s).
struct TravelCache {
    std::vector<std::string> points;
    std::vector<std::vector<double>> meters, seconds;
    int fetched_cells = 0;  // CỘNG DỒN qua các lần gọi (caller đặt 0): số ô đã hỏi OSRM (gồm hàng điểm 0)
    int added_cells = 0;    // CỘNG DỒN: số ô mới ghi vào cache (0 = nội dung cache không đổi → khỏi lưu lại)
};
// Đánh dấu: số không thể là giá trị OSRM (OSRM có trả số âm nhỏ, VD -0,4 m — đo 07/11).
inline constexpr double kUnknown = -1e6, kNoRoute = -2e6;
std::string point_key(Point p);  // "lat,lng" 6 số lẻ

// Gọi OSRM /table cho mọi điểm. Lỗi mạng / HTTP / JSON → không có + lý do trong error.
// Cặp điểm OSRM không tìm được đường → ô đó dùng chim bay × 1,3. timeout_seconds: chờ đọc tối đa (rules.osrm_timeout_seconds).
// cache (có thể null): ô giữa 2 điểm việc đã biết lấy từ cache; chỉ hỏi OSRM hàng điểm 0 (KTV → các việc) và hàng + cột
// các điểm còn thiếu (nhiều quá nửa → hỏi cả bảng một lần). Cột điểm 0 (về chỗ KTV) không dùng (tuyến không quay về)
// → giữ chim bay × 1,3. Thành công → cache được cập nhật: điểm của lần này đứng đầu (theo thứ tự points), sau đó các
// điểm cũ khác; OSRM lỗi → cache giữ nguyên.
std::optional<Matrix> osrm_matrix(const std::string& url, const std::vector<Point>& points, double speed_kmh,
                                  std::string& error, double timeout_seconds = 3, TravelCache* cache = nullptr);

}  // namespace ktv
