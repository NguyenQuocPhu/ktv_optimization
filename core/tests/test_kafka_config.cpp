// Config Kafka: parse .env, validate, mapping sang librdkafka properties.
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <stdexcept>
#include <string>

#include "ktv/kafka/config.hpp"

static int failures = 0;
#define CHECK(cond)                                                           \
    do {                                                                      \
        if (!(cond)) {                                                        \
            std::cerr << __FILE__ << ":" << __LINE__ << ": FAIL " #cond "\n"; \
            ++failures;                                                       \
        }                                                                     \
    } while (0)

namespace {

std::map<std::string, std::string> base_env() {
    return {
        {"KAFKA_BOOTSTRAP_SERVERS", "broker1:9092,broker2:9093"},
        {"KAFKA_SECURITY_PROTOCOL", "SASL_PLAINTEXT"},
        {"KAFKA_SASL_MECHANISM", "PLAIN"},
        {"KAFKA_SASL_USERNAME", "user"},
        {"KAFKA_SASL_PASSWORD", "secret"},
        {"KAFKA_GROUP_ID", "ktv-test"},
        {"KAFKA_TOPIC_IN", "topic-in"},
    };
}

bool rejects(const std::map<std::string, std::string>& env) {
    try {
        ktv::kafka_config_from_env(env);
        return false;
    } catch (const std::runtime_error&) {
        return true;
    }
}

}  // namespace

int main() {
    // 1. File .env: comment/blank/export/quote/CRLF; dòng không phải KEY=VALUE bị bỏ qua.
    const std::filesystem::path path = std::filesystem::temp_directory_path() / "ktv_test_kafka.env";
    {
        std::ofstream file(path);
        file << "# comment\n\nexport KAFKA_BOOTSTRAP_SERVERS=\"broker1:9092,broker2:9093\"\r\n"
                "KAFKA_SASL_MECHANISM='PLAIN'\n"
                "KAFKA_SASL_USERNAME= user \n"
                "dòng không có dấu bằng\n";
    }
    const auto values = ktv::load_env_file(path.string());
    CHECK(values.size() == 3);
    CHECK(values.at("KAFKA_BOOTSTRAP_SERVERS") == "broker1:9092,broker2:9093");
    CHECK(values.at("KAFKA_SASL_MECHANISM") == "PLAIN");
    CHECK(values.at("KAFKA_SASL_USERNAME") == "user");
    CHECK(ktv::load_env_file((path.parent_path() / "khong_ton_tai_ktv.env").string()).empty());

    // 2. Biến môi trường thật đè giá trị trong file.
    setenv("KAFKA_SASL_USERNAME", "user-tu-env", 1);
    const auto merged = ktv::kafka_env(path.string());
    CHECK(merged.at("KAFKA_SASL_USERNAME") == "user-tu-env");
    CHECK(merged.at("KAFKA_BOOTSTRAP_SERVERS") == "broker1:9092,broker2:9093");
    unsetenv("KAFKA_SASL_USERNAME");

    // 3. Config hợp lệ + mapping sang librdkafka.
    const ktv::KafkaConfig config = ktv::kafka_config_from_env(base_env());
    CHECK(config.bootstrap_servers == "broker1:9092,broker2:9093");
    CHECK(config.auto_offset_reset == "earliest");
    CHECK(ktv::describe(config).find("secret") == std::string::npos);  // không lộ password
    CHECK(ktv::describe(config).find("user") != std::string::npos);

    const auto props = ktv::rdkafka_properties(config);
    CHECK(props.at("bootstrap.servers") == "broker1:9092,broker2:9093");
    CHECK(props.at("security.protocol") == "SASL_PLAINTEXT");
    CHECK(props.at("sasl.mechanism") == "PLAIN");
    CHECK(props.at("sasl.username") == "user");
    CHECK(props.at("group.id") == "ktv-test");
    CHECK(props.at("auto.offset.reset") == "earliest");
    CHECK(props.at("enable.auto.commit") == "false");

    // Không SASL: không sinh key sasl.*.
    auto plain = base_env();
    plain["KAFKA_SECURITY_PROTOCOL"] = "PLAINTEXT";
    CHECK(ktv::rdkafka_properties(ktv::kafka_config_from_env(plain)).count("sasl.mechanism") == 0);

    // KAFKA_USE_SASL: true tự bật SASL theo transport; false mâu thuẫn với SASL thì từ chối.
    auto sasl_on = base_env();
    sasl_on["KAFKA_SECURITY_PROTOCOL"] = "PLAINTEXT";
    sasl_on["KAFKA_USE_SASL"] = "true";
    const ktv::KafkaConfig upgraded = ktv::kafka_config_from_env(sasl_on);
    CHECK(upgraded.security_protocol == "SASL_PLAINTEXT");
    CHECK(ktv::rdkafka_properties(upgraded).count("sasl.mechanism") == 1);

    sasl_on["KAFKA_SECURITY_PROTOCOL"] = "SSL";
    CHECK(ktv::kafka_config_from_env(sasl_on).security_protocol == "SASL_SSL");

    auto sasl_off = base_env();  // SASL_PLAINTEXT mà KAFKA_USE_SASL=false: mâu thuẫn.
    sasl_off["KAFKA_USE_SASL"] = "false";
    CHECK(rejects(sasl_off));

    auto sasl_weird = base_env();
    sasl_weird["KAFKA_USE_SASL"] = "maybe";
    CHECK(rejects(sasl_weird));

    auto sasl_local = base_env();  // false + PLAINTEXT: hợp lệ, bỏ cấu hình SASL còn sót.
    sasl_local["KAFKA_SECURITY_PROTOCOL"] = "PLAINTEXT";
    sasl_local["KAFKA_USE_SASL"] = "false";
    CHECK(ktv::rdkafka_properties(ktv::kafka_config_from_env(sasl_local)).count("sasl.mechanism") == 0);

    // 4. Thiếu/sai phải bị từ chối.
    auto env = base_env();
    env.erase("KAFKA_BOOTSTRAP_SERVERS");
    CHECK(rejects(env));
    env = base_env();
    env.erase("KAFKA_SASL_PASSWORD");
    CHECK(rejects(env));
    env = base_env();
    env.erase("KAFKA_SASL_MECHANISM");
    CHECK(rejects(env));
    env = base_env();
    env.erase("KAFKA_GROUP_ID");
    CHECK(rejects(env));
    env = base_env();
    env.erase("KAFKA_TOPIC_IN");
    CHECK(rejects(env));
    env = base_env();
    env["KAFKA_SECURITY_PROTOCOL"] = "SASL_TLS";
    CHECK(rejects(env));
    env = base_env();
    env["KAFKA_AUTO_OFFSET_RESET"] = "middle";
    CHECK(rejects(env));

    if (failures) std::cerr << failures << " lỗi\n";
    else std::cout << "test_kafka_config: OK\n";
    return failures != 0;
}
