// ============================================================================
// kafka/offsets — SỔ OFFSET cho worker nhiều luồng (Phase 8.2)
// ============================================================================
// Hiểu nhanh:
//   Nhiều luồng xử lý message của cùng một partition xong KHÔNG theo thứ tự (VD offset 10 chạy LNS 1 s,
//   11, 12 xong sau 10 ms). Chỉ được commit tới CHỖ HỔNG đầu tiên: 10 chưa xong → chưa commit gì;
//   10 xong → commit 13 (= offset kế tiếp cần đọc). Chết giữa chừng → đọc lại từ chỗ đã commit, không mất.
//
// Dùng thế nào (chỉ luồng chính gọi, không cần khóa):
//   OffsetTracker t;
//   t.start(0, 10); t.start(0, 11);   // đã giao cho luồng xử lý
//   t.done(0, 11);                    // xong (OUT đã được Kafka xác nhận)
//   t.advance();                      // {} — 10 chưa xong
//   t.done(0, 10); t.advance();       // {{0, 12}} → commit partition 0 tới offset 12
//   t.drop(0);                        // Kafka thu hồi partition 0: quên sổ của nó
//
// Phụ thuộc: không (test được không cần librdkafka).
// ============================================================================
#pragma once

#include <map>

namespace ktv {

class OffsetTracker {
public:
    void start(int partition, long long offset) { book_[partition][offset] = false; }
    void done(int partition, long long offset) {
        auto p = book_.find(partition);
        if (p == book_.end()) return;  // partition đã bị thu hồi: bỏ qua
        auto o = p->second.find(offset);
        if (o != p->second.end()) o->second = true;
    }
    // Partition → offset kế tiếp cần commit, chỉ với partition vừa tiến lên. Gỡ các offset đã commit khỏi sổ.
    std::map<int, long long> advance() {
        std::map<int, long long> next;
        for (auto& [partition, offsets] : book_)
            while (!offsets.empty() && offsets.begin()->second) {
                next[partition] = offsets.begin()->first + 1;
                offsets.erase(offsets.begin());
            }
        return next;
    }
    // Số message đã giao mà chưa commit của partition (−1 = mọi partition).
    size_t pending(int partition = -1) const {
        size_t n = 0;
        for (const auto& [p, offsets] : book_)
            if (partition < 0 || p == partition) n += offsets.size();
        return n;
    }
    void drop(int partition) { book_.erase(partition); }

private:
    std::map<int, std::map<long long, bool>> book_;  // partition → offset → đã xong
};

}  // namespace ktv
