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
    const TaskKind* created = find_kind("cscd", "ngung_ket_noi_4h");    // DoneSameCreatedDay (7.12: chuyển từ onsite)
    const TaskKind* month = find_kind("thu_hoi", "thu_hoi_thiet_bi");   // DoneWithinMonth
    CHECK(checkin && created && month);

    const Minutes planned = *parse_datetime("2026-09-10 09:00:00");

    {  // Hẹn + SLA: due = A + sla, không có complete_by.
        Task t;
        t.appointment = parse_datetime("2026-09-10 14:00:00");
        t.timezone = "14:00 - 16:00";
        t.sla_minutes = 120;
        Deadlines d = resolve_deadlines(t, *checkin, planned);
        CHECK(when(d.opens) == "2026-09-10 14:00:00");
        CHECK(when(d.due) == "2026-09-10 16:00:00");
        CHECK(!d.complete_by);
    }
    {  // Hẹn nhưng loại "trong ngày/tháng": complete_by = cuối ngày hẹn.
        Task t;
        t.appointment = parse_datetime("2026-09-10 14:00:00");
        t.timezone = "14:00 - 16:00";
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
        t.timezone = "14:00 - 15:00";
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
                    if (has_appt) t.appointment = parse_datetime("2026-06-03 14:00:00"), t.timezone = "14:00 - 16:00";
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

    {  // Mục E (7.17b): hoa_don thanh toán đúng tháng trước, cùng ngày-trong-tháng ngày chạy → hạn cuối hôm nay.
        const TaskKind* bill = find_kind("hoa_don", "tra_sau");
        CHECK(bill);
        const auto deadline = [&](const TaskKind& kind, const char* paid, const char* run, bool appointment = false) {
            Task t;
            t.create_date = parse_datetime("2026-10-01 08:00:00");
            t.complete_date = parse_datetime(paid);
            if (appointment) t.appointment = parse_datetime(std::string(run).substr(0, 10) + " 14:00:00"), t.timezone = "14:00 - 16:00";
            return when(resolve_deadlines(t, kind, *parse_datetime(run)).complete_by);
        };
        CHECK(deadline(*bill, "2026-09-12 10:00:00", "2026-10-12 09:00:00") == "2026-10-12 23:59:00");  // trùng 12
        CHECK(deadline(*bill, "2026-09-25 10:00:00", "2026-10-12 09:00:00") == "2026-10-31 23:59:00");  // lệch → cuối tháng
        CHECK(deadline(*bill, "2026-08-12 10:00:00", "2026-10-12 09:00:00") == "2026-10-31 23:59:00");  // không phải tháng trước
        CHECK(deadline(*bill, "2026-10-12 10:00:00", "2026-10-12 09:00:00") == "2026-10-31 23:59:00");  // cùng tháng → không áp
        CHECK(deadline(*bill, "2026-12-05 10:00:00", "2027-01-05 09:00:00") == "2027-01-05 23:59:00");  // qua năm
        CHECK(deadline(*bill, "2026-10-31 10:00:00", "2026-11-30 09:00:00") == "2026-11-30 23:59:00");  // 31 → cuối tháng 30 ngày
        CHECK(deadline(*bill, "2026-10-31 10:00:00", "2026-11-29 09:00:00") != "2026-11-29 23:59:00");  // 29 ≠ 30: không đến hạn hôm nay
        CHECK(deadline(*bill, "2026-09-12 10:00:00", "2026-10-12 09:00:00", true) == "2026-10-12 23:59:00");  // có hẹn: theo hẹn
        CHECK(deadline(*month, "2026-09-12 10:00:00", "2026-10-12 09:00:00") == "2026-10-31 23:59:00");  // thu_hoi: không áp
        Task no_paid;  // hoa_don không complete_date → cuối tháng như cũ
        no_paid.create_date = parse_datetime("2026-10-01 08:00:00");
        CHECK(when(resolve_deadlines(no_paid, *bill, *parse_datetime("2026-10-12 09:00:00")).complete_by) == "2026-10-31 23:59:00");
    }
    {  // 8.14: A = đầu khung timezone vào ngày của appointment (OA gửi appointment = cuối khung). 8.16: B = cuối khung.
        const TaskKind* logic = find_kind("bao_tri", "bao_tri_logic");
        const TaskKind* net = find_kind("trien_khai", "trien_khai_net");
        Task t;
        t.appointment = parse_datetime("2026-10-23 15:30:00");
        t.timezone = "13:30 - 15:30";
        t.sla_minutes = 60;
        Deadlines d = resolve_deadlines(t, *logic, *parse_datetime("2026-10-23 08:00:00"));
        CHECK(when(d.opens) == "2026-10-23 13:30:00" && when(d.due) == "2026-10-23 15:30:00");  // bảo trì SLA 60: hạn vẫn cuối khung
        t.sla_minutes = 120;
        d = resolve_deadlines(t, *net, *parse_datetime("2026-10-23 08:00:00"));
        CHECK(when(d.due) == "2026-10-23 15:30:00");  // triển khai: hạn = cuối khung
        d = resolve_deadlines(t, *find_kind("trien_khai", "giao_thiet_bi_cam"), *parse_datetime("2026-10-23 08:00:00"));
        CHECK(!d.due && when(d.complete_by) == "2026-10-23 23:59:00");  // 8.17: Cam chỉ hạn cuối ngày hẹn
        t.timezone = "13:30-15:30";
        CHECK(when(appointment_start(t)) == "2026-10-23 13:30:00");
        for (const char* bad : {"", "+07:00", "13h30 - 15h30", "25:00 - 26:00"}) {  // thiếu / sai → appointment − 2 giờ (a)
            t.timezone = bad;
            CHECK(when(appointment_start(t)) == "2026-10-23 13:30:00");
            CHECK(when(appointment_end(t)) == "2026-10-23 15:30:00");  // B = appointment
        }
        t.appointment = parse_datetime("2026-10-23 17:30:00");
        t.timezone = "08:00 - 10:00";  // khung không chứa giờ appointment: vẫn tin khung, chỉ lấy NGÀY của appointment
        CHECK(when(appointment_start(t)) == "2026-10-23 08:00:00" && when(appointment_end(t)) == "2026-10-23 10:00:00");
        t.appointment.reset();
        CHECK(!appointment_start(t) && !appointment_end(t));
        CHECK(parse_window(" 15:30 - 17:30 ") == std::make_pair(930, 1050) && !parse_window("15:30 - 17:30x"));
    }
    {  // 8.13 bill_date (Mobix "Hẹn …"): cùng ngày trong tháng với lần thanh toán THÁNG TRƯỚC; không có → nullopt.
        const auto date = [](const char* paid, const char* run) {
            Task t;
            if (*paid) t.complete_date = parse_datetime(paid);
            return when(bill_date(t, *parse_datetime(run)));
        };
        CHECK(date("2026-09-24 15:28:18", "2026-10-23 16:10:00") == "2026-10-24 00:00:00");  // chưa tới ngày: vẫn có
        CHECK(date("2026-09-12 10:00:00", "2026-10-23 16:10:00") == "2026-10-12 00:00:00");  // đã qua ngày: vẫn có
        CHECK(date("2026-10-31 10:00:00", "2026-11-05 09:00:00") == "2026-11-30 00:00:00");  // 31 → cuối tháng 30 ngày
        CHECK(date("2027-01-31 10:00:00", "2027-02-01 09:00:00") == "2027-02-28 00:00:00");  // tháng 2
        CHECK(date("2026-12-05 10:00:00", "2027-01-20 09:00:00") == "2027-01-05 00:00:00");  // qua năm
        CHECK(date("2026-07-25 10:00:00", "2026-10-23 09:00:00") == "(none)");  // cũ hơn tháng trước → không có lịch sử
        CHECK(date("2026-10-02 10:00:00", "2026-10-23 09:00:00") == "(none)");  // cùng tháng → không phải tháng trước
        CHECK(date("", "2026-10-23 09:00:00") == "(none)");
    }

    {  // workdays_until (7.16.1): T2–T6, không tính ngày bắt đầu, tính ngày hạn; hạn hôm nay/đã qua → 0.
        const auto at = [](const char* text) { return *ktv::parse_datetime(text); };
        CHECK(ktv::workdays_until(at("2026-09-10 09:00:00"), at("2026-09-10 23:00:00")) == 0);  // cùng ngày (thứ Năm)
        CHECK(ktv::workdays_until(at("2026-09-10 09:00:00"), at("2026-09-11 23:00:00")) == 1);  // thứ Sáu
        CHECK(ktv::workdays_until(at("2026-09-10 09:00:00"), at("2026-09-13 23:00:00")) == 1);  // T7/CN không tính
        CHECK(ktv::workdays_until(at("2026-09-10 09:00:00"), at("2026-09-30 23:00:00")) == 14);  // 11 · 14–18 · 21–25 · 28–30
        CHECK(ktv::workdays_until(at("2026-09-10 09:00:00"), at("2026-09-01 23:00:00")) == 0);  // đã qua
    }

    if (failures) std::cerr << failures << " lỗi\n";
    else std::cout << "test_sla: OK\n";
    return failures != 0;
}
