#include "ktv/normalization.hpp"

#include <cctype>
#include <sstream>

namespace ktv {
namespace {

// Chữ thường, bỏ dấu tiếng Việt, '_' / '-' thành dấu cách. "Đã Hủy" → "da huy", "hoan_tat" → "hoan tat".
std::string fold(const std::string& text) {
    // Mỗi chuỗi: các chữ có dấu (thường + hoa) quy về một chữ không dấu.
    static const std::pair<const char*, char> groups[] = {
        {"àáảãạăằắẳẵặâầấẩẫậÀÁẢÃẠĂẰẮẲẴẶÂẦẤẨẪẬ", 'a'}, {"èéẻẽẹêềếểễệÈÉẺẼẸÊỀẾỂỄỆ", 'e'},
        {"ìíỉĩịÌÍỈĨỊ", 'i'}, {"òóỏõọôồốổỗộơờớởỡợÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢ", 'o'},
        {"ùúủũụưừứửữựÙÚỦŨỤƯỪỨỬỮỰ", 'u'}, {"ỳýỷỹỵỲÝỶỸỴ", 'y'}, {"đĐ", 'd'}};
    std::string out;
    for (size_t i = 0; i < text.size();) {
        const unsigned char lead = static_cast<unsigned char>(text[i]);
        const size_t length = lead < 0x80 ? 1 : lead < 0xE0 ? 2 : lead < 0xF0 ? 3 : 4;
        const std::string letter = text.substr(i, length);
        i += length;
        if (length == 1) {
            out += lead == '_' || lead == '-' ? ' ' : static_cast<char>(std::tolower(lead));
            continue;
        }
        char base = 0;
        for (const auto& [letters, plain] : groups)
            if (std::string(letters).find(letter) != std::string::npos) base = plain;
        if (base) out += base;
    }
    return out;
}

// "Thiếu thì coi là còn mở" khi mã không có trong bảng: cảnh báo kèm mã + tên để mang đi hỏi team data.
void warn(NormalizedWorklist& result, const Task& task, const char* code, const std::string& problem) {
    result.warnings.push_back({"tasks." + task.task_group_name + ".task_status_id", problem, code});
}

std::string describe(const Task& task) {
    return std::to_string(task.task_status_id) + " (\"" + task.task_status_name + "\")";
}

}  // namespace

bool status_name_closed(const std::string& name) {
    std::istringstream words(fold(name));
    std::string text, word;
    while (words >> word) text += " " + word;
    text += " ";
    if (text.find(" chua ") != std::string::npos) return false;  // "Chưa hoàn tất", "Chưa thu hồi": còn mở.
    for (const char* closed : {" huy ", " hoan tat ", " da xu ly ", " dong checklist ", " da dong ", " da thu ",
                               " nhap kho ", " cancel ", " canceled ", " cancelled ", " closed ", " done ", " completed "})
        if (text.find(closed) != std::string::npos) return true;
    return false;
}

NormalizedWorklist normalize_worklist(const Message& message) {
    NormalizedWorklist result;
    // 7.21: KTV off vẫn có ca tồn → vẫn tính tuyến (catalogue ISC E-01, sheet 2 bước 1). Chỉ ghi log để đếm.
    if (message.staff.status == kStaffStatusOff)
        result.warnings.push_back({"staff.status", "KTV off (status 3), vẫn xếp tuyến", "STAFF_OFF"});

    const std::optional<long long> current_id =
        message.staff.current_task ? std::optional<long long>(message.staff.current_task->task_id) : std::nullopt;

    for (const Task& task : message.tasks) {
        ++result.stats.tasks;

        if (current_id && task.task_id == *current_id) {  // Row của việc đang làm: dùng làm current, không thành stop.
            result.current_task = task;
            ++result.stats.excluded_current;
            continue;
        }

        // Trạng thái theo nhóm (sheet 05). Cùng mã khác nghĩa theo nhóm nên tra theo cặp (nhóm, mã).
        bool route = false;
        if (const TaskStatus* status = find_status(task.task_group_name, task.task_status_id)) {
            route = status->action == StatusAction::Route;
            if (status->action == StatusAction::Unassigned)
                warn(result, task, "TASK_STATUS_UNASSIGNED", "\"chưa phân công\" " + describe(task) + " vẫn được gửi sang, không xếp");
            else if (status->action == StatusAction::Current)
                warn(result, task, "CURRENT_NOT_MATCHED",
                     "trạng thái đang làm " + describe(task) + " nhưng không trùng staff.current_task, không xếp");
        } else if (status_name_closed(task.task_status_name)) {
            warn(result, task, "TASK_STATUS_UNKNOWN_CLOSED",
                 "mã " + describe(task) + " không có trong danh mục, tên cho thấy đã xong/hủy, không xếp");
        } else {
            // Workbook dòng "khác": chỉ xếp việc còn mở. Không có bảng (hoa_don/onsite, mã lạ) và tên không cho thấy
            // đã xong → coi là còn mở: bỏ sót việc khó phát hiện hơn xếp thừa, và cảnh báo lộ ở /healthz.
            route = true;
            warn(result, task, "TASK_STATUS_UNKNOWN", "mã " + describe(task) + " không có trong danh mục, coi là còn mở, vẫn xếp");
        }
        if (!route) {
            ++result.stats.excluded_status;
            continue;
        }
        // complete_date KHÔNG loại task (workbook (3): là ngày hoàn tất kỳ trước, VD ngày thu bill trước); việc đã xong
        // do trạng thái ở trên quyết định.
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
