#include "ktv/kafka/producer.hpp"

#include <iostream>
#include <stdexcept>

namespace ktv {
namespace {

[[noreturn]] void fail(const std::string& text) { throw std::runtime_error("kafka: " + text); }

void log_callback(const rd_kafka_t*, int level, const char* facility, const char* message) {
    if (level > 4) return;  // chỉ lỗi/cảnh báo
    std::cerr << "kafka[" << facility << "] " << message << "\n";
}

}  // namespace

void KafkaProducer::on_delivery(rd_kafka_t*, const rd_kafka_message_t* message, void* opaque) {
    if (message->err == RD_KAFKA_RESP_ERR_NO_ERROR) return;
    auto* self = static_cast<KafkaProducer*>(opaque);
    ++self->failed_;
    std::cerr << "kafka: OUT không tới " << self->topic_ << ": " << rd_kafka_err2str(message->err) << "\n";
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

void KafkaProducer::send(const std::string& key, const std::string& value) {
    const rd_kafka_resp_err_t code = rd_kafka_producev(
        handle_, RD_KAFKA_V_TOPIC(topic_.c_str()), RD_KAFKA_V_KEY(key.data(), key.size()),
        RD_KAFKA_V_VALUE(const_cast<char*>(value.data()), value.size()), RD_KAFKA_V_MSGFLAGS(RD_KAFKA_MSG_F_COPY),
        RD_KAFKA_V_END);
    if (code != RD_KAFKA_RESP_ERR_NO_ERROR) {  // không ném: gateway vẫn trả route; worker thấy qua flush()
        ++failed_;
        std::cerr << "kafka: không gửi được OUT vào " << topic_ << ": " << rd_kafka_err2str(code) << "\n";
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

}  // namespace ktv
