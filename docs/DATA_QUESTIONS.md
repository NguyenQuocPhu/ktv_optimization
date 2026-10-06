# Sổ câu hỏi dữ liệu cho team data / Optimal Assign

Team data khó liên lạc thường xuyên và mô tả dữ liệu chưa bao quát hết. Để staging/prod vẫn chạy, parser ở worker/gateway/`ktv_core plan` chạy **chế độ nới lỏng**: chỉ trả `400` khi không thể xếp tuyến; mọi chỗ lệch hợp đồng khác vẫn xếp, theo giả định bên dưới, và được ghi thành **cảnh báo có mã**. File này là danh sách câu hỏi để mang đi hỏi, kèm cách đang xử lý tạm.

Cảnh báo **không** nằm trong response OUT (OUT contract đang tạm hoãn, và OA có thể cũng parse strict). Xem ở:

| Chỗ | Cách xem |
|---|---|
| `ktv_worker` đang chạy | `GET /healthz` → `data_issues`: mỗi loại cảnh báo + số lần gặp (tối đa 200 loại, còn lại dồn vào `(loại khác)`) |
| Log worker | Dòng `cảnh báo dữ liệu mới (<message_id>): ...` in **một lần** cho mỗi loại mới, kèm `message_id` để tra lại message gốc; mỗi message có `cảnh_báo=N` |
| File dữ liệu mẫu | `ktv_core plan file.jsonl` in bảng cảnh báo (nhiều nhất trước) ở cuối |
| Soát toàn bộ lệch hợp đồng | `ktv_core validate file.jsonl` chạy **strict**: liệt kê mọi lỗi như trước |

Khi gặp team data: mở `/healthz`, lấy các loại gặp nhiều nhất, hỏi đúng các câu dưới. Có câu trả lời thì sửa catalog/rule, cập nhật mục "Nhật ký trả lời" và (nếu hết lệch) cảnh báo tự biến mất.

## Mã cảnh báo

