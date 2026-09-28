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

    if (failures) std::cerr << failures << " lỗi\n";
    else std::cout << "test_sla: OK\n";
    return failures != 0;
}
