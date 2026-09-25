// CLI của lõi C++. Mỗi dòng file JSONL là một message API (input của một lần gọi AI cho một KTV).
//   ktv_core plan <messages.jsonl> [--rules rules.json] [--osrm http://127.0.0.1:5000] [--out responses.jsonl]
//   ktv_core validate <messages.jsonl>
//   ktv_core print-rules
#include <algorithm>
#include <ctime>
#include <fstream>
#include <iostream>
#include <map>

#include "ktv/plan.hpp"

namespace {

ktv::Minutes vietnam_now() { return static_cast<ktv::Minutes>(std::time(nullptr) / 60) + 7 * 60; }

int usage() {
    std::cerr << "cách dùng:\n  ktv_core plan <messages.jsonl> [--rules rules.json] [--osrm URL] [--out responses.jsonl]\n"
                 "  ktv_core validate <messages.jsonl>\n  ktv_core print-rules\n";
    return 2;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) return usage();
    std::string command = argv[1];
    std::string input, rules_path, out_path, osrm_url;
    for (int i = 2; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--rules" && i + 1 < argc) rules_path = argv[++i];
        else if (arg == "--out" && i + 1 < argc) out_path = argv[++i];
        else if (arg == "--osrm" && i + 1 < argc) osrm_url = argv[++i];
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
    std::ofstream file;
    if (!out_path.empty()) file.open(out_path);
    std::ostream* out = command == "plan" && !out_path.empty() ? &file : nullptr;

    std::map<std::string, int> status, sources, travels;
    std::vector<long long> times;
    long long count = 0, shown = 0;
    std::string line;
    while (std::getline(in, line)) {
        if (line.empty()) continue;
        ++count;
        const ktv::Minutes now = vietnam_now();
        std::vector<ktv::Error> errors;
        ktv::json data = ktv::json::parse(line, nullptr, false);
        ktv::Message message;
        if (data.is_discarded()) errors.push_back({"", "JSON hỏng"});
        else message = ktv::parse_message(data, errors);

        nlohmann::ordered_json response;
        if (!errors.empty()) {
            std::string text = "Sai định dạng tham số:";
            for (size_t i = 0; i < errors.size() && i < 3; ++i) text += " " + errors[i].path + " " + errors[i].problem + ";";
            response = ktv::error_response("400", text, message.message_id, now);
            if (shown++ < 20) std::cerr << "dòng " << count << ": " << text << "\n";
        } else if (command == "plan") {
            ktv::PlanResult result = ktv::plan(message, rules, now, osrm_url);
            response = std::move(result.response);
            if (response["success"]) {
                ++sources[ktv::kSourceNames[static_cast<int>(result.source)]];
                ++travels[result.travel];
                times.push_back(response["data"]["metrics"]["generated_in_ms"].get<long long>());
            }
        } else {
            response = {{"statuscode", "200"}};
        }
        ++status[response["statuscode"].get<std::string>()];
        if (out) *out << response.dump() << "\n";
    }

    std::cout << count << " message";
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
    return status.count("400") ? 1 : 0;
}
