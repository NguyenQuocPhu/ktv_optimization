#include "ktv/gateway/store.hpp"

namespace ktv {

void MemoryRouteStore::put(const std::string& staff_id, const std::string& date, std::string json) {
    std::lock_guard<std::mutex> lock(mutex_);
    routes_[staff_id][date] = std::move(json);
}

std::optional<std::string> MemoryRouteStore::get(const std::string& staff_id, const std::string& date) const {
    std::lock_guard<std::mutex> lock(mutex_);
    auto staff = routes_.find(staff_id);
    if (staff == routes_.end()) return std::nullopt;
    auto entry = staff->second.find(date);
    if (entry == staff->second.end()) return std::nullopt;
    return entry->second;
}

std::optional<std::string> MemoryRouteStore::get_latest(const std::string& staff_id) const {
    std::lock_guard<std::mutex> lock(mutex_);
    auto staff = routes_.find(staff_id);
    if (staff == routes_.end() || staff->second.empty()) return std::nullopt;
    return staff->second.rbegin()->second;  // map sắp theo date tăng dần
}

std::size_t MemoryRouteStore::size() const {
    std::lock_guard<std::mutex> lock(mutex_);
    std::size_t total = 0;
    for (const auto& [staff, by_date] : routes_) total += by_date.size();
    return total;
}

}  // namespace ktv
