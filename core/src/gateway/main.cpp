// ktv_gateway: API của team cho Mobix — GET /api/v1/staff/{id}/route, GET /api/v1/staff/{id}/replan (cần Redis).
//   ktv_gateway --port 8080 [--host H] [--seed out.jsonl] [--token T]
//               [--redis HOST:PORT [--redis-password P] [--redis-prefix ktv:]]
//               [--rules rules.json] [--osrm URL] [--at "YYYY-MM-DD HH:mm:ss"] [--env .env]
// --env: đọc KAFKA_* (như worker, không cần GROUP_ID/TOPIC_IN); KAFKA_TOPIC_OUT có giá trị thì route replan
//        được đẩy ra Kafka OUT (Phase 7.5). Không --env: không đẩy OUT.
#include <csignal>
#include <fstream>
#include <iostream>
#include <memory>
#include <string>
#include <thread>

#include "ktv/gateway/seed.hpp"
#include "ktv/gateway/server.hpp"
#include "ktv/gateway/store.hpp"
#include "ktv/kafka/config.hpp"

#ifdef KTV_WITH_KAFKA
#include "ktv/kafka/producer.hpp"
#endif

#ifdef KTV_WITH_REDIS
#include "ktv/gateway/redis_store.hpp"
#endif

namespace {

int usage() {
    std::cerr << "cách dùng: ktv_gateway [--host H] [--port N] [--seed out.jsonl] [--token T]\n"
                 "                   [--redis HOST:PORT [--redis-password P] [--redis-prefix ktv:]]\n"
                 "                   [--rules rules.json] [--osrm URL] [--at \"YYYY-MM-DD HH:mm:ss\"] [--env .env]\n";
    return 2;
}

}  // namespace

int main(int argc, char** argv) {
    // SIGINT/SIGTERM (Ctrl-C, docker stop): chặn ở mọi thread (kể cả thread librdkafka/httplib tạo sau), luồng chính
    // sigwait rồi dừng server; producer đẩy nốt OUT trong hàng đợi (tối đa 5 giây, ~KafkaProducer) trước khi thoát.
    sigset_t stop_signals;
    sigemptyset(&stop_signals);
    sigaddset(&stop_signals, SIGINT);
    sigaddset(&stop_signals, SIGTERM);
    pthread_sigmask(SIG_BLOCK, &stop_signals, nullptr);

    ktv::GatewayOptions options;
    std::string seed_path, redis_addr, redis_password, redis_prefix = "ktv:", rules_path, at, env_path;
    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        try {
            if (arg == "--port" && i + 1 < argc) options.port = std::stoi(argv[++i]);
            else if (arg == "--host" && i + 1 < argc) options.host = argv[++i];
            else if (arg == "--seed" && i + 1 < argc) seed_path = argv[++i];
            else if (arg == "--token" && i + 1 < argc) options.token = argv[++i];
            else if (arg == "--redis" && i + 1 < argc) redis_addr = argv[++i];
            else if (arg == "--redis-password" && i + 1 < argc) redis_password = argv[++i];
            else if (arg == "--redis-prefix" && i + 1 < argc) redis_prefix = argv[++i];
            else if (arg == "--rules" && i + 1 < argc) rules_path = argv[++i];
            else if (arg == "--osrm" && i + 1 < argc) options.osrm_url = argv[++i];
            else if (arg == "--at" && i + 1 < argc) at = argv[++i];
            else if (arg == "--env" && i + 1 < argc) env_path = argv[++i];
            else return usage();
        } catch (const std::exception&) {
            return usage();
        }
    }

    try {
        options.rules = rules_path.empty() ? ktv::default_rules() : ktv::load_rules(rules_path);
    } catch (const std::exception& error) {
        std::cerr << error.what() << "\n";
        return 2;
    }
    if (!at.empty()) {
        options.fixed_now = ktv::parse_datetime(at);
        if (!options.fixed_now) {
            std::cerr << "--at cần \"YYYY-MM-DD HH:mm:ss\": " << at << "\n";
            return 2;
        }
    }

#ifdef KTV_WITH_KAFKA
    std::unique_ptr<ktv::KafkaProducer> producer;
    if (!env_path.empty()) {
        try {
            const ktv::KafkaConfig kafka = ktv::kafka_config_from_env(ktv::kafka_env(env_path), false);
            if (!kafka.topic_out.empty()) {
                producer = std::make_unique<ktv::KafkaProducer>(kafka);
                options.send_out = [&producer](const std::string& key, const std::string& value) {
                    producer->send(key, value);
                };
                options.out_failed = [&producer] { return producer->failed(); };
            }
            std::cerr << "ktv_gateway: " << ktv::describe(kafka)
                      << (producer ? " → đẩy OUT vào " + kafka.topic_out : std::string(" (KAFKA_TOPIC_OUT trống: không OUT)"))
                      << "\n";
        } catch (const std::exception& error) {
            std::cerr << error.what() << "\n";
            return 2;
        }
    }
#else
    if (!env_path.empty()) {
        std::cerr << "bản build này không có Kafka (thiếu librdkafka khi build)\n";
        return 2;
    }
#endif

    std::unique_ptr<ktv::RouteStore> store;
    ktv::RedisStore* redis = nullptr;
#ifdef KTV_WITH_REDIS
    if (!redis_addr.empty()) {
        std::optional<ktv::RedisStore::Config> parsed = ktv::redis_config(redis_addr);
        if (!parsed) {
            std::cerr << "--redis cần dạng HOST:PORT\n";
            return 2;
        }
        ktv::RedisStore::Config config = *parsed;
        config.password = redis_password;
        config.prefix = redis_prefix;
        try {
            auto redis_store = std::make_unique<ktv::RedisStore>(config);
            redis = redis_store.get();
            store = std::move(redis_store);
        } catch (const std::exception& error) {
            std::cerr << error.what() << "\n";
            return 2;
        }
        std::cerr << "store: Redis " << redis_addr << " prefix=" << redis_prefix << "\n";
    }
#else
    if (!redis_addr.empty()) {
        std::cerr << "bản build này không có Redis (thiếu hiredis khi build)\n";
        return 2;
    }
#endif
    if (!store) store = std::make_unique<ktv::MemoryRouteStore>();

    if (!seed_path.empty()) {
        std::ifstream in(seed_path);
        if (!in) {
            std::cerr << "không mở được " << seed_path << "\n";
            return 2;
        }
        try {
            const std::size_t loaded = ktv::load_routes(in, *store);
            std::cerr << "nạp " << loaded << " bản từ " << seed_path << " (store có " << store->size() << ")\n";
        } catch (const std::exception& error) {
            std::cerr << "nạp seed lỗi: " << error.what() << "\n";
            return 2;
        }
    }

    auto server = ktv::make_gateway_server(*store, options, redis);
    if (!redis) std::cerr << "ktv_gateway: không có --redis → /replan trả 503\n";
    std::cerr << "ktv_gateway nghe " << options.host << ":" << options.port
              << (options.token.empty() ? " (không token)" : " (có token)") << "\n";
    if (!server->bind_to_port(options.host, options.port)) {
        std::cerr << "không listen được " << options.host << ":" << options.port << "\n";
        return 1;
    }
    std::thread listener([&server] { server->listen_after_bind(); });
    server->wait_until_ready();  // stop() chỉ tác dụng khi server đã chạy
    int signal_number = 0;
    sigwait(&stop_signals, &signal_number);
    std::cerr << "ktv_gateway: nhận tín hiệu " << signal_number << ", dừng (đẩy nốt OUT nếu có)\n";
    server->stop();
    listener.join();
    return 0;
}