| Mã | Khi nào | Đang xử lý tạm [GIẢ ĐỊNH] | Câu hỏi cho team data | Trạng thái |
|---|---|---|---|---|
| `UNKNOWN_FIELD` | Field không có trong file API (ở gốc, `staff`, lô, `current_task`, task, `sla`) hoặc nhóm task lạ trong `tasks` | Bỏ qua field | Field này là gì, có cần dùng khi xếp tuyến không? Khi nào thêm field mới thì báo trước được không? | mở |
| `TASK_GROUPS` | `tasks` thiếu một trong 5 nhóm | Coi nhóm đó là `[]` | Payload có luôn gửi đủ 5 nhóm không? | mở |
| `STAFF_ROLE` | `staff_role` ngoài 0–3 hoặc thiếu (**0 hợp lệ** từ 2026-10-02: workbook (3) "0 default" = không khớp lô nào của KTV) | Giữ nguyên số, vẫn xếp task (`staff_role` không dùng khi xếp tuyến) | Giá trị ngoài 0–3 nghĩa là gì? | mở |
| `CREATE_DATE_CONFLICT` | Gửi cả `create_date` lẫn `CreateDate` mà khác giá trị (hai tên là một field, tên nào cũng nhận) | Dùng `create_date` | Tên field chính thức là gì? Vì sao gửi cả hai? | mở |
| `CATALOG_MISMATCH` | `task_type_id` hoặc SLA/ưu tiên của task khác bảng sheet 05 | Dùng giá trị **input** (SLA, ưu tiên); định mức thời gian và kiểu hạn vẫn theo tên loại trong danh mục | Bảng số `task_type_id` + SLA chính thức là gì? Input hay danh mục là nguồn đúng? | mở |
| `UNKNOWN_TASK_TYPE` | Tên loại việc không có trong danh mục sheet 05 (cảnh báo ghi `nhóm/tên`) | Vẫn xếp: SLA/ưu tiên theo input, xử lý **60 phút** nếu input không có `handle_minutes`, **không** có hạn hoàn tất theo loại (chỉ hạn check-in nếu có hẹn + SLA) | Loại này thuộc dòng nào của sheet 05 (yêu cầu đúng hẹn, định mức thời gian)? Còn thiếu: `thu_hoi`, `hoa_don`, `onsite`, subtype `trien_khai_box` | mở |
| `TASK_GROUP` | Task nằm trong nhóm A nhưng `task_group_name`/`task_group_id` ghi nhóm khác | Vẫn xếp; **dùng nhóm chứa task** (sửa `task_group_name`/`task_group_id` theo nhóm đó) để tra danh mục | Nhóm nào đúng? | mở |
| `TASK_TYPE_OTHER_GROUP` | Loại việc không có trong nhóm của task nhưng có ở nhóm khác — VD `ngung_ket_noi_4h`, `chap_chon_suy_hao` gửi dưới `onsite` trong khi workbook (4) đã chuyển sang `cscd` | Dùng luật của nhóm kia (tra dự phòng, Phase 7.12) | OA đã chuyển hai loại CSKH sang khóa `cscd` chưa? Khi hết cảnh báo này có thể bỏ phần tra dự phòng | mở |
| `TASK_DROPPED` | Một task hỏng: không phải object, thiếu/sai kiểu field bắt buộc, tọa độ/ngày sai định dạng, `sla` hỏng, trùng `task_id` (bản sau) | **Bỏ riêng task đó**, các task khác của KTV vẫn xếp. Cảnh báo ghi field lỗi đầu tiên; task bị bỏ không sinh thêm cảnh báo khác | Vì sao task có dữ liệu này? Có sửa được ở nguồn không? | mở |
| `TASK_STATUS_UNKNOWN` | (nhóm, `task_status_id`) không có trong bảng sheet 05 (toàn bộ `hoa_don`/`onsite`, mã lạ ở nhóm khác) và `task_status_name` không cho thấy đã xong/hủy (kể cả tên rỗng) | Coi là **còn mở → xếp** (dòng "khác" của workbook: chỉ xếp việc còn mở; bỏ sót việc khó phát hiện hơn xếp thừa) | Mã này nghĩa là gì, còn mở hay đã xong/hủy? Gửi bảng trạng thái của `hoa_don`, `onsite` | mở |
| `TASK_STATUS_UNKNOWN_CLOSED` | Mã không có trong bảng nhưng `task_status_name` mang nghĩa đã xong/hủy ("Đã hủy", "Hoàn tất", "Đóng checklist", "Đã thu"…; có "chưa" thì không tính) | **Không xếp** | Xác nhận mã này đúng là đã xong/hủy | mở |
| `TASK_STATUS_UNASSIGNED` | Trạng thái "Chưa phân công" (`trien_khai` 99, `bao_tri` 2) vẫn được gửi sang | **Không xếp** (workbook: chưa phân công thì không đẩy qua AI) | Vì sao việc chưa phân công vẫn nằm trong message của KTV? | mở |
| `CURRENT_NOT_MATCHED` | Trạng thái "đang làm" (`trien_khai` 0, `bao_tri` 10) nhưng `task_id` không trùng `staff.current_task` | **Không xếp**; giờ xuất phát không bị đẩy thêm | `staff.current_task` có luôn được gửi khi KTV đang làm một việc không? Một KTV có thể đang làm hai việc? | mở |
| `STAFF_STATUS` | `staff.status` không phải số nguyên 1/2/3 | `"3"`, `3.0` → hiểu là số đó. Giá trị khác (`9`, `"off"`, `null`…) → **không xếp tuyến**, `422` "Trạng thái KTV không rõ" (xếp nhầm cho người đang nghỉ tệ hơn bỏ sót một lần) | Các giá trị status của KTV? Gửi dạng số hay chuỗi? | mở |
| `STAFF_PLOTS` | `plots` thiếu/rỗng, một lô hỏng, hoặc không đúng một lô chính | Bỏ lô hỏng; tên cụm lùi về `Lô <id>` / "Khu vực chưa xác định" | KTV có thể không có lô / nhiều lô chính không? | mở |
| `CURRENT_TASK` | `current_task` thiếu field hoặc hỏng | Coi như không có việc đang làm (xuất phát lúc lập tuyến thay vì +30 phút) | Khi KTV không làm gì, gửi `null` đúng không? | mở |
| `PLANNED_AT` | `planned_at` sai định dạng | Bỏ, lập tuyến theo giờ xử lý | OA có gửi `planned_at` không, định dạng nào? | mở |

