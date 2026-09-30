#include "ktv/api.hpp"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <regex>
#include <set>

namespace ktv {

const std::vector<TaskKind>& task_kinds() {
    using O = OnTime;
    static const std::vector<TaskKind> kinds = {
        {"trien_khai", 3, "trien_khai_net", 120, O::CheckinBeforeB, 3, 120},
        {"trien_khai", 4, "trien_khai_box", 120, O::CheckinBeforeB, 3, 60},  // Workbook mới đổi tên box_cam_only; định mức chờ chốt.
        {"trien_khai", 4, "box_cam_only", 120, O::CheckinBeforeB, 3, 60},    // Tên cũ, giữ để chạy fixture cũ. [CHỜ CHỐT bảng số]
        {"trien_khai", 5, "swap", 60, O::CheckinBeforeB, 3, 45},             // Workbook mới coi là subtype của trien_khai_box. [CHỜ CHỐT]
        {"trien_khai", 6, "giao_thiet_bi_cam", 60, O::DoneSameAppointmentDay, 3, 30},  // [CHỜ CHỐT]
        {"bao_tri", 1, "bao_tri_vat_ly", 60, O::CheckinBeforeB, 1, 60},
        {"bao_tri", 2, "bao_tri_logic", 60, O::CheckinBeforeB, 2, 45},
        {"thu_hoi", 1, "thu_hoi_thiet_bi", std::nullopt, O::DoneWithinMonth, 4, 20},
        {"hoa_don", 1, "hoa_don_tra_truoc", std::nullopt, O::DoneWithinMonth, 4, 15},
        {"hoa_don", 2, "hoa_don_tra_sau", std::nullopt, O::DoneWithinMonth, 4, 15},
        {"onsite", 1, "phieu_onsite", std::nullopt, O::DoneWithinMonth, 2, 45},
        {"onsite", 2, "ngung_ket_noi_4h", std::nullopt, O::DoneSameCreatedDay, 2, 30},
        {"onsite", 3, "chap_chon_suy_hao", std::nullopt, O::DoneSameCreatedDay, 4, 40},
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

const TaskKind& kind_or_default(const std::string& group, const std::string& name) {
    static const TaskKind unknown{"", 0, "", std::nullopt, OnTime::CheckinBeforeB, 3, 60};  // [GIẢ ĐỊNH]
    const TaskKind* kind = find_kind(group, name);
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

std::optional<Point> parse_latlng(const std::string& text) {
    static const std::regex pattern(R"(^(-?\d{1,2}(?:\.\d+)?),(-?\d{1,3}(?:\.\d+)?)$)");
    std::smatch match;
    if (!std::regex_match(text, match, pattern)) return std::nullopt;
    Point point{std::stod(match[1]), std::stod(match[2])};
    if (point.lat < 8 || point.lat > 24 || point.lng < 102 || point.lng > 110) return std::nullopt;  // Khung Việt Nam.
    return point;
}

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
    r.only(data, "", {"message_id", "planned_at", "trigger", "staff", "tasks"});
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
                // Không rõ KTV có đang làm không: xếp nhầm cho người đang nghỉ tệ hơn bỏ sót một lần → không xếp (422).
                s.status = kStaffStatusUnknown;
                r.tolerate("STAFF_STATUS", p + ".status", "cần 1, 2 hoặc 3 " + shown + ", không xếp tuyến");
            }
        }
        if (staff->contains("latlng")) {
            auto point = (*staff)["latlng"].is_string() ? parse_latlng((*staff)["latlng"].get<std::string>()) : std::nullopt;
            if (point) s.latlng = *point;
            else r.fail(p + ".latlng", "cần \"lat,lng\" trong Việt Nam");
        } else {
            r.fail(p + ".latlng", "thiếu field bắt buộc");
        }
        if (staff->contains("available")) {
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
        r.fail("tasks", "cần object 5 khóa");
        return message;
    }
    std::set<std::string> keys;
    for (auto it = tasks->begin(); it != tasks->end(); ++it) keys.insert(it.key());
    const std::set<std::string> groups(std::begin(kGroups), std::end(kGroups));
    if (keys != groups) {
        if (!warnings) {
            r.fail("tasks", "cần đúng 5 khóa trien_khai, bao_tri, thu_hoi, hoa_don, onsite");
        } else {  // Nới lỏng: nhóm lạ bỏ qua, nhóm thiếu coi như [].
            for (const std::string& key : keys)
                if (!groups.count(key)) r.tolerate("UNKNOWN_FIELD", "tasks." + key, "field không có trong file API");
            for (const std::string& key : groups)
                if (!keys.count(key)) r.tolerate("TASK_GROUPS", "tasks." + key, "thiếu nhóm, coi như []");
        }
    }
    std::set<long long> seen;
    for (int g = 0; g < 5; ++g) {
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
                     "complete_date", "location", "latlng", "handle_minutes", "task_plots_id", "staff_plots_id",
                     "staff_role", "block_id", "location_id", "contract_id", "contract_no"});
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
            const std::pair<const char*, std::optional<Minutes>*> date_fields[] = {
                {"create_date", &t.create_date}, {"complete_date", &t.complete_date}};
            for (const auto& field : date_fields) {
                if (!item.contains(field.first)) continue;
                const json& value = item[field.first];
                if (value.is_string() && value.get_ref<const std::string&>().empty()) {
                } else if (auto when = value.is_string() ? parse_datetime(value.get<std::string>()) : std::nullopt) {
                    *field.second = when;
                } else {
                    tr.fail(path + "." + field.first, "cần \"\" hoặc \"YYYY-MM-DD HH:mm:ss\"");
                }
            }
            if (item.contains("contract_id")) {
                const json& value = item["contract_id"];
                if (value.is_null()) {
                } else if (value.is_number_integer()) {
                    t.contract_id = value.get<long long>();
                } else {
                    tr.fail(path + ".contract_id", "cần số nguyên");
                }
            }
            t.contract_no = tr.text(item, path, "contract_no", false);
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
            // 1 chính / 2 kiêm nhiệm / 3 hỗ trợ. Không dùng khi xếp tuyến, nên nới lỏng thì giữ nguyên số lạ (VD 0).
            if (item.contains("staff_role")) {
                const json& value = item["staff_role"];
                if (value.is_number_integer() && value.get<int>() >= 1 && value.get<int>() <= 3) {
                    t.staff_role = value.get<int>();
                } else {
                    if (value.is_number_integer()) t.staff_role = value.get<int>();
                    tr.tolerate("STAFF_ROLE", path + ".staff_role", "cần 1, 2 hoặc 3 (đang là " + value.dump() + ")");
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
                if (const TaskKind* kind = find_kind(key, t.task_type_name)) {
                    if (t.task_type_id != kind->type_id)
                        tr.tolerate("CATALOG_MISMATCH", path + ".task_type_id", "danh mục ghi " + std::to_string(kind->type_id));
                    if (sla && (t.sla_minutes != kind->sla_minutes || t.priority_in_day != kind->priority))
                        tr.tolerate("CATALOG_MISMATCH", path + ".sla",
                                    "khác danh mục sheet 05 (" + key + "/" + t.task_type_name + ": input " +
                                        (t.sla_minutes ? std::to_string(*t.sla_minutes) : "null") + " phút/P" +
                                        std::to_string(t.priority_in_day) + ", danh mục " +
                                        (kind->sla_minutes ? std::to_string(*kind->sla_minutes) : "null") + " phút/P" +
                                        std::to_string(kind->priority) + ")");
                } else {
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
