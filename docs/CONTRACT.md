# Hợp đồng dữ liệu — KTV Routing

Tài liệu này dùng để thống nhất dữ liệu với frontend và team data. Định nghĩa chuẩn nằm ở `src/ktv_routing/contract.py`. Ví dụ bên dưới được tạo bằng cách chạy code thật.

## 1. Luồng

| Bước | Từ → đến | Dữ liệu |
|---|---|---|
| 1 | Frontend → routing | `WorkloadQuery`: thời điểm lập tuyến và điều kiện lọc người dùng chọn |
| 2 | Routing → team data | Chuyển nguyên `WorkloadQuery` |
| 3 | Team data → routing | `RouteRequest`: job đã gán của từng KTV (đã lọc), vị trí KTV, ca làm |
| 4 | Routing → frontend | `RouteResponse`: thứ tự làm, ETA, cảnh báo, số liệu tổng |

Team data cũng có thể gọi thẳng routing, bỏ qua bước 1 và 2, bằng cách gửi `RouteRequest`. Khi đó `filter` trong request chỉ dùng để truy vết.

## 2. Quy ước chung

- JSON dùng UTF-8, tên field đúng như các bảng bên dưới. **Field lạ bị từ chối**, để bắt sớm lỗi gõ sai (ví dụ `dueAt` thay vì `due_at`).
- Thời gian ghi dạng chuỗi ISO 8601. Mọi thời gian trong một request phải cùng kiểu với `planned_at`: hoặc cùng có múi giờ (`+07:00`), hoặc cùng không có.
- Tọa độ ghi dạng `{"lat": ..., "lng": ...}` theo hệ WGS84.
- Field không bắt buộc có thể bỏ hẳn hoặc để `null`.
- Nếu request **sai hợp đồng**, routing trả lỗi ghi rõ đường dẫn field, ví dụ `request.technicians[0].jobs[1].due_at: ...`.
- Nếu request **đúng hợp đồng nhưng dữ liệu có vấn đề** (thiếu tọa độ, trùng job...), routing vẫn trả tuyến và liệt kê vấn đề trong `issues`.

## 3. `WorkloadQuery` — frontend gửi

```json
{
  "planned_at": "2026-06-30T09:00:00+07:00",
  "filter": {
    "case_types": ["MAINTENANCE"],
    "branch_names": ["HNI_04"],
    "emp_accounts": []
  }
}
```

| Field | Bắt buộc | Ý nghĩa |
|---|---|---|
| `planned_at` | có | Thời điểm lập tuyến |
| `filter.case_types` | không | Loại việc, ví dụ `MAINTENANCE` |
| `filter.branch_names` | không | Chi nhánh |
| `filter.emp_accounts` | không | Chỉ lấy việc của các KTV này |

**Quy tắc lọc**
- Danh sách rỗng hoặc bị bỏ trống nghĩa là không lọc theo tiêu chí đó.
- Các tiêu chí kết hợp bằng **AND**. Các giá trị trong cùng một danh sách kết hợp bằng **OR**.
- Ví dụ trên nghĩa là: việc MAINTENANCE của chi nhánh HNI_04, của mọi KTV.

Team data áp filter khi lấy dữ liệu và phải **gửi lại đúng filter đó** trong `RouteRequest`. Nếu filter bị lệch, routing báo lỗi thay vì xếp tuyến trên sai phạm vi dữ liệu.

Muốn thêm tiêu chí lọc mới (quận, khách VIP...) thì bổ sung vào `JobFilter` ở cả hai phía.

## 4. `RouteRequest` — team data gửi

