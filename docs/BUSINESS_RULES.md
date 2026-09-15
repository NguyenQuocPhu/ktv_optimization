# Nghiệp vụ routing KTV — bản để xác nhận

Bản nháp ngày 2026-09-14. File này gom **mọi quy tắc có dính tới nghiệp vụ**: loại tác vụ, hạn, ưu tiên, cách xử lý dữ liệu (trùng, thiếu, sai), cách tính thời gian và quãng đường, cách chọn thứ tự làm việc. Mỗi quy tắc có nhãn nguồn:

| Nhãn | Nghĩa |
|---|---|
| **[TÀI LIỆU]** | Lấy từ file nghiệp vụ `Mô tả loại tác vụ - ưu tiên để Gợi ý công việc.xlsx` hoặc `METADATA_20260730.xlsx` |
| **[XÁC NHẬN]** | Người dùng đã nói rõ trong trao đổi |
| **[DỮ LIỆU]** | Rút ra từ export QOS tháng 6/2026 |
| **[GIẢ ĐỊNH]** | Tự đặt để chạy được, **cần xác nhận** |

Câu hỏi cần trả lời được đánh mã **Q1, Q2...** và gom ở [mục 11](#11-câu-hỏi-cần-xác-nhận).

Code tương ứng: rule chọn thứ tự ở `src/ktv_routing/rules.py`, thuật toán ở `src/ktv_routing/planner.py`, xử lý dữ liệu nguồn (phần team data sẽ làm thật) ở `simulator/ktv_simulator/`.

---

## 1. Phạm vi

- Team routing **chỉ xếp thứ tự** các job đã được gán cho từng KTV, kèm giờ tới, giờ xong, quãng đường và cảnh báo. **[XÁC NHẬN]**
- Routing **không** gán hay chuyển job giữa các KTV, **không** lưu trạng thái giữa các lần gọi, **không** quản lý vòng đời checklist. Các phần đó thuộc team checklist, team data và frontend. **[XÁC NHẬN]**
- Kiến trúc trong tài liệu gồm Rule engine, Cluster engine, Route optimization engine và Recommendation engine **[TÀI LIỆU]**. Routing hiện làm:
  - phần chấm điểm theo rule;
  - phần tối ưu thứ tự;
  - phần giải thích (chi phí từng rule).

  Cluster engine mới có một rule "hạn chế quay lại khu vực đã rời", chưa trả cụm cho app (Q18).

## 2. Loại tác vụ, SLA và ưu tiên

Bảng dưới lấy nguyên từ sheet "#Bảng KPI, SLA Tác vụ" **[TÀI LIỆU]**. Cột cuối là cách routing hiểu.

| # | Nhóm | Loại tác vụ | SLA mốc hẹn (A→B) | Yêu cầu đúng hẹn | Ưu tiên | Routing nhận |
|---|---|---|---|---|---|---|
| 1 | Triển khai | Triển khai mới (NET, Combo..) | 120 phút | Check-in trước mốc hẹn cuối (B) | 3 | `due_at` = B |
| 2 | Triển khai | Box, Cam Only | 120 phút | Check-in trước B | 3 | `due_at` = B |
| 3 | Triển khai | Swap | 60 phút | Check-in trước B | 3 | `due_at` = B |
| 4 | Triển khai | Gsafe | Trong ngày | Hoàn tất trong ngày hẹn | 3 | `complete_by` = cuối ngày hẹn |
| 5 | Triển khai | Giao thiết bị Cam | 60 phút | Hoàn tất trong ngày hẹn | 3 | `complete_by` = cuối ngày hẹn (Q3) |
| 6 | Bảo trì | Vật lý | 60 phút | Check-in trước B | **1** | `due_at` = B |
| 7 | Bảo trì | Logic | 60 phút | Check-in trước B | 2 | `due_at` = B |
| 8 | Thu hồi | Thu hồi thiết bị | N/A | Hoàn tất trong tháng | 4 | `complete_by` = cuối tháng |
| 9 | Thu bill | Thu bill trả trước, sau | N/A | Hoàn tất trong tháng | 4 | `complete_by` = cuối tháng |
| 10 | CSKH chủ động | Phiếu Onsite | N/A | Hoàn tất trong tháng | 2 | `complete_by` = cuối tháng |
| 11 | CSKH chủ động | Ngưng kết nối 4H | N/A | Hoàn tất trong ngày tạo phiếu | 2 | `complete_by` = cuối ngày tạo |
| 12 | CSKH chủ động | Mạng chập chờn, suy hao cao | N/A | Hoàn tất trong ngày tạo phiếu | 4 | `complete_by` = cuối ngày tạo |

- "Ưu tiên trong ngày": 1 là gấp nhất, 4 là thấp nhất. **[TÀI LIỆU]**
- Export QOS **chỉ có `CASE_TYPE = MAINTENANCE`** (METADATA: "ca vụ: default MAINTENANCE"), không nói là Vật lý hay Logic. Simulator tạm cho MAINTENANCE **ưu tiên 2**. **[GIẢ ĐỊNH]** (Q1)
- Bản trước coi "Ngưng kết nối 4H" và "Mạng chập chờn" là việc xử lý từ xa, không xếp tuyến. Tài liệu liệt kê hai loại này trong bảng gợi ý công việc, nên nay **vẫn xếp tuyến**. **[GIẢ ĐỊNH]** (Q2)
- Loại tác vụ không có trong bảng: không hạn, ưu tiên 3. **[GIẢ ĐỊNH]**

## 3. Đúng hẹn, trễ hẹn

| Khái niệm | Cách tính | Nguồn |
|---|---|---|
| **Trễ hẹn** (`late`) | Giờ **check-in** dự kiến **sau** `due_at` (mốc hẹn cuối B). Làm xong sau B **không** tính là trễ hẹn | **[TÀI LIỆU]** + **[XÁC NHẬN]** |
| **Tới sớm** | Tới trước `appointment_start` (mốc hẹn đầu A) thì **chờ tới A** mới check-in; thời gian chờ ghi ở `wait_minutes` | **[GIẢ ĐỊNH]** (Q4) |
| **Trễ hoàn tất** (`completion_late`) | Giờ **làm xong** dự kiến sau `complete_by` (cuối ngày hẹn, cuối ngày tạo phiếu, cuối tháng) | **[TÀI LIỆU]**; "ngày hẹn" là ngày nào: Q3 |
| **Đã quá hạn từ trước** | `due_at` ≤ giờ lập tuyến: job chắc chắn trễ dù đi thứ tự nào. Vẫn tính trễ, giao diện tách riêng để không đổ lỗi cho thứ tự | **[GIẢ ĐỊNH]** (Q5) |
| **Xong ngoài ca** (`after_shift_end`) | Giờ làm xong sau `shift_end`. Vẫn xếp, chỉ gắn cờ | **[GIẢ ĐỊNH]** (Q6) |
| **Tỷ lệ đúng hẹn dự kiến** | Số điểm check-in kịp hạn / số job chờ có `due_at`, **kể cả job không xếp được vì thiếu tọa độ** | **[GIẢ ĐỊNH]** |

Export QOS **không có giờ khách hẹn** (không có mốc A, B). METADATA chỉ có `NUM_APPOINTMENT` (số lần hẹn) và `APPOINTTIMES_ASSIGNED` (số lần đổi KTV) **[TÀI LIỆU]**. Vì vậy simulator tạm tính:

- **MAINTENANCE:** `due_at` = `CREATE_DATE` + **24 giờ**. Luật "check-in trong 24 giờ sau khi tạo" khớp với cột `FLAG_ON_TIME` ở **90%** checklist; luật 10 giờ dùng trước đây chỉ khớp 61%. **[DỮ LIỆU]** + **[GIẢ ĐỊNH]** (Q1)
- **Loại có mốc hẹn khác:** mốc A = `CREATE_DATE`, B = A + SLA; "ngày hẹn" = ngày tạo phiếu. **[GIẢ ĐỊNH]** (Q3)

## 4. Dữ liệu vào routing và cách xử lý dữ liệu xấu

Hợp đồng chi tiết ở `docs/CONTRACT.md`. Routing kiểm tra request trước khi xếp:

**Request bị từ chối (lỗi, không xếp):** **[GIẢ ĐỊNH]**
- Field lạ, sai kiểu, sai định dạng ngày.
- Datetime có múi giờ lẫn không múi giờ.
- Vĩ độ/kinh độ ngoài khoảng.
- `priority` không phải số nguyên.
- `shift_end` trước `shift_start`.
- `due_at` trước `appointment_start`.
- `previous_sequence` có phần tử rỗng.

**Dữ liệu xấu không dừng chương trình, trả kèm cảnh báo (issue):**

| Tình huống | Routing xử lý | Mã issue | Nguồn |
|---|---|---|---|
| Một KTV xuất hiện 2 lần trong request | Giữ lần đầu (theo `emp_account`) | `DUPLICATE_TECHNICIAN` | [GIẢ ĐỊNH] (Q7) |
| Một job nằm ở 2 KTV | Giữ ở KTV có `emp_account` đứng trước theo thứ tự chữ | `DUPLICATE_JOB` | [GIẢ ĐỊNH] (Q7) |
| Job chờ không có tọa độ | Bỏ khỏi tuyến; vẫn tính trong mẫu số tỷ lệ đúng hẹn | `JOB_LOCATION_UNKNOWN` | [GIẢ ĐỊNH] |
| KTV có nhiều job đang làm | Lấy job bắt đầu sau cùng làm điểm xuất phát | `MULTIPLE_IN_PROGRESS` | [GIẢ ĐỊNH] |
| Job đang làm thiếu giờ bắt đầu | Coi như bắt đầu lúc lập tuyến | `IN_PROGRESS_START_UNKNOWN` | [GIẢ ĐỊNH] |
| Vị trí KTV cũ hơn 240 phút | Vẫn dùng, cảnh báo | `STALE_TECHNICIAN_LOCATION` | [GIẢ ĐỊNH] (Q8) |
| Không biết vị trí KTV | Vẫn có thứ tự, không có giờ tới | `TECHNICIAN_LOCATION_UNKNOWN` | [GIẢ ĐỊNH] |
| Lập tuyến sau giờ hết ca | Vẫn xếp, cảnh báo | `TECHNICIAN_OFF_SHIFT` | [GIẢ ĐỊNH] (Q6) |
| OSRM lỗi hoặc thiếu đoạn đường | Dùng km chim bay cho KTV/đoạn đó | `ROAD_DISTANCE_FALLBACK` | [GIẢ ĐỊNH] |
| Quá nhiều job cho QHĐ | Dùng heuristic | `SEQUENCE_NOT_OPTIMAL` | [GIẢ ĐỊNH] |
| `previous_sequence` có job không còn trong request, hoặc lặp | Bỏ job đó, giữ lần xuất hiện đầu | – | [GIẢ ĐỊNH] |

## 5. Xử lý dữ liệu nguồn (phần team data sẽ làm thật)

Phần này hiện nằm trong simulator vì chưa có hệ thống thật. Nó ghi lại cách export QOS được hiểu. Team data làm thật thì đối chiếu từng dòng.

### 5.1 Đọc file
- Hai workbook gốc được đổi sang CSV UTF-8. Workbook lưu chữ dạng "shared strings"; bản đổi đầu tiên ra số thứ tự thay cho chữ, đã sửa. **[DỮ LIỆU]**
- Ô ngày `-1` hoặc `1000-01-01` nghĩa là trống (METADATA: "chưa hoàn tất = 1000-01-01"). **[TÀI LIỆU]**
- Ngày có dạng `6/29/2026 2:44 PM`, giờ địa phương không múi giờ. **[DỮ LIỆU]**

### 5.2 Checklist (`QOS_MAINTENANCE`)

| Tình huống | Cách xử lý | Nguồn |
|---|---|---|
| **Cùng `CHECKLIST_ID` nhiều dòng** (24.240 ID, chỉ khác `SERVICES_LIST`) | Giữ **dòng đầu**, bỏ các dòng sau (danh sách dịch vụ của dòng sau bị mất) | [DỮ LIỆU] + [GIẢ ĐỊNH] (Q9) |
| Thiếu `CHECKLIST_ID` hoặc `CREATE_DATE` | Bỏ dòng | [GIẢ ĐỊNH] |
| `EMP_ACCOUNT` trống | Không đưa vào request, đếm là "thiếu KTV" | [GIẢ ĐỊNH] |
| KTV của job | Export chỉ lưu `EMP_ACCOUNT` **cuối cùng**; coi như gán từ lúc tạo. 2,6% checklist đổi KTV ≥ 2 lần (`APPOINTTIMES_ASSIGNED`), nên KTV lúc đầu có thể khác | [TÀI LIỆU] + [GIẢ ĐỊNH] (Q10) |
| Job mở khi nào | Từ `CREATE_DATE` tới `FINISH_DATE`, **bất kể status cuối**. Lúc đang mở chưa ai biết cuối cùng job sẽ "xử lý qua phone" hay "đóng" | [GIẢ ĐỊNH] (Q11) |
| Status cuối còn mở (Chưa phân công, Đã phân công, Đang xử lý) mà `FINISH_DATE` trống | Job chưa đóng | [GIẢ ĐỊNH] |
| **Status cuối đã đóng mà `FINISH_DATE` trống** (43.469 checklist, phần lớn "Đóng checklist") | Đóng lúc check-in/checkout cuối; không có lượt nào thì đóng ngay lúc tạo | [DỮ LIỆU] + [GIẢ ĐỊNH] (Q11) |
| "Đóng checklist" | `FLAG_ON_TIME = NA` (hủy) ở cả 43.611 checklist; không coi là KTV đã làm xong | [TÀI LIỆU] + [DỮ LIỆU] |
| "Đã xử lý" nhưng `FINISH_DATE` muộn hơn checkout thật | Job vẫn mở tới `FINISH_DATE`; có KTV còn tới 13 job "mở". Chưa dùng checkout làm mốc xong | [DỮ LIỆU] (Q12) |

### 5.3 Check-in (`QOS_MAINT_CHECKIN_INFO`)

| Tình huống | Cách xử lý | Nguồn |
|---|---|---|
| Lượt check-in của checklist không có trong export | Bỏ | [GIẢ ĐỊNH] |
| Một checklist nhiều lượt (9,6% checklist) | Mỗi lượt là một cặp check-in/checkout riêng | [DỮ LIỆU] |
| **Thiếu checkout** (33% lượt) | Lượt kết thúc khi KTV check-in job khác, hoặc khi job đóng | [DỮ LIỆU] + [GIẢ ĐỊNH] (Q13) |
| KTV check-in job khác khi lượt trước chưa checkout | Lượt trước kết thúc; job trước quay về "chờ" nếu chưa đóng | [GIẢ ĐỊNH] (Q13) |
| Check-in vào job đã đóng | Không đổi job nào, chỉ cập nhật vị trí KTV | [GIẢ ĐỊNH] |
| Người check-in | File dùng `EMP_CODE` (khác `EMP_ACCOUNT`); 98,6% lượt khớp KTV được gán, nên dùng KTV của checklist | [DỮ LIỆU] |
| Check-in job sau **trước** checkout job trước (13,9% cặp liên tiếp) | Coi là nhập liệu chồng lượt; không dùng để học thời gian chuyển job | [DỮ LIỆU] + [GIẢ ĐỊNH] |
| Tọa độ ngoài khung Việt Nam (vĩ độ 8–24, kinh độ 102–110) | Bỏ tọa độ | [GIẢ ĐỊNH] |
| Check-in/checkout thiếu tọa độ | Dùng tọa độ job cho vị trí KTV | [GIẢ ĐỊNH] |

### 5.4 GPS (`sample_emp_coordinate`)
- `COORDINATE = "lat,lng"`. Không đọc được thì bỏ; giây lẻ làm tròn xuống. **[DỮ LIỆU]**
- File mẫu chỉ có 10 KTV trong 10 ngày, không có KTV nào của HNI_04. **[DỮ LIỆU]**

### 5.5 Vị trí hiện tại của KTV
Lấy **tọa độ mới nhất** trong ba nguồn: GPS, check-in, checkout. **[GIẢ ĐỊNH]** (Q8)

### 5.6 Tọa độ và khu vực của job
- **Export không có tọa độ khách hàng.** Tọa độ job được suy từ địa chỉ `OBJ_LOCATION` (địa chỉ lắp đặt). **[TÀI LIỆU]** (Q14)
- **Geocoding** (địa chỉ → phường/xã):
  1. Tách phần "Phường / Xã / Thị trấn" trong địa chỉ.
  2. Khớp chính xác tên phường, ưu tiên phường thuộc đúng tỉnh ghi trong địa chỉ.
  3. Không khớp chính xác thì so gần đúng (≥ 86% giống) trong tỉnh đó.

  Geocode được 474.959/477.773 checklist. **[GIẢ ĐỊNH]**
- **Ranh giới phường là file giả:** mỗi phường là trung vị tọa độ check-in của các checklist ghi phường đó, chỉ giữ phường có ≥ 3 check-in (3.497 phường). Tọa độ job = tâm phường, **lệch tọa độ check-in thật median 1,6 km**, trong khi mỗi chặng thật chỉ khoảng 1,1 km. **[DỮ LIỆU]** (Q14)
- **Khu vực (`area`) của job** = tên phường geocode được, dùng cho rule hạn chế quay lại khu vực. **[GIẢ ĐỊNH]** (Q18)

### 5.7 Ca làm
Mọi KTV cùng một ca do người chạy truyền vào (ví dụ 08:00–17:30). Tài liệu nhắc "thời điểm bắt đầu ca" nhưng export không có. **[GIẢ ĐỊNH]** (Q15)

### 5.8 Sự kiện realtime và khi nào xếp lại
- Export được đổi thành luồng sự kiện: tạo job, check-in, checkout, đóng job, GPS. Trùng giờ thì áp theo thứ tự đó. Trạng thái tại T chỉ dựng từ sự kiện trước T. **[GIẢ ĐỊNH]**
- KTV được xếp lại khi job của KTV thay đổi: tạo, check-in, checkout, đóng, hoặc lượt trước bị kết thúc do check-in job khác (`VISIT_ENDED`). GPS chỉ cập nhật vị trí, không kích hoạt xếp lại. **[GIẢ ĐỊNH]**
- Sheet "4. Thời gian AI chạy" **[TÀI LIỆU]** và trạng thái hiện tại:

| Tài liệu | Hiện tại |
|---|---|
| Đầu ngày từ 6h: bắt buộc | Frontend/scheduler gọi `planned_at` = 06:00; routing không tự hẹn giờ (Q16) |
| Hoàn thành 1 ca: chỉ tính phần còn lại | Có: job đóng thì bỏ khỏi request; KTV đó được xếp lại |
| Có ca mới phát sinh: bắt buộc | Có: sự kiện tạo job |
| Khách đổi giờ: bắt buộc | **Chưa có** sự kiện đổi giờ trong dữ liệu (Q16) |
| KTV bấm "Tối ưu lại" | Frontend gọi lại routing; đặt policy tuyến cũ `IGNORE` nếu muốn xếp lại hoàn toàn (Q17) |
| Mỗi 30–60 phút: chỉ kiểm tra | Chưa có; routing gọi lại với `previous_sequence` sẽ tự giữ tuyến nếu không tốt hơn rõ |

- Công việc bị **hủy** hiện là "đóng job" (status "Đóng checklist"). Chưa có sự kiện hủy riêng. **[GIẢ ĐỊNH]**

## 6. Thời gian

### 6.1 Điểm và giờ xuất phát **[GIẢ ĐỊNH]**
1. KTV **đang làm** một job: xuất phát tại job đó, lúc `giờ bắt đầu + thời gian làm dự kiến` (không trừ phần đã làm). Job đang làm không nằm trong danh sách xếp.
2. Không đang làm: xuất phát tại vị trí mới nhất, lúc lập tuyến.
3. Không biết vị trí: vẫn có thứ tự, không có giờ tới.
4. Giờ xuất phát không sớm hơn `shift_start`.

### 6.2 Thời gian làm tại chỗ
- **Mặc định:** bảng theo loại tác vụ: triển khai mới, Box/Cam 120 phút; Swap, Maintenance, Vật lý, Logic 60 phút; Thu hồi 15 phút; loại khác 60 phút. **[GIẢ ĐỊNH]** Tài liệu có mục "Thời gian xử lý chuẩn" nhưng chưa có số (Q19).
- **Có mô hình học từ lịch sử** (`--time-model`):
  - Median (checkout − check-in) của lượt đầu tới job, riêng từng KTV có ≥ 10 lượt.
  - KTV ít lượt hơn: median theo loại tác vụ, rồi median chung.
  - Median chung là **13 phút**; 11% lượt làm dưới 2 phút.

  **[DỮ LIỆU]**

### 6.3 Thời gian đi giữa hai điểm
- **Mặc định:** phút di chuyển theo nguồn km (mục 7): OSRM tính theo ô tô trên đường trống, chim bay tính 30 km/h. **Chưa tính kẹt xe, đường cấm, đường một chiều** dù tài liệu có nhắc. **[TÀI LIỆU]** + (Q20)
- **Có mô hình học:**
  - Tra bảng "khoảng chuyển job" = check-in job sau − checkout job trước của cùng KTV trong ngày, theo km chim bay × giờ rời điểm trước.
  - Con số này **gồm cả chờ khách, nghỉ trưa và việc khác**: 0,5–1 km mất median 44 phút; rời điểm lúc 11–12h mất 130–170 phút.

  **[DỮ LIỆU]** (Q20)

### 6.4 Chờ mốc hẹn
Xem mục 3: tới trước mốc hẹn đầu thì chờ. **[GIẢ ĐỊNH]** (Q4)

## 7. Quãng đường

| Nội dung | Quy tắc | Nguồn |
|---|---|---|
| Chim bay | Công thức Haversine, bán kính Trái Đất 6.371,0088 km | [GIẢ ĐỊNH] |
| Đường bộ | OSRM `/table`, profile **ô tô** (`driving`), bản đồ OpenStreetMap Việt Nam | [GIẢ ĐỊNH] (Q21) |
| OSRM public | Tối đa 100 điểm, khoảng 1 request/giây: gom nhiều KTV chung một request, chờ 1 giây giữa các lần | [DỮ LIỆU] |
| OSRM tự host | Mỗi KTV một request, 16 request song song | [GIẢ ĐỊNH] |
| Tọa độ trùng | Chỉ gửi một lần | – |
| OSRM lỗi / đoạn không có đường | Dùng chim bay cho KTV/đoạn đó, kèm cảnh báo | [GIẢ ĐỊNH] |
| Km dùng cho | Rule "Quãng đường", `leg_km`, `total_km`, bản đồ | – |
| Km dùng tra bảng thời gian học | Km **chim bay**, vì bảng được học theo chim bay | [GIẢ ĐỊNH] |
| Quay về điểm xuất phát cuối ngày | **Không tính** | [GIẢ ĐỊNH] (Q22) |
| KTV đi xe máy | OSRM đang tính ô tô; km xe máy có thể ngắn hơn (hẻm, đường cấm ô tô) | [GIẢ ĐỊNH] (Q21) |

## 8. Chọn thứ tự làm việc (quy hoạch động)

### 8.1 Mục tiêu trong tài liệu và chỗ tương ứng

| Mục tiêu / tiêu chí trong tài liệu | Trong routing |
|---|---|
| Đúng thời gian cam kết (SLA / khung giờ hẹn), "Highest Priority" | Tầng 1: Check-in trễ hẹn |
| Giảm tổng quãng đường | Tầng 3: Quãng đường |
| Gom công việc gần nhau, hạn chế quay đầu, hạn chế vào một khu vực nhiều lần | Tầng 3: Quay lại khu vực đã rời (cần `area`); km nhỏ tự gom điểm gần |
| Giảm thời gian di chuyển không tạo giá trị | Tầng 3: Thời gian di chuyển |
| Tăng số việc hoàn thành trong ngày | Tầng 2: Xong sau giờ hết ca |
| Thời gian chờ giữa các công việc thấp nhất | Tầng 3: Giờ xong job cuối (gồm chờ) |
| Ưu tiên trong ngày | Trọng số của rule trễ hẹn + rule "Job ưu tiên cao bị để muộn" |
| Hạn chế KTV tự chọn theo cảm tính | Ngoài routing: app hiển thị tuyến |
| Tự thích ứng khi thay đổi, "chỉ xây routing thêm, không phá" | Rule cứng giữ thứ tự tuyến cũ (mục 8.4) |
| Thời gian AI sinh tuyến ≤ 5 giây | Mục 10 |

### 8.2 Rule và thứ tự ưu tiên **[GIẢ ĐỊNH]** (Q23)

So hai thứ tự bằng từng **tầng**. Tầng 1 quyết định trước; tầng 2 chỉ phân xử khi tầng 1 bằng nhau; cứ thế. Trong một tầng, chi phí cộng có trọng số.

| Tầng | Rule | Cách tính | Trọng số |
|---|---|---|---|
| 1 | **Check-in trễ hẹn** | Mỗi job check-in sau hạn cộng trọng số ưu tiên của job | 1 |
| 2 | **Hoàn tất quá hạn** | Mỗi job xong sau `complete_by` cộng 1 | 1 |
| 2 | **Xong sau giờ hết ca** | Mỗi job xong sau `shift_end` cộng 1 | 1 |
| 3 | Quãng đường | Tổng km | 1 / km |
| 3 | Số phút check-in trễ | Tổng phút trễ | 0,1 / phút (10 phút ≈ 1 km) |
| 3 | Thời gian di chuyển | Tổng phút đi | 0,05 / phút (20 phút ≈ 1 km) |
| 3 | Quay lại khu vực đã rời | Số lần | 2 / lần (≈ 2 km) |
| 3 | Job ưu tiên cao bị để muộn | Trọng số ưu tiên × số giờ từ lúc xuất phát tới check-in | 0,5 (job ưu tiên 1 muộn 1 giờ ≈ 2 km) |
| 3 | Giờ xong job cuối | Phút từ lúc xuất phát tới lúc xong job cuối | 0,01 / phút (100 phút ≈ 1 km) |

**Trọng số ưu tiên trong ngày:** ưu tiên 1 = 4, ưu tiên 2 = 3, ưu tiên 3 = 2, ưu tiên 4 = 1, không có ưu tiên = 1. **[GIẢ ĐỊNH]** (Q24)

Hệ quả cần xác nhận:
- Để trễ **một** job ưu tiên 1 (4 điểm) tệ hơn để trễ **ba** job ưu tiên 4 (3 điểm).
- **Không có số km nào đổi được một lần trễ hẹn**: tầng 1 luôn thắng tầng 3.

Xem và sửa không cần code: `python -m ktv_routing --print-rules > rules.json`, sửa file, chạy lại với `--rules rules.json`. Web demo hiển thị bảng rule đang dùng.

### 8.3 Rule cứng và rule tính giờ
- **Giữ thứ tự tuyến cũ** (mục 8.4).
- **Không check-in trước mốc hẹn đầu:** tới sớm thì chờ, thứ tự vẫn hợp lệ. **[GIẢ ĐỊNH]** (Q4)

### 8.4 Giữ tuyến cũ (Rule 4 trong tài liệu)
Tài liệu: *"AI đánh giá: có tạo ra tuyến tốt hơn không? Nếu có sinh tuyến mới, nếu không giữ nguyên"* và *"chỉ xây routing thêm, không phá"*. **[TÀI LIỆU]**

- Request có thể gửi `previous_sequence`: thứ tự routing đã gợi ý lần trước.
- **Mặc định (`IF_BETTER`):** giữ thứ tự các job cũ, chèn job mới vào chỗ tốt nhất. Chỉ đổi sang thứ tự hoàn toàn mới khi **tốt hơn ở tầng 1 hoặc tầng 2**, hoặc bằng ở hai tầng đó và **tầng 3 giảm ít nhất 1,0** (≈ 1 km). **[GIẢ ĐỊNH]** (Q17)
- `KEEP`: luôn giữ. `IGNORE`: luôn xếp lại từ đầu (dùng cho nút "Tối ưu lại").
- Kết quả ghi ở `previous_route`: `NONE` (không có tuyến cũ), `KEPT` (giữ), `CHANGED` (đã đổi).
- Ai lưu và gửi lại `previous_sequence`: frontend hay team data? (Q17)

### 8.5 Thuật toán
- Mỗi KTV giải riêng. Trạng thái = (tập job đã làm, job làm cuối).
- Mỗi trạng thái giữ các "nhãn": (giờ xong, tổng chi phí từng tầng). Nhãn nào **thua một nhãn khác ở mọi mặt** thì bỏ.
- Đi tiếp một job thì tính giờ tới, chờ mốc hẹn, giờ xong và chi phí từng rule. Job chưa đủ điều kiện rule cứng thì không được đi.
- Tuyến cuối có khóa nhỏ nhất theo tầng là kết quả, trả kèm chi phí từng rule (`score`) để giải thích.

Giới hạn và trường hợp đặc biệt: **[GIẢ ĐỊNH]** (Q25)
- **Tới 9 job/KTV:** giải chính xác (`sequence_source = OPTIMAL`).
- **Trên 9 job:** heuristic. Mỗi bước chọn job làm khóa tăng ít nhất, rồi cải thiện bằng đảo đoạn (2-opt, tối đa 2 vòng, tối đa 40 job). `sequence_source = HEURISTIC`, kèm cảnh báo.
- **Mỗi trạng thái giữ tối đa 32 nhãn;** vượt thì cắt, kết quả `APPROXIMATE`.
- **Bảng thời gian học theo giờ** (nghỉ trưa) làm "xong sớm hơn" không phải lúc nào cũng tốt hơn, nên quanh các mốc giờ kết quả chỉ gần tối ưu.
- **Không biết điểm xuất phát:** vẫn xếp theo km giữa các job, không tính giờ.
- **Hai thứ tự hòa tuyệt đối:** lấy thứ tự tìm thấy trước (ổn định giữa các lần chạy).

### 8.6 Ví dụ

KTV ở X lúc 13:00, ba job, thời gian đi và km cho sẵn:

| Job | Làm | Hạn |
|---|---|---|
| A: lấy thiết bị | 15 phút | – |
| B: lắp cho khách | 30 phút | hẹn 14:30–16:00 (check-in trước 16:00) |
| C: bảo trì | 20 phút | check-in trước 15:00 |

- X→A→B→C: C check-in 15:10, **trễ 1 job**, 9 km.
- **X→A→C→B:** C check-in 13:45 kịp; tới B 14:15, chờ tới 14:30; **0 job trễ**, 10 km.

Routing chọn **X→A→C→B**: tầng 1 (0 trễ) thắng dù đi thêm 1 km.

## 9. Đầu ra

Sheet "5. Đầu ra AI" **[TÀI LIỆU]** và trạng thái:

| Tài liệu yêu cầu | Hiện có |
|---|---|
| Danh sách công việc theo thứ tự, giờ đến dự kiến | `stops[]`: `sequence`, `eta`, `wait_minutes`, `finish_at` |
| Tuyến theo cụm (Cluster 1, 2, 3) | **Chưa có** (Q18) |
| Map: vị trí hiện tại, các điểm, khoảng cách | `start_location`, `stops[].location`, `leg_km` |
| Map: đường đi thực tế | **Chưa có**, chỉ nối thẳng các điểm (Q26) |
| Tổng quãng đường, tổng thời gian di chuyển, thời gian hoàn thành | `total_km`, `total_travel_minutes`, `finish_at` |
| Tỷ lệ đúng hẹn dự kiến | `summary.on_time_rate_percent` |
| Giải thích lý do đề xuất | `score` (chi phí từng rule), `sequence_source`, `previous_route`, `issues` |
| Gợi ý thay thế | **Chưa có** |

## 10. Kết quả kiểm chứng

Ngày 2026-09-14, dữ liệu 16–30/06 cả nước, chạy bằng `research/backtest_routing.py`.

**Cách thử:**
- Tọa độ job là tọa độ check-in thật.
- Hạn check-in theo giả định ở mục 3 (MAINTENANCE: tạo + 24 giờ).
- Thời gian lấy từ mô hình học (mục 6).
- Planner xếp và chấm bằng cùng một cấu hình.

**Đoán job KTV làm tiếp theo** (60.465 lần chọn, trung bình 4,2 job để chọn)

| Cách đoán | Đúng |
|---|---|
| Planner QHĐ | 36,6% |
| Planner tham lam cũ (hạn sớm nhất trước) | 27,2% |
| Job gần nhất | 44,8% |
| Chọn ngẫu nhiên | 31,2% |

**Xếp lại cả ngày** (25.296 KTV-ngày, 101.587 job)

| | Thứ tự KTV thật đã đi | Planner QHĐ | Gần nhất trước |
|---|---|---|---|
| Tổng km | 188.468 | **153.126 (−19%)** | 142.770 (−24%) |
| Job check-in trễ (mô phỏng) | 8.396 | **6.229 (−26%)** | 9.229 (+10%) |

**Nhận xét:**
- QHĐ vừa giảm km vừa giảm trễ hẹn. Thứ tự "gần nhất trước" ngắn hơn một chút nhưng trễ nhiều hơn cả thực tế.
- Số job trễ thật (check-in thật sau hạn 24 giờ) là 6.956. Mô phỏng thứ tự thật cho 8.396 vì ETA còn lệch: sai số trung bình 128 phút, chỉ 24% điểm sai trong ±30 phút. Con số trễ phụ thuộc giả định hạn (Q1) và thời gian (Q19, Q20).

**Tốc độ** (server 24 CPU, một tiến trình):
- Chi nhánh 100 KTV mất 11 ms; toàn quốc 3.400 KTV mất 167 ms.
- Một KTV 9 job: p50 66 ms, lâu nhất 111 ms.
- HNI_04 thật kèm OSRM tự host: 289 ms; 103/105 KTV giải chính xác, 2 KTV có 12 job dùng heuristic.
- Tất cả thấp hơn nhiều mục tiêu ≤ 5 giây.

**Kiểm chứng code:**
- 50 test tự động pass, trong đó có test so QHĐ với thử hết mọi thứ tự trên bài 5–6 job ngẫu nhiên (có hạn, ưu tiên, khu vực, mốc hẹn).
- Web demo chạy thử: bảng rule, giải thích điểm, tua realtime, không lỗi JS.

## 11. Câu hỏi cần xác nhận

**Loại tác vụ và hạn**
- **Q1.** Export chỉ có `MAINTENANCE`. Đó là bảo trì Vật lý (ưu tiên 1) hay Logic (ưu tiên 2)? Hạn check-in của nó tính từ đâu? Tạm dùng "tạo phiếu + 24 giờ" vì khớp `FLAG_ON_TIME` 90%.
- **Q2.** "Ngưng kết nối 4H" và "Mạng chập chờn, suy hao cao" có cần KTV tới tận nơi không? Hiện vẫn xếp tuyến.
- **Q3.** "Ngày hẹn" của Gsafe và Giao thiết bị Cam lấy ở đâu? Giao thiết bị Cam có SLA 60 phút nhưng yêu cầu "hoàn tất trong ngày hẹn": dùng mốc nào?
- **Q4.** Tới trước mốc hẹn đầu (A) thì KTV phải chờ tới A, hay được check-in sớm nếu khách đồng ý?
- **Q5.** Job đã quá hạn khi lập tuyến có nên được ưu tiên làm ngay không, hay nhường job còn cứu được?
- **Q6.** Hết ca thì routing ngừng xếp thêm job, hay vẫn xếp và chỉ cảnh báo như hiện tại?

**Dữ liệu**
- **Q7.** Một job xuất hiện ở hai KTV, hoặc một KTV bị gửi hai lần: giữ bản nào? Hay coi cả request là lỗi?
- **Q8.** Vị trí KTV cũ bao lâu thì không nên dùng (đang là 4 giờ)? Có nguồn GPS realtime cho mọi KTV không?
- **Q9.** Checklist trùng `CHECKLIST_ID` chỉ khác `SERVICES_LIST`: nên gộp danh sách dịch vụ, hay đó là 2 việc riêng?
- **Q10.** Khi đổi KTV (`APPOINTTIMES_ASSIGNED` ≥ 2), có lịch sử gán lại theo giờ không?
- **Q11.** Có lịch sử đổi status theo giờ không? Hiện job coi như mở tới `FINISH_DATE`, và "Đóng checklist" không có giờ đóng thì suy từ lượt check-in cuối.
- **Q12.** Nên coi job "đã làm xong" ở checkout cuối hay ở `FINISH_DATE`?
- **Q13.** 33% lượt check-in không có checkout: do app không bắt buộc checkout, hay do lỗi dữ liệu?
- **Q14.** Team data có tọa độ khách hàng thật không? Tâm phường đang lệch 1,6 km.
- **Q15.** Ca làm của từng KTV lấy ở đâu?
- **Q16.** Có sự kiện "khách đổi giờ" và "hủy công việc" trong hệ thống thật không? Ai gọi routing lúc 6h sáng?

**Thuật toán và đầu ra**
- **Q17.** Ai lưu và gửi lại thứ tự tuyến cũ? Ngưỡng "tốt hơn rõ" để đổi tuyến là bao nhiêu?
- **Q18.** "Cụm" nghĩa là gì: theo phường, theo bán kính, hay theo mã khu vực phụ trách của KTV? Routing có phải trả tuyến chia theo cụm không?
- **Q19.** Có bảng "thời gian xử lý chuẩn" theo loại tác vụ không? Dữ liệu cho thấy bảo trì median chỉ 13 phút, không phải 60.
- **Q20.** Thời gian đi có cần tính kẹt xe theo giờ không? Dùng nguồn nào?
- **Q21.** Nên tính quãng đường theo xe máy thay cho ô tô không?
- **Q22.** Có cần tính quãng đường quay về nhà hoặc văn phòng cuối ca không?
- **Q23.** Thứ tự tầng ở mục 8.2 có đúng không (đúng hẹn → hoàn tất đúng hạn và trong ca → phần còn lại)? Trọng số tầng 3 có hợp lý không?
- **Q24.** Trọng số ưu tiên 1–4 = 4, 3, 2, 1 có đúng tinh thần "ưu tiên trong ngày" không? Hay job ưu tiên 1 phải luôn được làm trước, bất kể hạn?
- **Q25.** Một KTV thường có tối đa bao nhiêu job một lúc? QHĐ chính xác tới 9 job; nhiều hơn dùng heuristic.
- **Q26.** App cần đường đi thực tế trên bản đồ (từng khúc đường) không? Nếu cần, routing gọi thêm OSRM `/route`.
