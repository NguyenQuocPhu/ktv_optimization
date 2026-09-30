// ktv_worker — adapter Kafka của lõi: đọc topic IN → plan() → trả response.
// Bước hiện tại: đọc IN, xếp tuyến, ghi response ra stdout (hoặc --out file).
// Produce OUT + commit chỉ sau khi produce thành công: bước tiếp theo của Phase 7.
//
//   ktv_worker [--env .env] [--rules rules.json] [--osrm URL] [--at "YYYY-MM-DD HH:mm:ss"]
//              [--out responses.jsonl] [--max N]
//
// --max N: dừng sau N message (test nhanh); bỏ qua = chạy tới khi bị dừng.
// Cấu hình KAFKA_* xem .env.example; biến môi trường thật đè giá trị trong file .env.
#include <cstdlib>
#include <ctime>
#include <fstream>
#include <iostream>
#include <optional>
#include <string>
#include <vector>

#include "ktv/adapter/file.hpp"
#include "ktv/kafka/config.hpp"
#include "ktv/kafka/consumer.hpp"
#include "ktv/plan.hpp"

namespace {

ktv::Minutes vietnam_now() { return static_cast<ktv::Minutes>(std::time(nullptr) / 60) + 7 * 60; }

int usage() {
    std::cerr << "cách dùng:\n"
                 "  ktv_worker [--env .env] [--rules rules.json] [--osrm URL] [--at \"YYYY-MM-DD HH:mm:ss\"]\n"
                 "             [--out responses.jsonl] [--max N]\n";
    return 2;
}

}  // namespace

int main(int argc, char** argv) {
    std::string env_path = ".env", rules_path, out_path, osrm_url, at;
    long long max_messages = 0;
    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        if (arg == "--env" && i + 1 < argc) env_path = argv[++i];
        else if (arg == "--rules" && i + 1 < argc) rules_path = argv[++i];
        else if (arg == "--out" && i + 1 < argc) out_path = argv[++i];
        else if (arg == "--osrm" && i + 1 < argc) osrm_url = argv[++i];
        else if (arg == "--at" && i + 1 < argc) at = argv[++i];
        else if (arg == "--max" && i + 1 < argc) max_messages = std::atoll(argv[++i]);
        else return usage();
    }

    ktv::Rules rules;
    try {
        rules = rules_path.empty() ? ktv::default_rules() : ktv::load_rules(rules_path);
    } catch (const std::exception& error) {
        std::cerr << error.what() << "\n";
        return 2;
    }

    // --at giúp test xác định giờ; message có planned_at thì dùng của message (local_envelope).
    ktv::Minutes server_now = vietnam_now();
    if (!at.empty()) {
        if (auto parsed = ktv::parse_datetime(at)) {
            server_now = *parsed;
        } else {
            std::cerr << "--at cần \"YYYY-MM-DD HH:mm:ss\": " << at << "\n";
            return 2;
        }
    }

    ktv::KafkaConfig config;
    try {
        config = ktv::kafka_config_from_env(ktv::kafka_env(env_path));
    } catch (const std::exception& error) {
        std::cerr << error.what() << "\n";
        return 2;
    }
    std::cerr << "ktv_worker: " << ktv::describe(config) << "\n";
    if (!config.topic_out.empty())
        std::cerr << "ktv_worker: KAFKA_TOPIC_OUT chưa được dùng ở bước này (produce OUT làm sau)\n";

    std::ofstream file;
    if (!out_path.empty()) file.open(out_path);
    std::ostream* out = out_path.empty() ? &std::cout : &file;

    long long processed = 0;
    std::cerr << "ktv_worker: đang chờ message từ " << config.topic_in << " (Ctrl-C để dừng)\n";
    try {
        ktv::KafkaConsumer consumer(config);
        while (max_messages == 0 || processed < max_messages) {
            std::optional<ktv::KafkaConsumer::Record> record = consumer.poll(1000);
            if (!record) continue;
            ++processed;

            ktv::json value = ktv::json::parse(record->payload, nullptr, false);
            const ktv::Envelope envelope = ktv::local_envelope(value, processed, server_now);
            std::vector<ktv::Error> errors;
            ktv::Message message;
            if (value.is_discarded()) errors.push_back({"", "JSON hỏng"});
            else message = ktv::parse_message(value, errors);
            message.message_id = envelope.message_id;  // trace_id = message_id của envelope
            message.planned_at = envelope.planned_at;  // lập tuyến theo đúng planned_at của message

            nlohmann::ordered_json response;
            if (!errors.empty()) {
                std::string text = "Sai định dạng tham số:";
                for (size_t k = 0; k < errors.size() && k < 3; ++k) text += " " + errors[k].path + " " + errors[k].problem + ";";
                response = ktv::error_response("400", text, envelope.message_id, server_now);
            } else {
                ktv::PlanResult result = ktv::plan(message, rules, server_now, osrm_url);
                response = std::move(result.response);
            }

            *out << ktv::wrap_response(envelope, response).dump() << "\n";
            out->flush();
            consumer.commit(*record);  // chỉ commit sau khi đã ghi xong response

            std::cerr << "#" << processed << " " << record->topic << "[" << record->partition << "]@" << record->offset
                      << " key=" << record->key << " status=" << response["statuscode"].get<std::string>();
            for (const auto& [name, value_text] : record->headers) std::cerr << " header." << name << "=" << value_text;
            std::cerr << "\n";
        }
    } catch (const std::exception& error) {
        std::cerr << error.what() << "\n";
        return 1;
    }

    std::cerr << "ktv_worker: " << processed << " message đã xử lý\n";
    return 0;
}