```json
{
  "planned_at": "2026-06-30T09:00:00+07:00",
  "filter": {"case_types": ["MAINTENANCE"], "branch_names": ["HNI_04"], "emp_accounts": []},
  "technicians": [
    {
      "emp_account": "HNI04.KTV01",
      "shift_start": "2026-06-30T08:00:00+07:00",
      "shift_end": "2026-06-30T17:30:00+07:00",
      "last_location": {
        "location": {"lat": 21.0285, "lng": 105.8048},
        "recorded_at": "2026-06-30T08:52:00+07:00",
        "source": "GPS"
      },
      "previous_sequence": ["CL1003", "CL1002"],
      "jobs": [
        {
          "job_id": "CL1001", "state": "IN_PROGRESS",
          "location": {"lat": 21.0301, "lng": 105.812},
          "case_type": "MAINTENANCE", "address": "Phường Láng, Hà Nội",
          "due_at": "2026-06-30T16:00:00+07:00", "priority": 2,
          "started_at": "2026-06-30T08:30:00+07:00"
        },
        {
          "job_id": "CL1002", "state": "PENDING",
          "location": {"lat": 21.0452, "lng": 105.801},
          "case_type": "MAINTENANCE", "address": "Phường Nghĩa Đô, Hà Nội", "area": "Nghĩa Đô",
          "appointment_start": "2026-06-30T09:30:00+07:00",
          "due_at": "2026-06-30T10:30:00+07:00", "priority": 2
        },
        {
          "job_id": "CL1003", "state": "PENDING",
          "location": {"lat": 21.012, "lng": 105.83},
          "case_type": "THU HỒI THIẾT BỊ", "address": "Phường Kim Liên, Hà Nội", "area": "Kim Liên",
          "complete_by": "2026-06-30T23:59:59+07:00", "priority": 4
        },
        {
          "job_id": "CL1004", "state": "PENDING", "location": null,
          "case_type": "MAINTENANCE", "address": "Không rõ địa chỉ",
          "due_at": "2026-06-30T18:00:00+07:00", "priority": 2
        }
      ]
    }
  ]
}
```

**Cấp request**

| Field | Bắt buộc | Ý nghĩa |
|---|---|---|
| `planned_at` | có | Phải bằng `planned_at` trong query |
| `filter` | không | Filter đã áp dụng |
| `technicians` | có | Danh sách KTV, được phép rỗng |

**Mỗi KTV** (`technicians[]`)

| Field | Bắt buộc | Ý nghĩa | Nguồn dữ liệu |
|---|---|---|---|
| `emp_account` | có | Account của KTV | Hệ thống checklist |
| `jobs` | có | Job đã gán cho KTV và **còn cần xử lý** | Hệ thống checklist |
| `last_location` | không | Vị trí mới nhất: `location`, `recorded_at`, `source` | GPS |
| `shift_start`, `shift_end` | không | Ca làm trong ngày | Nhân sự |
| `previous_sequence` | không | Thứ tự routing đã gợi ý lần trước (danh sách `job_id`). Có thì routing giữ thứ tự cũ, chỉ chèn job mới, trừ khi tuyến xếp lại tốt hơn rõ | Nơi lưu tuyến đã phát cho KTV (chưa chốt) |

**Mỗi job** (`jobs[]`)

| Field | Bắt buộc | Ý nghĩa |
|---|---|---|
| `job_id` | có | CHECKLIST_ID |
| `state` | có | `PENDING`: KTV còn phải tới. `IN_PROGRESS`: KTV đang làm tại chỗ |
| `location` | có, được để `null` | Tọa độ nhà khách. Nếu `null`, job không được xếp và có issue |
| `case_type` | không | Loại tác vụ. Routing dùng để ước lượng thời gian làm |
| `address` | không | Chỉ để hiển thị và truy vết |
| `appointment_start` | không | Mốc hẹn đầu (A). KTV tới sớm hơn thì chờ tới A mới check-in |
| `due_at` | không | **Hạn check-in** (mốc hẹn cuối B). Check-in sau mốc này là trễ hẹn; làm xong sau B không tính trễ hẹn. Không được trước `appointment_start` |
| `complete_by` | không | Hạn hoàn tất (trong ngày hẹn, trong ngày tạo phiếu, trong tháng). Làm xong sau mốc này là trễ hoàn tất |
| `priority` | không | Ưu tiên trong ngày, số nguyên: 1 gấp nhất … 4. Routing quy ra trọng số khi so thứ tự |
| `area` | không | Khu vực/cụm của job. Routing hạn chế quay lại khu vực đã rời |
| `started_at` | không | Lúc bắt đầu làm. Nên có khi job là `IN_PROGRESS` |

Routing so các thứ tự bằng rule nghiệp vụ theo tầng (đúng hẹn → hoàn tất đúng hạn và trong ca → quãng đường và các đánh đổi khác). Chi tiết, trọng số và các điểm chờ xác nhận: `docs/BUSINESS_RULES.md`.

