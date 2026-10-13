import json, sys
from uat_lib import *

ONLY = set(sys.argv[1:])


def case(no, title):
    def wrap(fn):
        if not ONLY or str(no) in ONLY:
            print(f'\n=== Case {no} — {title}')
            fn()
    return wrap


D = '2026-10-13'  # thứ 3
h = lambda d, t: f'{d} {t}:00'


@case(1, 'Ba ca hẹn ở 3 khung khác nhau')
def _():
    run('c01', h(D, '07:30'), [T(1, 'Ca C (14-16)', 'net', (-3, 0), h(D, '14:00')), T(2, 'Ca A (08-10)', 'net', (0, 3), h(D, '08:00')),
                                T(3, 'Ca B (10-12)', 'net', (3, 0), h(D, '10:00'))])


@case(2, 'Ưu tiên cao nhưng hẹn muộn')
def _():
    run('c02', h(D, '07:30'), [T(1, 'Bảo trì VL ưt1 (16-18)', 'vatly', (3, 0), h(D, '16:00'), sla=120),
                                T(2, 'Triển khai ưt3 (08-10)', 'net', (0, 3), h(D, '08:00'))])


@case(3, 'Hai ca cùng khung — cùng loại')
def _():
    run('c03', h(D, '07:30'), [T(1, 'VL xa 9km', 'vatly', (9, 0), h(D, '08:00'), sla=120),
                                T(2, 'VL gần 2km', 'vatly', (0, 2), h(D, '08:00'), sla=120)])


@case(4, 'Hai ca cùng khung — khác ưu tiên')
def _():
    run('c04', h(D, '07:30'), [T(1, 'VL ưt1 xa 6km', 'vatly', (6, 0), h(D, '08:00'), sla=120),
                                T(2, 'Swap ưt3 gần 2km', 'swap', (0, 2), h(D, '08:00'), sla=120)])


@case(5, 'Chèn trước ca hẹn — vẫn kịp')
def _():
    run('c05', h(D, '08:15'), [T(1, 'Ca hẹn 08-10 (20′)', 'logic', (10, 0), h(D, '08:00'), sla=120),
                                T(2, 'Thu bill (5′, xử lý 15′)', 'bill', (0.3125, 2.48), handle=15)])


@case(6, 'Chèn trước ca hẹn — sẽ trễ')
def _():
    run('c06', h(D, '08:15'), [T(1, 'Ca hẹn 08-10 (20′)', 'logic', (10, 0), h(D, '08:00'), sla=120),
                                T(2, 'Thu bill xa 40′', 'bill', (-20, 0), handle=15)])


@case(7, 'Hai ca cùng nhà — gom điểm dừng')
def _():
    run('c07', h(D, '07:30'), [T(1, 'Thu bill @Nhà K', 'bill', (3, 2), addr='12 Lê Lợi, Bến Nghé'),
                                T(2, 'Thu hồi TB @Nhà K', 'thuhoi', (3, 2), addr='12 Lê Lợi, Bến Nghé'),
                                T(3, 'VL hẹn 09:00', 'vatly', (-3, 0), h(D, '09:00')),
                                T(4, 'Logic hẹn 10:30', 'logic', (4, 4), h(D, '10:30'))])


@case(8, 'Nhóm có ca hẹn — neo theo khung hẹn')
def _():
    run('c08', h(D, '07:30'), [T(1, 'Swap hẹn 08-10 @Nhà K', 'swap', (2, 2), h(D, '08:00'), sla=120, addr='12 Lê Lợi'),
                                T(2, 'Thu bill @Nhà K', 'bill', (2, 2), addr='12 Lê Lợi'),
                                T(3, 'Logic hẹn 11:00', 'logic', (-4, 0), h(D, '11:00'))])


@case(9, 'Cùng địa chỉ — không xét mốc 5 ngày (ngày 01/10)')
def _():
    d = '2026-10-01'
    run('c09', h(d, '07:30'), [T(1, 'Bảo trì hẹn 09:00 @Nhà K', 'logic', (3, 0), h(d, '09:00'), addr='12 Lê Lợi'),
                                T(2, 'Thu bill (~20 ngày LV) @Nhà K', 'bill', (3, 0), addr='12 Lê Lợi'),
                                T(3, 'VL hẹn 13:30', 'vatly', (-3, -3), h(d, '13:30'))])


