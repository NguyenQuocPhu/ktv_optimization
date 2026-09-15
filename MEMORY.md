# KTV Routing — Working Memory

> Đọc file này trước khi tiếp tục project. Giữ file ngắn. Cập nhật khi có dữ
> liệu, giả định, quyết định hoặc kết quả kiểm chứng mới, đồng thời xóa phần đã
> lỗi thời.

## Phạm vi (chốt 2026-09-11)

Team chỉ làm phần routing ở giữa: nhận job đã gán sẵn cho KTV, trả về thứ tự
làm cùng ETA. Frontend, database và hệ thống checklist (gán việc, vòng đời
status, SLA) thuộc các team khác. Không xây lại phần của họ. Thứ gì chỉ cần để
chạy thử thì để trong `simulator/`, và lõi không được import nó.

## Cấu trúc

- `src/ktv_routing/` là phần deploy, chỉ dùng thư viện chuẩn:
  `contract.py`, `planner.py`, `travel.py` (km/phút: OSRM hoặc chim bay),
  `service.py`, `__main__.py`.
- `simulator/ktv_simulator/` đóng vai hệ thống nguồn + team data. Từ 2026-09-12
  dữ liệu ở dạng luồng sự kiện realtime (người dùng chọn, thay hẳn provider đọc
  CSV bằng pandas): `events.py` (export → JSONL, pandas, chạy một lần),
  `provider.py` (`EventWorkloadProvider`, consumer thư viện chuẩn),
  `geocoding.py`, `convert_xlsx.py`, `fake_boundary.py`, `__main__.py` (`--at`,
  `--replay`, `--serve`). Web demo `web.py` + `web.html`: form lọc →
  `POST /api/plan`; nút ▶ Chạy → `POST /api/replay` mỗi giây, ghép tuyến của KTV
  vừa xếp lại vào bảng; bản đồ Leaflet, danh sách KTV, bảng điểm dừng, cảnh báo,
  JSON. Chỉ là đồ nghề, không deploy.
- `research/` là phân tích offline: `time_model.py` (học thời gian → JSON cho
  routing), `backtest_routing.py` (planner so với KTV thật), cùng bộ cũ
  `qos_data.py`, `features.py`, `baselines.py`, `build_dataset.py`,
  `evaluate_baselines.py`, `analyze_emp_coordinate.py`, `data_visualize.ipynb`.
  Research import `ktv_routing` và `ktv_simulator`, không chiều ngược lại.
- Chạy test: `.venv/bin/python -m unittest discover -s tests -v`. Test không cần
  mạng: OSRM được giả lập bằng HTTP server local trong `tests/test_routing.py`.
  `tests/test_research.py` cần pandas.
  Research đã chạy thử trên dữ liệu tổng hợp: build dataset → evaluate đều PASS.

## Quyết định

**Hợp đồng**

- Luồng: `WorkloadQuery(planned_at, filter)` → team data → `RouteRequest` →
  `plan_routes` → `RouteResponse`.
- `JobFilter` gồm `case_types`, `branch_names`, `emp_accounts`.
  - Danh sách rỗng nghĩa là không lọc.
  - Các tiêu chí khác nhau kết hợp bằng AND; các giá trị trong một danh sách
    kết hợp bằng OR.
  - Team data phải trả lại đúng filter đã nhận; `RoutingService` kiểm tra điều
    này và báo lỗi nếu lệch.
- Job gửi sang chỉ có 2 state: `PENDING` và `IN_PROGRESS`. Việc map 8 status
  của công ty sang 2 state này là việc của team data; simulator đang giữ bản
  giả định.
- JSON có field lạ thì bị từ chối. Mọi datetime phải cùng kiểu (có hoặc không
  có múi giờ) với `planned_at`.
- `due_at` và `priority` do team checklist cung cấp. `service_minutes` do
  routing tự ước lượng: mặc định bảng V0 theo `CASE_TYPE`; có `--time-model`
  thì median riêng KTV và bảng khoảng chuyển job học từ lịch sử (2026-09-13).

