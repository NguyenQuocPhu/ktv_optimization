#include "ktv/sla.hpp"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <string>

namespace ktv {

namespace {

Minutes end_of_month(Minutes t) {
    std::string text = format_datetime(t);  // "YYYY-MM-DD ..."
    int year = std::stoi(text.substr(0, 4)), month = std::stoi(text.substr(5, 2));
    char next[32];
    std::snprintf(next, sizeof next, "%04d-%02d-01 00:00:00", month == 12 ? year + 1 : year, month % 12 + 1);
    return *parse_datetime(next) - 1;
}

int day_of_month(Minutes t) { return std::stoi(format_datetime(t).substr(8, 2)); }
int month_index(Minutes t) {  // năm × 12 + tháng: tháng liền trước = index − 1 (qua năm cũng đúng)
    const std::string text = format_datetime(t);
    return std::stoi(text.substr(0, 4)) * 12 + std::stoi(text.substr(5, 2));
}

}  // namespace

std::optional<Minutes> appointment_start(const Task& task) {
    if (!task.appointment) return std::nullopt;
    if (const auto window = parse_window(task.timezone)) return start_of_day(*task.appointment) + window->first;
    return *task.appointment - kDefaultWindowMinutes;
}

std::optional<Minutes> appointment_end(const Task& task) {
    if (!task.appointment) return std::nullopt;
    if (const auto window = parse_window(task.timezone)) return start_of_day(*task.appointment) + window->second;
    return task.appointment;
}

std::optional<Minutes> bill_date(const Task& task, Minutes planned_at) {
    if (!task.complete_date || month_index(*task.complete_date) + 1 != month_index(planned_at)) return std::nullopt;
    const int day = std::min(day_of_month(*task.complete_date), day_of_month(end_of_month(planned_at)));
    return start_of_day(planned_at) + static_cast<Minutes>(day - day_of_month(planned_at)) * 1440;
}

Deadlines resolve_deadlines(const Task& task, const TaskKind& kind, Minutes planned_at) {
    Deadlines d;
    if (task.appointment) {
        d.opens = appointment_start(task);  // 8.14: A = đầu khung timezone
        if (task.sla_minutes) d.due = appointment_end(task);  // 8.16: check-in ≤ B = cuối khung timezone
        // Xong trong ngày hẹn: không có SLA, hoặc loại "hoàn tất trong ngày hẹn" (Giao TB Cam) dù input có SLA (8.5b).
        if (!task.sla_minutes || kind.on_time == OnTime::DoneSameAppointmentDay)
            d.complete_by = start_of_day(*task.appointment) + 1439;
        return d;
    }
    // Không hẹn: "trong ngày tạo phiếu / trong tháng" lấy theo create_date nếu có, không thì planned_at.
    const Minutes base = task.create_date.value_or(planned_at);
    if (kind.on_time == OnTime::DoneSameCreatedDay || kind.on_time == OnTime::DoneSameAppointmentDay)
        d.complete_by = start_of_day(base) + 1439;
    else if (kind.on_time == OnTime::DoneWithinMonth && std::string(kind.group) == "hoa_don" &&
             bill_date(task, planned_at) == start_of_day(planned_at))
        // Mục E (7.17b): KH thanh toán tháng trước đúng ngày này → còn 0 ngày → chèn ngay trong ngày.
        d.complete_by = start_of_day(planned_at) + 1439;
    else if (kind.on_time == OnTime::DoneWithinMonth)
        d.complete_by = end_of_month(base);
    return d;  // Loại "check-in trước B" mà không hẹn: chưa có B, không tính trễ.
}

const char* projected_sla(double checkin, double done, double due, double complete_by, double service, double now,
                          const Rules& rules) {
    double deadline = !std::isnan(due) ? due : complete_by;
    if (std::isnan(deadline)) return "ON_TIME";
    double at = !std::isnan(due) ? checkin : done;
    if (deadline < now) return "ALREADY_BREACHED";
    if (at > deadline) return "WILL_BREACH";
    double spare = deadline - at;
    if (spare < rules.at_risk_minutes || spare < rules.at_risk_ratio * service) return "AT_RISK";
    return "ON_TIME";
}

int workdays_until(Minutes from, Minutes deadline) {
    const long long from_day = start_of_day(from) / 1440;
    const long long deadline_day = start_of_day(deadline) / 1440;
    if (deadline_day <= from_day) return 0;  // hạn trong ngày hôm nay hoặc đã qua
    int count = 0;
    for (long long day = from_day + 1; day <= deadline_day; ++day) {
        const int weekday = static_cast<int>((day + 4) % 7);  // 0 = CN; 1970-01-01 là thứ Năm
        if (weekday >= 1 && weekday <= 5) ++count;
    }
    return count;
}

}  // namespace ktv
