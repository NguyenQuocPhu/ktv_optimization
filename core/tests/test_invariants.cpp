// Bất biến trên diện rộng: sinh message hợp lệ ngẫu nhiên (seeded) và chạy toàn bộ benchmark có sẵn.
// Mỗi message kiểm: output hợp lệ, đếm việc, thứ tự/seq, thời gian đơn điệu, tất định, và khớp normalization.
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <random>
#include <map>
#include <set>
#include <string>
#include <vector>

#include "ktv/normalization.hpp"
#include "ktv/plan.hpp"
#include "ktv/sla.hpp"

static int failures = 0;
static long long checks = 0;
static void record_failure(int line, const char* what) {
    std::cerr << __FILE__ << ":" << line << ": FAIL " << what << "\n";
    if (++failures > 60) {
        std::cerr << "quá nhiều lỗi, dừng\n";
        std::exit(1);
    }
}
#define CHECK(cond)                                     \
    do {                                                \
        ++checks;                                       \
        if (!(cond)) record_failure(__LINE__, #cond);   \
    } while (0)

using ktv::json;
using ojson = nlohmann::ordered_json;

static std::uniform_real_distribution<double> unit(0, 1);

static std::string latlng(std::mt19937& rng) {
    char text[40];
    std::snprintf(text, sizeof text, "%.6f,%.6f", 21.0 + unit(rng) * 0.1, 105.8 + unit(rng) * 0.1);
    return text;
}

static std::string when(std::mt19937& rng, ktv::Minutes base, int span) {
    return ktv::format_datetime(base + static_cast<ktv::Minutes>(unit(rng) * span) - span / 2);
}

// Một message hợp lệ: kind lấy từ catalog nên type_id/sla/priority luôn khớp.
static json random_message(std::mt19937& rng, const std::vector<ktv::TaskKind>& kinds, long long seed) {
    const ktv::Minutes base = *ktv::parse_datetime("2026-09-10 09:00:00");
    json tasks;
    for (const char* group : ktv::kGroups) tasks[group] = json::array();

    long long next_id = 1000 + seed * 100;
    std::vector<long long> ids;
    const int count = static_cast<int>(unit(rng) * 7);  // 0..6 việc
    const char* statuses[] = {"6", "6", "6", "10", "0", "97"};
    for (int i = 0; i < count; ++i) {
        const ktv::TaskKind& kind = kinds[static_cast<size_t>(unit(rng) * kinds.size()) % kinds.size()];
        const long long id = next_id++;
        ids.push_back(id);
        const bool has_location = unit(rng) > 0.15;
        const std::string status = statuses[static_cast<size_t>(unit(rng) * 6) % 6];
        json sla = kind.sla_minutes ? json(*kind.sla_minutes) : json(nullptr);
        json task = {
            {"task_id", id},
            {"task_group_id", kind.type_id == 0 ? 1 : 0},  // ghi đè bên dưới
            {"task_group_name", kind.group},
            {"task_type_id", kind.type_id},
            {"task_type_name", kind.name},
            {"task_sub_id", 0},
            {"task_sub_name", ""},
            {"task_status_id", std::stoi(status)},
            {"task_status_name", ""},
            {"sla", {{"sla_minutes", sla}, {"priority_in_day", kind.priority}}},
            // ~10% số ca có hẹn rơi vào ngày mai / hôm qua (7.18, 7.20.2: mai → DEFERRED NEXT_DAY, hôm qua vẫn xếp).
            {"appointment", [&] {
                 const double roll = unit(rng);
                 return roll < 0.32 ? when(rng, base, 480)
                        : roll < 0.36 ? when(rng, base + 1440, 480)
                        : roll < 0.40 ? when(rng, base - 1440, 480) : std::string();
             }()},
            {"create_date", unit(rng) < 0.3 ? when(rng, base, 4320) : ""},
            {"complete_date", unit(rng) < 0.15 ? when(rng, base, 720) : ""},
            {"location", ""},
            {"latlng", has_location ? latlng(rng) : ""},
            {"handle_minutes", unit(rng) < 0.3 ? json(5 + static_cast<int>(unit(rng) * 120)) : json("")},
            {"task_plots_id", unit(rng) < 0.2 ? 0 : 7},
            {"staff_plots_id", 7},
            {"staff_role", unit(rng) < 0.2 ? 3 : 1},
            {"block_id", 4}};
        // task_group_id = chỉ số nhóm 1..5
        int gid = 0;
        while (std::string(ktv::kGroups[gid]) != kind.group) ++gid;
        task["task_group_id"] = gid + 1;
        tasks[kind.group].push_back(task);
    }

    json current = nullptr;
    if (unit(rng) < 0.35) {
        if (!ids.empty() && unit(rng) < 0.6) {  // trùng một row có thật
            current = {{"task_id", ids[static_cast<size_t>(unit(rng) * ids.size()) % ids.size()]},
                       {"task_status_id", 10}, {"task_type_id", 1}};
        } else {
            current = {{"task_id", 999999}, {"task_status_id", 10}, {"task_type_id", 1}};
        }
    }

    return json{
        {"message_id", "M" + std::to_string(seed)},
        {"planned_at", when(rng, base, 720)},
        {"trigger", "DAY_START"},
        {"staff", {{"staff_id", "1"}, {"staff_account", "A"}, {"latlng", latlng(rng)},
                   {"available", unit(rng) < 0.3 ? "08:00-17:30,17:30-21:00" : "08:00-17:30"},
                   {"plots", json::array({{{"id", 7}, {"name", "P"}, {"role", 1}, {"block_id", 4}}})},
                   {"current_task", current}}},
        {"tasks", tasks}};
}

// 7.16.1: điều kiện lọc K — giống hệt plan.cpp (ca "hoàn tất trong tháng" còn > K ngày làm việc thì không xếp).
static bool beyond_k(const ktv::Task& task, ktv::Minutes now, const ktv::Rules& rules) {
    const ktv::TaskKind& kind = ktv::kind_or_default(task.task_group_name, task.task_type_name);
    if (kind.on_time != ktv::OnTime::DoneWithinMonth) return false;
    const ktv::Deadlines deadlines = ktv::resolve_deadlines(task, kind, now);
    return deadlines.complete_by && ktv::workdays_until(now, *deadlines.complete_by) > rules.k_month_days;
}

// Kiểm bất biến của một lần plan so với danh sách candidate từ normalization (đã trừ ca bị lọc K).
static void check_invariants(const ktv::Message& message, const ojson& response, const ktv::NormalizedWorklist& worklist,
                             ktv::Minutes now, const ktv::Rules& rules, const std::vector<long long>& unplaced) {
    const std::string code = response["statuscode"].get<std::string>();
    CHECK(code == "200" || code == "422" || code == "424");
    if (code == "422") {
        CHECK(!response["success"].get<bool>());
        CHECK(response["data"].is_null());
        return;
    }
    CHECK(response["success"].get<bool>());
    const auto& data = response["data"];
    CHECK(data["staff_id"] == message.staff.staff_id);
    const auto& clusters = data["clusters"];
    CHECK(!clusters.empty());
    const auto& metrics = data["metrics"];

    long long task_rows = 0;
    long long cluster_tasks = 0;
    // 7.19.3: tổng metrics/cụm = tổng dòng (km tính theo phần mười để tránh sai số float).
    long long tenths = 0, travel = 0, handle = 0, idle = 0, rest = 0;
    std::map<long long, std::string> deferred;  // task_id → insert_reason của dòng DEFERRED
    auto tenth = [](const ojson& value) { return std::llround(value.get<double>() * 10); };
    std::set<long long> seen;
    std::string last_time;
    for (size_t c = 0; c < clusters.size(); ++c) {
        const auto& cluster = clusters[c];
        CHECK(cluster["cluster_seg"] == static_cast<int>(c) + 1);
        CHECK(cluster["cluster_code"] == "CL-" + std::to_string(c + 1));
        cluster_tasks += cluster["task_count"].get<long long>();
        CHECK(cluster["radius_m"].get<long long>() >= 0);
        int expected_seq = 1;
        bool deferred_tail = false;
        long long cluster_tenths = 0, cluster_handle = 0, first_tenths = -1;
        const auto& schedule = cluster["schedule"];
        for (size_t i = 0; i < schedule.size(); ++i) {
            const auto& row = schedule[i];
            const std::string type = row["entry_type"].get<std::string>();
            if (type == "IDLE") idle += row["duration_minutes"].get<long long>();
            if (type == "BREAK") rest += row["duration_minutes"].get<long long>();
            // 7.19.2: IDLE chờ khung hẹn nằm cùng cụm với TASK nó chờ (dòng ngay sau).
            if (type == "IDLE" && row["label"].get<std::string>().rfind("Chờ tới khung hẹn", 0) == 0)
                CHECK(i + 1 < schedule.size() && schedule[i + 1]["entry_type"] == "TASK");
            if (type == "TASK") {
                const long long leg = tenth(row["travel_km_before"]);
                if (first_tenths < 0) first_tenths = leg;
                else cluster_tenths += leg;
                tenths += leg;
                travel += row["travel_minutes_before"].get<long long>();
                handle += row["handle_minutes"].get<long long>();
                cluster_handle += row["handle_minutes"].get<long long>();
            }
            CHECK(row.contains("entry_type") && !row.contains("type"));
            const std::string entry = row["entry_type"].get<std::string>();
            if (entry == "DEFERRED") {  // 7.20.2: seq 0, không giờ, cuối schedule, không tính vào tổng
                deferred_tail = true;
                CHECK(row["seq"] == 0 && row["at"] == "" && row["start_at"] == "" && row["end_at"] == "");
                CHECK(row["task_role"] == "inserted" && row["projected_sla"] == "" && row["travel_km_before"] == 0.0);
                CHECK(deferred.emplace(row["task_id"].get<long long>(), row["insert_reason"].get<std::string>()).second);
                continue;
            }
            CHECK(!deferred_tail);  // DEFERRED chỉ ở cuối
            CHECK(row["seq"] == expected_seq++);
            CHECK(entry == "TASK" || entry == "IDLE" || entry == "BREAK");
            const std::string start = row["start_at"].get<std::string>();
            if (!last_time.empty()) CHECK(!(start < last_time));  // thời gian không lùi
            last_time = row["end_at"].get<std::string>();
            if (entry == "TASK") {
                ++task_rows;
                CHECK(seen.insert(row["task_id"].get<long long>()).second);  // không lặp
                const std::string ps = row["projected_sla"].get<std::string>();
                CHECK(ps == "ON_TIME" || ps == "AT_RISK" || ps == "WILL_BREACH" || ps == "ALREADY_BREACHED");
            }
        }
        CHECK(tenth(cluster["travel_km_inbound"]) == std::max<long long>(first_tenths, 0));  // cụm toàn DEFERRED: 0
        CHECK(tenth(cluster["travel_km_internal"]) == cluster_tenths);
        CHECK(cluster["handle_minutes"].get<long long>() == cluster_handle);
    }
    CHECK(tenth(metrics["total_distance_km"]) == tenths);
    CHECK(metrics["total_travel_minutes"].get<long long>() == travel);
    CHECK(metrics["total_handle_minutes"].get<long long>() == handle);
    CHECK(metrics["idle_minutes"].get<long long>() == idle);
    CHECK(metrics["break_minutes"].get<long long>() == rest);
    std::set<long long> beyond;
    for (const ktv::Task* candidate : worklist.candidates)
        if (beyond_k(*candidate, now, rules)) beyond.insert(candidate->task_id);
    CHECK(task_rows == metrics["tasks_total"].get<long long>());
    CHECK(cluster_tasks == task_rows);
    // unplaced = ca hẹn ngày sau (NEXT_DAY) + ca lọc K (BEYOND_K; nhóm cùng địa chỉ có thể "cứu" ca tháng > K — 7.16.3 —
    // nên K chỉ kiểm tập con). 7.20.2: đúng tập đó ra OUT dạng DEFERRED với đúng lý do.
    std::set<long long> listed(unplaced.begin(), unplaced.end());
    std::set<long long> next_day;
    for (const ktv::Task* candidate : worklist.candidates)
        if (candidate->appointment && ktv::start_of_day(*candidate->appointment) > ktv::start_of_day(now))
            next_day.insert(candidate->task_id);
    for (long long id : next_day) CHECK(listed.count(id) == 1);
    for (long long id : listed) {
        CHECK(next_day.count(id) == 1 || beyond.count(id) == 1);
        CHECK(deferred.count(id) == 1);
        if (deferred.count(id)) CHECK(deferred[id] == (next_day.count(id) ? "NEXT_DAY" : "BEYOND_K"));
    }
    CHECK(deferred.size() == listed.size());
    std::set<long long> expected_ids;
    for (const ktv::Task* candidate : worklist.candidates)
        if (!listed.count(candidate->task_id)) expected_ids.insert(candidate->task_id);
    CHECK(cluster_tasks == static_cast<long long>(expected_ids.size()));
    CHECK(static_cast<long long>(seen.size()) == static_cast<long long>(expected_ids.size()));
    for (long long id : expected_ids) CHECK(seen.count(id) == 1);

    const double rate = metrics["on_time_rate_forecast"].get<double>();
    CHECK(rate >= 0.0 && rate <= 100.0);
    CHECK(metrics["cluster_count"].get<long long>() == static_cast<long long>(clusters.size()));
    CHECK(metrics["generated_in_ms"].is_number());
}

// warnings = nullptr: strict; có: nới lỏng như worker/gateway (benchmark = dữ liệu kiểu thật, có thể lệch danh mục mới).
static json parse_line(const std::string& line, std::vector<ktv::Error>& errors, ktv::Message& message,
                       std::vector<ktv::Error>* warnings = nullptr) {
    json data = json::parse(line, nullptr, false);
    if (data.is_discarded()) errors.push_back({"", "JSON hỏng"});
    else message = ktv::parse_message(data, errors, warnings);
    return data;
}

int main(int argc, char** argv) {
    const ktv::Rules rules = ktv::default_rules();
    const ktv::Minutes now = *ktv::parse_datetime("2026-09-10 09:00:00");
    const std::vector<ktv::TaskKind>& kinds = ktv::task_kinds();

    {  // 3000 message ngẫu nhiên: mọi bất biến phải giữ; một phần kiểm tất định.
        std::mt19937 rng(20260910);
        for (long long seed = 0; seed < 3000; ++seed) {
            json msg = random_message(rng, kinds, seed);
            std::vector<ktv::Error> errors;
            ktv::Message message = ktv::parse_message(msg, errors);
            CHECK(errors.empty());
            if (!errors.empty()) continue;
            ktv::NormalizedWorklist worklist = ktv::normalize_worklist(message);
            ktv::PlanResult result = ktv::plan(message, rules, now);
            check_invariants(message, result.response, worklist, now, rules, result.unplaced);
            if (result.response["statuscode"] == "200" || result.response["statuscode"] == "424")
                CHECK(result.routed == static_cast<int>(worklist.candidates.size()) - static_cast<int>(result.unplaced.size()));
            CHECK(result.excluded == worklist.stats.excluded_missing_location);
            if (seed % 10 == 0) {  // 300 bài kiểm tất định
                // generated_in_ms là thời gian đo bằng đồng hồ (máy bận: 0 → 1 ms) → bỏ ra trước khi so, không thì test chập chờn.
                auto stable = [](ojson response) {
                    if (response["data"].is_object()) response["data"]["metrics"].erase("generated_in_ms");
                    return response;
                };
                ktv::PlanResult again = ktv::plan(message, rules, now);
                CHECK(stable(again.response) == stable(result.response));
            }
        }
    }

    if (argc >= 2 && std::filesystem::exists(argv[1])) {  // benchmark thật
        std::ifstream in(argv[1]);
        std::string line;
        long long total = 0, ok = 0, no_task = 0, errors_seen = 0;
        while (std::getline(in, line)) {
            if (line.find_first_not_of(" \t\r\n") == std::string::npos) continue;
            ++total;
            std::vector<ktv::Error> errors, warnings;
            ktv::Message message;
            // Nới lỏng như đường thật: benchmark sinh theo danh mục cũ (VD phieu_onsite SLA null trước 7.15) chỉ còn cảnh báo.
            parse_line(line, errors, message, &warnings);
            if (!errors.empty()) {
                ++errors_seen;
                continue;
            }
            ktv::NormalizedWorklist worklist = ktv::normalize_worklist(message);
            ktv::PlanResult result = ktv::plan(message, rules, now);
            check_invariants(message, result.response, worklist, now, rules, result.unplaced);
            if (result.response["statuscode"] == "200" || result.response["statuscode"] == "424") ++ok;
            else ++no_task;
        }
        CHECK(total == 5332);
        CHECK(errors_seen == 0);
        CHECK(ok + no_task == total);
        // 7.16.1 (lọc K) làm đổi phân bố so với mốc cũ 5202/130 — chỉ in ra để đối chiếu, không assert số cứng.
        std::cout << "benchmark: " << total << " message, " << ok << " tuyến, " << no_task << " 422\n";
    }

    if (failures) std::cerr << failures << " lỗi / " << checks << " kiểm\n";
    else std::cout << "test_invariants: OK (" << checks << " kiểm)\n";
    return failures != 0;
}
