# Kế hoạch thuật toán: AI routing + tự cải thiện

Trạng thái: **đã chốt hướng** (2026-09-22). Chi tiết thuật toán từng phần sẽ được trình bày và duyệt trước khi code.

Tài liệu nguồn:
- `API-Goi-y-cong-viec.xlsx`: hợp đồng input/output của `POST /api/v1/worklist/suggest`.
- `data/Mô tả loại tác vụ- ưu tiên để Gợi ý công việc (1).xlsx`: loại tác vụ, SLA, rule, tiêu chí đánh giá.
- `docs/BUSINESS_RULES.md`: nghiệp vụ hiện tại của planner và các câu hỏi chờ xác nhận.

## 1. Bài toán theo API

- Mỗi lần gọi là **một nhân viên**, gồm các việc đã giao thuộc 5 nhóm: triển khai, bảo trì, thu hồi, hóa đơn, onsite.
- Dữ liệu thật: mỗi KTV trung bình khoảng 4–10 việc/ngày.
- Output:
  - thứ tự việc, chia thành cụm theo khu vực;
  - các dòng lịch: TASK, BREAK (nghỉ), IDLE (chờ tới giờ hẹn);
  - `projected_sla` cho từng việc;
  - `metrics` cho cả ngày.
- Thời gian sinh tuyến ≤ 5 giây.

Về mặt toán, đây là bài **một người đi qua nhiều điểm, mỗi điểm có khung giờ**, cỡ nhỏ. QHĐ hiện tại giải chính xác tới 9 việc (p50 khoảng 66 ms). Phần "xếp thứ tự" vì thế không phải điểm nghẽn.

Backtest 16–30/06 cho thấy cái làm hỏng tuyến nằm ở hai chỗ khác:
- **Ước lượng thời gian sai.** Với config mặc định, ETA lệch trung bình −78 phút; với mô hình học từ lịch sử, còn −1 phút.
- **Chưa biết KTV thực sự ưu tiên điều gì.** KTV chọn đúng "việc gần nhất" 44,8% số lần, còn planner chỉ khớp 36,6%.

## 2. Quyết định

**Lõi tối ưu đơn giản, giải thích được, cộng AI ở hai chỗ: dự đoán thời gian và tự chỉnh trọng số rule.**

| Phương án | Quyết định | Lý do |
|---|---|---|
| Lõi QHĐ + rule theo tầng (đang có) | **Dùng** | Chính xác với cỡ bài toán này, nhanh, nghiệp vụ đọc và chỉnh được qua file rule |
| OR-Tools / HGS / ALNS | Chỉ làm **dự phòng** khi một KTV có quá nhiều việc cho QHĐ | Mỗi lần gọi chỉ 1 người và dưới 15 việc nên gần như không hơn QHĐ. Xem lại khi routing phải gán việc cho nhiều KTV cùng lúc |
| Học tăng cường / bộ giải neural | **Không dùng** | Cần môi trường mô phỏng đủ tốt, khó giải thích, dữ liệu chưa đủ |
| ML dự đoán thời gian | **Dùng** (thành phần B) | Đây là nguồn sai số lớn nhất của ETA và `projected_sla` |
| Học trọng số từ kết quả thật | **Dùng** (thành phần C) | Có cơ sở: Amazon Last Mile Challenge, inverse optimization |

## 3. Kiến trúc

```text
                 ┌──── B. AI dự đoán ─────────────────────┐
input API ──▶   │ thời gian xử lý, thời gian di chuyển   │ ──▶ A. QHĐ theo rule ──▶ chia cụm ──▶ output API
                 │ + khoảng dao động (cho AT_RISK)         │          ▲ trọng số (file rule, có phiên bản)
                 └─────────────────────────────────────────┘          │
log gợi ý + kết quả thật ──▶ C. học lại hằng đêm ──▶ backtest kiểm tra ──▶ phát hành phiên bản config
```

Lúc chạy, AI không tự đưa ra quyết định. Bộ tối ưu đưa ra quyết định dựa trên số do mô hình dự đoán, và trọng số của phiên bản config đang phát hành. Nhờ vậy mọi tuyến đều truy được "vì sao" và "phiên bản nào".

## 4. Thành phần A: lõi tối ưu

Giữ QHĐ hiện tại và mở rộng cho đủ API.