**Lưu trạng thái**

- Routing không lưu trạng thái. Bản SQLite cũ (event, checklist, báo cáo cuối
  ngày) đã gỡ, sao lưu tại `.temp/backup_before_routing_core_2026-09-11.tar.gz`.
- Bản gán việc và web demo nằm trong commit `45535cc`.

**Thuật toán (QHĐ theo rule nghiệp vụ, 2026-09-14; thay cách chọn tham lam)**

- Điểm và giờ xuất phát, theo thứ tự ưu tiên:
  1. Job `IN_PROGRESS`, xuất phát lúc `started_at + service`.
  2. `last_location`; nếu cũ hơn 240 phút vẫn dùng nhưng kèm issue.
  3. `UNKNOWN`: tuyến không có ETA.
- Giờ xuất phát không sớm hơn `shift_start`.
- Nghiệp vụ lấy từ `data/Mô tả loại tác vụ- ưu tiên để Gợi ý công việc (1).xlsx`
  (bảng 12 loại tác vụ: mốc hẹn A→B, yêu cầu đúng hẹn, ưu tiên 1–4; mục tiêu;
  4 rule; thời điểm chạy; đầu ra; tiêu chí; kiến trúc). Toàn bộ nghiệp vụ, kể cả
  xử lý dữ liệu, ghi ở `docs/BUSINESS_RULES.md` kèm Q1–Q26 chờ người dùng xác nhận.
- Người dùng sửa: **trễ hẹn = check-in sau hạn**, không phải làm xong sau hạn.
  Hạn hoàn tất (`complete_by`) mới so với giờ xong.
- Rule ở `src/ktv_routing/rules.py`, tầng mặc định [giả định]: (1) LATE_CHECKIN
  có trọng số ưu tiên P1..P4 = 4,3,2,1 → (2) LATE_COMPLETION + AFTER_SHIFT → (3)
  KM 1, LATE_MINUTES 0,1, TRAVEL_MINUTES 0,05, AREA_REENTRY 2, PRIORITY_DELAY 0,5,
  FINISH 0,01. Sửa bằng `--print-rules` / `--rules`.
- QHĐ: trạng thái (mask job đã làm, job cuối); nhãn (giờ xong, tổng chi phí từng
  tầng, FINISH lấy từ giờ xong); bỏ nhãn thua ở mọi mặt (an toàn cả khi có chờ
  mốc hẹn). ≤ 9 job chính xác, > 9 tham lam theo khóa + 2-opt; ≤ 32 nhãn/trạng
  thái (vượt thì APPROXIMATE). Rule 4: `previous_sequence` → giữ thứ tự cũ, chèn
  job mới; `IF_BETTER` đổi khi tốt hơn ở tầng trên hoặc tầng cuối giảm ≥ 1,0.
- Đo (24 CPU, chim bay, 1 tiến trình): 1 KTV 5 job 0,33 ms; chi nhánh 100×4
  10,8 ms; toàn quốc 3.400×3 167 ms; một KTV 9 job p50 66 ms, 12 job chính xác
  2,6 s (nên để heuristic). HNI_04 thật 09:00: 103/105 KTV OPTIMAL, 2 KTV 12 job
  HEURISTIC, 289 ms gồm OSRM 84 ms.
- Km/phút lấy từ `TravelModel`, một lần cho mọi KTV:
  - `OsrmTravel` (mặc định ở CLI và web demo, chốt 2026-09-11): gọi OSRM
    `/table`, response có `leg_km`, `leg_minutes` mỗi điểm dừng và
    `travel_source` mỗi tuyến; ETA = xong điểm trước + `leg_minutes`.
  - `HaversineTravel`: chim bay 30 km/h; mặc định của `plan_routes` trong code
    và là dự phòng khi OSRM lỗi (issue `ROAD_DISTANCE_FALLBACK`).
