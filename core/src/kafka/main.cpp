// ktv_worker — adapter Kafka của lõi: đọc topic IN → plan() → trả response.
// Bước hiện tại: đọc IN, xếp tuyến, ghi response ra stdout (hoặc --out file, ghi nối).
// Produce OUT + commit chỉ sau khi produce thành công: bước tiếp theo của Phase 7.
//
//   ktv_worker [--env .env] [--rules rules.json] [--osrm URL] [--at "YYYY-MM-DD HH:mm:ss"]
//              [--out responses.jsonl] [--max N]
//
// --max N: dừng sau N message (test nhanh); bỏ qua = chạy tới khi bị dừng (Ctrl-C / SIGTERM).
// Cấu hình KAFKA_* xem .env.example; biến môi trường thật đè giá trị trong file .env.
#include <csignal>
#include <cstdlib>
#include <ctime>
#include <fstream>
#include <iostream>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

#include "ktv/adapter/file.hpp"
#include "ktv/kafka/config.hpp"
#include "ktv/kafka/consumer.hpp"
#include "ktv/plan.hpp"

namespace {

volatile std::sig_atomic_t g_stop = 0;  // Ctrl-C / SIGTERM: xong message đang xử lý rồi thoát, đóng consumer đàng hoàng.
void request_stop(int) { g_stop = 1; }

ktv::Minutes vietnam_now() { return static_cast<ktv::Minutes>(std::time(nullptr) / 60) + 7 * 60; }

int usage() {
    std::cerr << "cách dùng:\n"
                 "  ktv_worker [--env .env] [--rules rules.json] [--osrm URL] [--at \"YYYY-MM-DD HH:mm:ss\"]\n"
                 "             [--out responses.jsonl] [--max N]\n";
    return 2;
}

// Một message → response nghiệp vụ (400 nếu sai contract, còn lại theo plan()).
nlohmann::ordered_json respond(const ktv::json& value, const ktv::Envelope& envelope, const ktv::Rules& rules,
                               ktv::Minutes now, const std::string& osrm_url) {
    std::vector<ktv::Error> errors;
    ktv::Message message;
    if (value.is_discarded()) errors.push_back({"", "JSON hỏng"});
    else message = ktv::parse_message(value, errors);
    if (!errors.empty()) {
        std::string text = "Sai định dạng tham số:";
        for (size_t k = 0; k < errors.size() && k < 3; ++k) text += " " + errors[k].path + " " + errors[k].problem + ";";
        return ktv::error_response("400", text, envelope.message_id, now);
    }
    message.message_id = envelope.message_id;  // trace_id = message_id của envelope
    message.planned_at = envelope.planned_at;  // lập tuyến theo đúng planned_at của message
    return ktv::plan(message, rules, now, osrm_url).response;
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

    // --at cố định giờ cho test; không có thì mỗi message lấy giờ lúc xử lý (worker chạy cả ngày).
    // Message có planned_at thì vẫn dùng của message (local_envelope).
    std::optional<ktv::Minutes> fixed_now;
    if (!at.empty()) {
        fixed_now = ktv::parse_datetime(at);
        if (!fixed_now) {
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

    // Ghi nối: chạy lại cùng file không xóa response của các message đã commit.
    std::ofstream file;
    if (!out_path.empty()) {
        file.open(out_path, std::ios::app);
        if (!file) {
            std::cerr << "không mở được " << out_path << " để ghi\n";
            return 2;
        }
    }
    std::ostream* out = out_path.empty() ? &std::cout : &file;

    std::signal(SIGINT, request_stop);
    std::signal(SIGTERM, request_stop);

    long long processed = 0;
    std::cerr << "ktv_worker: đang chờ message từ " << config.topic_in << " (Ctrl-C để dừng)\n";
    try {
        ktv::KafkaConsumer consumer(config);
        while (!g_stop && (max_messages == 0 || processed < max_messages)) {
            std::optional<ktv::KafkaConsumer::Record> record = consumer.poll(1000);
            if (!record) continue;
            ++processed;

            const ktv::Minutes now = fixed_now.value_or(vietnam_now());
            // Thiếu message_id: lấy vị trí Kafka làm ID — không trùng giữa các lần chạy / replica.
            const std::string position =
                record->topic + "-" + std::to_string(record->partition) + "-" + std::to_string(record->offset);
            const ktv::json value = ktv::json::parse(record->payload, nullptr, false);
            const ktv::Envelope envelope = ktv::local_envelope(value, position, now);

            std::string line, status;
            try {
                nlohmann::ordered_json response = respond(value, envelope, rules, now, osrm_url);
                status = response["statuscode"].get<std::string>();
                line = ktv::wrap_response(envelope, response).dump();
            } catch (const std::exception& error) {
                // Lỗi của MỘT message không được làm chết worker: chết thì restart đọc lại đúng message đó
                // và chết tiếp → kẹt cả partition. Trả response 500 rồi commit như bình thường.
                status = "500";
                line = ktv::wrap_response(envelope, ktv::error_response("500", std::string("Lỗi xử lý: ") + error.what(),
                                                                        envelope.message_id, now))
                           .dump(-1, ' ', false, nlohmann::json::error_handler_t::replace);
            }

            *out << line << "\n";
            out->flush();
            if (!*out) throw std::runtime_error("ghi response thất bại, không commit " + position);
            consumer.commit(*record);  // chỉ commit sau khi đã ghi xong response

            std::cerr << "#" << processed << " " << position << " key=" << record->key << " status=" << status;
            for (const auto& [name, value_text] : record->headers) std::cerr << " header." << name << "=" << value_text;
            std::cerr << "\n";
        }
    } catch (const std::exception& error) {
        std::cerr << error.what() << "\n";
        return 1;
    }

    std::cerr << "ktv_worker: " << processed << " message đã xử lý" << (g_stop ? " (dừng theo tín hiệu)" : "") << "\n";
    return 0;
}