@case(10, 'Cùng địa chỉ, 2 khung hẹn xa nhau')
def _():
    run('c10', h(D, '07:30'), [T(1, 'Ca X 08-10 @Nhà K', 'logic', (3, 0), h(D, '08:00'), sla=120, addr='12 Lê Lợi'),
                                T(2, 'Ca Y 16-18 @Nhà K', 'logic', (3, 0), h(D, '16:00'), sla=120, addr='12 Lê Lợi'),
                                T(3, 'VL hẹn 12:00', 'vatly', (-4, 0), h(D, '12:00'))])


@case(11, 'Hai địa chỉ cách ~36 m')
def _():
    run('c11', h(D, '07:30'), [T(1, 'Ca P @12 Lê Lợi', 'logic', (3, 0), h(D, '09:00'), sla=120),
                                T(2, 'Ca Q @14 Lê Lợi (36 m)', 'logic', (3.03, 0.02), h(D, '09:30'), sla=120),
                                T(3, 'VL hẹn 13:30', 'vatly', (-3, -3), h(D, '13:30'))])


ROUTE3 = lambda d: [T(1, 'Hẹn 1 (08:30)', 'logic', (5, 0), h(d, '08:30')), T(2, 'Hẹn 2 (10:30)', 'logic', (10, 0), h(d, '10:30')),
                    T(3, 'Hẹn 3 (13:30)', 'logic', (15, 0), h(d, '13:30'))]


@case(12, 'Ca chèn nằm giữa tuyến')
def _():
    run('c12', h(D, '07:30'), ROUTE3(D) + [T(9, 'Thu hồi giữa ca1-ca2', 'thuhoi', (7.5, 0.2))])


@case(13, 'Ca chèn ngược hướng, quá xa')
def _():
    run('c13', h(D, '07:30'), ROUTE3(D) + [T(9, 'Thu hồi ngược hướng 25km', 'thuhoi', (-25, 0))])


AFTERNOON = lambda d: [T(1, 'Hẹn 13:00', 'logic', (3, 0), h(d, '13:00')), T(2, 'Hẹn 14:30 (xong 15:30)', 'logic', (5, 0), h(d, '14:30'))]


@case(14, 'Chèn vào khoảng trống cuối ngày')
def _():
    run('c14', h(D, '12:00'), AFTERNOON(D) + [T(9, 'Thu bill (đi 15′, xử lý 15′)', 'bill', (12.5, 0), handle=15)], staff_xy=(1, 0))


@case(15, 'Rảnh nhưng ca quá xa (90′)')
def _():
    run('c15', h(D, '12:00'), AFTERNOON(D) + [T(9, 'Thu bill xa 90′', 'bill', (50, 0), handle=15)], staff_xy=(1, 0))


# Khoảng trống chỉ đủ 1 ca chèn: ca hẹn 08:00 xong 09:00, ca trực tới 09:40, mỗi ca chèn xử lý 25′ (~3′ đi).
def gap1(case_id, d, a, b):
    run(case_id, h(d, '07:30'), [T(1, 'Hẹn 08:00 (xong 09:00)', 'logic', (2, 0), h(d, '08:00'))] + [a, b], available='08:00-09:40')


D14 = '2026-10-14'  # còn 12 ngày LV tới 31/10


@case(16, 'Xét số ngày còn lại (Gsafe 0 ngày vs Thu hồi 12 ngày)')
def _():
    gap1('c16', D14, T(8, 'Gsafe (hạn trong ngày hẹn)', 'gsafe', (2.5, 0.5), h(D14, '08:00'), handle=25),
         T(9, 'Thu hồi (12 ngày LV)', 'thuhoi', (2.5, -0.5), handle=25))


