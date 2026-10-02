// ktv_worker — adapter Kafka của lõi: đọc topic IN → plan() → trả response.
// Bước hiện tại: đọc IN, xếp tuyến, ghi response ra stdout (hoặc --out file, ghi nối);
// có --redis thì ghi thêm state + route vào Redis cho gateway trả Mobix (Phase 7.3, adapter/publish).
// KAFKA_TOPIC_OUT có giá trị: route được ghi thì đẩy ra OUT (key = staff_id) và chờ Kafka xác nhận rồi mới
// commit IN; không xác nhận trong 10 giây → thoát, không commit (Phase 7.5). Trống: không đẩy OUT.
//
//   ktv_worker [--env .env] [--rules rules.json] [--osrm URL] [--at "YYYY-MM-DD HH:mm:ss"]
//              [--out responses.jsonl] [--max N] [--health-port N]
//              [--redis HOST:PORT [--redis-password P] [--redis-prefix ktv:]] [--log-payload none|error|all]
//
// Log: mỗi sự kiện MỘT dòng JSON trên stderr (adapter/log.hpp). Mỗi message IN một dòng event "message": vị trí Kafka,
// staff_id, statuscode, message, errors (đầy đủ), warnings, đếm task, có/không đẩy OUT, thời gian; payload IN theo
// --log-payload (mặc định error = chỉ khi 400/500). stdout / --out vẫn là response (= OUT) như cũ.
//
// --max N: dừng sau N message (test nhanh); bỏ qua = chạy tới khi bị dừng (Ctrl-C / SIGTERM).
// --health-port N: mở GET /healthz (worker còn chạy + bộ đếm) và GET /readyz (nối được broker) cho
//   k8s/giám sát; bỏ qua = không mở.
// --redis: version của state = {timestamp Kafka ms, offset}. Redis lỗi → thoát, không commit (restart đọc lại).
// Cấu hình KAFKA_* xem .env.example; biến môi trường thật đè giá trị trong file .env.
#include <httplib.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cctype>
#include <csignal>
#include <cstdlib>
#include <ctime>
#include <fstream>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include "ktv/adapter/file.hpp"
#include "ktv/adapter/http.hpp"
#include "ktv/adapter/log.hpp"
#include "ktv/adapter/publish.hpp"
#include "ktv/kafka/config.hpp"
#include "ktv/kafka/consumer.hpp"
#include "ktv/kafka/producer.hpp"
#include "ktv/plan.hpp"

