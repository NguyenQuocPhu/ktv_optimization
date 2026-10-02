// SLA: hạn theo loại việc + giờ hẹn + create_date, và dự báo projected_sla.
#include <cmath>
#include <iostream>
#include <string>

#include "ktv/sla.hpp"

static int failures = 0;
#define CHECK(cond)                                                           \
    do {                                                                      \
        if (!(cond)) {                                                        \
            std::cerr << __FILE__ << ":" << __LINE__ << ": FAIL " #cond "\n"; \
            ++failures;                                                       \
        }                                                                     \
    } while (0)

using namespace ktv;

static std::string when(const std::optional<Minutes>& t) { return t ? format_datetime(*t) : "(none)"; }

int main() {
    const TaskKind* checkin = find_kind("bao_tri", "bao_tri_vat_ly");   // CheckinBeforeB
    const TaskKind* created = find_kind("onsite", "ngung_ket_noi_4h");  // DoneSameCreatedDay
    const TaskKind* month = find_kind("thu_hoi", "thu_hoi_thiet_bi");   // DoneWithinMonth
    CHECK(checkin && created && month);

    const Minutes planned = *parse_datetime("2026-09-10 09:00:00");

    {  // Hẹn + SLA: due = A + sla, không có complete_by.
        Task t;
        t.appointment = parse_datetime("2026-09-10 14:00:00");
        t.sla_minutes = 120;
        Deadlines d = resolve_deadlines(t, *checkin, planned);
        CHECK(when(d.opens) == "2026-09-10 14:00:00");
        CHECK(when(d.due) == "2026-09-10 16:00:00");
        CHECK(!d.complete_by);
    }
    {  // Hẹn nhưng loại "trong ngày/tháng": complete_by = cuối ngày hẹn.
        Task t;
        t.appointment = parse_datetime("2026-09-10 14:00:00");
        Deadlines d = resolve_deadlines(t, *month, planned);
        CHECK(when(d.opens) == "2026-09-10 14:00:00" && !d.due);
        CHECK(when(d.complete_by) == "2026-09-10 23:59:00");
    }
    {  // Không hẹn, trong ngày tạo phiếu: lấy theo create_date.
        Task t;
        t.create_date = parse_datetime("2026-06-03 08:30:00");
        Deadlines d = resolve_deadlines(t, *created, planned);
        CHECK(when(d.complete_by) == "2026-06-03 23:59:00");
    }
    {  // Không create_date: fallback planned_at.
        Task t;
        Deadlines d = resolve_deadlines(t, *created, planned);
        CHECK(when(d.complete_by) == "2026-09-10 23:59:00");
    }
    {  // Trong tháng: cuối tháng của create_date.
        Task t;
        t.create_date = parse_datetime("2026-06-03 08:30:00");
        Deadlines d = resolve_deadlines(t, *month, planned);
        CHECK(when(d.complete_by) == "2026-06-30 23:59:00");
    }
    {  // 7.15: onsite/phieu_onsite "rule như bao_tri": hẹn + SLA 60 → check-in trước hẹn + 60; không hẹn → không hạn.
        const TaskKind* onsite = find_kind("onsite", "phieu_onsite");
        CHECK(onsite && onsite->on_time == OnTime::CheckinBeforeB && onsite->sla_minutes == 60 && onsite->priority == 2);
        Task t;
        t.appointment = parse_datetime("2026-09-10 14:00:00");
        t.sla_minutes = 60;
        Deadlines d = resolve_deadlines(t, *onsite, planned);
        CHECK(when(d.due) == "2026-09-10 15:00:00" && !d.complete_by);
        Task no_appointment;
        no_appointment.create_date = parse_datetime("2026-06-03 08:30:00");
        d = resolve_deadlines(no_appointment, *onsite, planned);
        CHECK(!d.opens && !d.due && !d.complete_by);  // trước 7.15: hoàn tất cuối tháng 2026-06-30
    }
    {  // Check-in trước B nhưng không hẹn: chưa có mốc nào.
        Task t;
        Deadlines d = resolve_deadlines(t, *checkin, planned);
        CHECK(!d.opens && !d.due && !d.complete_by);
    }

    {  // projected_sla: dùng due nếu có, không thì complete_by; ALREADY khi hạn đã qua lúc lập tuyến.
        const Rules rules = default_rules();  // at_risk 20 phút, 20% service
        const double none = NAN;
        CHECK(std::string(projected_sla(120, 180, 100, none, 60, 0, rules)) == "WILL_BREACH");
        CHECK(std::string(projected_sla(90, 150, 100, none, 60, 0, rules)) == "AT_RISK");
        CHECK(std::string(projected_sla(10, 70, 100, none, 60, 0, rules)) == "ON_TIME");
        CHECK(std::string(projected_sla(10, 70, 100, none, 60, 200, rules)) == "ALREADY_BREACHED");
        CHECK(std::string(projected_sla(120, 180, none, 100, 60, 0, rules)) == "WILL_BREACH");  // theo done
        CHECK(std::string(projected_sla(10, 70, none, none, 60, 0, rules)) == "ON_TIME");
    }

    {  // Biên projected_sla: dư đúng 20 phút vẫn ON_TIME; 19 phút thì AT_RISK; hạn == now chưa quá hạn.
        const Rules rules = default_rules();
        const double none = NAN;
        CHECK(std::string(projected_sla(80, 140, 100, none, 10, 0, rules)) == "ON_TIME");   // dư 20
        CHECK(std::string(projected_sla(81, 141, 100, none, 10, 0, rules)) == "AT_RISK");  // dư 19
        CHECK(std::string(projected_sla(100, 160, 100, none, 10, 0, rules)) == "AT_RISK"); // check-in == hạn
        CHECK(std::string(projected_sla(10, 70, 100, none, 60, 100, rules)) == "ON_TIME"); // hạn == now
        CHECK(std::string(projected_sla(10, 70, 99, none, 60, 100, rules)) == "ALREADY_BREACHED");
        // Ngưỡng theo tỷ lệ service: dư 35 < 20% của 200 = 40.
        CHECK(std::string(projected_sla(215, 275, 250, none, 200, 0, rules)) == "AT_RISK");
        CHECK(std::string(projected_sla(205, 265, 250, none, 200, 0, rules)) == "ON_TIME");  // dư 45
        // complete_by dùng done: done == hạn chưa trễ hoàn tất, nhưng dư 0 → AT_RISK.
        CHECK(std::string(projected_sla(10, 100, none, 100, 60, 0, rules)) == "AT_RISK");
    }

    {  // Ma trận resolve_deadlines: 3 loại đúng hẹn × có/không hẹn × có/không create_date.
        const Minutes planned = *parse_datetime("2026-09-10 09:00:00");
        const TaskKind* kinds[] = {checkin, created, month};
        for (const TaskKind* kind : kinds) {
            for (int has_appt = 0; has_appt < 2; ++has_appt) {
                for (int has_created = 0; has_created < 2; ++has_created) {
                    Task t;
                    t.sla_minutes = 60;
                    if (has_appt) t.appointment = parse_datetime("2026-06-03 14:00:00");
                    if (has_created) t.create_date = parse_datetime("2026-06-03 08:00:00");
                    Deadlines d = resolve_deadlines(t, *kind, planned);
                    if (has_appt) {
                        // Có hẹn + sla_minutes: due = hẹn + sla, không dùng complete_by (bất kể loại đúng hẹn).
                        CHECK(d.opens && format_datetime(*d.opens) == "2026-06-03 14:00:00");
                        CHECK(d.due && !d.complete_by);
                    } else if (kind == checkin) {
                        CHECK(!d.opens && !d.due && !d.complete_by);
                    } else if (kind == created) {
                        const std::string day = has_created ? "2026-06-03" : "2026-09-10";
                        CHECK(d.complete_by && format_datetime(*d.complete_by).substr(0, 10) == day);
                    } else {
                        const std::string end = has_created ? "2026-06-30" : "2026-09-30";
                        CHECK(d.complete_by && format_datetime(*d.complete_by).substr(0, 10) == end);
                    }
                }
            }
        }
    }

    if (failures) std::cerr << failures << " lỗi\n";
    else std::cout << "test_sla: OK\n";
    return failures != 0;
}
