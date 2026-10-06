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

// Mục E (thu bill): ngày-trong-tháng KH thanh toán kỳ trước trùng ngày chạy → đến hạn hôm nay. Chỉ so ngày, không kiểm
// tháng (OA chọn lần thanh toán, gửi lần gần nhất — [GIẢ ĐỊNH]); ngày 31 ở tháng 30 ngày → quy về ngày cuối tháng này.
bool paid_day_today(Minutes paid, Minutes now) {
    return std::min(day_of_month(paid), day_of_month(end_of_month(now))) == day_of_month(now);
}

}  // namespace

Deadlines resolve_deadlines(const Task& task, const TaskKind& kind, Minutes planned_at) {
    Deadlines d;
    if (task.appointment) {
        d.opens = task.appointment;
        if (task.sla_minutes) d.due = *task.appointment + *task.sla_minutes;  // Check-in ≤ B = A + SLA.
        else d.complete_by = start_of_day(*task.appointment) + 1439;          // Xong trong ngày hẹn.
        return d;
    }
    // Không hẹn: "trong ngày tạo phiếu / trong tháng" lấy theo create_date nếu có, không thì planned_at.
    const Minutes base = task.create_date.value_or(planned_at);
    if (kind.on_time == OnTime::DoneSameCreatedDay || kind.on_time == OnTime::DoneSameAppointmentDay)
        d.complete_by = start_of_day(base) + 1439;
    else if (kind.on_time == OnTime::DoneWithinMonth && std::string(kind.group) == "hoa_don" && task.complete_date &&
             paid_day_today(*task.complete_date, planned_at))
        d.complete_by = start_of_day(planned_at) + 1439;  // Mục E: còn 0 ngày → chèn ngay trong ngày.
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
