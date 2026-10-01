// ktv_worker — adapter Kafka của lõi: đọc topic IN → plan() → trả response.
// Bước hiện tại: đọc IN, xếp tuyến, ghi response ra stdout (hoặc --out file, ghi nối);
// có --redis thì ghi thêm state + route vào Redis cho gateway trả Mobix (Phase 7.3, adapter/publish).
// KAFKA_TOPIC_OUT có giá trị: route được ghi thì đẩy ra OUT (key = staff_id) và chờ Kafka xác nhận rồi mới
// commit IN; không xác nhận trong 10 giây → thoát, không commit (Phase 7.5). Trống: không đẩy OUT.
//
//   ktv_worker [--env .env] [--rules rules.json] [--osrm URL] [--at "YYYY-MM-DD HH:mm:ss"]
//              [--out responses.jsonl] [--max N] [--health-port N]
//              [--redis HOST:PORT [--redis-password P] [--redis-prefix ktv:]]
//
// --max N: dừng sau N message (test nhanh); bỏ qua = chạy tới khi bị dừng (Ctrl-C / SIGTERM).
// --health-port N: mở GET /healthz (worker còn chạy + bộ đếm) và GET /readyz (nối được broker) cho
//   k8s/giám sát; bỏ qua = không mở.
// --redis: version của state = {timestamp Kafka ms, offset}. Redis lỗi → thoát, không commit (restart đọc lại).
// Cấu hình KAFKA_* xem .env.example; biến môi trường thật đè giá trị trong file .env.
#include <httplib.h>

#include <algorithm>
#include <atomic>
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
                 "             [--redis HOST:PORT [--redis-password P] [--redis-prefix ktv:]]\n";
    return 2;
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
    std::string env_path = ".env", rules_path, out_path, osrm_url, at, max_text, port_text;
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
        else return usage();
    }
    // Gõ nhầm (VD "8O81") không được âm thầm thành 0 = tắt health / chạy vô hạn.
    if (!max_text.empty()) {
        const auto value = whole(max_text, 1, 1'000'000'000);
        if (!value) {
            std::cerr << "--max cần số nguyên dương: " << max_text << "\n";
            return 2;
        }
        max_messages = *value;
    }
    if (!port_text.empty()) {
        const auto value = whole(port_text, 1, 65535);
        if (!value) {
            std::cerr << "--health-port cần cổng 1–65535: " << port_text << "\n";
            return 2;
        }
        health_port = static_cast<int>(*value);
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
    std::unique_ptr<ktv::KafkaProducer> producer;
    if (config.topic_out.empty()) {
        std::cerr << "ktv_worker: KAFKA_TOPIC_OUT trống → không đẩy OUT\n";
    } else {
        try {
            producer = std::make_unique<ktv::KafkaProducer>(config);
        } catch (const std::exception& error) {
            std::cerr << error.what() << "\n";
            return 2;
        }
        std::cerr << "ktv_worker: đẩy OUT vào " << config.topic_out << "\n";
    }
    ktv::SendOut send_out;
    if (producer) send_out = [&producer](const std::string& key, const std::string& value) { producer->send(key, value); };

    std::unique_ptr<ktv::RedisStore> store;
    if (!redis_addr.empty()) {
        std::optional<ktv::RedisStore::Config> redis = ktv::redis_config(redis_addr);
        if (!redis) {
            std::cerr << "--redis cần dạng HOST:PORT: " << redis_addr << "\n";
            return 2;
        }
        redis->password = redis_password;
        redis->prefix = redis_prefix;
        try {
            store = std::make_unique<ktv::RedisStore>(*redis);
        } catch (const std::exception& error) {
            std::cerr << error.what() << "\n";
            return 2;
        }
        std::cerr << "ktv_worker: Redis " << redis_addr << " prefix=" << redis_prefix << "\n";
    }

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

    Health health;
    std::unique_ptr<HealthServer> health_server;
    if (health_port > 0) {
        health_server = start_health(health, health_port);
        if (!health_server) {
            std::cerr << "không mở được cổng health " << health_port << "\n";
            return 2;
        }
        std::cerr << "ktv_worker: /healthz ở cổng " << health_port << "\n";
    }

    std::signal(SIGINT, request_stop);
    std::signal(SIGTERM, request_stop);

    long long processed = 0;
    std::cerr << "ktv_worker: đang chờ message từ " << config.topic_in << " (Ctrl-C để dừng)\n";
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

            const ktv::Minutes now = fixed_now.value_or(ktv::vietnam_now());
            // Thiếu message_id: lấy vị trí Kafka làm ID — không trùng giữa các lần chạy / replica.
            const std::string position =
                record->topic + "-" + std::to_string(record->partition) + "-" + std::to_string(record->offset);
            // Lỗi Redis ném ra vòng ngoài: thoát, KHÔNG commit. Lỗi tính tuyến đã thành 500 bên trong.
            const ktv::json in = ktv::json::parse(record->payload, nullptr, false);
            const ktv::Published published =
                ktv::plan_and_store(in, ktv::local_envelope(in, position, now), now, {record->timestamp_ms, record->offset},
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

            std::cerr << "#" << processed << " " << position << " key=" << record->key << " status=" << status;
            if (store) std::cerr << " route=" << (published.route_stored ? "ghi" : "giữ") << (published.used_mobix_loc ? " vị_trí=mobix" : "");
            if (producer) std::cerr << " out=" << (published.out_sent ? "gửi" : "không");
            if (!warnings.empty()) std::cerr << " cảnh_báo=" << warnings.size();
            for (const auto& [name, value_text] : record->headers) std::cerr << " header." << name << "=" << value_text;
            std::cerr << "\n";
            for (const std::string& key : record_issues(health, warnings))  // chỉ log loại mới; đếm xem /healthz
                std::cerr << "  cảnh báo dữ liệu mới (" << published.message_id << "): " << key << "\n";
        }
    } catch (const std::exception& error) {
        std::cerr << error.what() << "\n";
        return 1;
    }

    std::cerr << "ktv_worker: " << processed << " message đã xử lý" << (g_stop ? " (dừng theo tín hiệu)" : "") << "\n";
    return 0;
}