| Yêu cầu API | Hướng xử lý (trình bày chi tiết trước khi code) |
|---|---|
| 5 nhóm việc, SLA theo `task_type` | Bảng loại tác vụ quy ra mốc hẹn đầu A, hạn check-in B (= A + `sla_minutes`), và hạn hoàn tất (trong ngày hẹn / ngày tạo phiếu / trong tháng) |
| `current_task` khóa đầu tuyến | Đã có (`_resolve_start`); cần thêm dữ liệu, xem mục 8 |
| Có `appointment`: tới sớm thì chờ | Đã có (`wait_minutes`) → sinh dòng IDLE |
| Nghỉ trưa (BREAK) | Coi như một "việc ảo" không có vị trí, nằm trong một khung giờ; QHĐ tự chọn chèn vào chỗ nào |
| `available` có nhiều khung giờ, gồm OT | Việc không được bắt đầu vào khoảng nghỉ giữa hai khung; dùng OT là một rule mềm có phạt |
| Không vượt giờ kết thúc ca | Đã có (AFTER_SHIFT); phần vượt ghi vào `overload_minutes` |
| Chia cụm, `revisit_count` | Xếp tuyến trước rồi cắt thành các đoạn liên tiếp theo địa bàn/khoảng cách. Rule AREA_REENTRY trong QHĐ đã phạt việc quay lại khu vực |
| Địa bàn chính ưu tiên hơn địa bàn hỗ trợ | Thêm rule mềm ở tầng thấp |
| Rule 4: chỉ đổi tuyến khi tốt hơn | Đã có (`previous_sequence`, policy IF_BETTER); API cần thêm field, xem mục 8 |
| `projected_sla` | So giờ check-in dự kiến với hạn, dùng khoảng dao động từ thành phần B |
| ≤ 5 giây | Hiện chưa tới 0,3 giây mỗi KTV |

## 5. Thành phần B: AI dự đoán thời gian

Cách làm giống Uber DeepETA: **giữ bộ tính đường vật lý, dùng ML học phần sai lệch.**

- **Thời gian di chuyển** = OSRM (hoặc đường chim bay) + phần sai lệch do mô hình học. Phần sai lệch học theo: km, giờ rời đi, khu vực, KTV. Nó đã gồm cả thời gian gửi xe, chờ khách, tắc đường.
- **Thời gian xử lý** (`handle_minutes` khi input bỏ trống): học theo loại tác vụ × KTV × giờ trong ngày × số lần tới lại.
- **Khoảng dao động:** học theo quantile (ví dụ P50 và P80), không chỉ một con số. Việc là AT_RISK khi giờ check-in ở mức P80 vượt hạn. Cách này thay ngưỡng cứng "đệm dưới 20 phút" bằng mức rủi ro đo được.
- **Mô hình:** bắt đầu bằng gradient boosting. Bảng median hiện có (`research/time_model.py`, đã xóa 2026-09-30 — còn trong tag `python-legacy-2026-09-30`) là mốc so sánh. Chỉ dùng deep learning khi dữ liệu và kết quả cho thấy đáng làm.
- **Đo:** MAE và độ lệch trung bình của ETA theo từng bước trong tuyến, và tỷ lệ AT_RISK dự báo so với trễ thật.

## 6. Thành phần C: tự cải thiện

### 6.1 Vòng lặp

1. **Log mỗi lần gọi API:** `trace_id`, input, output, phiên bản config và mô hình.
2. **Thu kết quả thật:** giờ check-in/check-out từng việc, thứ tự KTV thực tế làm, KTV có bấm "Tối ưu lại" không.
3. **Hằng đêm:** học lại mô hình thời gian (B) và dò lại trọng số rule (6.2).
4. **Kiểm tra:** chạy backtest trên dữ liệu gần nhất. Chỉ đạt khi **không tăng số việc trễ SLA** và không làm tệ các chỉ số chính ở mục 7.
5. **Phát hành:** chạy song song hoặc A/B ở vài chi nhánh trước, rồi mới áp dụng rộng. Mọi phiên bản đều quay lui được.

### 6.2 Học trọng số

- **Phương pháp:** inverse optimization, tức từ các tuyến người thật đã chọn, học ngược ra trọng số của hàm chi phí.
- **Cơ sở:**
  - Trong Amazon Last Mile Routing Challenge 2021, phương pháp này đứng thứ 2/48, và vẫn giữ hạng khi chỉ dùng 20% dữ liệu.
  - Đội thắng cuộc cũng dùng bộ giải tối ưu cổ điển, cộng phần học từ tài xế.
- **Kết quả học được** chính là file rule (`rules.py` / JSON), nên nghiệp vụ đọc và duyệt được.
- **Bắt đầu đơn giản:** dò trọng số bằng backtest, chọn bộ trọng số khớp hành vi tốt nhất mà không tăng trễ SLA. Chuyển sang thuật toán inverse optimization đầy đủ khi cần.

### 6.3 Giới hạn bắt buộc

