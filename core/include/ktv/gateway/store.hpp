// ============================================================================
// gateway/store — KHO BẢN ROUTE MỚI NHẤT theo (staff_id, date)
// ============================================================================
// Hiểu nhanh:
//   Gateway chỉ đọc. Store là chỗ giữ bản route đã tính cho từng KTV/ngày.
//   Module này không biết HTTP, Kafka hay Redis: chỉ `put`, `get`, `get_latest`, `size`.
//   Bản in-memory phục vụ pilot; Redis sẽ là một implementation khác cùng interface.
//
// Dùng thế nào:
//   MemoryRouteStore store;
//   store.put("00201964", "2026-08-19", out_json);
//   auto v = store.get_latest("00201964");
//
// Phụ thuộc: không (chỉ thư viện chuẩn).
// ============================================================================
#pragma once

#include <cstddef>
#include <map>
#include <mutex>
#include <optional>
#include <string>

namespace ktv {

// Hợp đồng store: giá trị là nguyên chuỗi JSON của response OUT.
class RouteStore {
public:
    virtual ~RouteStore() = default;

    virtual void put(const std::string& staff_id, const std::string& date, std::string json) = 0;
    virtual std::optional<std::string> get(const std::string& staff_id, const std::string& date) const = 0;
    virtual std::optional<std::string> get_latest(const std::string& staff_id) const = 0;
    virtual std::size_t size() const = 0;
};

// Bản in-memory, an toàn nhiều luồng. Date dạng "YYYY-MM-DD" nên so chuỗi = so ngày.
class MemoryRouteStore : public RouteStore {
public:
    void put(const std::string& staff_id, const std::string& date, std::string json) override;
    std::optional<std::string> get(const std::string& staff_id, const std::string& date) const override;
    std::optional<std::string> get_latest(const std::string& staff_id) const override;
    std::size_t size() const override;

private:
    mutable std::mutex mutex_;
    std::map<std::string, std::map<std::string, std::string>> routes_;  // staff -> date -> json
};

}  // namespace ktv
