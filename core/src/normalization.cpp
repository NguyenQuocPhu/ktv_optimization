#include "ktv/normalization.hpp"

namespace ktv {

NormalizedWorklist normalize_worklist(const Message& message) {
    NormalizedWorklist result;
    result.staff_off = message.staff.status == kStaffOff || message.staff.status == kStaffStatusUnknown;

    const std::optional<long long> current_id =
        message.staff.current_task ? std::optional<long long>(message.staff.current_task->task_id) : std::nullopt;

    for (const Task& task : message.tasks) {
        ++result.stats.tasks;
        if (result.staff_off) continue;  // KTV off: không xếp bất kỳ việc nào.

        if (current_id && task.task_id == *current_id) {  // Row của việc đang làm: dùng làm current, không thành stop.
            result.current_task = task;
            ++result.stats.excluded_current;
            continue;
        }
        if (task.task_status_id != kStatusRoutable) {  // 10 không khớp current, hoặc mã khác.
            ++result.stats.excluded_status;
            continue;
        }
        if (task.complete_date) {
            ++result.stats.excluded_completed;
            continue;
        }
        if (!task.latlng) {
            ++result.stats.excluded_missing_location;
            continue;
        }
        result.candidates.push_back(&task);
    }

    // current_task không có row trong tasks: dựng bản tối thiểu để giữ behavior cũ (điểm/giờ xuất phát).
    if (!result.current_task && message.staff.current_task) {
        Task minimal;
        minimal.task_id = message.staff.current_task->task_id;
        minimal.task_status_id = message.staff.current_task->task_status_id;
        minimal.task_type_id = message.staff.current_task->task_type_id;
        result.current_task = minimal;
    }

    result.stats.candidates = static_cast<int>(result.candidates.size());
    return result;
}

}  // namespace ktv