- OSRM public `router.project-osrm.org` (đo 2026-09-11): từ chối từ 101 tọa độ
  mỗi request (`TooBig`), khoảng 0,6–1,7 giây mỗi request, chính sách khoảng
  1 request/giây, không cần proxy từ server. `OsrmTravel` loại tọa độ trùng.
  Với server public (`parallel_requests=1`): gom nhiều KTV vào một request
  ≤ 100 điểm và chờ 1 giây giữa các request. Với OSRM tự host (chốt 2026-09-12,
  vì routing là realtime nên ưu tiên thời gian một request): mỗi KTV một request,
  16 luồng song song, KTV lỗi thì chỉ KTV đó fallback.

**Mô hình thời gian (2026-09-13, người dùng duyệt làm bước 1–2; bước chọn thuật
toán tối ưu phải bàn với người dùng trước)**

- File JSON `ktv-time-model/1` do `research/time_model.py` sinh, lõi đọc bằng
  `load_time_model` (thư viện chuẩn). Thời gian làm: median lượt đầu theo KTV
  (≥ 10 lượt) → theo CASE_TYPE → chung. Khoảng chuyển job = check-in job sau −
  checkout job trước, cùng KTV cùng ngày; median theo 7 bucket km chim bay
  (≤0,05/0,5/1/2/4/8) × giờ rời điểm (ô ≥ 30 cặp). Bỏ cặp chồng lượt (gap < 0).
- `leg_minutes` khi có mô hình = khoảng chuyển job (gồm chờ, nghỉ trưa); km vẫn
  từ TravelModel. Bucket tra theo km chim bay, không theo OSRM.
- Train 01–15/06, test 16–30/06: thời gian làm MAE 59,4 → 36,9, trong ±10 phút
  5% → 49%; khoảng chuyển MAE 93,8 → 70,6, lệch TB −94 → −35. Median làm 13
  phút; 0,5–1 km chuyển 44 phút; checkout 11–12h chuyển 130–170 phút.
- Dữ liệu: tâm phường giả lệch tọa độ check-in thật median 1,6 km (chặng thật
  median 1,1 km), nên backtest dùng tọa độ check-in thật. 98,6% lượt check-in
  đúng KTV được gán. 11% lượt làm < 2 phút.

**Backtest 16–30/06 cả nước (2026-09-13, `artifacts/backtest/2026-06-16_30.json`)**

- A. 60.465 lần chọn job kế (≥ 2 job, TB 4,2; ≥ 10 job chỉ 5%): đoán đúng job
  KTV làm tiếp — planner (hạn sớm nhất) 27,2%, gần nhất 44,8%, mới nhất 34,5%,
  ngẫu nhiên 31,2%. 19.250 cặp KTV đi thẳng tới job tạo sau lúc checkout.
- B. 25.296 KTV-ngày: km chim bay thực tế 188,5 nghìn, planner 216,1 nghìn
  (+15%), gần nhất 142,8 nghìn (−24%). Job trễ mô phỏng gần như không đổi theo
  thứ tự (~36,7–37,4 nghìn), thực tế 25,7 nghìn.
- C. ETA theo thứ tự thật: MAE 149,8 → 128,0 phút, lệch TB −78 → −1 khi dùng mô
  hình; sai ≤ 30 phút chỉ 21,6% → 24,1%.
- Bước 3 đã làm sau khi bàn với người dùng: QHĐ theo rule (2026-09-14,
  `artifacts/backtest/2026-06-16_30_dp.json`, hạn check-in tạo + 24 giờ, trễ theo
  check-in, tọa độ check-in thật). A: đoán đúng job kế 36,6% (tham lam 27,2%).
  B, planner xếp và chấm bằng mô hình học: km thực tế 188.468 → QHĐ 153.126
  (−19%), gần nhất 142.770; job check-in trễ thực tế 8.396 → QHĐ 6.229 (−26%),
  gần nhất 9.229. Cấu hình mặc định: km 148.360, trễ 6.895 → 5.261. Trễ thật
  6.956. Lưu ý: backtest phải cho planner xếp bằng đúng cấu hình đang chấm (QHĐ
  phụ thuộc giờ; lần đầu xếp bằng cấu hình mặc định rồi chấm bằng mô hình học
  cho kết quả sai lệch).

