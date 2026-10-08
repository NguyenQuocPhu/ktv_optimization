#include "ktv/api.hpp"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <ctime>
#include <regex>
#include <set>

namespace ktv {

const std::vector<TaskKind>& task_kinds() {
    using O = OnTime;
    // Cột cuối (định mức phút khi input bỏ trống handle_minutes) = TGXL chuẩn, catalogue ISC sheet 5 (tham khảo nghiệp vụ,
    // 7.25). Riêng cscd: ISC ghi đã bỏ 2 loại này, workbook (5) không có loại nào → số cũ, không có nguồn [GIẢ ĐỊNH].
    static const std::vector<TaskKind> kinds = {
        {"trien_khai", 3, "trien_khai_net", 120, O::CheckinBeforeB, 3, 120},
        {"trien_khai", 4, "trien_khai_box", 120, O::CheckinBeforeB, 3, 120},  // Workbook mới đổi tên box_cam_only.
        {"trien_khai", 4, "box_cam_only", 120, O::CheckinBeforeB, 3, 120},   // Tên cũ, giữ để chạy fixture cũ. [CHỜ CHỐT bảng số]
        {"trien_khai", 5, "swap", 60, O::CheckinBeforeB, 3, 60},             // Workbook mới coi là subtype của trien_khai_box. [CHỜ CHỐT]
        {"trien_khai", 6, "giao_thiet_bi_cam", 60, O::DoneSameAppointmentDay, 3, 60, true},  // ca chèn (7.16.2) [CHỜ CHỐT]
        {"bao_tri", 1, "bao_tri_vat_ly", 60, O::CheckinBeforeB, 1, 60},
        {"bao_tri", 2, "bao_tri_logic", 60, O::CheckinBeforeB, 2, 60},
        {"thu_hoi", 1, "thu_hoi_thiet_bi", std::nullopt, O::DoneWithinMonth, 4, 15, true},
        {"hoa_don", 1, "hoa_don_tra_truoc", std::nullopt, O::DoneWithinMonth, 4, 15, true},
        {"hoa_don", 2, "hoa_don_tra_sau", std::nullopt, O::DoneWithinMonth, 4, 15, true},
        {"onsite", 1, "phieu_onsite", 60, O::CheckinBeforeB, 2, 60},  // Workbook (4): "rule như bao_tri" (7.15; trước: trong tháng)
        // Workbook API (4) tách nhóm 6 "cscd" (CSKH chủ động): hai loại dưới đây chuyển từ onsite sang (người dùng chốt 7.12).
        // OA còn gửi dưới onsite theo hợp đồng cũ → kind_in_any_group tra ra đây, kèm cảnh báo TASK_TYPE_OTHER_GROUP.
        {"cscd", 2, "ngung_ket_noi_4h", std::nullopt, O::DoneSameCreatedDay, 2, 30, true},
        {"cscd", 3, "chap_chon_suy_hao", std::nullopt, O::DoneSameCreatedDay, 4, 40, true},
    };
    return kinds;
}

const TaskKind* find_kind(const std::string& group, const std::string& name) {
    for (const auto& kind : task_kinds())
        if (group == kind.group && name == kind.name) return &kind;
    return nullptr;
}

std::string issue_key(const Error& warning) {
    std::string path;
    for (size_t i = 0; i < warning.path.size(); ++i) {  // "[12]" → "[]"
        path += warning.path[i];
        if (warning.path[i] == '[')
            while (i + 1 < warning.path.size() && std::isdigit(static_cast<unsigned char>(warning.path[i + 1]))) ++i;
    }
    return warning.code + " " + path + (warning.problem.empty() ? "" : " — " + warning.problem);
}

const std::vector<TaskStatus>& task_statuses() {
    using A = StatusAction;
    // Sheet 05 workbook API (3) + (4), 2026-10-02. cscd và mã không có ở đây: dòng "khác" — chỉ xếp việc còn mở
    // (normalization đoán theo task_status_name).
    static const std::vector<TaskStatus> statuses = {
        {"trien_khai", 96, "Đang di chuyển", A::Route},
        {"trien_khai", 97, "Đã nhận tuyến", A::Route},
        {"trien_khai", 98, "Đã phân công", A::Route},
        {"trien_khai", 0, "check_in (việc đang thực hiện)", A::Current},
        {"trien_khai", 99, "Chưa phân công", A::Unassigned},
        {"trien_khai", 5, "Đã xử lý đang theo dõi", A::Skip},
        {"trien_khai", 1, "Đã hoàn tất", A::Skip},
        {"trien_khai", -2, "Huỷ thi công", A::Skip},
        {"trien_khai", -1, "Chờ xác minh", A::Skip},
        {"bao_tri", 0, "Đã phân công", A::Route},
        {"bao_tri", 6, "Đã nhận ca", A::Route},
        {"bao_tri", 7, "Đang di chuyển", A::Route},
        {"bao_tri", 10, "check_in (việc đang thực hiện)", A::Current},
        {"bao_tri", 2, "Chưa phân công", A::Unassigned},  // ghi chú workbook: chưa phân công thì không đẩy qua AI
        {"bao_tri", 5, "Đã xử lý và đang theo dõi", A::Skip},
        {"bao_tri", 1, "Đã xử lý hoàn tất", A::Skip},
        {"bao_tri", 3, "Đã xử lý hoàn tất qua phone", A::Skip},
        {"bao_tri", 97, "Đã hủy", A::Skip},
        {"bao_tri", 100, "Đóng checklist", A::Skip},
        {"thu_hoi", 0, "Chưa thu hồi", A::Route},
        {"thu_hoi", 1, "Đã thu hồi", A::Skip},  // workbook không ghi "không xếp"; người dùng duyệt coi là xong
        {"thu_hoi", 2, "Đã nhập kho", A::Skip},
        {"thu_hoi", -1, "Đã hủy", A::Skip},
        // Workbook API (4), 2026-10-02.
        {"hoa_don", 0, "Chưa thanh toán", A::Route},
        {"hoa_don", 1, "Đã thanh toán", A::Skip},
        {"onsite", 0, "Chưa xử lý", A::Route},
        {"onsite", 10, "Đang xử lý (check in)", A::Current},
        {"onsite", 1, "Đã hoàn tất", A::Skip},
    };
    return statuses;
}

const TaskStatus* find_status(const std::string& group, int status) {
    for (const auto& item : task_statuses())
        if (group == item.group && status == item.status) return &item;
    return nullptr;
}

const TaskKind* kind_in_any_group(const std::string& name) {
    for (const auto& kind : task_kinds())
        if (name == kind.name) return &kind;
    return nullptr;
}

const TaskKind& kind_or_default(const std::string& group, const std::string& name) {
    static const TaskKind unknown{"", 0, "", std::nullopt, OnTime::CheckinBeforeB, 3, 60};  // [GIẢ ĐỊNH]
    const TaskKind* kind = find_kind(group, name);
    if (!kind) kind = kind_in_any_group(name);  // tra dự phòng: loại đã chuyển nhóm (VD onsite → cscd)
    return kind ? *kind : unknown;
}

namespace {

// Ngày → số ngày kể từ 1970-01-01 (thuật toán của Howard Hinnant).
long long days_from_civil(int y, unsigned m, unsigned d) {
    y -= m <= 2;
    const long long era = (y >= 0 ? y : y - 399) / 400;
    const unsigned yoe = static_cast<unsigned>(y - era * 400);
    const unsigned doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1;
    const unsigned doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    return era * 146097 + static_cast<long long>(doe) - 719468;
}

void civil_from_days(long long z, int& y, unsigned& m, unsigned& d) {
    z += 719468;
    const long long era = (z >= 0 ? z : z - 146096) / 146097;
    const unsigned doe = static_cast<unsigned>(z - era * 146097);
    const unsigned yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    const unsigned doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    const unsigned mp = (5 * doy + 2) / 153;
    d = doy - (153 * mp + 2) / 5 + 1;
    m = mp < 10 ? mp + 3 : mp - 9;
    y = static_cast<int>(yoe + era * 400 + (m <= 2));
}

}  // namespace

Minutes vietnam_now() { return static_cast<Minutes>(std::time(nullptr) / 60) + 7 * 60; }

std::optional<Point> parse_latlng(const std::string& text) {
    static const std::regex pattern(R"(^(-?\d{1,2}(?:\.\d+)?),(-?\d{1,3}(?:\.\d+)?)$)");
    std::smatch match;
    if (!std::regex_match(text, match, pattern)) return std::nullopt;
    Point point{std::stod(match[1]), std::stod(match[2])};
    if (point.lat < 8 || point.lat > 24 || point.lng < 102 || point.lng > 110) return std::nullopt;  // Khung Việt Nam.
    return point;
}

namespace {

std::optional<std::vector<std::pair<int, int>>> parse_available(const std::string& text) {
    static const std::regex part(R"((\d{2}):(\d{2})-(\d{2}):(\d{2}))");
    std::vector<std::pair<int, int>> windows;
    size_t start = 0;
    while (start <= text.size()) {
        size_t comma = text.find(',', start);
        std::string piece = text.substr(start, comma == std::string::npos ? std::string::npos : comma - start);
        std::smatch m;
        if (!std::regex_match(piece, m, part)) return std::nullopt;
        int from = std::stoi(m[1]) * 60 + std::stoi(m[2]), to = std::stoi(m[3]) * 60 + std::stoi(m[4]);
        if (from >= to || to > 24 * 60) return std::nullopt;
        windows.emplace_back(from, to);
        if (comma == std::string::npos) break;
        start = comma + 1;
    }
    return windows;
}

// Số nguyên kể cả khi gửi dạng 3.0 hoặc "3" (dữ liệu nguồn hay lẫn kiểu). Không phải số nguyên → không có.
std::optional<int> whole_number(const json& value) {
    if (value.is_number_integer()) return value.get<int>();
    if (value.is_number_float() && value.get<double>() == std::floor(value.get<double>()) && std::abs(value.get<double>()) < 1e6)
        return static_cast<int>(value.get<double>());
    if (value.is_string()) {
        const std::string& text = value.get_ref<const std::string&>();
        if (!text.empty() && text.size() <= 6 && std::all_of(text.begin(), text.end(), [](unsigned char c) { return std::isdigit(c); }))
            return std::stoi(text);
    }
    return std::nullopt;
}

// Đọc từng field, ghi lỗi theo đường dẫn (thống nhất với bản Python legacy — tag python-legacy-2026-09-30).
struct Reader {
    std::vector<Error>& errors;
    std::vector<Error>* warnings = nullptr;  // Có = chế độ nới lỏng.