**Chỉ gửi những job cần xếp tuyến.** Không gửi job đã xong, đã hủy, đã xử lý qua điện thoại hoặc đang tạm hoãn. Việc quy các status của hệ thống checklist về `PENDING` / `IN_PROGRESS` là phần của team data.

## 5. `RouteResponse` — routing trả

Km và phút di chuyển do routing tự lấy từ OSRM (đường bộ); team data không cần gửi. Ví dụ dưới đây chạy bằng code thật với OSRM tự host (cùng kết quả với OSRM public), thời gian đã làm tròn đến giây.

```json
{
  "planned_at": "2026-06-30T09:00:00+07:00",
  "filter": {"case_types": ["MAINTENANCE"], "branch_names": ["HNI_04"], "emp_accounts": []},
  "routes": [
    {
      "emp_account": "HNI04.KTV01",
      "start_at": "2026-06-30T09:30:00+07:00",
      "start_location": {"lat": 21.0301, "lng": 105.812},
      "start_source": "IN_PROGRESS_JOB",
      "in_progress_job_id": "CL1001",
      "travel_source": "OSRM",
      "sequence_source": "OPTIMAL",
      "previous_route": "CHANGED",
      "score": {
        "LATE_CHECKIN": 0.0, "LATE_COMPLETION": 0.0, "AFTER_SHIFT": 0.0, "LATE_MINUTES": 0.0,
        "KM": 9.815, "TRAVEL_MINUTES": 13.417, "AREA_REENTRY": 0.0, "PRIORITY_DELAY": 1.449, "FINISH": 88.417
      },
      "stops": [
        {
          "sequence": 1, "job_id": "CL1002",
          "location": {"lat": 21.0452, "lng": 105.801},
          "leg_km": 3.289, "leg_minutes": 4.52,
          "eta": "2026-06-30T09:34:31+07:00", "wait_minutes": 0.0,
          "finish_at": "2026-06-30T10:34:31+07:00",
          "due_at": "2026-06-30T10:30:00+07:00",
          "late": false, "completion_late": null, "after_shift_end": false
        },
        {
          "sequence": 2, "job_id": "CL1003",
          "location": {"lat": 21.012, "lng": 105.83},
          "leg_km": 6.526, "leg_minutes": 8.9,
          "eta": "2026-06-30T10:43:25+07:00", "wait_minutes": 0.0,
          "finish_at": "2026-06-30T10:58:25+07:00",
          "due_at": null,
          "late": null, "completion_late": false, "after_shift_end": false
        }
      ],
      "total_km": 9.815,
      "total_travel_minutes": 13.42,
      "total_service_minutes": 75.0,
      "finish_at": "2026-06-30T10:58:25+07:00"
    }
  ],
  "issues": [
    {"code": "JOB_LOCATION_UNKNOWN", "emp_account": "HNI04.KTV01", "job_id": "CL1004", "detail": "address=Không rõ địa chỉ"}
  ],
  "summary": {
    "technicians": 1, "jobs": 4, "pending_jobs": 3, "in_progress_jobs": 1,
    "routed_stops": 2, "stops_without_eta": 0, "late_stops": 0, "completion_late_stops": 0,
    "stops_after_shift_end": 0, "sla_evaluable_jobs": 2, "on_time_stops": 1, "on_time_rate_percent": 50.0,
    "total_km": 9.815, "travel_ms": 4.9, "planning_ms": 5.1
  }
}
```

**Cách đọc ví dụ**
- KTV đang làm CL1001 từ 08:30, dự kiến xong lúc 09:30, nên tuyến xuất phát từ CL1001 lúc 09:30.
- Tuyến cũ gửi lên là CL1003 → CL1002. Routing so tuyến cũ với tuyến xếp lại theo rule nghiệp vụ; tuyến CL1002 → CL1003 tốt hơn đủ ngưỡng nên được chọn (`previous_route: CHANGED`).
- CL1002: từ CL1001 đi 3,289 km đường bộ mất 4,52 phút, tới 09:34, sau mốc hẹn đầu 09:30 nên không chờ. Check-in trước hạn 10:30 nên `late: false`, dù làm xong lúc 10:34: trễ hẹn tính theo **check-in**.
- CL1003 là thu hồi thiết bị: không có hạn check-in (`late: null`), hạn hoàn tất cuối ngày (`completion_late: false`), làm 15 phút.
- CL1004 không có tọa độ, nên không nằm trên tuyến và được báo trong `issues`. Nó vẫn nằm trong mẫu số tỷ lệ đúng hẹn: 1/2 = 50%.
- `score` là chi phí từng rule của thứ tự đã chọn, dùng để giải thích vì sao chọn thứ tự này.