@case(17, 'Hoà số ngày — xét ưu tiên (Cam ưt3 vs Thu bill trúng ngày ưt4)')
def _():
    gap1('c17', D14, T(8, 'Giao TB Cam (hạn trong ngày hẹn)', 'cam', (2.5, 0.5), h(D14, '08:00'), handle=25),
         T(9, 'Thu bill trúng ngày TT tháng trước', 'bill', (2.5, -0.5), handle=25, complete_date='2026-09-14 10:00:00'))


@case(18, 'Thu bill vs Thu hồi — xét thuận tuyến')
def _():
    gap1('c18', D14, T(8, 'Thu bill GẦN tuyến', 'bill', (2.5, 0.1), handle=25),
         T(9, 'Thu hồi XA tuyến hơn', 'thuhoi', (2.5, 1.2), handle=25))


@case(19, 'Hoà cả ba tiêu chí')
def _():
    gap1('c19', D14, T(8, 'Thu hồi #8', 'thuhoi', (2.5, 0.5), handle=25), T(9, 'Thu hồi #9', 'thuhoi', (2.5, -0.5), handle=25))


D12 = '2026-10-12'
AFT2 = lambda d: [T(1, 'Hẹn 13:00', 'logic', (2, 0), h(d, '13:00')), T(2, 'Hẹn 14:30 (xong 15:30)', 'logic', (4, 0), h(d, '14:30'))]


@case(20, 'Trúng ngày thanh toán tháng trước (12/09 → 12/10)')
def _():
    run('c20', h(D12, '07:30'), [T(1, 'Logic hẹn 09:00', 'logic', (5, 0), h(D12, '09:00')),
                                  T(9, 'Thu bill (TT 12/09)', 'bill', (3, 1), complete_date='2026-09-12 10:00:00')])


@case(21, 'Chưa tới ngày TT (25/09) nhưng KTV rảnh 2h cuối ca')
def _():
    run('c21', h(D12, '12:00'), AFT2(D12) + [T(9, 'Thu bill (TT 25/09) gần tuyến', 'bill', (5, 0.5), complete_date='2026-09-25 10:00:00')],
        staff_xy=(1, 0))


@case(22, 'Chưa tới ngày TT, KTV kín lịch, tranh với Giao TB Cam')
def _():
    gap1('c22', D12, T(8, 'Giao TB Cam (hạn trong ngày)', 'cam', (2.5, 0.5), h(D12, '08:00'), handle=25),
         T(9, 'Thu bill (TT 25/09)', 'bill', (2.5, -0.5), handle=25, complete_date='2026-09-25 10:00:00'))


@case(23, 'Khách không có lịch sử thanh toán, KTV có khoảng trống')
def _():
    run('c23', h(D12, '12:00'), AFT2(D12) + [T(9, 'Thu bill (không lịch sử)', 'bill', (5, 0.5))], staff_xy=(1, 0))


@case(24, 'Còn nhiều ngày — nhường chỗ (09/10, Thu hồi 15 ngày vs Gsafe)')
def _():
    d = '2026-10-09'
    gap1('c24', d, T(8, 'Gsafe (hạn trong ngày hẹn)', 'gsafe', (2.5, 0.5), h(d, '08:00'), handle=25),
         T(9, 'Thu hồi (15 ngày LV)', 'thuhoi', (2.5, -0.5), handle=25))


@case(25, 'Còn ít ngày — được tranh (27/10, Thu hồi 3 ngày thuận tuyến hơn vs Thu bill 3 ngày xa hơn)')
def _():
    d = '2026-10-27'
    gap1('c25', d, T(8, 'Thu bill (3 ngày LV) xa tuyến hơn', 'bill', (2.5, 1.2), handle=25),
         T(9, 'Thu hồi (3 ngày LV) thuận tuyến', 'thuhoi', (2.5, 0.1), handle=25))


@case(26, 'Khoảng trống thừa thật — bỏ mốc 5 ngày (02/10)')
def _():
    d = '2026-10-02'
    run('c26', h(d, '12:00'), AFT2(d) + [T(9, 'Thu hồi (~20 ngày LV) gần tuyến', 'thuhoi', (5, 0.5))], staff_xy=(1, 0))


