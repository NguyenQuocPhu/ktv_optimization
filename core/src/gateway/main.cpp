// CLI gateway read model: ktv_gateway --port 8080 [--host H] [--seed out.jsonl] [--token T]
//                                    [--redis HOST:PORT [--redis-password P] [--redis-prefix KTV:]]
#include <fstream>
#include <iostream>
#include <memory>
#include <string>

#include "ktv/gateway/seed.hpp"
#include "ktv/gateway/server.hpp"
#include "ktv/gateway/store.hpp"

#ifdef KTV_WITH_REDIS
#include "ktv/gateway/redis_store.hpp"
#endif

namespace {

int usage() {
    std::cerr << "cách dùng: ktv_gateway [--host H] [--port N] [--seed out.jsonl] [--token T]\n"
                 "                   [--redis HOST:PORT [--redis-password P] [--redis-prefix KTV:]]\n";
    return 2;
}

}  // namespace

int main(int argc, char** argv) {
    ktv::GatewayOptions options;
    std::string seed_path, redis_addr, redis_password, redis_prefix = "ktv:";
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
            else return usage();
        } catch (const std::exception&) {
            return usage();
        }
    }

    std::unique_ptr<ktv::RouteStore> store;
#ifdef KTV_WITH_REDIS
    if (!redis_addr.empty()) {
        const auto colon = redis_addr.rfind(':');
        if (colon == std::string::npos) {
            std::cerr << "--redis cần dạng HOST:PORT\n";
            return 2;
        }
        ktv::RedisStore::Config config;
        try {
            config.host = redis_addr.substr(0, colon);
            config.port = std::stoi(redis_addr.substr(colon + 1));
        } catch (const std::exception&) {
            std::cerr << "--redis cần dạng HOST:PORT\n";
            return 2;
        }
        config.password = redis_password;
        config.prefix = redis_prefix;
        try {
            store = std::make_unique<ktv::RedisStore>(config);
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

    auto server = ktv::make_gateway_server(*store, options);
    std::cerr << "ktv_gateway nghe " << options.host << ":" << options.port
              << (options.token.empty() ? " (không token)" : " (có token)") << "\n";
    if (!server->listen(options.host, options.port)) {
        std::cerr << "không listen được " << options.host << ":" << options.port << "\n";
        return 1;
    }
    return 0;
}