**Mỗi tuyến** (`routes[]`)

| Field | Ý nghĩa |
|---|---|
| `start_at` | Lúc KTV rảnh để đi tới điểm đầu tiên |
| `start_location`, `start_source` | Điểm xuất phát và nguồn của nó: `IN_PROGRESS_JOB`, `LAST_LOCATION`, `STALE_LOCATION` hoặc `UNKNOWN` |
| `in_progress_job_id` | Job KTV đang làm, nếu có |
| `travel_source` | `OSRM`: km/phút theo đường bộ. `HAVERSINE`: chim bay 30 km/h (khi cấu hình vậy, hoặc OSRM lỗi) |
| `sequence_source` | `OPTIMAL`: quy hoạch động tìm được thứ tự tốt nhất theo rule. `APPROXIMATE`: phải cắt bớt nhãn, gần tối ưu. `HEURISTIC`: quá nhiều job, dùng tham lam theo rule + 2-opt |
| `previous_route` | `NONE`: request không có tuyến cũ. `KEPT`: giữ thứ tự cũ, chỉ chèn job mới. `CHANGED`: đổi vì tuyến xếp lại tốt hơn rõ |
| `score` | Chi phí từng rule mềm của thứ tự đã chọn (mã rule: `docs/BUSINESS_RULES.md` mục 8.2). Rỗng nếu không biết điểm xuất phát |
| `stops` | Thứ tự làm việc |
| `total_km`, `total_travel_minutes`, `total_service_minutes` | Tổng quãng đường, tổng `leg_minutes` và tổng thời gian làm |
| `finish_at` | Lúc làm xong điểm cuối |

**Mỗi điểm dừng** (`stops[]`)

| Field | Ý nghĩa |
|---|---|
| `sequence` | Thứ tự làm, bắt đầu từ 1 |
| `leg_km` | Km từ điểm trước (điểm xuất phát với điểm số 1), theo `travel_source`. `null` nếu không biết điểm xuất phát |
| `leg_minutes` | Phút từ lúc xong điểm trước tới lúc tới điểm này. Mặc định là phút di chuyển theo `travel_source`. Khi routing chạy với mô hình thời gian học từ lịch sử, con số này gồm cả chờ và nghỉ (tra theo km chim bay × giờ rời điểm trước). `null` khi `leg_km` là `null` |
| `eta` | Giờ tới = giờ xong điểm trước (hoặc `start_at`) + `leg_minutes`. `null` nếu không biết điểm xuất phát |
| `wait_minutes` | Chờ tới `appointment_start`; giờ check-in = `eta` + `wait_minutes` |
| `finish_at` | Giờ check-in + thời gian làm do routing ước lượng (bảng theo `case_type`, hoặc median riêng của KTV khi có mô hình thời gian) |
| `late` | **Check-in** sau `due_at` (trễ hẹn). `null` nếu thiếu giờ hoặc hạn |
| `completion_late` | `finish_at` sau `complete_by` (trễ hoàn tất). `null` nếu thiếu giờ hoặc hạn |
| `after_shift_end` | Xong sau giờ hết ca |

**Summary**

| Field | Ý nghĩa |
|---|---|
| `late_stops` | Số điểm check-in trễ hẹn |
| `completion_late_stops` | Số điểm làm xong sau hạn hoàn tất |
| `sla_evaluable_jobs` | Số job `PENDING` có `due_at`, **kể cả job không xếp được**, để tỷ lệ đúng hạn không bị che bởi dữ liệu thiếu |
| `on_time_stops`, `on_time_rate_percent` | Số điểm dự kiến check-in kịp hạn và tỷ lệ tương ứng trên `sla_evaluable_jobs` |
| `stops_without_eta` | Số điểm không tính được ETA |
| `travel_ms` | Thời gian lấy km/phút di chuyển (gọi OSRM) |
| `planning_ms` | Tổng thời gian xếp tuyến, đã gồm `travel_ms` |

