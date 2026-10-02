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
| `STAFF_ROLE` | `staff_role` không phải 1/2/3 (thực tế đã gặp **0**) hoặc thiếu | Giữ nguyên số, vẫn xếp task (`staff_role` không dùng khi xếp tuyến) | Role 0 nghĩa là gì? Mô tả cũ: `staff_plots_id = 0 && staff_role = 0` = KTV không thuộc lô của task — đúng không, có cần xử lý khác (VD không xếp, hay ưu tiên thấp) không? | mở |
| `CATALOG_MISMATCH` | `task_type_id` hoặc SLA/ưu tiên của task khác bảng sheet 05 | Dùng giá trị **input** (SLA, ưu tiên); định mức thời gian và kiểu hạn vẫn theo tên loại trong danh mục | Bảng số `task_type_id` + SLA chính thức là gì? Input hay danh mục là nguồn đúng? | mở |
| `UNKNOWN_TASK_TYPE` | Tên loại việc không có trong danh mục sheet 05 (cảnh báo ghi `nhóm/tên`) | Vẫn xếp: SLA/ưu tiên theo input, xử lý **60 phút** nếu input không có `handle_minutes`, **không** có hạn hoàn tất theo loại (chỉ hạn check-in nếu có hẹn + SLA) | Loại này thuộc dòng nào của sheet 05 (yêu cầu đúng hẹn, định mức thời gian)? Còn thiếu: `thu_hoi`, `hoa_don`, `onsite`, subtype `trien_khai_box` | mở |
| `TASK_GROUP` | Task nằm trong nhóm A nhưng `task_group_name`/`task_group_id` ghi nhóm khác | Vẫn xếp; **dùng nhóm chứa task** (sửa `task_group_name`/`task_group_id` theo nhóm đó) để tra danh mục | Nhóm nào đúng? | mở |
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
- Bảng trạng thái của `hoa_don` và `onsite` (workbook (3) sheet 05 chưa có): mã nào là còn mở / đã xong / đã hủy? Hiện mã lạ được đoán theo `task_status_name` (mã `TASK_STATUS_UNKNOWN*`). Staging đang gửi `task_status_name` rỗng → không đoán được, đều xếp.
- Việc đang làm: OA gửi được **giờ check-in** của `staff.current_task` không? Có thì tính được thời gian còn lại (định mức − đã làm) thay vì luôn 30 phút (IMPLEMENTATION_SPEC 7.7–7.10, ghi chú việc đang làm).
- OA có gửi message IN mới cho KTV **mỗi khi task đổi trạng thái** (check-in, hoàn tất, gán thêm, đổi hẹn) không, hay chỉ khi gán việc? Route (worker lẫn `replan`) chỉ biết danh sách việc trong IN mới nhất: nếu OA không gửi lại, việc KTV đã làm xong vẫn nằm trong tuyến tới IN kế tiếp. (Đã chốt 2026-10-01: IN mới thì worker tính luôn — IMPLEMENTATION_SPEC Phase 7.)
- Kafka timestamp của topic IN là `CreateTime` (giờ OA gửi) hay `LogAppendTime`? Version của state dùng timestamp này (Phase 7.3).
- Các câu nghiệp vụ Q1–Q26 ở [BUSINESS_RULES.md](BUSINESS_RULES.md) mục 11.

## Nhật ký trả lời

| Ngày | Câu | Trả lời | Đã đổi gì |
|---|---|---|---|
| | | | |
