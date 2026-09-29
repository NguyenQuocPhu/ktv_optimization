// CLI gateway read model: ktv_gateway --port 8080 [--host H] [--seed out.jsonl] [--token T]
#include <fstream>
#include <iostream>
#include <string>

#include "ktv/gateway/seed.hpp"
#include "ktv/gateway/server.hpp"
#include "ktv/gateway/store.hpp"

namespace {

int usage() {
    std::cerr << "cách dùng: ktv_gateway [--host H] [--port N] [--seed out.jsonl] [--token T]\n";
    return 2;
}

}  // namespace

int main(int argc, char** argv) {
    ktv::GatewayOptions options;
    std::string seed_path;
    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        try {
            if (arg == "--port" && i + 1 < argc) options.port = std::stoi(argv[++i]);
            else if (arg == "--host" && i + 1 < argc) options.host = argv[++i];
            else if (arg == "--seed" && i + 1 < argc) seed_path = argv[++i];
            else if (arg == "--token" && i + 1 < argc) options.token = argv[++i];
            else return usage();
        } catch (const std::exception&) {
            return usage();
        }
    }

    ktv::MemoryRouteStore store;
    if (!seed_path.empty()) {
        std::ifstream in(seed_path);
        if (!in) {
            std::cerr << "không mở được " << seed_path << "\n";
            return 2;
        }
        const std::size_t loaded = ktv::load_routes(in, store);
        std::cerr << "nạp " << loaded << " bản từ " << seed_path << " (store có " << store.size() << ")\n";
    }

    auto server = ktv::make_gateway_server(store, options);
    std::cerr << "ktv_gateway nghe " << options.host << ":" << options.port
              << (options.token.empty() ? " (không token)" : " (có token)") << "\n";
    if (!server->listen(options.host, options.port)) {
        std::cerr << "không listen được " << options.host << ":" << options.port << "\n";
        return 1;
    }
    return 0;
}