    void fail(const std::string& path, const std::string& problem) { errors.push_back({path, problem}); }

    // Lệch hợp đồng nhưng vẫn xếp được: nới lỏng → cảnh báo có mã; strict → lỗi như cũ.
    void tolerate(const char* code, const std::string& path, const std::string& problem) {
        if (warnings) warnings->push_back({path, problem, code});
        else fail(path, problem);
    }

    // Lỗi của MỘT phần (task / lô / current_task) đã gom vào `local`. Strict: chuyển thành lỗi, giữ phần đó
    // như cũ. Nới lỏng: một cảnh báo `code` (nêu lỗi đầu tiên) và bỏ riêng phần đó. Trả true = giữ phần đó.
    bool keep(const std::vector<Error>& local, const char* code, const std::string& path) {
        if (local.empty()) return true;
        if (!warnings) {
            errors.insert(errors.end(), local.begin(), local.end());
            return true;
        }
        const Error& first = local.front();
        const std::string field = first.path.size() > path.size() ? first.path.substr(path.size() + 1) : "";
        warnings->push_back({path, (field.empty() ? "" : field + ": ") + first.problem, code});
        return false;
    }

    const json* field(const json& obj, const std::string& path, const char* key, bool required) {
        auto it = obj.find(key);
        if (it == obj.end()) {
            if (required) fail(path + "." + key, "thiếu field bắt buộc");
            return nullptr;
        }
        return &*it;
    }