**Không học theo KTV một cách mù quáng.** Thứ tự KTV thật đã làm có 8.396 job check-in trễ, còn planner giảm còn 6.229 (−26%). Học nguyên hành vi thì sẽ học luôn cả thói quen gây trễ.

- **Nghiệp vụ khóa, máy không được sửa:** ràng buộc cứng, và thứ tự các tầng (đúng hẹn luôn đứng đầu).
- **Máy được học:** trọng số trong tầng thấp, gồm km, thời gian đi, quay lại khu vực, địa bàn chính/phụ, gom cụm. Đây là những thứ KTV biết mà bản đồ không biết: đường khó đi, chỗ gửi xe, khách hay vắng nhà giờ nào.

## 7. Đo lường

Dùng chung cho backtest, kiểm tra trước khi phát hành, và theo dõi khi đã chạy thật.

| Chỉ số | Nguồn | Mục tiêu |
|---|---|---|
| Tỷ lệ việc đúng SLA (thật, không phải dự báo) | Tiêu chí đánh giá AI | Cao nhất; là điều kiện chặn khi phát hành |
| Tổng km, tổng thời gian di chuyển | Tiêu chí đánh giá AI | Thấp nhất |
| Số cụm, số lần quay lại khu vực | Tiêu chí đánh giá AI | Thấp nhất |
| Số việc hoàn thành trong ca | Tiêu chí đánh giá AI | Cao nhất |
| Thời gian chờ giữa các việc | Tiêu chí đánh giá AI | Thấp nhất |
| Thời gian sinh tuyến | Tiêu chí đánh giá AI | ≤ 5 giây |
| Sai số ETA (MAE, độ lệch) theo từng bước | Thêm | Giảm dần qua các phiên bản |
| Độ tin của AT_RISK (dự báo so với trễ thật) | Thêm | Khớp |
| Tỷ lệ KTV làm theo gợi ý | Thêm | Tăng. Nếu giảm, gợi ý đang thiếu thông tin |

## 8. Rà soát dữ liệu đầu vào

### 8.1 Boundary (lô/địa bàn): không cần tọa độ, với điều kiện

Routing tính khoảng cách từ `latlng` của từng việc. Địa bàn chỉ dùng để:
- chấm điểm địa bàn chính/phụ;
- đếm số lần quay lại khu vực;
- đặt tên cụm.

Cả ba việc này chỉ cần **mã địa bàn và tên**. `center` và `radius_m` của cụm tính từ tọa độ các việc trong cụm. Đa giác boundary chỉ cần khi:
1. việc không có `task_plots_id`, phải suy ra địa bàn từ tọa độ; hoặc
2. vẽ ranh giới lên bản đồ. Việc này thuộc frontend, không thuộc routing.

**Điều kiện** để không cần boundary:
- Mọi việc có `task_plots_id` đúng.
- Có **tên địa bàn của việc**. Hiện chỉ có tên cho địa bàn của nhân viên (`staff.plots[]`). Trong JSON mẫu, việc thuộc địa bàn 4, 5, 7, không nằm trong `staff.plots` (2, 3), nên không có tên để đặt cho cụm.

### 8.2 Dữ liệu còn thiếu hoặc chưa rõ

**Mức A: chặn thiết kế.** Phải có trước khi code.

| # | Dữ liệu | Vấn đề | Đề xuất |
|---|---|---|---|
| A1 | Tuyến lần trước | Rule 4 "chỉ đổi khi tốt hơn" và lịch "mỗi 30–60 phút chỉ kiểm tra" cần tuyến cũ để so, nhưng input không có | Thêm `previous_sequence: [task_id]`, hoặc để server lưu tuyến theo `staff_id` (khi đó routing không còn stateless) |
| A2 | `current_task` thiếu vị trí, giờ bắt đầu, thời lượng | Không tính được khi nào KTV rảnh và xuất phát từ đâu | Thêm `latlng`, `checkin_at` (giờ bắt đầu), `handle_minutes` |
| A3 | Hạn của việc không theo phút | "Trong ngày tạo phiếu" và "trong tháng" cần ngày tạo phiếu, nhưng input không có | Thêm `created_at`. Tốt nhất là team nguồn tự tính và gửi thẳng hạn check-in (B) và hạn hoàn tất |
| A4 | `appointment` chỉ có một mốc | Bảng tác vụ ghi "mốc hẹn A→B, 120 phút", tức B = A + `sla_minutes`. Cần xác nhận. Ghi chú "lấy xanh đỏ theo KPI" chưa rõ nghĩa | Xác nhận công thức B; giải thích "xanh đỏ" |
| A5 | "Phải xếp đúng khung giờ" là ràng buộc cứng | Nếu không thể kịp mọi hẹn thì trả lỗi, hay vẫn xếp và báo `WILL_BREACH`? | Đề xuất: vẫn xếp, báo trễ. Hiện planner xử lý như rule mềm ở tầng cao nhất |
| A6 | Nghỉ trưa | Output có BREAK 45 phút nhưng input không nói nghỉ lúc nào, bao lâu, cố định hay linh hoạt | Thêm vào `staff` (vd `break: "11:30-13:30,45"`) hoặc chốt một cấu hình chung |
| A7 | Dữ liệu lịch sử của 4 nhóm còn lại | Mô hình thời gian (B) và học trọng số (C) mới chỉ có dữ liệu MAINTENANCE | Export check-in/check-out lịch sử của triển khai, thu hồi, hóa đơn, onsite |
| A8 | Luồng kết quả thật | Thành phần C cần giờ check-in/out thật và thứ tự thật, gắn với `trace_id` | Team nguồn cung cấp feed hoặc export định kỳ |

