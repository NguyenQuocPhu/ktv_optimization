// End-to-end CLI: chạy binary ktv_core trên object/JSONL, kiểm tra số output, envelope và mã lỗi.
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

static int failures = 0;
#define CHECK(cond)                                                           \
    do {                                                                      \
        if (!(cond)) {                                                        \
            std::cerr << __FILE__ << ":" << __LINE__ << ": FAIL " #cond "\n"; \
            ++failures;                                                       \
        }                                                                     \
    } while (0)

namespace fs = std::filesystem;

// Một message hợp lệ, một task status 6. Dùng cả bản compact và bản pretty nhiều dòng.
static const char* kStaffTasks =
    R"("staff":{"staff_id":"1","staff_account":"A","latlng":"21.02,105.80","available":"08:00-17:30",)"
    R"("plots":[{"id":1,"name":"P","role":1,"block_id":1}],"current_task":null},)"
    R"("tasks":{"trien_khai":[{"task_id":1,"task_group_id":1,"task_group_name":"trien_khai","task_type_id":3,)"
    R"("task_type_name":"trien_khai_net","task_sub_id":0,"task_sub_name":"","task_status_id":6,)"
    R"("task_status_name":"","sla":{"sla_minutes":120,"priority_in_day":3},"appointment":"",)"
    R"("location":"","latlng":"21.03,105.81","handle_minutes":"","task_plots_id":1,"staff_plots_id":1,)"
    R"("staff_role":1,"block_id":1}],"bao_tri":[],"thu_hoi":[],"hoa_don":[],"onsite":[]})";

static std::string valid_message(const std::string& id) {
    return "{\"message_id\":\"" + id + "\",\"planned_at\":\"2026-09-10 09:00:00\",\"trigger\":\"DAY_START\"," +
           kStaffTasks + "}";
}

static void write_file(const fs::path& path, const std::string& text) {
    std::ofstream out(path, std::ios::binary);
    out << text;
}

static int run(const std::string& command) { return std::system(command.c_str()); }

static std::vector<std::string> read_lines(const fs::path& path) {
    std::vector<std::string> lines;
    std::ifstream in(path);
    std::string line;
    while (std::getline(in, line))
        if (line.find_first_not_of(" \t\r\n") != std::string::npos) lines.push_back(line);
    return lines;
}

int main(int argc, char** argv) {
    if (argc < 2) {
        std::cerr << "cần đường dẫn binary ktv_core\n";
        return 2;
    }
    const std::string bin = "\"" + std::string(argv[1]) + "\"";
    const fs::path dir = fs::temp_directory_path() / "ktv_cli_test";
    fs::remove_all(dir);
    fs::create_directories(dir);

    write_file(dir / "one.json", valid_message("m1"));
    write_file(dir / "two.jsonl", valid_message("m1") + "\n" + valid_message("m2") + "\n");
    write_file(dir / "bad.jsonl", valid_message("m1") + "\n{bad}\n");
    write_file(dir / "broken.json", "{\n  \"message_id\": \"m1\",\n  \"staff\": {}\n");

    const std::string at = " --at \"2026-09-10 09:00:00\"";

    {  // Object hợp lệ → đúng một output, có envelope và planned_at theo --at.
        const std::string out = (dir / "one_out.jsonl").string();
        CHECK(run(bin + " plan \"" + (dir / "one.json").string() + "\"" + at + " --out \"" + out + "\"") == 0);
        std::vector<std::string> lines = read_lines(out);
        CHECK(lines.size() == 1);
        if (lines.size() == 1) {
            nlohmann::json r = nlohmann::json::parse(lines[0]);
            CHECK(r["message_id"] == "m1" && r["run_code"] == "m1" && r["trigger"] == "DAY_START");
            CHECK(r["planned_at"] == "2026-09-10 09:00:00" && r["schema_version"] == "1");
            CHECK(r["statuscode"] == "200" && r["success"] == true);
        }
    }
    {  // JSONL hai dòng → hai output.
        const std::string out = (dir / "two_out.jsonl").string();
        CHECK(run(bin + " plan \"" + (dir / "two.jsonl").string() + "\"" + at + " --out \"" + out + "\"") == 0);
        CHECK(read_lines(out).size() == 2);
    }
    {  // JSONL có dòng hỏng → vẫn hai output; dòng hỏng trả 400.
        const std::string out = (dir / "bad_out.jsonl").string();
        run(bin + " plan \"" + (dir / "bad.jsonl").string() + "\"" + at + " --out \"" + out + "\"");
        std::vector<std::string> lines = read_lines(out);
        CHECK(lines.size() == 2);
        if (lines.size() == 2) {
            CHECK(nlohmann::json::parse(lines[0])["statuscode"] == "200");
            nlohmann::json bad = nlohmann::json::parse(lines[1]);
            CHECK(bad["statuscode"] == "400" && bad["success"] == false && bad["data"].is_null());
        }
    }
    {  // Object pretty bị hỏng → đúng một output lỗi, không phải một lỗi mỗi dòng.
        const std::string out = (dir / "broken_out.jsonl").string();
        run(bin + " plan \"" + (dir / "broken.json").string() + "\"" + at + " --out \"" + out + "\"");
        std::vector<std::string> lines = read_lines(out);
        CHECK(lines.size() == 1);
        if (lines.size() == 1) CHECK(nlohmann::json::parse(lines[0])["statuscode"] == "400");
    }
    {  // --at sai format → thoát khác 0.
        CHECK(run(bin + " plan \"" + (dir / "one.json").string() + "\" --at \"khong-phai-gio\"") != 0);
    }

    fs::remove_all(dir);
    if (failures) std::cerr << failures << " lỗi\n";
    else std::cout << "test_cli: OK\n";
    return failures != 0;
}
