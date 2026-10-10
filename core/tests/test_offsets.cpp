// Phase 8.2: sổ offset — commit tới chỗ hổng đầu tiên, nhiều partition, thu hồi partition.
#include <iostream>

#include "ktv/kafka/offsets.hpp"

static int failures = 0;
#define CHECK(cond)                                                           \
    do {                                                                      \
        if (!(cond)) {                                                        \
            std::cerr << __FILE__ << ":" << __LINE__ << ": FAIL " #cond "\n"; \
            ++failures;                                                       \
        }                                                                     \
    } while (0)

int main() {
    using Next = std::map<int, long long>;
    ktv::OffsetTracker t;
    CHECK(t.advance().empty());
    for (long long o = 10; o <= 13; ++o) t.start(0, o);
    t.done(0, 11);
    t.done(0, 13);
    CHECK(t.advance().empty());  // 10 chưa xong: hổng ở đầu
    t.done(0, 10);
    CHECK(t.advance() == (Next{{0, 12}}));  // 10, 11 xong; hổng ở 12
    CHECK(t.pending(0) == 2);
    t.done(0, 12);
    CHECK(t.advance() == (Next{{0, 14}}));
    CHECK(t.advance().empty() && t.pending() == 0);  // không commit lại

    // Nhiều partition độc lập.
    t.start(0, 20);
    t.start(1, 5);
    t.start(1, 6);
    t.done(1, 5);
    t.done(0, 20);
    CHECK(t.advance() == (Next{{0, 21}, {1, 6}}));
    CHECK(t.pending(1) == 1 && t.pending(0) == 0);

    // Thu hồi partition 1: quên sổ, done muộn của nó bị bỏ qua.
    t.drop(1);
    t.done(1, 6);
    CHECK(t.pending() == 0 && t.advance().empty());

    if (failures) std::cerr << failures << " lỗi\n";
    else std::cout << "test_offsets: OK\n";
    return failures != 0;
}