Vẫn trả `400` (không xếp được): JSON hỏng / không phải object; thiếu hoặc hỏng `staff`, `staff.staff_id`, `staff.staff_account`, `staff.latlng`, `staff.available`; `tasks` không phải object.

## Câu hỏi mở khác (chưa có mã cảnh báo)

- Envelope thật của OA: `message_id`, `planned_at`, `trigger` nằm trong body hay header Kafka? Key message là gì? (Worker đang log header để soi khi có message đầu tiên.)
- ~~Bảng trạng thái của `hoa_don` và `onsite`~~ — đã có trong workbook (4) (xem Nhật ký). Còn: bảng trạng thái của nhóm `cscd` (workbook (4) thêm nhóm 6 nhưng chưa có bảng); mã lạ vẫn đoán theo `task_status_name`.
- Nhóm `cscd` (workbook (4) nhóm 6): tên khóa đúng là `cscd`? Danh sách loại việc và bảng trạng thái của `cscd`? (Đang tạm: 2 loại `ngung_ket_noi_4h`, `chap_chon_suy_hao`; trạng thái đoán theo tên.)
- `onsite`: workbook (4) cùng một dòng ghi cả "Hoàn tất trong ngày hẹn" lẫn "rule như bao_tri" (check-in trước mốc B). Đang theo "như bao_tri" (Phase 7.15) — xác nhận? Hai loại `ngung_ket_noi_4h`, `chap_chon_suy_hao` (CSKH chủ động) còn thuộc `onsite` không, hay đã sang `cscd`?
- `complete_date`: từ 7.17 dùng cho `hoa_don` (mục E). Còn hỏi: có nhóm nào vẫn dùng `complete_date` theo nghĩa cũ "task này đã xong" mà không đổi `task_status_id` không? Nhóm khác (`thu_hoi`…) gửi `complete_date` thì nghĩa là gì (hiện bỏ qua)?
- Việc đang làm: OA gửi được **giờ check-in** của `staff.current_task` không? Có thì tính được thời gian còn lại (định mức − đã làm) thay vì luôn 30 phút (IMPLEMENTATION_SPEC 7.7–7.10, ghi chú việc đang làm).
- OA có gửi message IN mới cho KTV **mỗi khi task đổi trạng thái** (check-in, hoàn tất, gán thêm, đổi hẹn) không, hay chỉ khi gán việc? Route (worker lẫn `replan`) chỉ biết danh sách việc trong IN mới nhất: nếu OA không gửi lại, việc KTV đã làm xong vẫn nằm trong tuyến tới IN kế tiếp. (Đã chốt 2026-10-01: IN mới thì worker tính luôn — IMPLEMENTATION_SPEC Phase 7.)
- Kafka timestamp của topic IN là `CreateTime` (giờ OA gửi) hay `LogAppendTime`? Version của state dùng timestamp này (Phase 7.3).
- **Danh sách ngày lễ** (7.16.1): "số ngày làm việc còn lại" tính theo T2–T6 **trừ ngày lễ** (catalogue, tham số #2), nhưng chưa có danh sách lễ → hiện tính bỏ qua lễ (`rules.holidays` để rỗng). Cần lễ chính thức để `days_left` (lọc K, urgency) khỏi lệch quanh lễ.
- **Thu bill trúng ngày thanh toán tháng trước** (catalogue mục E, làm ở 7.17): AI đọc `complete_date` của `hoa_don` là ngày KH thanh toán kỳ trước, kích hoạt khi `complete_date` + 1 tháng == ngày chạy (đúng **tháng trước**, cùng ngày-trong-tháng; 7.17b). [GIẢ ĐỊNH] KH thanh toán nhiều lần → OA gửi lần gần nhất (AI chỉ nhận một ngày). Xác nhận: OA có gửi đúng ngày này vào `complete_date` không, hay sẽ có cờ `denHanHomNay` riêng?
- **Ca tồn từ tháng trước** (thu hồi / thu bill, IMPLEMENTATION_SPEC 7.17): ca tạo tháng trước chưa làm xong, sang tháng này có tính là đã trễ không? Hiện (người dùng chốt tạm giữ 2026-10-06): hạn = cuối tháng của `create_date` → `ALREADY_BREACHED`, luôn chèn và gấp nhất. Nếu không tính trễ → đổi hạn về cuối tháng của ngày chạy.
- Các câu nghiệp vụ Q1–Q26 ở [BUSINESS_RULES.md](BUSINESS_RULES.md) mục 11.

## Nhật ký trả lời

| Ngày | Câu | Trả lời | Đã đổi gì |
|---|---|---|---|
| 2026-10-02 | `staff_role = 0` | Workbook API (3) sheet 02: "0 default / 1 chính / 2 kiêm nhiệm / 3 hỗ trợ" | 0 hợp lệ, hết cảnh báo (Phase 7.10) |
| 2026-10-02 | `staff_plots_id = 0` | Sheet 02: "không có default 0, tính ưu tiên xuống `block_id`" | Task lô 0 lùi xuống block trong rule quay lại khu vực |
| 2026-10-02 | Ý nghĩa `task_status_id` | Sheet 05: bảng trạng thái theo nhóm `trien_khai`/`bao_tri`/`thu_hoi` (chưa có `hoa_don`/`onsite`) | Lọc theo (nhóm, status), Phase 7.7 |
| 2026-10-02 | `complete_date` | Sheet 02: "ngày hoàn tất ca vụ trước đó (ngày thu bill trước)" | Không còn loại task, Phase 7.8 |
| 2026-10-06 | `complete_date` cho thu bill | Người dùng chốt: áp mục E cho `hoa_don`, `complete_date` + 1 tháng == ngày chạy (7.17b; bản đầu chỉ so ngày-trong-tháng); `thu_hoi` không áp | Hạn thu bill = cuối hôm nay khi trùng ngày, Phase 7.17 |
| 2026-10-02 | Tên `create_date` | Sheet 02 ghi `CreateDate` | Nhận cả hai tên, Phase 7.10 |
| 2026-10-02 | Nhóm `cscd` | Workbook (4) sheet 05: nhóm 6 "CSKH chủ động" | Nhận `cscd` (không bắt buộc), 2 loại CSKH chuyển từ `onsite`, tra dự phòng — Phase 7.12 |
| 2026-10-02 | Luật hạn của `onsite` | Workbook (4) sheet 05 nhóm 5: "rule như bao_tri" (SLA 60, P2) | `phieu_onsite` → check-in trước B, Phase 7.15 |
| 2026-10-02 | Trạng thái `hoa_don` / `onsite` | Workbook (4) sheet 05: `hoa_don` 0 chưa thanh toán / 1 đã thanh toán; `onsite` 0 chưa xử lý / 10 đang xử lý / 1 đã hoàn tất | Thêm vào bảng trạng thái, Phase 7.11 |
| 2026-10-02 | Tên topic IN | Sheet 00: prod `inside-par-…`, staging `stag-inside-par-…` | `.env.example`, README |
