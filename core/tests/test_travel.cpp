// travel: đọc ma trận OSRM, ô null giữ ước lượng, OSRM lỗi thì plan trả 424 với chim bay × 1,3.
#include <httplib.h>

#include <algorithm>
#include <chrono>
#include <iostream>
#include <sstream>
#include <thread>

#include "ktv/plan.hpp"

static int failures = 0;
#define CHECK(cond)                                                           \
    do {                                                                      \
        if (!(cond)) {                                                        \
            std::cerr << __FILE__ << ":" << __LINE__ << ": FAIL " #cond "\n"; \
            ++failures;                                                       \
        }                                                                     \
    } while (0)

using namespace ktv;

int main() {
    // OSRM giả: 3 điểm; ô [0][2] không tới được (null).
    httplib::Server server;
    std::string last_path;
    server.Get(R"(/table/v1/driving/.*)", [&](const httplib::Request& req, httplib::Response& res) {
        last_path = req.path;
        size_t n = std::count(req.path.begin(), req.path.end(), ';') + 1;
        if (n == 3) {
            res.set_content(R"({"code":"Ok","distances":[[0,2291,null],[2169,0,1500],[3000,1400,0]],
                               "durations":[[0,300,null],[280,0,200],[400,190,0]]})",
                            "application/json");
            return;
        }
        json distances, durations;  // Mọi cặp khác nhau: 1 km, 2 phút.
        for (size_t i = 0; i < n; ++i)
            for (size_t j = 0; j < n; ++j) distances[i][j] = i == j ? 0 : 1000, durations[i][j] = i == j ? 0 : 120;
        res.set_content(json{{"code", "Ok"}, {"distances", distances}, {"durations", durations}}.dump(), "application/json");
    });
    server.Get("/broken/table/v1/driving/.*", [](const httplib::Request&, httplib::Response& res) {
        res.status = 500;
        res.set_content(R"({"code":"InvalidQuery","message":"lỗi thử"})", "application/json");
    });
    server.Get("/malformed/table/v1/driving/.*", [](const httplib::Request& req, httplib::Response& res) {
        size_t n = std::count(req.path.begin(), req.path.end(), ';') + 1;  // Đủ n hàng nhưng hàng là số / hàng thiếu ô.
        json distances = json::array(), durations = json::array();
        for (size_t i = 0; i < n; ++i) distances.push_back(0), durations.push_back(json::array({0}));
        res.set_content(json{{"code", "Ok"}, {"distances", distances}, {"durations", durations}}.dump(), "application/json");
    });
    server.Get("/slow/table/v1/driving/.*", [](const httplib::Request& req, httplib::Response& res) {
        std::this_thread::sleep_for(std::chrono::milliseconds(1500));  // OSRM chậm (bảng lớn)
        size_t n = std::count(req.path.begin(), req.path.end(), ';') + 1;
        json distances, durations;
        for (size_t i = 0; i < n; ++i)
            for (size_t j = 0; j < n; ++j) distances[i][j] = i == j ? 0 : 1000, durations[i][j] = i == j ? 0 : 120;
        res.set_content(json{{"code", "Ok"}, {"distances", distances}, {"durations", durations}}.dump(), "application/json");
    });
    // OSRM giả theo tọa độ (8.29): hiểu sources / destinations; giá trị chỉ phụ thuộc 2 tọa độ (thứ tự điểm nào cũng
    // ra cùng số) → so được bảng ghép từ cache với bảng hỏi đầy đủ. Điểm lat 21.5 không có đường (null).
    std::vector<std::string> rect_queries;
    server.Get("/rect/table/v1/driving/.*", [&](const httplib::Request& req, httplib::Response& res) {
        std::vector<Point> pts;
        std::stringstream list(req.path.substr(req.path.rfind('/') + 1));
        for (std::string item; std::getline(list, item, ';');) {
            const size_t comma = item.find(',');
            pts.push_back({std::stod(item.substr(comma + 1)), std::stod(item.substr(0, comma))});
        }
        auto indices = [&](const char* name) {
            std::vector<int> out;
            if (!req.has_param(name)) {
                for (size_t i = 0; i < pts.size(); ++i) out.push_back(static_cast<int>(i));
                return out;
            }
            std::stringstream text(req.get_param_value(name));
            for (std::string item; std::getline(text, item, ';');) out.push_back(std::stoi(item));
            return out;
        };
        const std::vector<int> rows = indices("sources"), cols = indices("destinations");
        rect_queries.push_back((req.has_param("sources") ? req.get_param_value("sources") : "*") + "|" +
                               (req.has_param("destinations") ? req.get_param_value("destinations") : "*"));
        json distances = json::array(), durations = json::array();
        for (int i : rows) {
            json d = json::array(), t = json::array();
            for (int j : cols) {
                const Point a = pts[i], b = pts[j];
                if ((a.lat == 21.5 || b.lat == 21.5) && i != j) {
                    d.push_back(nullptr), t.push_back(nullptr);
                    continue;
                }
                const double meters = std::round(1e5 * (std::abs(a.lat - b.lat) + std::abs(a.lng - b.lng))) + (a.lat < b.lat ? 7 : 0);
                d.push_back(i == j ? 0.0 : meters), t.push_back(i == j ? 0.0 : meters / 8);
            }
            distances.push_back(d), durations.push_back(t);
        }
        res.set_content(json{{"code", "Ok"}, {"distances", distances}, {"durations", durations}}.dump(), "application/json");
    });
    int port = server.bind_to_any_port("127.0.0.1");
    std::thread thread([&] { server.listen_after_bind(); });
    std::string url = "http://127.0.0.1:" + std::to_string(port);

    std::vector<Point> points{{21.0248, 105.7961}, {21.0122, 105.7995}, {21.0043, 105.8021}};
    std::string error;
    auto m = osrm_matrix(url, points, 30, error);
    CHECK(m && error.empty());
    CHECK(last_path == "/table/v1/driving/105.796100,21.024800;105.799500,21.012200;105.802100,21.004300");
    if (m) {
        CHECK(std::abs(m->km[0][1] - 2.291) < 1e-9 && std::abs(m->minutes[0][1] - 5) < 1e-9);
        CHECK(std::abs(m->km[1][0] - 2.169) < 1e-9);  // Đường bộ không đối xứng.
        double estimate = distance_km(points[0], points[2]) * kRoadFactor;
        CHECK(std::abs(m->km[0][2] - estimate) < 1e-9 && std::abs(m->minutes[0][2] - estimate / 30 * 60) < 1e-9);
    }

    error.clear();
    CHECK(!osrm_matrix(url + "/broken", points, 30, error) && error.find("500") != std::string::npos);
    error.clear();
    CHECK(!osrm_matrix(url + "/malformed", points, 30, error) && error.find("sai kích thước") != std::string::npos);
    error.clear();
    CHECK(!osrm_matrix("http://127.0.0.1:1", points, 30, error) && !error.empty());  // Không có server.
    // rules.osrm_timeout_seconds: OSRM trả sau 1,5 s — chờ 1 s → lỗi (plan về chim bay 424), chờ 3 s → có bảng.
    error.clear();
    CHECK(!osrm_matrix(url + "/slow", points, 30, error, 1) && !error.empty());
    error.clear();
    CHECK(osrm_matrix(url + "/slow", points, 30, error, 3) && error.empty());
    CHECK(rules_from_json(json{{"osrm_timeout_seconds", 8}}).osrm_timeout_seconds == 8);
    CHECK(default_rules().osrm_timeout_seconds == 3);

    // plan với OSRM hỏng: vẫn trả tuyến, mã 424.
    json data = json::parse(R"({
      "planned_at": "2026-09-28 09:20:00",
      "staff": {"staff_id": "1", "staff_account": "A", "latlng": "21.0248,105.7961",
                "plots": [{"id": 2, "name": "Trung Kính", "role": 1, "block_id": 1}],
                "available": "08:00-17:30", "current_task": null},
      "tasks": {"trien_khai": [], "bao_tri": [], "thu_hoi": [], "onsite": [],
        "hoa_don": [{"task_id": 1, "task_group_id": 4, "task_group_name": "hoa_don", "task_type_id": 1,
          "task_type_name": "tra_sau", "task_sub_id": 0, "task_sub_name": "", "task_status_id": 6,
          "task_status_name": "check_in", "sla": {"sla_minutes": null, "priority_in_day": 4}, "appointment": "",
          "location": "", "latlng": "21.0043,105.8021", "handle_minutes": 20, "task_plots_id": 2,
          "staff_plots_id": 2, "staff_role": 1, "block_id": 1}]}})");
    std::vector<Error> errors;
    Message message = parse_message(data, errors);
    PlanResult bad = plan(message, default_rules(), *parse_datetime("2026-09-10 09:20:00"), url + "/broken");
    CHECK(bad.response["success"] == true && bad.response["statuscode"] == "424" && std::string(bad.travel) == "ESTIMATED");
    PlanResult malformed = plan(message, default_rules(), *parse_datetime("2026-09-10 09:20:00"), url + "/malformed");
    CHECK(malformed.response["statuscode"] == "424" && std::string(malformed.travel) == "ESTIMATED");  // Trước đây: ném type_error.
    PlanResult good = plan(message, default_rules(), *parse_datetime("2026-09-10 09:20:00"), url);
    CHECK(good.response["statuscode"] == "200" && std::string(good.travel) == "OSRM");
    CHECK(good.response["data"]["metrics"]["total_distance_km"] == 1.0 && good.response["data"]["metrics"]["total_travel_minutes"] == 2);

    // ---- 8.29: cache bảng OSRM giữa các điểm việc ----
    {
        const std::string rect = url + "/rect";
        auto same = [](const Matrix& a, const Matrix& b, bool row0) {  // so mọi ô trừ cột 0 (về chỗ KTV: không dùng)
            for (size_t i = row0 ? 0 : 1; i < a.km.size(); ++i)
                for (size_t j = 1; j < a.km.size(); ++j)
                    if (std::abs(a.km[i][j] - b.km[i][j]) > 1e-9 || std::abs(a.minutes[i][j] - b.minutes[i][j]) > 1e-9)
                        return false;
            return true;
        };
        std::vector<Point> tasks{{21.0122, 105.7995}, {21.0043, 105.8021}, {21.0301, 105.7800}, {21.0200, 105.8100}};
        auto with_start = [&](Point start, std::vector<Point> extra = {}) {
            std::vector<Point> pts{start};
            pts.insert(pts.end(), tasks.begin(), tasks.end());
            pts.insert(pts.end(), extra.begin(), extra.end());
            return pts;
        };
        TravelCache cache;
        // Lần đầu (cache rỗng): cả bảng một lần; cache = 4 điểm việc, đủ 4×3 ô.
        std::vector<Point> p1 = with_start({21.0248, 105.7961});
        rect_queries.clear();
        auto m1 = osrm_matrix(rect, p1, 30, error, 3, &cache);
        auto full1 = osrm_matrix(rect, p1, 30, error);
        CHECK(m1 && full1 && same(*m1, *full1, true) && rect_queries.size() == 2 && rect_queries[0] == "*|*");
        CHECK(cache.points.size() == 4 && cache.points[0] == "21.012200,105.799500" && cache.added_cells == 12);
        CHECK(cache.meters[0][1] == full1->km[1][2] * 1000 && cache.seconds[1][0] == full1->minutes[2][1] * 60);
        // KTV di chuyển, việc không đổi: chỉ hỏi hàng điểm 0; bảng = bảng hỏi đầy đủ; cache không đổi nội dung.
        std::vector<Point> p2 = with_start({21.0400, 105.7700});
        rect_queries.clear();
        cache.fetched_cells = cache.added_cells = 0;
        auto m2 = osrm_matrix(rect, p2, 30, error, 3, &cache);
        auto full2 = osrm_matrix(rect, p2, 30, error);
        CHECK(m2 && same(*m2, *full2, true) && rect_queries.front() == "0|0;1;2;3;4" && cache.added_cells == 0);
        CHECK(rect_queries.size() == 2 && cache.fetched_cells == 5);  // 1 hàng × 5 ô
        // Thêm 1 việc: hàng (0 + việc mới) và cột việc mới; còn lại từ cache.
        std::vector<Point> p3 = with_start({21.0400, 105.7700}, {{21.0010, 105.8200}});
        rect_queries.clear();
        cache.fetched_cells = cache.added_cells = 0;
        auto m3 = osrm_matrix(rect, p3, 30, error, 3, &cache);
        auto full3 = osrm_matrix(rect, p3, 30, error);
        CHECK(m3 && same(*m3, *full3, true) && cache.added_cells == 8 && cache.points.size() == 5);
        CHECK(rect_queries.size() == 3 && rect_queries[0] == "0;5|0;1;2;3;4;5" && rect_queries[1] == "1;2;3;4;5|5");
        // Bỏ 2 việc + đổi thứ tự: không hỏi gì ngoài hàng 0; điểm lần này đứng đầu cache, điểm cũ giữ phía sau.
        tasks = {{21.0200, 105.8100}, {21.0122, 105.7995}};
        std::vector<Point> p4 = with_start({21.0400, 105.7700});
        rect_queries.clear();
        cache.fetched_cells = cache.added_cells = 0;
        auto m4 = osrm_matrix(rect, p4, 30, error, 3, &cache);
        auto full4 = osrm_matrix(rect, p4, 30, error);
        CHECK(m4 && same(*m4, *full4, true) && rect_queries.front() == "0|0;1;2" && cache.points.size() == 5);
        CHECK(cache.points[0] == "21.020000,105.810000" && cache.points[1] == "21.012200,105.799500");
        // Điểm không có đường (null): ghi kNoRoute, lần sau không hỏi lại, ô dùng chim bay × 1,3.
        tasks.push_back({21.5, 105.9});
        std::vector<Point> p5 = with_start({21.0400, 105.7700});
        cache.fetched_cells = cache.added_cells = 0;
        auto m5 = osrm_matrix(rect, p5, 30, error, 3, &cache);
        CHECK(m5 && cache.meters[0][2] == kNoRoute && std::abs(m5->km[1][3] - distance_km(p5[1], p5[3]) * kRoadFactor) < 1e-9);
        rect_queries.clear();
        cache.fetched_cells = cache.added_cells = 0;
        auto m6 = osrm_matrix(rect, p5, 30, error, 3, &cache);
        CHECK(m6 && rect_queries.size() == 1 && rect_queries[0] == "0|0;1;2;3" && same(*m5, *m6, true));
        // OSRM lỗi: không có bảng, cache giữ nguyên.
        const TravelCache before = cache;
        error.clear();
        tasks.push_back({21.0700, 105.8300});
        CHECK(!osrm_matrix(url + "/broken", with_start({21.0400, 105.7700}), 30, error, 3, &cache) && !error.empty());
        CHECK(cache.points == before.points && cache.meters == before.meters);
        // plan() nhận cache: lần 2 cùng việc chỉ hỏi 1 hàng, kết quả giống hệt lần đầu.
        TravelCache plan_cache;
        PlanResult first = plan(message, default_rules(), *parse_datetime("2026-09-10 09:20:00"), rect, false, &plan_cache);
        rect_queries.clear();
        PlanResult second = plan(message, default_rules(), *parse_datetime("2026-09-10 09:20:00"), rect, false, &plan_cache);
        CHECK(std::string(second.travel) == "OSRM" && rect_queries.size() == 1 && rect_queries[0].rfind("0|", 0) == 0);
        CHECK(first.response["data"]["clusters"] == second.response["data"]["clusters"]);  // cùng tuyến (ms tính khác)
    }

    server.stop();
    thread.join();
    if (failures) std::cerr << failures << " lỗi\n";
    else std::cout << "test_travel: OK\n";
    return failures != 0;
}