def m8(done_c0=False):
    d1 = '2026-10-14'
    cur = T(10, 'C0 đã check-in', 'vatly', (0, 0), h(D, '08:00'), status=(1, 'Đã xử lý hoàn tất') if done_c0 else (10, 'check_in'))
    t1 = T(11, 'T1 VL hẹn 10:00', 'vatly', (3, 0), h(D, '10:00'), status=(10, 'check_in') if done_c0 else None)
    return [cur, t1, T(12, 'T2 Logic hẹn 11:30', 'logic', (5, 2), h(D, '11:30')), T(13, 'T3 Swap hẹn 13:30', 'swap', (-2, 4), h(D, '13:30')),
            T(14, 'T4 Net hẹn 14:30', 'net', (-4, 0), h(D, '14:30')),
            T(15, 'T5 Thu bill trúng ngày TT', 'bill', (2, -2), complete_date='2026-09-13 10:00:00'),
            T(16, 'N1 Logic hẹn MAI 09:00', 'logic', (1, 1), h(d1, '09:00')), T(17, 'N2 Net hẹn MAI 10:00', 'net', (-1, 1), h(d1, '10:00'))]


@case(27, 'Hiển thị đủ ca, chỉ ca hôm nay có số thứ tự')
def _():
    out, rows, names = run('c27', h(D, '09:00'), m8(), current={'task_id': 10, 'task_status_id': 10, 'task_type_id': 1})
    seqd = [r for r in rows if r['entry_type'] == 'TASK' and r['seq'] > 0]
    cur = [r for r in rows if r['entry_type'] == 'TASK' and r['seq'] == 0]
    defer = [r for r in rows if r['entry_type'] == 'DEFERRED']
    print(f'   → hiển thị {len(seqd) + len(cur) + len(defer)} ca (có STT {len(seqd)}, đang làm seq 0: {len(cur)}, '
          f'DEFERRED {len(defer)}); input 8 ca')


@case(28, 'Chỉ số tuyến không cộng ca không tính tuyến')
def _():
    print('   (dùng chung output case 27 — xem dòng metrics ở case 27)')


@case(29, 'Sau khi check-in (C0 xong, check-in T1)')
def _():
    run('c29', h(D, '10:05'), m8(True), staff_xy=(3, 0), current={'task_id': 11, 'task_status_id': 10, 'task_type_id': 1})


# M9: ngày dày — 3 ca hẹn phía đông, 3 ca hẹn phía tây xen kẽ khung, thêm ca chính không hẹn; tranh SLA vs km.
def M9():
    return [T(1, 'Đ1 VL 08:00', 'vatly', (6, 0), h(D, '08:00')), T(2, 'T1 Logic 08:30', 'logic', (-6, 0), h(D, '08:30')),
            T(3, 'Đ2 Swap 09:30', 'swap', (7, 1), h(D, '09:30')), T(4, 'T2 Logic 10:00', 'logic', (-7, 1), h(D, '10:00')),
            T(5, 'Đ3 VL 13:30', 'vatly', (8, -1), h(D, '13:30')), T(6, 'T3 Swap 14:00', 'swap', (-8, -1), h(D, '14:00')),
            T(7, 'Thu bill trúng ngày (Đ)', 'bill', (6.5, 0.5), complete_date='2026-09-13 10:00:00'),
            T(8, 'Thu bill trúng ngày (T)', 'bill', (-6.5, 0.5), complete_date='2026-09-13 10:00:00')]


@case(30, 'Chế độ Quãng đường (so với Mặc định)')
def _():
    for m in (0, 2):
        run(f'c30_mode{m}', h(D, '07:30'), M9(), mode=m)


@case(31, 'Chế độ SLA (so với Mặc định)')
def _():
    for m in (0, 1):
        run(f'c31_mode{m}', h(D, '07:30'), M9(), mode=m)


def BASE():
    return [T(1, 'VL hẹn 09:00', 'vatly', (3, 0), h(D, '09:00')), T(2, 'Logic hẹn 10:30', 'logic', (6, 2), h(D, '10:30')),
            T(3, 'Net hẹn 13:30', 'net', (2, 5), h(D, '13:30')), T(4, 'Thu bill trúng ngày', 'bill', (4, 1), complete_date='2026-09-13 10:00:00')]


