// CLI của lõi C++ (adapter local, chưa có Kafka).
// Input: một object JSON (pretty-printed) hoặc file JSONL, mỗi record là một message API (một KTV).
//   ktv_core plan <input> [--rules rules.json] [--osrm URL] [--at "YYYY-MM-DD HH:mm:ss"] [--out responses.jsonl] [--explain]
//   ktv_core validate <input>
//   ktv_core print-rules
// Output mỗi record là một response OUT prototype (message_id, run_code, trigger, ..., data).
// `plan` parse nới lỏng như worker (lệch hợp đồng mà vẫn xếp được → cảnh báo, đếm ở cuối);
// `validate` parse strict: soát MỌI chỗ lệch hợp đồng của file.
#include <algorithm>
#include <ctime>
#include <fstream>
#include <iostream>
#include <map>
#include <vector>

#include "ktv/adapter/file.hpp"
#include "ktv/plan.hpp"

namespace {


int usage() {
    std::cerr << "cách dùng:\n  ktv_core plan <input> [--rules rules.json] [--osrm URL] [--at \"YYYY-MM-DD HH:mm:ss\"] [--out responses.jsonl] [--explain]\n"
                 "  ktv_core validate <input>\n  ktv_core print-rules\n";
    return 2;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) return usage();
    std::string command = argv[1];
    std::string input, rules_path, out_path, osrm_url, at;
    bool explain = false;
    for (int i = 2; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--rules" && i + 1 < argc) rules_path = argv[++i];
        else if (arg == "--out" && i + 1 < argc) out_path = argv[++i];
        else if (arg == "--osrm" && i + 1 < argc) osrm_url = argv[++i];
        else if (arg == "--at" && i + 1 < argc) at = argv[++i];
        else if (arg == "--explain") explain = true;
        else if (input.empty() && arg[0] != '-') input = arg;
        else return usage();
    }

    ktv::Rules rules;
    try {
        rules = rules_path.empty() ? ktv::default_rules() : ktv::load_rules(rules_path);
    } catch (const std::exception& error) {
        std::cerr << error.what() << "\n";
        return 2;
    }
    if (command == "print-rules") {
        std::cout << ktv::rules_to_json(rules).dump(2) << "\n";
        return 0;
    }
    if ((command != "plan" && command != "validate") || input.empty()) return usage();

    std::ifstream in(input);
    if (!in) {
        std::cerr << "không mở được " << input << "\n";
        return 2;
    }
    const std::vector<ktv::json> records = ktv::read_records(in);

    // --at giúp test xác định giờ. Sai format phải báo lỗi, không âm thầm dùng giờ máy.
    ktv::Minutes server_now = ktv::vietnam_now();
    if (!at.empty()) {
        if (auto parsed = ktv::parse_datetime(at)) {
            server_now = *parsed;
        } else {
            std::cerr << "--at cần \"YYYY-MM-DD HH:mm:ss\": " << at << "\n";
            return 2;
        }
    }

    std::ofstream file;
    if (!out_path.empty()) file.open(out_path);
    std::ostream* out = command == "plan" && !out_path.empty() ? &file : nullptr;

    std::map<std::string, int> status, sources, travels, issues;
    std::vector<long long> times;
    long long shown = 0;
    for (size_t i = 0; i < records.size(); ++i) {
        const ktv::json& record = records[i];
        const ktv::Envelope envelope = ktv::local_envelope(record, "local-" + std::to_string(i + 1), server_now);
        std::vector<ktv::Error> errors, warnings;
        // plan: nới lỏng như worker; validate: strict để soát hết chỗ lệch hợp đồng.
        const ktv::Message message = ktv::parse_record(record, envelope, errors, command == "plan" ? &warnings : nullptr);
        for (const ktv::Error& warning : warnings) ++issues[ktv::issue_key(warning)];

        nlohmann::ordered_json response;
        if (!errors.empty()) {
            response = ktv::bad_request(errors, envelope.message_id, server_now);
            if (shown++ < 20) std::cerr << "record " << i + 1 << ": " << response["message"].get<std::string>() << "\n";
        } else if (command == "plan") {
            ktv::PlanResult result = ktv::plan(message, rules, server_now, osrm_url, explain);
            response = std::move(result.response);
            for (const ktv::Error& warning : result.warnings) ++issues[ktv::issue_key(warning)];
            if (response["success"]) {
                ++sources[ktv::kSourceNames[static_cast<int>(result.source)]];
                ++travels[result.travel];
                times.push_back(response["data"]["metrics"]["generated_in_ms"].get<long long>());
            }
        } else {
            response = {{"statuscode", "200"}};
        }

        ++status[response["statuscode"].get<std::string>()];
        if (out) {
            if (command == "plan") *out << ktv::wrap_response(envelope, response).dump() << "\n";
            else *out << response.dump() << "\n";  // validate: giữ output gọn như cũ
        }
    }

    std::cout << records.size() << " record";
    for (auto& [code, n] : status) std::cout << " · " << code << ": " << n;
    std::cout << "\n";
    if (!times.empty()) {
        std::sort(times.begin(), times.end());
        std::cout << "cách xếp:";
        for (auto& [name, n] : sources) std::cout << " " << name << " " << n;
        std::cout << "\nkhoảng cách:";
        for (auto& [name, n] : travels) std::cout << " " << name << " " << n;
        std::cout << "\ngenerated_in_ms: p50 " << times[times.size() / 2] << " · p95 " << times[times.size() * 95 / 100]
                  << " · max " << times.back() << "\n";
    }
    if (!issues.empty()) {  // Sổ câu hỏi cho team data: loại gặp nhiều nhất trước (mã: docs/DATA_QUESTIONS.md).
        std::vector<std::pair<int, std::string>> ranked;
        for (auto& [key, n] : issues) ranked.emplace_back(n, key);
        std::sort(ranked.rbegin(), ranked.rend());
        std::cerr << "cảnh báo dữ liệu (" << issues.size() << " loại, vẫn xếp tuyến):\n";
        for (size_t k = 0; k < ranked.size() && k < 30; ++k) std::cerr << "  " << ranked[k].first << " × " << ranked[k].second << "\n";
    }
    return status.count("400") ? 1 : 0;
}
