#include "ktv/kafka/producer.hpp"

#include <iostream>

#include "ktv/adapter/log.hpp"
#include <stdexcept>

namespace ktv {
namespace {

[[noreturn]] void fail(const std::string& text) { throw std::runtime_error("kafka: " + text); }

void log_callback(const rd_kafka_t*, int level, const char* facility, const char* message) {
    if (level > 4) return;  // chỉ lỗi/cảnh báo
    nlohmann::ordered_json line = ktv::log_event("kafka");
    line["level"] = level;
    line["facility"] = facility;
    line["text"] = message;
    ktv::write_log(line);
}

}  // namespace

void KafkaProducer::on_delivery(rd_kafka_t*, const rd_kafka_message_t* message, void* opaque) {
    auto* self = static_cast<KafkaProducer*>(opaque);
    const bool ok = message->err == RD_KAFKA_RESP_ERR_NO_ERROR;
    if (message->_private && self->on_delivered)  // 8.2: token = _private − 1 (0 = gửi không kèm token)
        self->on_delivered(reinterpret_cast<std::uintptr_t>(message->_private) - 1, message->err);
    if (ok) return;
    ++self->failed_;
    nlohmann::ordered_json line = ktv::log_event("out_failed");  // Kafka báo không giao được OUT
    line["topic"] = self->topic_;
    line["reason"] = rd_kafka_err2str(message->err);
    ktv::write_log(line);
}

KafkaProducer::KafkaProducer(const KafkaConfig& config) : topic_(config.topic_out) {
    if (topic_.empty()) fail("thiếu KAFKA_TOPIC_OUT");
    rd_kafka_conf_t* conf = rd_kafka_conf_new();
    rd_kafka_conf_set_log_cb(conf, log_callback);
    rd_kafka_conf_set_dr_msg_cb(conf, on_delivery);
    rd_kafka_conf_set_opaque(conf, this);
    char error[512];
    for (const auto& [key, value] : rdkafka_properties(config, false)) {
        if (rd_kafka_conf_set(conf, key.c_str(), value.c_str(), error, sizeof error) != RD_KAFKA_CONF_OK) {
            rd_kafka_conf_destroy(conf);
            fail(key + ": " + error);
        }
    }
    handle_ = rd_kafka_new(RD_KAFKA_PRODUCER, conf, error, sizeof error);
    if (handle_ == nullptr) fail("không tạo được producer: " + std::string(error));
}

KafkaProducer::~KafkaProducer() {
    if (handle_ == nullptr) return;
    rd_kafka_flush(handle_, 5000);
    rd_kafka_destroy(handle_);
}

void KafkaProducer::send(const std::string& key, const std::string& value) { send(key, value, ~std::uint64_t{0}); }

void KafkaProducer::send(const std::string& key, const std::string& value, std::uint64_t token) {
    void* opaque = reinterpret_cast<void*>(static_cast<std::uintptr_t>(token + 1));  // ~0 + 1 = 0: không token
    const rd_kafka_resp_err_t code = rd_kafka_producev(
        handle_, RD_KAFKA_V_TOPIC(topic_.c_str()), RD_KAFKA_V_KEY(key.data(), key.size()),
        RD_KAFKA_V_VALUE(const_cast<char*>(value.data()), value.size()), RD_KAFKA_V_MSGFLAGS(RD_KAFKA_MSG_F_COPY),
        RD_KAFKA_V_OPAQUE(opaque), RD_KAFKA_V_END);
    if (code != RD_KAFKA_RESP_ERR_NO_ERROR && opaque && on_delivered) on_delivered(token, code);
    if (code != RD_KAFKA_RESP_ERR_NO_ERROR) {  // không ném: gateway vẫn trả route; worker thấy qua flush()
        ++failed_;
        nlohmann::ordered_json line = ktv::log_event("out_failed");  // không đưa được vào hàng đợi
        line["topic"] = topic_;
        line["reason"] = rd_kafka_err2str(code);
        ktv::write_log(line);
    }
    rd_kafka_poll(handle_, 0);  // chạy callback giao nhận của các message trước (gateway không flush)
}

bool KafkaProducer::flush(int timeout_ms) {
    const bool drained = rd_kafka_flush(handle_, timeout_ms) == RD_KAFKA_RESP_ERR_NO_ERROR;
    const long long now_failed = failed_;
    const bool clean = drained && now_failed == failed_at_flush_;
    failed_at_flush_ = now_failed;
    return clean;
}

bool out_error_is_system(rd_kafka_resp_err_t error) {
    switch (error) {
        case RD_KAFKA_RESP_ERR__MSG_TIMED_OUT:  // quá message.timeout.ms chưa được xác nhận (broker chậm / mất)
        case RD_KAFKA_RESP_ERR__TIMED_OUT:
        case RD_KAFKA_RESP_ERR__TRANSPORT:
        case RD_KAFKA_RESP_ERR__ALL_BROKERS_DOWN:
        case RD_KAFKA_RESP_ERR__QUEUE_FULL:
        case RD_KAFKA_RESP_ERR__FATAL:
        case RD_KAFKA_RESP_ERR__AUTHENTICATION:
        case RD_KAFKA_RESP_ERR__DESTROY:
        case RD_KAFKA_RESP_ERR__UNKNOWN_TOPIC:
        case RD_KAFKA_RESP_ERR__UNKNOWN_PARTITION:
        case RD_KAFKA_RESP_ERR_UNKNOWN_TOPIC_OR_PART:
        case RD_KAFKA_RESP_ERR_TOPIC_AUTHORIZATION_FAILED:
        case RD_KAFKA_RESP_ERR_CLUSTER_AUTHORIZATION_FAILED:
        case RD_KAFKA_RESP_ERR_LEADER_NOT_AVAILABLE:
        case RD_KAFKA_RESP_ERR_NOT_LEADER_FOR_PARTITION:
        case RD_KAFKA_RESP_ERR_REQUEST_TIMED_OUT:
        case RD_KAFKA_RESP_ERR_BROKER_NOT_AVAILABLE:
        case RD_KAFKA_RESP_ERR_NETWORK_EXCEPTION:
        case RD_KAFKA_RESP_ERR_NOT_ENOUGH_REPLICAS:
        case RD_KAFKA_RESP_ERR_NOT_ENOUGH_REPLICAS_AFTER_APPEND:
        case RD_KAFKA_RESP_ERR_KAFKA_STORAGE_ERROR:
            return true;
        default:  // MSG_SIZE_TOO_LARGE, INVALID_MSG, RECORD_LIST_TOO_LARGE... và lỗi lạ: để OUT tối giản phân xử
            return false;
    }
}

}  // namespace ktv