**Mức B: mâu thuẫn trong spec.**

| # | Chỗ mâu thuẫn |
|---|---|
| B1 | Quy tắc nhắc `staff.end_at`, input không có field này mà chỉ có `available`. Mẫu ghi `shift_end_at` 17:00 trong khi `available` là 08:00–17:30 |
| B2 | `task_type_id` trùng giữa các nhóm (`bao_tri_vat_ly` = 1, `thu_hoi_thiet_bi` = 1), nên khóa phải là cặp (`task_group_id`, `task_type_id`). Spec cũng ghi "cần chốt bảng số" |
| B3 | Loại con ghi đè SLA: mẫu có `trien_khai_net` + sub `gsafe` với `sla_minutes` 120, nhưng danh mục ghi gsafe là "trong ngày". Loại con có đổi SLA không? |
| B4 | `task_plots_id`, `staff_plots_id`, `staff_role` (ở việc) và `plots[].role` (ở nhân viên) trùng ý nghĩa. Dùng field nào để xác định "địa bàn chính"? |
| B5 | Mọi việc trong mẫu có trạng thái `check_in` (6). Danh mục trạng thái của việc chưa tới làm là gì? |
| B6 | `location_id` có trong JSON mẫu nhưng không có trong bảng field |
| B7 | `available` gồm ca chính và OT nhưng không đánh dấu khung nào là OT. Dùng OT khi nào? |

**Mức C: nên có, giúp tuyến chính xác hơn.**

| # | Dữ liệu | Lợi ích |
|---|---|---|
| C1 | Thời điểm ghi nhận GPS của nhân viên | Biết vị trí đã cũ (hiện planner dùng ngưỡng 240 phút) |
| C2 | Độ tin của `latlng` việc (GPS thật hay geocode theo phường) | Dữ liệu hiện tại nhiều việc chung tâm phường, nên km giữa các việc trong cùng phường bằng 0 |
| C3 | Phương tiện (xe máy?) | OSRM đang dùng profile ô tô |
| C4 | Tuyến đường để vẽ lên bản đồ | File mô tả có "tuyến đường trên bản đồ (MBX)" nhưng output API không có geometry. Cần chốt ai vẽ: routing trả polyline, hay app gọi MBX |

## 9. Lộ trình

1. **Đáp ứng đúng API.** Chốt mục 8 mức A, B; mở rộng lõi A (mục 4); adapter đổi input/output API sang contract hiện có. Thuật toán trình bày trước khi code.
2. **Mô hình thời gian ML** + AT_RISK theo khoảng dao động (mục 5), đo bằng backtest.
3. **Log + vòng tự cải thiện** (mục 6): dò trọng số có kiểm tra, rồi chạy song song hoặc A/B ở vài chi nhánh.

## Nguồn

- [Inverse Optimization for Routing Problems (arXiv 2307.07357)](https://arxiv.org/html/2307.07357v3)
- [Learning from Drivers to Tackle the Amazon Last Mile Routing Research Challenge (arXiv 2205.04001)](https://arxiv.org/pdf/2205.04001)
- [Amazon Science: Winning Last Mile Challenge team](https://www.amazon.science/academic-engagements/winning-last-mile-challenge-team-addresses-problem-of-combining-mathematical-routes-with-driver-knowledge)
- [MIT News: Last Mile Routing Research Challenge winners](https://news.mit.edu/2021/last-mile-routing-research-challenge-three-winning-teams-0824)
- [Uber: DeepETA](https://www.uber.com/in/en/blog/deepeta-how-uber-predicts-arrival-times/)
- [DeeprETA: An ETA Post-processing System at Scale (arXiv 2206.02127)](https://arxiv.org/pdf/2206.02127)
- [Deep RL for dynamic time slot assignment in after-sales service (arXiv 2509.17870)](https://arxiv.org/pdf/2509.17870)