**Simulator (luồng sự kiện, 2026-09-12)**

- Sự kiện JOB_CREATED, CHECKIN, CHECKOUT, JOB_CLOSED, GPS; dòng đầu là header
  `format = ktv-events/1`. Trạng thái tại T chỉ dựng từ sự kiện `at ≤ T`.
- Job mở từ tạo tới đóng, bất kể status cuối (qua phone, Đóng checklist có
  FINISH vẫn được xếp trước lúc đóng). Status cuối đã đóng mà FINISH trống: đóng
  lúc visit cuối, không có thì lúc tạo (`at_inferred`).
- `IN_PROGRESS` từ CHECKIN tới CHECKOUT. Check-in job khác hoặc job đóng thì lượt
  thiếu checkout kết thúc.
- Vị trí KTV: tọa độ mới nhất từ GPS/CHECKIN/CHECKOUT (sự kiện thiếu tọa độ thì
  dùng tọa độ job).
- Hạn theo bảng loại tác vụ trong file nghiệp vụ (`TASK_TYPES`, `job_deadlines`).
  Export không có giờ khách hẹn: mốc A = CREATE_DATE [giả định]. MAINTENANCE (loại
  duy nhất trong export): ưu tiên 2, hạn check-in = tạo + 24 giờ, vì luật này khớp
  `FLAG_ON_TIME` 90% (10 giờ chỉ 61%). "Ngưng kết nối 4H", "Mạng chập chờn" nay
  vẫn xếp tuyến (trước coi là xử lý từ xa). Sự kiện JOB_CREATED có `area` = tên
  phường geocode.
- METADATA: `APPOINTTIMES_ASSIGNED` = số lần đổi KTV (2,6% ≥ 2), `NUM_APPOINTMENT` =
  số lần hẹn; không có cột giờ hẹn.
- Provider lưu snapshot đầu mỗi ngày (file offset + trạng thái) để tua lùi.
- Realtime: mỗi nhịp chỉ xếp lại KTV có thay đổi job khớp bộ lọc; GPS không kích
  hoạt. Phải báo cả `VISIT_ENDED` (check-in job ngoài bộ lọc làm kết thúc lượt
  của job trong bộ lọc). Thiếu nó thì bảng ghép lệch với build đầy đủ; đã gặp
  thật với TIN0302.DATBT ngày 15/06.

## Dữ liệu (`data/`, gitignore)

- `QOS_MAINTENANCE.xlsx` được upload lại ngày 2026-09-11 và convert thành
  `QOS_MAINTENANCE_utf8.csv`.
  - Workbook lưu text dạng shared strings; `convert_xlsx.py` đã được sửa để đọc
    được. Bản convert cũ ra số thứ tự thay cho chữ.
  - 502.210 dòng, 477.773 checklist; 24.240 ID bị lặp và chỉ khác nhau ở
    `SERVICES_LIST`.
  - `CREATE_DATE` nằm trong khoảng 2026-06-01 → 06-30. Ngày có dạng
    `6/29/2026 2:44 PM`. `OBJ_LOCATION` không dấu.
- `QOS_MAINT_CHECKIN_INFO_utf8.csv`: 521.003 dòng.
  - Quan hệ checklist 1→N visit (39.087 ID lặp).
  - `EMP_CODE` khác `EMP_ACCOUNT`.
- Boundary thật `boundary_2026-07-31.geojson` chưa có.
  - Đang dùng file giả `boundary_fake_from_checkins.geojson`, sinh bằng
    `simulator/ktv_simulator/fake_boundary.py`.
  - Mỗi phường/xã là trung vị `LAT_LNG_IN` của các checklist ghi phường đó;
    5.565 phường tìm thấy, giữ 3.497 phường có ≥ 3 check-in.
  - Job trong cùng một phường trùng tọa độ, nên leg bằng 0 km.
