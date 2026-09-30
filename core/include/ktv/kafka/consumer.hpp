// ============================================================================
// kafka/consumer — ĐỌC MESSAGE TỪ TOPIC IN (librdkafka)
// ============================================================================
// Chỉ build khi có librdkafka (pkg-config rdkafka). Thiếu thư viện thì ktv_worker
// tắt, phần còn lại vẫn build bình thường (KTV_WITH_KAFKA).
//
// Dùng thế nào:
//   KafkaConsumer consumer(config);
//   while (true)
//     if (auto record = consumer.poll(1000)) { ...xử lý...; consumer.commit(*record); }
//
// Phụ thuộc: kafka/config.
// ============================================================================
#pragma once

#include <optional>
#include <string>
#include <utility>
#include <vector>

#include <librdkafka/rdkafka.h>

#include "ktv/kafka/config.hpp"

namespace ktv {

class KafkaConsumer {
public:
    struct Record {
        std::string topic;
        int partition = -1;
        long long offset = -1;
        std::string key;
        std::string payload;
        std::vector<std::pair<std::string, std::string>> headers;  // để soi envelope nằm header hay body
    };

    explicit KafkaConsumer(const KafkaConfig& config);
    ~KafkaConsumer();
    KafkaConsumer(const KafkaConsumer&) = delete;
    KafkaConsumer& operator=(const KafkaConsumer&) = delete;

    // Chờ tối đa timeout_ms. Không có message (hết dữ liệu/hết partition) → nullopt.
    // Lỗi consumer → ném std::runtime_error để caller dừng và báo.
    std::optional<Record> poll(int timeout_ms);

    // Commit offset của record + 1 (đồng bộ). Ném khi lỗi.
    void commit(const Record& record);

private:
    rd_kafka_t* handle_ = nullptr;
};

}  // namespace ktv
