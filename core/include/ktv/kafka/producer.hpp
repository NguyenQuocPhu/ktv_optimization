// ============================================================================
// kafka/producer — ĐẨY ROUTE RA TOPIC OUT (librdkafka) cho Optimal Assign lưu Oracle
// ============================================================================
// Hiểu nhanh:
//   "Hòm thư gửi đi". send() bỏ message vào hàng đợi rồi trả ngay; librdkafka gửi nền và Kafka
//   xác nhận sau. Worker cần chắc chắn đã tới nơi trước khi commit IN → gọi flush() và kiểm kết quả.
//   Gateway không chờ (Mobix đang đợi) → chỉ đếm số message giao nhận lỗi để xem ở /healthz.
//   acks=all + idempotence: Kafka tự gửi lại khi mạng chập chờn mà không ghi trùng.
//   VD worker: send("00201964", out_json); if (!flush(10000)) → thoát, không commit, đọc lại IN.
//
// Dùng thế nào:
//   KafkaProducer producer(config);                  // config.topic_out phải có; sai cấu hình → ném
//   producer.send(staff_id, out_json);               // không chờ
//   bool delivered = producer.flush(10'000);         // worker: chờ mọi message đã gửi được xác nhận
//   long long lost = producer.failed();              // gateway: số message giao nhận lỗi từ lúc chạy
//
// Trong file này có:
//   KafkaProducer   HÀM CHÍNH: send / flush / failed
//
// Ẩn trong producer.cpp: dựng cấu hình librdkafka (rdkafka_properties(config, false)), callback giao nhận.
// Chỉ build khi có librdkafka (KTV_WITH_KAFKA).
// Phụ thuộc: kafka/config.
// ============================================================================
#pragma once

#include <atomic>
#include <string>

#include <librdkafka/rdkafka.h>

#include "ktv/kafka/config.hpp"

namespace ktv {

class KafkaProducer {
public:
    explicit KafkaProducer(const KafkaConfig& config);
    ~KafkaProducer();  // chờ tối đa 5 giây cho message còn trong hàng đợi rồi đóng
    KafkaProducer(const KafkaProducer&) = delete;
    KafkaProducer& operator=(const KafkaProducer&) = delete;

    // Bỏ vào hàng đợi, không chờ, không ném. Hàng đợi đầy / topic sai tên → log + đếm vào failed().
    void send(const std::string& key, const std::string& value);
    // Chờ mọi message đã send() được Kafka xác nhận. true = tất cả tới nơi và không có lỗi mới từ lần flush trước.
    bool flush(int timeout_ms);
    long long failed() const { return failed_; }

private:
    static void on_delivery(rd_kafka_t*, const rd_kafka_message_t* message, void* opaque);

    rd_kafka_t* handle_ = nullptr;
    std::string topic_;
    std::atomic<long long> failed_{0};
    long long failed_at_flush_ = 0;
};

}  // namespace ktv