- Chạy thật HNI_04 + MAINTENANCE ngày 2026-06-15 lúc 09:00, 13:00, 16:00:
  - Bản provider CSV cũ: 330–345 job, 101–106 KTV. Geocode 344/345. Planning
    khoảng 1,3 ms; toàn lệnh (nạp CSV) khoảng 20 giây. Vị trí KTV đều lấy từ
    `LAST_FINISHED_JOB`, vì GPS sample không có KTV HNI_04.
  - Bản luồng sự kiện (2026-09-12, OSRM tự host): 09:00 có 348 job khớp lọc, 105
    KTV, 311 điểm, 244 trễ, 456 km; 13:00 có 336 job, 101 KTV, 302 điểm, 165 trễ,
    534 km (bản cũ: 345/105/321/254/465 và 333/101/297/163/488). Khác vì job
    cuối cùng xử lý qua phone hoặc đóng vẫn mở tới lúc đóng, và vị trí KTV lấy
    từ check-in/checkout. Toàn lệnh ~3 giây.
  - Luồng sự kiện: 1.723.413 sự kiện 01/06–29/07; 43.469 JOB_CLOSED suy ra giờ;
    33% lượt check-in không có checkout.
  - Lúc 09:00 có 254 điểm trễ, nhưng 241 điểm đã quá hạn trước cả giờ lập tuyến.
    Giả định SLA "10 giờ từ CREATE_DATE" mâu thuẫn với dữ liệu thực tế
    (89% đúng hẹn), nên cần hỏi team checklist.
  - Có KTV còn 13 job "mở": `FINISH_DATE` của "Đã xử lý" có thể chậm hơn lúc
    làm xong thật; nên cân nhắc dùng CHECKOUT làm mốc hoàn thành.
  - So OSRM public với chim bay (09:00 và 13:00): tổng km tăng khoảng 1,6 lần
    (279,9 → 452,5 km lúc 09:00); mỗi đoạn đường bộ dài hơn chim bay median
    1,6 lần (p10 1,3, p90 2,7); tốc độ OSRM median 32 km/h. Giờ xong tuyến trễ
    thêm median khoảng 1 phút, tối đa 19 phút. Không tuyến nào đổi thứ tự (vì
    ưu tiên SLA trước), số điểm trễ gần như không đổi. Tra km 1,2–2,0 giây mỗi
    mốc giờ, không có fallback.
- `sample_emp_coordinate.csv`: 11.322 điểm GPS của 10 KTV trong 10 ngày, dạng
  `COORDINATE="lat,lng"`.
- `CASE_TYPE` chỉ có MAINTENANCE, nên mọi job cùng SLA và priority; thứ tự tuyến
  gần như theo giờ tạo.
- `FLAG_ON_TIME`:
  - YES = đúng hẹn, NO = trễ, NA = hủy, INPROCESS = đang xử lý.
  - Cả 43.611 checklist `Đóng checklist` đều mang NA, và chỉ 252 trong số đó có
    checkout. Không coi `Đóng checklist` là KTV đã làm xong.
- `-1` trong cột ngày nghĩa là trống.
- 100% checklist có `EMP_ACCOUNT`, nhưng export chỉ lưu giá trị cuối, không có
  lịch sử gán.

## Hiệu năng (stress test 2026-09-12, `tests/stress_routing.py`)

- Server 24 CPU, Python 3.12. Chim bay, p50 một request / throughput 1 → 16
  tiến trình: 1 KTV×5 job 0,03 ms / 35,5k → 476k req/s; chi nhánh 100×4
  1,8 ms / 550 → 7,2k req/s; toàn quốc 3.400×3 49 ms / 21 → 241 req/s.
- OSRM giả trễ 5 ms: chi nhánh 59 ms, 17 → 51 req/s (OSRM giả Python là chỗ
  nghẽn). OSRM public thật: ~1,2 s cho HNI_04, tối đa ~1 req/s theo chính sách.
