// ============================================================================
// kafka/config — CẤU HÌNH KẾT NỐI KAFKA TỪ .env / BIẾN MÔI TRƯỜNG
// ============================================================================
// Hiểu nhanh:
//   Giá trị lấy từ biến môi trường thật (đè lên) + file .env (mặc định ".env").
//   Tên field và ví dụ từng môi trường: xem .env.example ở gốc repo.
//   Module này KHÔNG cần librdkafka: parse/validate thuần, test chạy mọi nơi.
//
// Dùng thế nào:
//   KafkaConfig config = kafka_config_from_env(kafka_env(".env"));
//   auto props = rdkafka_properties(config);   // đưa vào rd_kafka_conf_set
//
// Phụ thuộc: chỉ thư viện chuẩn.
// ============================================================================
#pragma once

#include <map>
#include <string>

namespace ktv {

struct KafkaConfig {
    std::string bootstrap_servers;                 // KAFKA_BOOTSTRAP_SERVERS: host:port, phẩy giữa các broker
    std::string security_protocol = "PLAINTEXT";   // PLAINTEXT | SSL | SASL_PLAINTEXT | SASL_SSL
    std::string sasl_mechanism;                    // PLAIN | SCRAM-SHA-256 | SCRAM-SHA-512 (khi bật SASL)
    std::string sasl_username;
    std::string sasl_password;
    std::string client_id = "ktv-core-ai";
    std::string group_id;                          // Consumer group. Khác client_id.
    std::string topic_in;
    std::string topic_out;                         // Route ra cho OA (Phase 7.5). Trống = không đẩy OUT.
    std::string auto_offset_reset = "earliest";    // earliest | latest
};

// Đọc file .env: KEY=VALUE, bỏ comment/blank, bóc quote, chấp nhận "export ".
// File không có → map rỗng (không lỗi).
std::map<std::string, std::string> load_env_file(const std::string& path);

// Biến môi trường thật, đè lên giá trị trong file (env thắng). env_path rỗng: chỉ đọc biến môi trường.
std::map<std::string, std::string> kafka_env(const std::string& env_path);

// Validate và dựng config. Thiếu/sai → ném std::runtime_error với lý do cụ thể.
// consumer = false (gateway chỉ produce): không bắt buộc KAFKA_GROUP_ID / KAFKA_TOPIC_IN.
KafkaConfig kafka_config_from_env(const std::map<std::string, std::string>& env, bool consumer = true);

// Đổi sang cặp key/value của librdkafka. consumer = false: bỏ group/offset, thêm acks=all + idempotence
// (Kafka không ghi trùng khi tự gửi lại), chờ xác nhận tối đa 10 giây.
std::map<std::string, std::string> rdkafka_properties(const KafkaConfig& config, bool consumer = true);

// Mô tả để log; KHÔNG chứa password.
std::string describe(const KafkaConfig& config);

}  // namespace ktv