def base():
    run('c3x_base', h(D, '07:30'), BASE())


@case(32, 'Có ca mới được giao')
def _():
    base(); run('c32', h(D, '07:45'), BASE() + [T(5, 'MỚI Logic hẹn 11:30', 'logic', (5, 4), h(D, '11:30'))])


@case(33, 'Hoàn tất một ca (VL 09:00 check-out lúc 10:05)')
def _():
    t = BASE(); t[0] = T(1, 'VL hẹn 09:00', 'vatly', (3, 0), h(D, '09:00'), status=(1, 'Đã xử lý hoàn tất'))
    base(); run('c33', h(D, '10:05'), t, staff_xy=(3, 0))


@case(34, 'Hẹn lại sang khung muộn hơn (Net 13:30 → 15:30)')
def _():
    t = BASE(); t[2] = T(3, 'Net hẹn 15:30 (đổi)', 'net', (2, 5), h(D, '15:30'))
    base(); run('c34', h(D, '07:45'), t)


@case(35, 'Hủy ca đầu tuyến')
def _():
    t = BASE(); t[0] = T(1, 'VL hẹn 09:00', 'vatly', (3, 0), h(D, '09:00'), status=(97, 'Đã hủy'))
    base(); run('c35', h(D, '07:45'), t)


@case(36, 'Điều chuyển ca sang KTV khác')
def _():
    base()
    run('c36_A', h(D, '07:45'), [t for t in BASE() if t['task_id'] != 2])
    run('c36_B', h(D, '07:45'), [T(21, 'B: VL hẹn 09:30', 'vatly', (7, 3), h(D, '09:30')), T(2, 'Logic hẹn 10:30 (từ A)', 'logic', (6, 2), h(D, '10:30'))],
        staff='00209999', staff_xy=(8, 4))


@case(37, 'Khoá ca đầu tuyến khi đang trên đường')
def _():
    t = BASE(); t[0] = T(1, 'VL hẹn 09:00 (đang di chuyển)', 'vatly', (3, 0), h(D, '09:00'), status=(7, 'Đang di chuyển'))
    base(); run('c37', h(D, '08:20'), t + [T(5, 'MỚI Logic hẹn 08:30 gần hơn', 'logic', (1.2, 0.4), h(D, '08:30'))], staff_xy=(1, 0))


@case(38, 'Thay đổi nhỏ không làm xáo trộn tuyến (thêm ca rất xa)')
def _():
    base(); run('c38', h(D, '07:45'), BASE() + [T(5, 'MỚI Thu hồi rất xa', 'thuhoi', (-30, 0))])


@case(39, 'Có GPS (KTV ở (2,3) km)')
def _():
    run('c39', h(D, '07:30'), BASE(), staff_xy=(2, 3))


@case(40, 'Không có GPS đầu ngày')
def _():
    run('c40_rong', h(D, '06:00'), BASE(), latlng='')
    run('c40_bo_field', h(D, '06:00'), BASE(), latlng='DROP')


def OVER():
    t = [T(i, f'Logic hẹn {hh}', 'logic', (x, y), h(D, hh)) for i, (hh, x, y) in
         enumerate([('09:00', 3, 0), ('10:00', 5, 1), ('11:00', 6, 3), ('13:30', 4, 5), ('14:30', 1, 6), ('15:30', -2, 4)], 1)]
    t += [T(10 + i, f'Thu bill {i} trúng ngày', 'bill', (x, y), handle=30, complete_date='2026-09-13 10:00:00') for i, (x, y) in
          enumerate([(-3, 1), (-4, -2), (0, -4), (3, -3), (-5, 3)], 1)]
    t += [T(20, 'VL hẹn 16:30', 'vatly', (-1, 2), h(D, '16:30'))]
    return t


@case(41, 'Quá tải trong ngày')
def _():
    run('c41', h(D, '07:30'), OVER())


@case(42, 'Sang ngày hôm sau (14/10, ca hôm trước chưa làm)')
def _():
    run('c42', h('2026-10-14', '07:30'), OVER())