- Web demo `/api/plan` HNI_04 chim bay: bản pandas cũ p50 67 ms, ~15 req/s
  (pandas lọc ~54 ms). Bản luồng sự kiện: p50 10,7 ms, ~80 req/s ở 1 hoặc 4 luồng
  (tuần tự trong lock); request đầu tới 15/06 09:00 mất 2,9 s để đọc sự kiện.
- Luồng sự kiện 300 MB: đọc từ đầu tới 15/06 09:00 mất 3,1 s, tới hết 29/07 thêm
  4,1 s; tua lùi nhờ snapshot 0,06–0,4 s (59 snapshot).
- Tua realtime, OSRM tự host: HNI_04 08:00–18:00 nhịp 5 phút có 1.854 sự kiện
  job, 1.187 lượt xếp lại KTV, mỗi nhịp p50 17 ms, p95 26 ms, max 116 ms. Toàn
  quốc 09:00–10:00 nhịp 1 phút: ~150 sự kiện job, ~105 KTV xếp lại mỗi phút, mỗi
  nhịp p50 77 ms, p95 191 ms.
- Kiểm chứng ghép tuyến (node chạy đúng code của web.html): sau 96 nhịp HNI_04 và
  24 nhịp toàn quốc, job + trạng thái từng KTV trên bảng ghép trùng build đầy đủ;
  tổng quan tính bằng JS trùng `RouteSummary`.
- Không stress test OSRM public; script chặn URL đó.
- OSRM tự host (Docker `ktv-osrm`, CH, `127.0.0.1:5000`, dữ liệu `data/osrm/`,
  dựng 2026-09-12): extract 1m20s / 13,1 GB RAM, contract 3m17s / 4,5 GB, 4,4 GB
  đĩa; chạy 2,6 GB RAM. Kết quả trùng server public. `/table` 6/100/500/1000
  điểm: 7 / 54 / 379 / 1000 ms (tăng gần theo bình phương).
- Stress với OSRM tự host, cách cũ (gom ≤ 100 điểm, tuần tự), p50 và 1 → 16
  tiến trình: 1 KTV 3,7 ms / 210 → 3.370 req/s; chi nhánh 284 ms / 3,6 → 53
  req/s; toàn quốc 7,6 s / 0,2 → 3,2 req/s. Tăng `max_locations` lên 1000 chậm
  hơn (chi nhánh 353 ms).
- Cách mới (mỗi KTV một request, 16 luồng, mặc định khi tự host): 1 KTV 3,6 ms /
  235 → 3.350 req/s; chi nhánh 71 ms / 14 req/s, 4 tiến trình 57 req/s, 16 tiến
  trình 48 req/s với p50 337 ms (OSRM hết CPU, đây là trần); toàn quốc 1,9 s /
  0,6 → 3,2 req/s. Tuyến giống hệt cách cũ.
- HNI_04 dữ liệu thật: tra km ~42–46 ms cả hai cách (88 tọa độ khác nhau vì tâm
  phường giả; gom = 1 request, song song = 75 request). Tổng km 465,2 (public
  452,5, lệch ~3% do phiên bản bản đồ khác nhau).
- urllib mở TCP mới mỗi request (~3 ms overhead); keep-alive là cải tiến có thể
  làm tiếp, chưa làm.

## Câu hỏi mở với team khác

1. Job gửi sang đã có tọa độ chưa? Câu trả lời quyết định geocoding thuộc về ai.
2. Ai tính `due_at`? Có khung giờ hẹn với khách không?
3. Hai bên gọi nhau kiểu gì: API đồng bộ hay hàng đợi/bảng?
4. Khi nào tính lại tuyến: mỗi khi có thay đổi hay theo chu kỳ? Có cần giữ
   nguyên điểm mà KTV đang trên đường tới không?
5. Vị trí KTV có do team GPS gửi kèm không?
6. Ai lưu tuyến đã phát cho KTV?
7. "Đã xử lý và đang theo dõi" có cần tới hiện trường không? (hiện đang coi là không)