namespace {

volatile std::sig_atomic_t g_stop = 0;  // Ctrl-C / SIGTERM: xong message đang xử lý rồi thoát, đóng consumer đàng hoàng.
void request_stop(int) { g_stop = 1; }


int usage() {
    std::cerr << "cách dùng:\n"
                 "  ktv_worker [--env .env] [--rules rules.json] [--osrm URL] [--at \"YYYY-MM-DD HH:mm:ss\"]\n"
                 "             [--out responses.jsonl] [--max N] [--health-port N]\n"
                 "             [--redis HOST:PORT [--redis-password P] [--redis-prefix ktv:]]\n"
                 "             [--log-payload none|error|all]\n";
    return 2;
}

// Lỗi khiến worker không chạy / phải dừng: một dòng log "fatal" + mã thoát.
int fatal(const std::string& error, int code) {
    nlohmann::ordered_json line = ktv::log_event("fatal");
    line["error"] = error;
    line["exit_code"] = code;
    ktv::write_log(line);
    return code;
}

// Số nguyên trong [low, high], cả chuỗi phải là chữ số. Sai → không có.
std::optional<long long> whole(const std::string& text, long long low, long long high) {
    if (text.empty() || text.size() > 12 || !std::all_of(text.begin(), text.end(), [](unsigned char c) { return std::isdigit(c); }))
        return std::nullopt;
    const long long value = std::stoll(text);
    if (value < low || value > high) return std::nullopt;
    return value;
}

// Số liệu cho /healthz và /readyz.
// /healthz (sống): vòng poll trả về mỗi ≤ 1 giây kể cả khi broker mất, nên poll quá kMaxPollGap giây
//   nghĩa là worker bị treo (kẹt trong một message) → 503 để k8s restart.
// /readyz (sẵn sàng): broker có trả lời trong kMaxBrokerGap giây gần đây không. Broker mất thì restart
//   worker không giúp gì → tách riêng, không làm /healthz đỏ.
constexpr long long kMaxPollGap = 60;
constexpr long long kMaxBrokerGap = 60;
constexpr long long kBrokerCheckEvery = 15;  // giây giữa hai lần hỏi metadata broker
constexpr size_t kMaxIssueKeys = 200;  // Số loại cảnh báo dữ liệu giữ riêng; vượt thì dồn vào "(loại khác)".
struct Health {
    const long long started = std::time(nullptr);
    std::atomic<long long> last_poll{std::time(nullptr)};
    std::atomic<long long> last_broker_ok{0};  // 0 = chưa lần nào nối được broker
    std::mutex mutex;  // khóa các field dưới
    long long processed = 0;
    std::map<std::string, long long> by_status;    // statuscode → số message
    std::map<std::string, long long> data_issues;  // issue_key(cảnh báo) → số lần: sổ câu hỏi cho team data
    std::string last_message_at;                   // giờ VN lúc xử lý message gần nhất
};

// Cộng cảnh báo vào sổ. Trả các khóa lần đầu gặp để log chi tiết đúng một lần.
std::vector<std::string> record_issues(Health& health, const std::vector<ktv::Error>& warnings) {
    std::vector<std::string> fresh;
    std::lock_guard<std::mutex> lock(health.mutex);
    for (const ktv::Error& warning : warnings) {
        std::string key = ktv::issue_key(warning);
        if (!health.data_issues.count(key) && health.data_issues.size() >= kMaxIssueKeys) key = "(loại khác)";
        if (health.data_issues[key]++ == 0) fresh.push_back(key);
    }
    return fresh;
}

// HTTP /healthz chạy ở luồng riêng; hủy thì dừng + join (mọi đường thoát của main đều an toàn).
struct HealthServer {
    httplib::Server server;
    std::thread thread;
    ~HealthServer() {
        server.stop();
        if (thread.joinable()) thread.join();
    }
};

std::unique_ptr<HealthServer> start_health(Health& health, int port) {
    auto hs = std::make_unique<HealthServer>();
    hs->server.Get("/readyz", [&health](const httplib::Request&, httplib::Response& response) {
        const long long last = health.last_broker_ok;
        const bool ready = last > 0 && std::time(nullptr) - last <= kMaxBrokerGap;
        response.status = ready ? 200 : 503;
        response.set_content(nlohmann::json{{"ready", ready}}.dump(), "application/json");
    });
    hs->server.Get("/healthz", [&health](const httplib::Request&, httplib::Response& response) {
        const long long now = std::time(nullptr), gap = now - health.last_poll, broker = health.last_broker_ok;
        nlohmann::json body{{"ok", gap <= kMaxPollGap}, {"uptime_s", now - health.started}, {"seconds_since_poll", gap},
                            {"kafka_reachable", broker > 0 && now - broker <= kMaxBrokerGap},
                            {"seconds_since_broker_ok", broker > 0 ? nlohmann::json(now - broker) : nlohmann::json(nullptr)}};
        {
            std::lock_guard<std::mutex> lock(health.mutex);
            body["processed"] = health.processed;
            body["by_status"] = health.by_status;
            body["last_message_at"] = health.last_message_at;
            body["data_issues"] = health.data_issues;
        }
        response.status = gap <= kMaxPollGap ? 200 : 503;
        response.set_content(body.dump(), "application/json");
    });
    ktv::exclusive_port(hs->server);  // hai worker trùng cổng → báo lỗi thay vì /healthz trả của tiến trình khác
    if (!hs->server.bind_to_port("0.0.0.0", port)) return nullptr;
    hs->thread = std::thread([server = &hs->server] { server->listen_after_bind(); });
    // Chờ server chạy hẳn: stop() của httplib chỉ tác dụng khi server đã chạy. Thiếu dòng này, worker thoát
    // ngay (VD cấu hình Kafka sai) thì ~HealthServer gọi stop() quá sớm và join() chờ mãi → tiến trình treo.
    hs->server.wait_until_ready();
    return hs;
}

}  // namespace

