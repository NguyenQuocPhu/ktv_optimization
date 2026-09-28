// Cluster: cắt thứ tự TASK theo chặng > 2 km; output dùng entry_type; seq đánh lại theo cụm.
#include <iostream>
#include <string>

#include "ktv/cluster.hpp"
#include "ktv/plan.hpp"

static int failures = 0;
#define CHECK(cond)                                                           \
    do {                                                                      \
        if (!(cond)) {                                                        \
            std::cerr << __FILE__ << ":" << __LINE__ << ": FAIL " #cond "\n"; \
            ++failures;                                                       \
        }                                                                     \
    } while (0)

using ktv::json;

static json make_task(long long id, const char* latlng, int plot_id) {
    return json{
        {"task_id", id}, {"task_group_id", 1}, {"task_group_name", "trien_khai"},
        {"task_type_id", 3}, {"task_type_name", "trien_khai_net"},
        {"task_sub_id", 0}, {"task_sub_name", ""},
        {"task_status_id", 6}, {"task_status_name", ""},
        {"sla", {{"sla_minutes", 120}, {"priority_in_day", 3}}},
        {"appointment", ""}, {"create_date", ""}, {"complete_date", ""},
        {"location", ""}, {"latlng", latlng}, {"handle_minutes", ""},
        {"task_plots_id", plot_id}, {"staff_plots_id", plot_id}, {"staff_role", 1}, {"block_id", 1}};
}

static json make_message(json tasks, int plot_id) {
    return json{{"message_id", "M"}, {"planned_at", "2026-09-10 09:00:00"}, {"trigger", "DAY_START"},
                {"staff", {{"staff_id", "1"}, {"staff_account", "A"}, {"latlng", "21.00,105.80"},
                           {"available", "08:00-17:30"},
                           {"plots", json::array({{{"id", plot_id}, {"name", "P"}, {"role", 1}, {"block_id", 1}}})},
                           {"current_task", nullptr}}},
                {"tasks", {{"trien_khai", tasks}, {"bao_tri", json::array()}, {"thu_hoi", json::array()},
                           {"hoa_don", json::array()}, {"onsite", json::array()}}}};
}

int main() {
    {  // split_clusters: TASK đầu luôn mở cụm; cắt khi chặng > ngưỡng.
        auto one = ktv::split_clusters({5.0, 1.0}, ktv::kClusterSplitKm);  // inbound 5 km không tạo cụm rỗng
        CHECK(one.size() == 1 && one[0].first == 0 && one[0].count == 2);
        auto two = ktv::split_clusters({1.0, 2.5, 0.5}, ktv::kClusterSplitKm);
        CHECK(two.size() == 2 && two[0].count == 1 && two[1].first == 1 && two[1].count == 2);
        CHECK(ktv::split_clusters({}, 2.0).empty());
        CHECK(ktv::split_clusters({1.0}, ktv::kClusterSplitKm).size() == 1);
    }

    {  // Biên ngưỡng: đúng 2.0 km không cắt, hơn 2.0 km thì cắt; toàn 0 gom một cụm.
        CHECK(ktv::split_clusters({1.0, 2.0}, ktv::kClusterSplitKm).size() == 1);
        CHECK(ktv::split_clusters({1.0, 2.0001}, ktv::kClusterSplitKm).size() == 2);
        CHECK(ktv::split_clusters({0.0, 0.0, 0.0}, ktv::kClusterSplitKm).size() == 1);
        auto mixed = ktv::split_clusters({0.0, 0.5, 3.0, 0.2}, ktv::kClusterSplitKm);
        CHECK(mixed.size() == 2 && mixed[0].first == 0 && mixed[0].count == 2 && mixed[1].first == 2 && mixed[1].count == 2);
        CHECK(ktv::split_clusters({9.0, 9.0, 9.0}, ktv::kClusterSplitKm).size() == 3);
    }

    const ktv::Minutes server_now = *ktv::parse_datetime("2026-09-10 09:00:05");

    {  // 1 TASK gần + 2 TASK xa nhau: 2 cụm, thứ tự TASK giữ nguyên, seq đánh lại.
        json tasks = json::array({make_task(1, "21.0000,105.8000", 5),   // gần điểm xuất phát
                                  make_task(2, "21.1000,105.8000", 5),   // xa > 2 km → mở cụm
                                  make_task(3, "21.1005,105.8000", 5)}); // sát task 2 → cùng cụm
        std::vector<ktv::Error> errors;
        ktv::PlanResult r = ktv::plan(ktv::parse_message(make_message(tasks, 5), errors), ktv::default_rules(), server_now);
        CHECK(errors.empty());
        CHECK(r.response["statuscode"] == "200");
        const auto& clusters = r.response["data"]["clusters"];
        CHECK(clusters.size() == 2);
        CHECK(clusters[0]["cluster_seg"] == 1 && clusters[0]["cluster_code"] == "CL-1");
        CHECK(clusters[1]["cluster_seg"] == 2 && clusters[1]["cluster_code"] == "CL-2");
        CHECK(clusters[0]["task_count"] == 1 && clusters[1]["task_count"] == 2);
        CHECK(r.response["data"]["metrics"]["cluster_count"] == 2);
        CHECK(r.response["data"]["metrics"]["tasks_total"] == 3);

        // Ghép TASK theo đúng thứ tự cụm: [1] rồi [2,3], seq mỗi cụm bắt đầu từ 1.
        std::vector<long long> order;
        for (const auto& cluster : clusters) {
            int expected_seq = 1;
            for (const auto& row : cluster["schedule"]) {
                CHECK(row.contains("entry_type") && !row.contains("type"));
                CHECK(row["seq"] == expected_seq++);
                if (row["entry_type"] == "TASK") order.push_back(row["task_id"]);
            }
        }
        CHECK((order == std::vector<long long>{1, 2, 3}));
    }

    {  // Hai TASK plot 0 rất gần nhau: 1 cụm, không gộp tên "Lô 0", revisit 0.
        json tasks = json::array({make_task(7, "21.0000,105.8000", 0), make_task(8, "21.0002,105.8000", 0)});
        std::vector<ktv::Error> errors;
        ktv::PlanResult r = ktv::plan(ktv::parse_message(make_message(tasks, 0), errors), ktv::default_rules(), server_now);
        CHECK(errors.empty());
        const auto& clusters = r.response["data"]["clusters"];
        CHECK(clusters.size() == 1);
        CHECK(clusters[0]["name"].get<std::string>().find("Khu vực chưa xác định") != std::string::npos);
        CHECK(r.response["data"]["metrics"]["revisit_count"] == 0);
    }

    if (failures) std::cerr << failures << " lỗi\n";
    else std::cout << "test_cluster: OK\n";
    return failures != 0;
}
