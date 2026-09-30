// ============================================================================
// kafka/consumer — ĐỌC MESSAGE TỪ TOPIC IN (librdkafka)
// ============================================================================
// Hiểu nhanh:
//   "Hòm thư" của worker. Đăng ký topic IN rồi mỗi lần poll() lấy ra MỘT message
//   (payload + key + header + vị trí partition/offset). Xử lý xong mới commit() để
//   Kafka ghi nhận "đã đọc tới đây"; chưa commit mà tắt thì lần sau đọc lại (at-least-once).
//   Mất mạng/broker chập chờn: poll() chỉ log rồi trả rỗng, librdkafka tự nối lại.
//   Sai quyền/sai topic/lỗi nặng: poll() ném lỗi để worker dừng và báo.
//
// Dùng thế nào:
//   KafkaConsumer consumer(config);
//   while (!stop)
//     if (auto record = consumer.poll(1000)) { ...xử lý...; consumer.commit(*record); }
//
// Trong file này có:
//   KafkaConsumer::Record – một message đọc được
//   poll, commit          – lấy một message / xác nhận đã xử lý xong
//   reachable             – broker có trả lời không (cho /readyz)
//
// Ẩn trong consumer.cpp: dựng cấu hình librdkafka, log lỗi, phân loại lỗi tạm thời / lỗi nặng.
// Chỉ build khi có librdkafka (pkg-config rdkafka). Thiếu thư viện thì ktv_worker
// tắt, phần còn lại vẫn build bình thường (KTV_WITH_KAFKA).
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

    // Chờ tối đa timeout_ms. Không có message, hoặc lỗi tạm thời (đã log) → nullopt.
    // Lỗi nặng (fatal, sai auth/quyền, topic không tồn tại) → ném std::runtime_error.
    std::optional<Record> poll(int timeout_ms);

    // Commit offset của record + 1 (đồng bộ). true = đã commit. false = Kafka đang chia lại partition
    // (rebalance/generation cũ, đã log): message sẽ được giao lại, bình thường — không dừng worker.
    // Lỗi khác → ném.
    bool commit(const Record& record);

    // Hỏi broker (metadata) xem có nối được không. Dùng cho /readyz: poll() không phân biệt được
    // "đang chờ message" với "chưa bao giờ nối được broker".
    bool reachable(int timeout_ms);

private:
    rd_kafka_t* handle_ = nullptr;
};

}  // namespace ktv