int main(int argc, char** argv) {
    std::string env_path = ".env", rules_path, out_path, osrm_url, at, max_text, port_text, payload_text = "error";
    std::string redis_addr, redis_password, redis_prefix = "ktv:";
    long long max_messages = 0;
    int health_port = 0;
    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        if (arg == "--env" && i + 1 < argc) env_path = argv[++i];
        else if (arg == "--rules" && i + 1 < argc) rules_path = argv[++i];
        else if (arg == "--out" && i + 1 < argc) out_path = argv[++i];
        else if (arg == "--osrm" && i + 1 < argc) osrm_url = argv[++i];
        else if (arg == "--at" && i + 1 < argc) at = argv[++i];
        else if (arg == "--max" && i + 1 < argc) max_text = argv[++i];
        else if (arg == "--health-port" && i + 1 < argc) port_text = argv[++i];
        else if (arg == "--redis" && i + 1 < argc) redis_addr = argv[++i];
        else if (arg == "--redis-password" && i + 1 < argc) redis_password = argv[++i];
        else if (arg == "--redis-prefix" && i + 1 < argc) redis_prefix = argv[++i];
        else if (arg == "--log-payload" && i + 1 < argc) payload_text = argv[++i];
        else return usage();
    }
    // Gõ nhầm (VD "8O81") không được âm thầm thành 0 = tắt health / chạy vô hạn.
    if (!max_text.empty()) {
        const auto value = whole(max_text, 1, 1'000'000'000);
        if (!value) {
            return fatal("--max cần số nguyên dương: " + max_text, 2);
        }
        max_messages = *value;
    }
    if (!port_text.empty()) {
        const auto value = whole(port_text, 1, 65535);
        if (!value) {
            return fatal("--health-port cần cổng 1–65535: " + port_text, 2);
        }
        health_port = static_cast<int>(*value);
    }
    const std::optional<ktv::PayloadLog> payload_log = ktv::payload_log_from(payload_text);
    if (!payload_log) return fatal("--log-payload cần none, error hoặc all: " + payload_text, 2);

    ktv::Rules rules;
    try {
        rules = rules_path.empty() ? ktv::default_rules() : ktv::load_rules(rules_path);
    } catch (const std::exception& error) {
        return fatal(error.what(), 2);
    }

    // --at cố định giờ cho test; không có thì mỗi message lấy giờ lúc xử lý (worker chạy cả ngày).
    // Message có planned_at thì vẫn dùng của message (local_envelope).
    std::optional<ktv::Minutes> fixed_now;
    if (!at.empty()) {
        fixed_now = ktv::parse_datetime(at);
        if (!fixed_now) {
            return fatal("--at cần \"YYYY-MM-DD HH:mm:ss\": " + at, 2);
        }
    }

    ktv::KafkaConfig config;
    try {
        config = ktv::kafka_config_from_env(ktv::kafka_env(env_path));
    } catch (const std::exception& error) {
        return fatal(error.what(), 2);
    }
    std::unique_ptr<ktv::KafkaProducer> producer;
    if (!config.topic_out.empty()) {
        try {
            producer = std::make_unique<ktv::KafkaProducer>(config);
        } catch (const std::exception& error) {
            return fatal(error.what(), 2);
        }
    }
    ktv::SendOut send_out;
    if (producer) send_out = [&producer](const std::string& key, const std::string& value) { producer->send(key, value); };

    std::unique_ptr<ktv::RedisStore> store;
    if (!redis_addr.empty()) {
        std::optional<ktv::RedisStore::Config> redis = ktv::redis_config(redis_addr);
        if (!redis) {
            return fatal("--redis cần dạng HOST:PORT: " + redis_addr, 2);
        }
        redis->password = redis_password;
        redis->prefix = redis_prefix;
        try {
            store = std::make_unique<ktv::RedisStore>(*redis);
        } catch (const std::exception& error) {
            return fatal(error.what(), 2);
        }
    }

    // Ghi nối: chạy lại cùng file không xóa response của các message đã commit.
    std::ofstream file;
    if (!out_path.empty()) {
        file.open(out_path, std::ios::app);
        if (!file) {
            return fatal("không mở được " + out_path + " để ghi", 2);
        }
    }
    std::ostream* out = out_path.empty() ? &std::cout : &file;

    Health health;
    std::unique_ptr<HealthServer> health_server;
    if (health_port > 0) {
        health_server = start_health(health, health_port);
        if (!health_server) return fatal("không mở được cổng health " + std::to_string(health_port), 2);
    }

    std::signal(SIGINT, request_stop);
    std::signal(SIGTERM, request_stop);

    long long processed = 0;
    {
        nlohmann::ordered_json line = ktv::log_event("start");
        line["kafka"] = ktv::describe(config);  // không chứa password
        line["topic_in"] = config.topic_in;
        line["topic_out"] = config.topic_out.empty() ? nlohmann::ordered_json(nullptr) : nlohmann::ordered_json(config.topic_out);
        line["redis"] = redis_addr.empty() ? nlohmann::ordered_json(nullptr) : nlohmann::ordered_json(redis_addr + " prefix=" + redis_prefix);
        line["osrm"] = osrm_url.empty() ? nlohmann::ordered_json(nullptr) : nlohmann::ordered_json(osrm_url);
        line["rules"] = rules_path.empty() ? "mặc định" : rules_path;
        line["health_port"] = health_port > 0 ? nlohmann::ordered_json(health_port) : nlohmann::ordered_json(nullptr);
        line["log_payload"] = payload_text;
        line["out"] = out_path.empty() ? "stdout" : out_path;
        ktv::write_log(line);
    }
    try {
        ktv::KafkaConsumer consumer(config);
        long long last_check = 0;
        while (!g_stop && (max_messages == 0 || processed < max_messages)) {
            const long long tick = std::time(nullptr);
            if (health_server && tick - last_check >= kBrokerCheckEvery) {  // chỉ cần khi có /readyz
                last_check = tick;
                if (consumer.reachable(2000)) health.last_broker_ok = std::time(nullptr);
            }
            std::optional<ktv::KafkaConsumer::Record> record = consumer.poll(1000);
            health.last_poll = std::time(nullptr);
            if (!record) continue;
            health.last_broker_ok = health.last_poll.load();  // nhận được message = broker đang nối
            ++processed;
            const auto started = std::chrono::steady_clock::now();

            const ktv::Minutes now = fixed_now.value_or(ktv::vietnam_now());
            // Thiếu message_id: lấy vị trí Kafka làm ID — không trùng giữa các lần chạy / replica.
            const std::string position =
                record->topic + "-" + std::to_string(record->partition) + "-" + std::to_string(record->offset);
            // Lỗi Redis ném ra vòng ngoài: thoát, KHÔNG commit. Lỗi tính tuyến đã thành 500 bên trong.
            const ktv::json in = ktv::json::parse(record->payload, nullptr, false);
            const ktv::Envelope envelope = ktv::local_envelope(in, position, now);
            const ktv::Published published = ktv::plan_and_store(in, envelope, now, {record->timestamp_ms, record->offset},
                                                                 rules, osrm_url, store.get(), send_out);
            // OUT phải tới nơi trước khi commit IN; không thì thoát (ném ra vòng ngoài), restart đọc lại và gửi lại.
            if (published.out_sent && !producer->flush(10'000))
                throw std::runtime_error("OUT không được Kafka xác nhận, không commit " + position);
            const std::string& status = published.status;
            const std::vector<ktv::Error>& warnings = published.warnings;
            const std::string line =
                published.out ? published.out->dump(-1, ' ', false, nlohmann::json::error_handler_t::replace) : "";

            if (published.out) *out << line << "\n";  // IN cũ (STALE): không có response, chỉ commit
            out->flush();
            if (!*out) throw std::runtime_error("ghi response thất bại, không commit " + position);
            // Chỉ commit sau khi đã ghi xong response. false = đang rebalance (đã log): message sẽ được giao
            // lại cho consumer nhận partition, response có thể ghi hai lần — đúng at-least-once, không dừng worker.
            consumer.commit(*record);
            {
                std::lock_guard<std::mutex> lock(health.mutex);
                ++health.processed;
                ++health.by_status[status];
                health.last_message_at = ktv::format_datetime(ktv::vietnam_now());
            }

            // Một dòng JSON / message: đủ để biết vì sao ra statuscode này mà không phải lục Kafka OUT.
            nlohmann::ordered_json log = ktv::log_event("message");
            log["topic"] = record->topic;
            log["partition"] = record->partition;
            log["offset"] = record->offset;
            log["key"] = record->key;
            log["headers"] = nlohmann::ordered_json::object();
            for (const auto& [name, value_text] : record->headers) log["headers"][name] = value_text;
            log["message_id"] = published.message_id;
            log["run_code"] = envelope.run_code.empty() ? envelope.message_id : envelope.run_code;
            log["staff_id"] = published.staff_id;
            log["statuscode"] = status;  // "STALE" = IN cũ hơn state đang có, bỏ qua (không response, không OUT)
            log["message"] = published.out ? (*published.out)["message"] : nlohmann::ordered_json(nullptr);
            log["errors"] = ktv::errors_json(published.errors);
            log["warnings"] = nlohmann::ordered_json::array();
            for (const ktv::Error& warning : warnings) log["warnings"].push_back(ktv::issue_key(warning));
            log["tasks"] = {{"received", published.stats.tasks},
                            {"routed", published.stats.candidates},
                            {"skipped_status", published.stats.excluded_status},
                            {"current", published.stats.excluded_current},
                            {"missing_location", published.stats.excluded_missing_location}};
            log["sent_to"] = published.out_sent ? nlohmann::ordered_json("kafka") : nlohmann::ordered_json(nullptr);
            log["stored"] = published.route_stored;
            log["used_mobix_loc"] = published.used_mobix_loc;
            const auto& data = published.out ? (*published.out)["data"] : nlohmann::ordered_json(nullptr);
            log["generated_in_ms"] = data.is_object() ? data["metrics"]["generated_in_ms"] : nlohmann::ordered_json(nullptr);
            log["total_ms"] = std::round(std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - started).count() * 10) / 10;
            if (ktv::payload_wanted(*payload_log, status)) log["payload"] = ktv::payload_json(in, record->payload);
            ktv::write_log(log);
            for (const std::string& key : record_issues(health, warnings)) {  // loại cảnh báo mới: một dòng; đếm ở /healthz
                nlohmann::ordered_json fresh = ktv::log_event("data_issue_new");
                fresh["issue"] = key;
                fresh["message_id"] = published.message_id;
                ktv::write_log(fresh);
            }
        }
    } catch (const std::exception& error) {
        return fatal(error.what(), 1);
    }

    nlohmann::ordered_json stop = ktv::log_event("stop");
    stop["processed"] = processed;
    stop["reason"] = g_stop ? "signal" : "max";
    ktv::write_log(stop);
    return 0;
}
