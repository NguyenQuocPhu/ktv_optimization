#include "ktv/kafka/config.hpp"

#include <cctype>
#include <fstream>
#include <stdexcept>

#include <unistd.h>

extern char** environ;  // POSIX: mảng NAME=VALUE của tiến trình.

namespace ktv {
namespace {

std::string trimmed(const std::string& text) {
    const size_t start = text.find_first_not_of(" \t");
    if (start == std::string::npos) return "";
    const size_t end = text.find_last_not_of(" \t");
    return text.substr(start, end - start + 1);
}

std::string lower(std::string text) {
    for (char& ch : text) ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
    return text;
}

std::string get(const std::map<std::string, std::string>& env, const char* key, const std::string& fallback = "") {
    const auto found = env.find(key);
    return found == env.end() || found->second.empty() ? fallback : found->second;
}

[[noreturn]] void fail(const std::string& text) { throw std::runtime_error("kafka: " + text); }

}  // namespace

std::map<std::string, std::string> load_env_file(const std::string& path) {
    std::map<std::string, std::string> values;
    std::ifstream in(path);
    if (!in) return values;
    std::string line;
    while (std::getline(in, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        std::string text = trimmed(line);
        if (text.empty() || text[0] == '#') continue;
        if (text.rfind("export ", 0) == 0) text = trimmed(text.substr(7));
        const size_t equals = text.find('=');
        if (equals == std::string::npos) continue;  // dòng không phải KEY=VALUE: bỏ qua
        const std::string key = trimmed(text.substr(0, equals));
        std::string value = trimmed(text.substr(equals + 1));
        if (value.size() >= 2 && (value.front() == '"' || value.front() == '\'') && value.back() == value.front())
            value = value.substr(1, value.size() - 2);
        if (!key.empty()) values[key] = value;
    }
    return values;
}

std::map<std::string, std::string> kafka_env(const std::string& env_path) {
    std::map<std::string, std::string> values;
    if (!env_path.empty()) values = load_env_file(env_path);
    for (char** item = environ; item != nullptr && *item != nullptr; ++item) {
        const std::string text = *item;
        const size_t equals = text.find('=');
        if (equals != std::string::npos) values[text.substr(0, equals)] = text.substr(equals + 1);
    }
    return values;
}

KafkaConfig kafka_config_from_env(const std::map<std::string, std::string>& env) {
    KafkaConfig config;
    config.bootstrap_servers = get(env, "KAFKA_BOOTSTRAP_SERVERS");
    config.security_protocol = get(env, "KAFKA_SECURITY_PROTOCOL", "PLAINTEXT");
    config.sasl_mechanism = get(env, "KAFKA_SASL_MECHANISM");
    config.sasl_username = get(env, "KAFKA_SASL_USERNAME");
    config.sasl_password = get(env, "KAFKA_SASL_PASSWORD");
    config.client_id = get(env, "KAFKA_CLIENT_ID", "ktv-core-ai");
    config.group_id = get(env, "KAFKA_GROUP_ID");
    config.topic_in = get(env, "KAFKA_TOPIC_IN");
    config.topic_out = get(env, "KAFKA_TOPIC_OUT");
    config.auto_offset_reset = get(env, "KAFKA_AUTO_OFFSET_RESET", "earliest");

    if (config.bootstrap_servers.empty()) fail("thiếu KAFKA_BOOTSTRAP_SERVERS");

    // KAFKA_USE_SASL (tuỳ chọn, theo quy ước công ty): true = bật SASL theo transport đang chọn;
    // false = xác nhận chạy không SASL. Bỏ trống = dùng nguyên KAFKA_SECURITY_PROTOCOL.
    const std::string use_sasl = lower(get(env, "KAFKA_USE_SASL"));
    if (!use_sasl.empty()) {
        const bool on = use_sasl == "true" || use_sasl == "1" || use_sasl == "yes";
        const bool off = use_sasl == "false" || use_sasl == "0" || use_sasl == "no";
        if (!on && !off) fail("KAFKA_USE_SASL chỉ nhận true/false (hoặc 1/0, yes/no): " + use_sasl);
        if (on && config.security_protocol == "PLAINTEXT") config.security_protocol = "SASL_PLAINTEXT";
        else if (on && config.security_protocol == "SSL") config.security_protocol = "SASL_SSL";
        else if (off && config.security_protocol.rfind("SASL", 0) == 0)
            fail("KAFKA_USE_SASL=false nhưng KAFKA_SECURITY_PROTOCOL=" + config.security_protocol + " đang bật SASL");
    }

    if (config.security_protocol != "PLAINTEXT" && config.security_protocol != "SSL" &&
        config.security_protocol != "SASL_PLAINTEXT" && config.security_protocol != "SASL_SSL")
        fail("KAFKA_SECURITY_PROTOCOL không hợp lệ: " + config.security_protocol);
    if (config.security_protocol.rfind("SASL", 0) != 0) {
        // Không SASL: bỏ cấu hình SASL còn sót để đổi PLAINTEXT/SSL khi test local không phải xóa từng dòng.
        config.sasl_mechanism.clear();
        config.sasl_username.clear();
        config.sasl_password.clear();
    } else {
        if (config.sasl_mechanism.empty()) fail("thiếu KAFKA_SASL_MECHANISM (PLAIN | SCRAM-SHA-256 | SCRAM-SHA-512)");
        if (config.sasl_username.empty()) fail("thiếu KAFKA_SASL_USERNAME");
        if (config.sasl_password.empty()) fail("thiếu KAFKA_SASL_PASSWORD");
    }
    if (config.group_id.empty()) fail("thiếu KAFKA_GROUP_ID (consumer group; khác KAFKA_CLIENT_ID)");
    if (config.topic_in.empty()) fail("thiếu KAFKA_TOPIC_IN");
    if (config.auto_offset_reset != "earliest" && config.auto_offset_reset != "latest")
        fail("KAFKA_AUTO_OFFSET_RESET chỉ nhận earliest hoặc latest");
    return config;
}

std::map<std::string, std::string> rdkafka_properties(const KafkaConfig& config) {
    std::map<std::string, std::string> props{
        {"bootstrap.servers", config.bootstrap_servers},
        {"security.protocol", config.security_protocol},
        {"client.id", config.client_id},
        {"group.id", config.group_id},
        {"auto.offset.reset", config.auto_offset_reset},
        {"enable.auto.commit", "false"},  // worker tự commit sau khi xử lý xong từng message
    };
    if (!config.sasl_mechanism.empty()) {
        props["sasl.mechanism"] = config.sasl_mechanism;
        props["sasl.username"] = config.sasl_username;
        props["sasl.password"] = config.sasl_password;
    }
    return props;
}

std::string describe(const KafkaConfig& config) {
    std::string text = "bootstrap=" + config.bootstrap_servers + " protocol=" + config.security_protocol;
    if (!config.sasl_mechanism.empty()) text += " sasl=" + config.sasl_mechanism + " user=" + config.sasl_username;
    text += " group=" + config.group_id + " topic_in=" + config.topic_in;
    return text;
}

}  // namespace ktv