    void only(const json& obj, const std::string& path, std::initializer_list<const char*> allowed) {
        for (auto it = obj.begin(); it != obj.end(); ++it) {
            bool ok = false;
            for (const char* key : allowed) ok |= it.key() == key;
            if (!ok) tolerate("UNKNOWN_FIELD", path + "." + it.key(), "field không có trong file API");
        }
    }

    template <class T>
    T integer(const json& obj, const std::string& path, const char* key, bool required = true) {
        const json* value = field(obj, path, key, required);
        if (!value) return 0;
        if (!value->is_number_integer()) {
            fail(path + "." + key, "cần số nguyên");
            return 0;
        }
        return value->get<T>();
    }

    std::string text(const json& obj, const std::string& path, const char* key, bool required = true, bool non_empty = false) {
        const json* value = field(obj, path, key, required);
        if (!value) return {};
        if (!value->is_string() || (non_empty && value->get_ref<const std::string&>().empty())) {
            fail(path + "." + key, non_empty ? "cần chuỗi khác rỗng" : "cần chuỗi");
            return {};
        }
        return value->get<std::string>();
    }
};

}  // namespace

std::optional<Minutes> parse_datetime(const std::string& text) {
    int y, mo, d, h, mi, s;
    char tail;
    if (text.size() != 19 || std::sscanf(text.c_str(), "%4d-%2d-%2d %2d:%2d:%2d%c", &y, &mo, &d, &h, &mi, &s, &tail) != 6)
        return std::nullopt;
    if (text[4] != '-' || text[7] != '-' || text[10] != ' ' || text[13] != ':' || text[16] != ':') return std::nullopt;
    static const int month_days[] = {31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
    bool leap = (y % 4 == 0 && y % 100 != 0) || y % 400 == 0;
    if (mo < 1 || mo > 12 || d < 1 || d > month_days[mo - 1] || (mo == 2 && d == 29 && !leap) || h > 23 || mi > 59 || s > 59)
        return std::nullopt;
    return days_from_civil(y, mo, d) * 1440 + h * 60 + mi;  // Giây bị bỏ: routing tính theo phút.
}

std::string format_datetime(Minutes value) {
    long long days = value >= 0 ? value / 1440 : (value - 1439) / 1440;
    int minute = static_cast<int>(value - days * 1440);
    int y;
    unsigned m, d;
    civil_from_days(days, y, m, d);
    char buffer[32];
    std::snprintf(buffer, sizeof buffer, "%04d-%02u-%02u %02d:%02d:00", y, m, d, minute / 60, minute % 60);
    return buffer;
}

Message parse_message(const json& data, std::vector<Error>& errors, std::vector<Error>* warnings) {
    Reader r{errors, warnings};
    Message message;
    if (!data.is_object()) {
        r.fail("", "cần object JSON");
        return message;
    }
    r.only(data, "", {"message_id", "planned_at", "trigger", "priority_type", "staff", "tasks"});
    if (data.contains("priority_type")) {  // 7.23: chế độ sắp xếp; nới lỏng: sai → 0 (mặc định) + cảnh báo
        const json& value = data["priority_type"];
        if (value.is_number_integer() && value.get<int>() >= 0 && value.get<int>() <= 2) message.priority_type = value.get<int>();
        else r.tolerate("PRIORITY_TYPE", ".priority_type", "cần 0, 1 hoặc 2 (đang là " + value.dump() + "), dùng 0");
    }
    message.message_id = r.text(data, "", "message_id", false);
    message.trigger = r.text(data, "", "trigger", false);
    if (data.contains("planned_at")) {  // Nới lỏng: sai thì bỏ, lập tuyến theo giờ server.
        const json& value = data["planned_at"];
        message.planned_at = value.is_string() ? parse_datetime(value.get<std::string>()) : std::nullopt;
        if (!message.planned_at) r.tolerate("PLANNED_AT", ".planned_at", "cần \"YYYY-MM-DD HH:mm:ss\"");
    }

    // ---- staff
    const json* staff = r.field(data, "", "staff", true);
    if (staff && !staff->is_object()) {
        r.fail("staff", "cần object");
    } else if (staff) {
        const std::string p = "staff";
        r.only(*staff, p, {"staff_id", "staff_account", "staff_location", "latlng", "available", "plots", "current_task", "status"});
        auto& s = message.staff;
        s.staff_id = r.text(*staff, p, "staff_id", true, true);
        s.staff_account = r.text(*staff, p, "staff_account", true, true);
        s.staff_location = r.text(*staff, p, "staff_location", false);
        if (staff->contains("status")) {  // Không bắt buộc: workbook mới có, JSON mẫu không gửi.
            const json& value = (*staff)["status"];
            const std::optional<int> number = whole_number(value);
            const std::string shown = "(đang là " + value.dump() + ")";
            if (value.is_number_integer() && *number >= 1 && *number <= 3) {
                s.status = *number;
            } else if (number && *number >= 1 && *number <= 3) {  // "3" hoặc 3.0: hiểu là số, vẫn ghi sổ.
                s.status = *number;
                r.tolerate("STAFF_STATUS", p + ".status", "cần số nguyên 1, 2 hoặc 3 " + shown + ", đã hiểu là " + std::to_string(*number));
            } else {
                // 7.21: status không còn chặn tuyến (KTV off cũng xếp) → giá trị lạ vẫn xếp, chỉ ghi sổ.
                s.status = kStaffStatusUnknown;
                r.tolerate("STAFF_STATUS", p + ".status", "cần 1, 2 hoặc 3 " + shown + ", vẫn xếp tuyến như rảnh/bận");
            }
        }
        if (staff->contains("latlng")) {
            auto point = (*staff)["latlng"].is_string() ? parse_latlng((*staff)["latlng"].get<std::string>()) : std::nullopt;
            if (point) s.latlng = *point;
            else r.fail(p + ".latlng", "cần \"lat,lng\" trong Việt Nam");
        } else {
            r.fail(p + ".latlng", "thiếu field bắt buộc");
        }
        const json* available = staff->contains("available") ? &(*staff)["available"] : nullptr;
        const bool no_available = !available || available->is_null() ||
                                  (available->is_string() && available->get<std::string>().empty());
        if (no_available && s.status == kStaffStatusOff) {
            // 7.21 [GIẢ ĐỊNH]: KTV off vẫn xếp tuyến nhưng có thể không có lịch trực → khung mặc định. Chỉ cho KTV off.
            s.available = *parse_available(kOffDefaultAvailable);
            r.tolerate("AVAILABLE_DEFAULT", p + ".available",
                       std::string("KTV off thiếu available, dùng mặc định ") + kOffDefaultAvailable);
        } else if (available) {
            auto windows = (*staff)["available"].is_string() ? parse_available((*staff)["available"].get<std::string>()) : std::nullopt;
            if (windows) s.available = *windows;
            else r.fail(p + ".available", "cần \"HH:mm-HH:mm,...\" với giờ đầu < giờ cuối");
        } else {
            r.fail(p + ".available", "thiếu field bắt buộc");
        }
        // Lô chỉ dùng để đặt tên cụm: nới lỏng thì lô hỏng bị bỏ, tên cụm lùi về "Lô <id>".
        const json* plots = r.field(*staff, p, "plots", false);
        if (!plots) {
            r.tolerate("STAFF_PLOTS", p + ".plots", "thiếu field bắt buộc");
        } else if (!plots->is_array() || plots->empty()) {
            r.tolerate("STAFF_PLOTS", p + ".plots", "cần mảng khác rỗng");
        } else {
            int main_plots = 0;
            for (size_t i = 0; i < plots->size(); ++i) {
                const json& item = (*plots)[i];
                std::string path = p + ".plots[" + std::to_string(i) + "]";
                std::vector<Error> local;
                Reader pr{local, warnings};
                if (!item.is_object()) {
                    pr.fail(path, "cần object");
                    r.keep(local, "STAFF_PLOTS", path);
                    continue;
                }
                pr.only(item, path, {"id", "name", "role", "block_id"});
                Plot plot{pr.integer<int>(item, path, "id"), pr.text(item, path, "name", false),
                          pr.integer<int>(item, path, "role"), pr.integer<int>(item, path, "block_id")};
                if (item.contains("role") && item["role"].is_number_integer() && plot.role != 1 && plot.role != 2)
                    pr.fail(path + ".role", "cần 1 hoặc 2");
                if (!r.keep(local, "STAFF_PLOTS", path)) continue;
                main_plots += plot.role == 1;
                s.plots.push_back(plot);
            }
            if (main_plots != 1) r.tolerate("STAFF_PLOTS", p + ".plots", "cần đúng một lô chính (role = 1)");
        }
        // Việc đang làm: nới lỏng thì hỏng = coi như không có (xuất phát lúc lập tuyến thay vì +30 phút).
        if (!staff->contains("current_task")) {
            r.tolerate("CURRENT_TASK", p + ".current_task", "thiếu field bắt buộc (null nếu không có)");
        } else if (!(*staff)["current_task"].is_null()) {
            const json& current = (*staff)["current_task"];
            const std::string cp = p + ".current_task";
            std::vector<Error> local;
            Reader cr{local, warnings};
            CurrentTask current_task;
            if (!current.is_object()) {
                cr.fail(cp, "cần object hoặc null");
            } else {
                cr.only(current, cp, {"task_id", "task_status_id", "task_type_id"});
                current_task = CurrentTask{cr.integer<long long>(current, cp, "task_id"),
                                           cr.integer<int>(current, cp, "task_status_id"),
                                           cr.integer<int>(current, cp, "task_type_id")};
            }
            if (r.keep(local, "CURRENT_TASK", cp) && current.is_object()) s.current_task = current_task;
        }
    }

    // ---- tasks
    const json* tasks = r.field(data, "", "tasks", true);
    if (!tasks) return message;
    if (!tasks->is_object()) {
        r.fail("tasks", "cần object 5 khóa trien_khai, bao_tri, thu_hoi, hoa_don, onsite (+ cscd nếu có)");
        return message;
    }
    std::set<std::string> keys;
    for (auto it = tasks->begin(); it != tasks->end(); ++it) keys.insert(it.key());
    const std::set<std::string> groups(std::begin(kGroups), std::end(kGroups));
    const std::set<std::string> required(std::begin(kGroups), std::begin(kGroups) + kRequiredGroups);
    bool unknown_key = false, missing_key = false;
    for (const std::string& key : keys) unknown_key |= !groups.count(key);
    for (const std::string& key : required) missing_key |= !keys.count(key);
    if (unknown_key || missing_key) {
        if (!warnings) {
            r.fail("tasks", "cần đúng 5 khóa trien_khai, bao_tri, thu_hoi, hoa_don, onsite (+ cscd nếu có)");
        } else {  // Nới lỏng: nhóm lạ bỏ qua, nhóm bắt buộc thiếu coi như [].
            for (const std::string& key : keys)
                if (!groups.count(key)) r.tolerate("UNKNOWN_FIELD", "tasks." + key, "field không có trong file API");
            for (const std::string& key : required)
                if (!keys.count(key)) r.tolerate("TASK_GROUPS", "tasks." + key, "thiếu nhóm, coi như []");
        }
    }
    std::set<long long> seen;
    for (int g = 0; g < kGroupCount; ++g) {
        const std::string key = kGroups[g];
        auto found = tasks->find(key);
        if (found == tasks->end()) continue;
        if (!found->is_array()) {
            r.tolerate("TASK_DROPPED", "tasks." + key, "cần mảng ([] nếu không có việc)");
            continue;
        }
        for (size_t i = 0; i < found->size(); ++i) {
            const json& item = (*found)[i];
            const std::string path = "tasks." + key + "[" + std::to_string(i) + "]";
            // Lỗi của task này gom vào `local`: nới lỏng thì chỉ bỏ task này (TASK_DROPPED), không bỏ cả KTV.
            // Cảnh báo của task cũng gom riêng: task bị bỏ chỉ để lại một TASK_DROPPED, không đếm dư vào sổ.
            std::vector<Error> local, local_warnings;
            Reader tr{local, warnings ? &local_warnings : nullptr};
            if (!item.is_object()) {
                tr.fail(path, "cần object");
                r.keep(local, "TASK_DROPPED", path);
                continue;
            }
            tr.only(item, path,
                    {"task_id", "task_group_id", "task_group_name", "task_type_id", "task_type_name", "task_sub_id",
                     "task_sub_name", "task_status_id", "task_status_name", "sla", "appointment", "create_date",
                     "CreateDate", "complete_date", "location", "latlng", "handle_minutes", "task_plots_id", "staff_plots_id",
                     "staff_role", "block_id", "location_id", "contract_id", "contract_no", "contract_name",
                     "bill_number", "timezone"});
            Task t;
            t.task_id = tr.integer<long long>(item, path, "task_id");
            t.task_group_id = tr.integer<int>(item, path, "task_group_id");
            t.task_type_id = tr.integer<int>(item, path, "task_type_id");
            t.task_sub_id = tr.integer<int>(item, path, "task_sub_id", false);
            t.task_status_id = tr.integer<int>(item, path, "task_status_id");
            t.task_plots_id = tr.integer<int>(item, path, "task_plots_id");
            t.staff_plots_id = tr.integer<int>(item, path, "staff_plots_id");
            t.block_id = tr.integer<int>(item, path, "block_id");
            tr.integer<int>(item, path, "location_id", false);
            t.task_group_name = tr.text(item, path, "task_group_name");
            t.task_type_name = tr.text(item, path, "task_type_name");
            t.task_status_name = tr.text(item, path, "task_status_name");
            t.task_sub_name = tr.text(item, path, "task_sub_name", false);
            t.location = tr.text(item, path, "location", false);

            if (item.contains("appointment")) {
                const json& value = item["appointment"];
                if (value.is_string() && value.get_ref<const std::string&>().empty()) {
                } else if (auto when = value.is_string() ? parse_datetime(value.get<std::string>()) : std::nullopt) {
                    t.appointment = when;
                } else {
                    tr.fail(path + ".appointment", "cần \"\" hoặc \"YYYY-MM-DD HH:mm:ss\"");
                }
            }
            // "" = không có; sai định dạng = lỗi của task.
            auto read_date = [&](const char* key) -> std::optional<Minutes> {
                if (!item.contains(key)) return std::nullopt;
                const json& value = item[key];
                if (value.is_string() && value.get_ref<const std::string&>().empty()) return std::nullopt;
                if (auto when = value.is_string() ? parse_datetime(value.get<std::string>()) : std::nullopt) return when;
                tr.fail(path + "." + key, "cần \"\" hoặc \"YYYY-MM-DD HH:mm:ss\"");
                return std::nullopt;
            };
            // create_date: workbook (3) ghi "CreateDate", staging gửi "create_date" → hai tên là MỘT field, tên nào cũng
            // nhận. Gửi cả hai mà khác giá trị → lấy create_date + cảnh báo.
            const std::optional<Minutes> snake = read_date("create_date"), camel = read_date("CreateDate");
            t.create_date = snake ? snake : camel;
            if (snake && camel && *snake != *camel)
                tr.tolerate("CREATE_DATE_CONFLICT", path + ".CreateDate",
                            "khác create_date (" + format_datetime(*snake) + " ≠ " + format_datetime(*camel) + "), dùng create_date");
            t.complete_date = read_date("complete_date");
            if (item.contains("contract_id")) {
                const json& value = item["contract_id"];
                if (value.is_null()) {
                } else if (value.is_number_integer()) {
                    t.contract_id = value.get<long long>();
                } else {
                    tr.fail(path + ".contract_id", "cần số nguyên");
                }
            }
            if (item.contains("contract_no") && !item["contract_no"].is_null()) {  // null = không có, như contract_id
                if (item["contract_no"].is_string()) t.contract_no = item["contract_no"].get<std::string>();
                else tr.fail(path + ".contract_no", "cần chuỗi hoặc null");
            }
            // 7.27: field chuỗi mới của workbook (6); null = không có.
            for (auto [key, target] : {std::pair<const char*, std::string*>{"contract_name", &t.contract_name},
                                       {"bill_number", &t.bill_number}, {"timezone", &t.timezone}}) {
                if (!item.contains(key) || item[key].is_null()) continue;
                if (item[key].is_string()) *target = item[key].get<std::string>();
                else tr.fail(path + "." + key, "cần chuỗi hoặc null");
            }
            if (!item.contains("latlng")) {
                tr.fail(path + ".latlng", "thiếu field bắt buộc");
            } else if (const json& value = item["latlng"]; !(value.is_string() && value.get_ref<const std::string&>().empty())) {
                // Thiếu tọa độ ("") là hợp lệ: quy tắc cứng 4 loại việc đó khỏi tuyến.
                if (auto point = value.is_string() ? parse_latlng(value.get<std::string>()) : std::nullopt) t.latlng = point;
                else tr.fail(path + ".latlng", "cần \"\" hoặc \"lat,lng\" trong Việt Nam");
            }
            if (item.contains("handle_minutes")) {
                const json& value = item["handle_minutes"];
                if (value.is_null()) {
                } else if (value.is_number_integer()) {
                    if (value.get<int>() > 0) t.handle_minutes = value.get<int>();  // 0 = dùng định mức.
                    else if (value.get<int>() < 0) tr.fail(path + ".handle_minutes", "cần số nguyên ≥ 0");
                } else if (value.is_string() && value.get_ref<const std::string&>().empty()) {
                } else {
                    tr.fail(path + ".handle_minutes", "cần \"\", null hoặc số nguyên ≥ 0");
                }
            }
            // 0 default (không khớp lô nào của KTV — workbook (3)) / 1 chính / 2 kiêm nhiệm / 3 hỗ trợ. Không dùng khi xếp
            // tuyến, nên nới lỏng thì giữ nguyên số lạ.
            if (item.contains("staff_role")) {
                const json& value = item["staff_role"];
                if (value.is_number_integer() && value.get<int>() >= 0 && value.get<int>() <= 3) {
                    t.staff_role = value.get<int>();
                } else {
                    if (value.is_number_integer()) t.staff_role = value.get<int>();
                    tr.tolerate("STAFF_ROLE", path + ".staff_role", "cần 0, 1, 2 hoặc 3 (đang là " + value.dump() + ")");
                }
            } else {
                tr.tolerate("STAFF_ROLE", path + ".staff_role", "thiếu field bắt buộc");
            }

            const json* sla = tr.field(item, path, "sla", true);
            if (sla && !sla->is_object()) {
                tr.fail(path + ".sla", "cần object {sla_minutes, priority_in_day}");
                sla = nullptr;
            } else if (sla) {
                const std::string sp = path + ".sla";
                tr.only(*sla, sp, {"sla_minutes", "priority_in_day"});
                if (const json* value = tr.field(*sla, sp, "sla_minutes", true)) {
                    if (value->is_number_integer() && value->get<int>() > 0) t.sla_minutes = value->get<int>();
                    else if (!value->is_null()) tr.fail(sp + ".sla_minutes", "cần số nguyên > 0 hoặc null");
                }
                if (const json* value = tr.field(*sla, sp, "priority_in_day", true)) {
                    if (value->is_number_integer() && value->get<int>() >= 1 && value->get<int>() <= 4) t.priority_in_day = value->get<int>();
                    else tr.fail(sp + ".priority_in_day", "cần 1–4");
                }
            }

            // Danh mục (sheet 05) chỉ cho định mức thời gian + kiểu hạn; SLA/ưu tiên xếp tuyến lấy từ input.
            // Nên nới lỏng: lệch/ngoài danh mục vẫn xếp (loại ngoài danh mục dùng kind_or_default).
            // Task đã hỏng (sẽ bị bỏ) thì không cảnh báo thêm về danh mục cho đỡ nhiễu.
            if (!warnings || local.empty()) {
                if (t.task_group_name != key || t.task_group_id != g + 1) {
                    tr.tolerate("TASK_GROUP", path, "nằm trong nhóm " + key + " nhưng task_group khác, dùng nhóm " + key);
                    // plan() tra danh mục theo task_group_name: phải cùng nhóm với lúc kiểm ở đây, không thì
                    // âm thầm rơi về loại mặc định (sai định mức + kiểu hạn).
                    t.task_group_name = key;
                    t.task_group_id = g + 1;
                }
                const TaskKind* kind = find_kind(key, t.task_type_name);
                if (!kind && (kind = kind_in_any_group(t.task_type_name)))  // loại đã chuyển nhóm: dùng luật nhóm kia
                    tr.tolerate("TASK_TYPE_OTHER_GROUP", path + ".task_type_name",
                                t.task_type_name + " thuộc nhóm " + kind->group + " (workbook API (4)), không phải " + key +
                                    "; dùng luật nhóm " + kind->group);
                if (kind) {
                    if (t.task_type_id != kind->type_id)
                        tr.tolerate("CATALOG_MISMATCH", path + ".task_type_id", "danh mục ghi " + std::to_string(kind->type_id));
                    if (sla && (t.sla_minutes != kind->sla_minutes || t.priority_in_day != kind->priority))
                        tr.tolerate("CATALOG_MISMATCH", path + ".sla",
                                    "khác danh mục sheet 05 (" + key + "/" + t.task_type_name + ": input " +
                                        (t.sla_minutes ? std::to_string(*t.sla_minutes) : "null") + " phút/P" +
                                        std::to_string(t.priority_in_day) + ", danh mục " +
                                        (kind->sla_minutes ? std::to_string(*kind->sla_minutes) : "null") + " phút/P" +
                                        std::to_string(kind->priority) + ")");
                } else {  // không có ở nhóm nào
                    tr.tolerate("UNKNOWN_TASK_TYPE", path + ".task_type_name",
                                "không có trong danh mục sheet 05: " + key + "/" + t.task_type_name);
                }
            }
            // Strict: kiểm trùng như cũ. Nới lỏng: chỉ task sạch mới giữ chỗ ID, task trùng sau bị bỏ.
            if ((!warnings || local.empty()) && !seen.insert(t.task_id).second)
                tr.fail(path + ".task_id", "trùng với việc khác trong message");
            if (r.keep(local, "TASK_DROPPED", path)) {
                if (warnings) warnings->insert(warnings->end(), local_warnings.begin(), local_warnings.end());
                message.tasks.push_back(std::move(t));
            }
        }
    }
    // Staging có thể gửi kèm row task của việc đang làm để bổ sung dữ liệu; không coi là lỗi.
    // Việc lọc/tách current khỏi candidate thuộc bước normalization, không phải parser.
    return message;
}

}  // namespace ktv
