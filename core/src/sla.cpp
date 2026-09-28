#include "ktv/sla.hpp"

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

}  // namespace ktv