## 6. Mã issue

| Mã | Khi nào | Routing xử lý thế nào |
|---|---|---|
| `JOB_LOCATION_UNKNOWN` | Job không có `location` | Bỏ khỏi tuyến. Nếu là job `IN_PROGRESS` thì không dùng làm điểm xuất phát |
| `DUPLICATE_JOB` | Cùng một `job_id` xuất hiện ở nhiều KTV | Giữ ở KTV đứng đầu theo thứ tự `emp_account` |
| `DUPLICATE_TECHNICIAN` | Cùng một `emp_account` xuất hiện nhiều lần | Giữ lần đầu |
| `MULTIPLE_IN_PROGRESS` | KTV có nhiều hơn một job `IN_PROGRESS` | Lấy job bắt đầu sau cùng làm điểm xuất phát |
| `IN_PROGRESS_START_UNKNOWN` | Job `IN_PROGRESS` thiếu `started_at` | Giả định job vừa bắt đầu tại `planned_at` |
| `STALE_TECHNICIAN_LOCATION` | `last_location` cũ hơn 240 phút | Vẫn dùng vị trí này |
| `TECHNICIAN_LOCATION_UNKNOWN` | Không có vị trí, cũng không có job đang làm có tọa độ | Tuyến có thứ tự nhưng không có ETA |
| `TECHNICIAN_OFF_SHIFT` | `planned_at` ≥ `shift_end` mà vẫn còn job chờ | Vẫn xếp; các điểm có `after_shift_end: true` |
| `ROAD_DISTANCE_FALLBACK` | OSRM lỗi, quá tải, hoặc không tìm được đường cho một đoạn | Đoạn đó (hoặc cả tuyến) dùng chim bay; `detail` ghi lý do |
| `SEQUENCE_NOT_OPTIMAL` | KTV có nhiều job hơn giới hạn quy hoạch động, hoặc phải cắt bớt nhãn | Vẫn trả thứ tự (heuristic hoặc gần tối ưu); `detail` ghi lý do |

## 7. Cần các team xác nhận

| # | Câu hỏi | Team | Ảnh hưởng |
|---|---|---|---|
| 1 | Job gửi sang có sẵn tọa độ không, hay chỉ có địa chỉ? | Data / checklist | Nếu chỉ có địa chỉ, cần chốt ai geocode |
| 2 | `appointment_start`, `due_at` (mốc hẹn A, B) và `complete_by` lấy từ đâu? Export hiện không có giờ khách hẹn | Checklist | Quyết định job nào bị tính trễ hẹn |
| 3 | 8 status quy về `PENDING` / `IN_PROGRESS` thế nào, đặc biệt "Đã xử lý và đang theo dõi"? | Checklist | Quyết định job nào được lên tuyến |
| 4 | Hai bên gọi nhau kiểu gì: API đồng bộ, hay hàng đợi / bảng? | Data, frontend | Chỉ đổi entrypoint, không đổi hợp đồng |
| 5 | Khi nào tính lại tuyến? Có cần giữ nguyên điểm KTV đang trên đường tới? | Frontend, vận hành | Có thể cần thêm trạng thái "đang di chuyển tới job" |
| 6 | Vị trí KTV lấy từ đâu, cập nhật bao lâu một lần? | GPS | Ảnh hưởng `last_location` |
| 7 | Ai lưu tuyến đã phát cho KTV và gửi lại trong `previous_sequence`? | Database, frontend | Routing hiện không lưu gì; thiếu tuyến cũ thì mỗi lần gọi đều xếp lại từ đầu |
| 8 | UI còn tiêu chí lọc nào khác? | Frontend | Mở rộng `JobFilter` |
| 9 | Toàn bộ rule nghiệp vụ, trọng số và giả định xử lý dữ liệu | Nghiệp vụ | Xem `docs/BUSINESS_RULES.md` mục 11 (Q1–Q26) |
