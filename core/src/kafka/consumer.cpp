#include "ktv/kafka/consumer.hpp"

#include <iostream>
#include <stdexcept>

namespace ktv {
namespace {

[[noreturn]] void fail(const std::string& text) { throw std::runtime_error("kafka: " + text); }

// In log lỗi/cảnh báo của librdkafka (resolve DNS, connect, auth, ACL...) ra stderr.
// Không in info/debug cho đỡ rối. level: 0..4 = emerg..warning.
void log_callback(const rd_kafka_t*, int level, const char* facility, const char* message) {
    if (level > 4) return;
    std::cerr << "kafka[" << facility << "] " << message << "\n";
}

}  // namespace

KafkaConsumer::KafkaConsumer(const KafkaConfig& config) {
    rd_kafka_conf_t* conf = rd_kafka_conf_new();
    rd_kafka_conf_set_log_cb(conf, log_callback);
    char error[512];
    for (const auto& [key, value] : rdkafka_properties(config)) {
        if (rd_kafka_conf_set(conf, key.c_str(), value.c_str(), error, sizeof error) != RD_KAFKA_CONF_OK) {
            rd_kafka_conf_destroy(conf);
            fail(key + ": " + error);
        }
    }
    // rd_kafka_new nhận conf; nhánh lỗi không destroy conf (theo example của librdkafka).
    handle_ = rd_kafka_new(RD_KAFKA_CONSUMER, conf, error, sizeof error);
    if (handle_ == nullptr) fail("không tạo được consumer: " + std::string(error));
    rd_kafka_poll_set_consumer(handle_);

    rd_kafka_topic_partition_list_t* topics = rd_kafka_topic_partition_list_new(1);
    rd_kafka_topic_partition_list_add(topics, config.topic_in.c_str(), RD_KAFKA_PARTITION_UA);
    const rd_kafka_resp_err_t code = rd_kafka_subscribe(handle_, topics);
    rd_kafka_topic_partition_list_destroy(topics);
    if (code != RD_KAFKA_RESP_ERR_NO_ERROR) fail("subscribe " + config.topic_in + ": " + rd_kafka_err2str(code));
}

KafkaConsumer::~KafkaConsumer() {
    if (handle_ != nullptr) {
        rd_kafka_consumer_close(handle_);
        rd_kafka_destroy(handle_);
    }
}

std::optional<KafkaConsumer::Record> KafkaConsumer::poll(int timeout_ms) {
    rd_kafka_message_t* message = rd_kafka_consumer_poll(handle_, timeout_ms);
    if (message == nullptr) return std::nullopt;
    if (message->err != RD_KAFKA_RESP_ERR_NO_ERROR) {
        const bool eof = message->err == RD_KAFKA_RESP_ERR__PARTITION_EOF;
        const std::string text = rd_kafka_message_errstr(message);
        rd_kafka_message_destroy(message);
        if (eof) return std::nullopt;  // hết dữ liệu hiện có, không phải lỗi
        fail("consume: " + text);
    }

    Record record;
    record.topic = rd_kafka_topic_name(message->rkt);
    record.partition = message->partition;
    record.offset = message->offset;
    if (message->key != nullptr && message->key_len > 0)
        record.key.assign(static_cast<const char*>(message->key), message->key_len);
    if (message->payload != nullptr && message->len > 0)
        record.payload.assign(static_cast<const char*>(message->payload), message->len);

    rd_kafka_headers_t* headers = nullptr;
    if (rd_kafka_message_headers(message, &headers) == RD_KAFKA_RESP_ERR_NO_ERROR && headers != nullptr) {
        const char* name = nullptr;
        const void* value = nullptr;
        size_t size = 0;
        for (size_t index = 0;
             rd_kafka_header_get_all(headers, index, &name, &value, &size) == RD_KAFKA_RESP_ERR_NO_ERROR; ++index) {
            record.headers.emplace_back(name, std::string(static_cast<const char*>(value), size));
        }
    }
    rd_kafka_message_destroy(message);
    return record;
}

void KafkaConsumer::commit(const Record& record) {
    rd_kafka_topic_partition_list_t* offsets = rd_kafka_topic_partition_list_new(1);
    rd_kafka_topic_partition_t* item = rd_kafka_topic_partition_list_add(offsets, record.topic.c_str(), record.partition);
    item->offset = record.offset + 1;
    const rd_kafka_resp_err_t code = rd_kafka_commit(handle_, offsets, 0 /* đồng bộ */);
    rd_kafka_topic_partition_list_destroy(offsets);
    if (code != RD_KAFKA_RESP_ERR_NO_ERROR) fail("commit: " + std::string(rd_kafka_err2str(code)));
}

}  // namespace ktv
