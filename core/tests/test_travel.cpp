// travel: đọc ma trận OSRM, ô null giữ ước lượng, OSRM lỗi thì plan trả 424 với chim bay × 1,3.
#include <httplib.h>

#include <algorithm>
#include <iostream>
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

    // plan với OSRM hỏng: vẫn trả tuyến, mã 424.
    json data = json::parse(R"({
      "planned_at": "2026-09-28 09:20:00",
      "staff": {"staff_id": "1", "staff_account": "A", "latlng": "21.0248,105.7961",
                "plots": [{"id": 2, "name": "Trung Kính", "role": 1, "block_id": 1}],
                "available": "08:00-17:30", "current_task": null},
      "tasks": {"trien_khai": [], "bao_tri": [], "thu_hoi": [], "onsite": [],
        "hoa_don": [{"task_id": 1, "task_group_id": 4, "task_group_name": "hoa_don", "task_type_id": 2,
          "task_type_name": "hoa_don_tra_sau", "task_sub_id": 0, "task_sub_name": "", "task_status_id": 6,
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

    server.stop();
    thread.join();
    if (failures) std::cerr << failures << " lỗi\n";
    else std::cout << "test_travel: OK\n";
    return failures != 0;
}
