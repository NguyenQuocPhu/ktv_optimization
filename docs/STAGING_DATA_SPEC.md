# Data staging và plan refactor Core AI

> Nguồn chuẩn: `data/sample/Data staging.txt` (payload thật staging) +
> `API-Goi-y-cong-viec (2).xlsx` (contract mới, thay bản cũ đã xóa).
> Follow payload `data/sample/Data staging.txt` và API workbook bản mới. Các mapping chưa
> được nguồn xác nhận dùng giả định prototype bên dưới; nếu staging đổi, sửa adapter/mapping.

## 1. Staff (1 KTV / message)

| Field | Bắt buộc | Giá trị hợp lệ (theo staging) | Vi phạm thì sao |
|---|---|---|---|
| `staff_id` | ✔ | string khác rỗng, giữ số 0 đầu (`"00201964"`) | 400 |
| `staff_account` | ✔ | string (`ISC01.LUANPT2`) | 400; có thể dùng làm Kafka key nếu producer xác nhận |
| `latlng` | ✔ | `"lat,lng"` trong VN 8–24 / 102–110 | 400 nếu sai format |
| `available` | ✔ | `"HH:mm-HH:mm,..."` giờ đầu < cuối (`07:30-18:00`) | 400 |
| `plots[]` | ✔, ≥1 | `{id, name?, role, block_id}`. Tạm dùng chung mapping với `staff_role`: 1-chính / 2-kiêm nhiệm / 3-hỗ trợ | `role=0` là chưa biết lô/role; vẫn route nhưng không áp điểm theo role |
| `status` | ✔ | 1-rảnh / 2-bận / 3-off. Staging `2` | 1/2 được xử lý; 3 coi là off và không sinh tuyến. Giả định này nằm ở normalization/use case |
| `current_task` | ✔ (null được) | `{task_id, task_status_id, task_type_id}` hoặc null | Staging có thể đồng thời gửi task đầy đủ trong `tasks`; ghép theo `task_id` để bổ sung dữ liệu cho current task, nhưng không đưa cùng task vào tuyến lần thứ hai |
| `staff_location` | bỏ | Bản mới đã bỏ | Ignore nếu có gửi |

## 2. Tasks (5 nhóm cố định: trien_khai, bao_tri, thu_hoi, hoa_don, onsite; rỗng = `[]`)

| Field | Bắt buộc | Giá trị hợp lệ | Vi phạm thì sao |
|---|---|---|---|
| `task_id, task_group_id/name, task_type_id/name` | ✔ | khớp sheet 05 mới. `trien_khai_net=3, trien_khai_box=4, vat_ly=1, logic=2`. Sub `swap/gsafe/giao_cam` nằm dưới `box` theo `init_status/tasktype` | 400 nếu sai nhóm; `task_type_name` lạ → 400 |
| `task_sub_id/name` | – | `0` / `""` nếu không có | Cho qua |
| `task_status_id/name` | ✔ | Prototype chỉ xử lý `6` và `10`: `6=ROUTABLE`, `10=CURRENT` | Mã khác `6/10` được coi `EXCLUDED`, không xếp tuyến. Nếu `10` không khớp `staff.current_task`, không route như task chờ và ghi cảnh báo |
| `sla.{sla_minutes, priority_in_day}` | ✔ | `sla_minutes`: int > 0 hoặc null. `priority`: 1–4 | 400 nếu sai. Phải khớp bảng 05 |
| `appointment` | – | `""` hoặc `"YYYY-MM-DD HH:mm:ss"`. `""` = AI tự xếp | 400 nếu sai format |
| `create_date, complete_date` | – | Cùng format trên. `""` = chưa có ngày | `complete_date` được hiểu là ngày hoàn thành. Có giá trị thì task đã hoàn thành và không đưa vào tuyến; `create_date` được giữ trong domain data để áp rule cần ngày tạo |
| `location` | – | string tự do (địa chỉ staging khá bẩn) | Cho qua, chỉ hiển thị |
| `latlng` | ✔ | `"lat,lng"` VN hoặc `""` (= thiếu tọa độ → loại khỏi tuyến, Rule cứng 4) | 400 nếu format sai; `""` thì loại + vẫn đếm SLA |
| `handle_minutes` | – | số nguyên dương, `0` hoặc `null` | `0` / `null` nghĩa là dùng định mức theo task type. Giá trị âm hoặc sai kiểu là dữ liệu lỗi |
| `task_plots_id, staff_plots_id, staff_role, block_id` | ✔ | số nguyên; `0` được chấp nhận như staging. Tạm map `staff_role`: 1 chính / 2 kiêm nhiệm / 3 hỗ trợ | `staff_plots_id=0` cùng `staff_role=0` nghĩa là KTV không thuộc lô nào cho task này. Không loại task đã giao; vẫn route theo SLA/toạ độ, không áp điểm ưu tiên theo lô. Nếu `task_plots_id=0`, coi plot của task là chưa xác định; không gộp các task plot 0 thành một lô |
| `contract_id` | không ghi rõ là bắt buộc | số nguyên — ID hợp đồng (ObjID) | Giữ trong task model để truy vết; không dùng để chấm điểm/xếp tuyến |
| `contract_no` | không ghi rõ là bắt buộc | chuỗi — số hợp đồng | Giữ trong task model để hiển thị/truy vết; không dùng để chấm điểm/xếp tuyến |
| `location_id` | – | Có trong mẫu/API cũ | Giữ nếu được gửi; chưa dùng trong thuật toán |

## 3. Quy ước Kafka prototype (chưa kết nối broker)

Topic IN: `stag-inside-par-assignment-optimal-assign-task-emp-assigned-queue`.
Topic OUT: `chatbot-ftel-bot-gateway-optimize-task-queue`.

Vì staging payload chưa có envelope, prototype giữ `staff` và `tasks` ở root như payload
hiện tại, đồng thời thêm các field root:

```json
{
  "message_id": "local-00201964-20260819-day-start-01",
  "planned_at": "2026-08-19 07:30:00",
  "trigger": "DAY_START",
  "staff": {},
  "tasks": {}
}
```

`trigger` prototype: `DAY_START`, `TASK_DONE`, `TASK_NEW`, `RESCHEDULE`, `REOPTIMIZE`.
`message_id` duy nhất cho một yêu cầu tính; `trace_id` trong response echo lại ID này.
`planned_at` là thời điểm làm cơ sở lập tuyến theo giờ Việt Nam. Khi chạy file local phải
được truyền rõ hoặc ghi rõ trong fixture; không âm thầm thay bằng giờ chạy máy.

Kafka key chưa phải contract đã được hai bên chốt. Prototype đề xuất IN/OUT dùng cùng
partition key theo ngày + `staff_id` để giữ thứ tự của một KTV; xác nhận key/partition với
producer và Infra khi được cấp broker. `run_code` nằm trong payload, không đưa vào key.

### Response Kafka OUT prototype

Giữ response nghiệp vụ đúng cấu trúc sheet 03/04, thêm metadata tương quan ở root để các
consumer có thể đối soát mà không cần parse input topic:

```json
{
  "message_id": "local-00201964-20260819-day-start-01",
  "run_code": "local-00201964-20260819-day-start-01",
  "trigger": "DAY_START",
  "planned_at": "2026-08-19 07:30:00",
  "schema_version": "1",
  "success": true,
  "statuscode": "200",
  "message": "",
  "trace_id": "local-00201964-20260819-day-start-01",
  "server_time": "2026-08-19 07:30:01",
  "data": {"staff_id": "00201964", "clusters": [], "metrics": {}}
}
```

Một input tạo một output, kể cả lỗi hợp đồng/xử lý; consumer không được để message lỗi
làm kẹt partition. Lỗi input trả response lỗi có correlation ID; lỗi hạ tầng tạm thời retry.
Quy ước retry/DLQ và format lỗi cuối cùng sẽ chốt cùng Infra. Hai consumer group đọc OUT
độc lập: Optimal Assign lưu Oracle, Bot Gateway cập nhật dữ liệu phục vụ Mobix.

## 4. Output mới cần biết (sheet 03 bản mới)

`type` → `entry_type`. Thêm `task_role` (main/inserted), `priority` (điểm rule),
`insert_reason` (same_address...). Dùng cho Rule 5 chèn + giải thích.

## 5. Giả định prototype (không chặn refactor)

1. Domain chỉ cần ba nhóm status cho prototype: `ROUTABLE` (mã `6`, task được xếp), `CURRENT` (mã `10` khớp `staff.current_task`, dùng làm điểm/giờ xuất phát, không xếp thành stop thứ hai), `EXCLUDED` (mọi mã khác `6/10`, coi như không cần xếp). Nếu task status `10` không khớp `staff.current_task`, không route task đó và ghi cảnh báo.
2. `staff_plots_id=0` + `staff_role=0` nghĩa là KTV không thuộc lô nào cho task đó; vẫn xếp task đã được giao, nhưng không áp rule ưu tiên theo lô. `task_plots_id=0` nghĩa là chưa xác định lô của task; dùng tọa độ thay cho grouping theo lô.
3. `staff.status=3` là off nên không sinh tuyến; `1` và `2` vẫn xử lý.
4. `complete_date` khác rỗng là task hoàn tất và bị loại khỏi candidates; `handle_minutes=0/null` dùng định mức.
5. Row task có cùng ID với `current_task` bổ sung dữ liệu cho việc đang làm. Nếu không có row khớp, dùng thông tin tối thiểu trong `current_task` và vị trí hiện tại của staff; không tạo stop trùng.
6. `contract_id` / `contract_no` được hiểu theo mô tả mới: ObjID và số hợp đồng; giữ trong task model nhưng không dùng trong scoring.
7. Envelope và key Kafka trong tài liệu là quy ước local prototype; khi có broker chỉ cập nhật adapter/config nếu contract nguồn khác.

## 6. Plan refactor code (chưa triển khai)

1. Tách contract/JSON parsing khỏi domain model; hỗ trợ `handle_minutes` null/0 và thêm `Task.contract_id` (ObjID số nguyên) cùng `Task.contract_no` (chuỗi).
2. Thêm normalization theo quy tắc prototype: chỉ status `6` là `ROUTABLE`, status `10` khớp `current_task` là `CURRENT`, các status khác `EXCLUDED`; lọc task hoàn tất, ghép `current_task`, xử lý task không có quan hệ KTV-lô mà không loại task đã giao.
3. Tách `plan.cpp` thành các bước nội bộ: normalize → KPI/SLA rules & priority → clustering → route optimizer (giữ QHĐ hiện tại) → output mapping. Clustering/re-entry dùng plot ID khi khác 0; task không có plot/quan hệ staff-plot vẫn được định tuyến theo toạ độ, không bị gộp với task plot 0 và không nhận điểm role. Clustering hiện tại gộp cả tuyến thành một cụm; thay đổi thành clusterer thật là phần cần triển khai riêng.
4. Thêm application use case cho `ProcessWorklist`, batch đầu ngày (nhiều worklist KTV dùng chung pipeline), và `Reoptimize` (cùng pipeline, cần input mới nhất khi tích hợp nguồn thật).
5. Làm file adapter local để đọc payload staging + envelope prototype, ghi response OUT prototype; kiểm thử trên `Data staging.txt` và bộ JSONL hiện có.
6. Khi có broker, thêm Kafka consumer/producer adapter. Domain/application không phụ thuộc Kafka; flow worker là consume IN → process → produce OUT → commit input.
7. API Mobix nằm ở Bot Gateway theo sơ đồ hiện tại; chưa tạo sáu API cho các bước pipeline.

### Sơ đồ module đích

```text
core/include/ktv/contract/     message, response, JSON contract
core/include/ktv/application/  process_worklist, batch, reoptimize
core/include/ktv/domain/       normalize, rules/priority, cluster, route/DP, travel
core/include/ktv/ports/        interface travel và message I/O khi cần
core/src/adapters/file/        prototype local trước broker
core/src/adapters/kafka/       consumer/producer sau khi có broker
```

Luồng phụ thuộc một chiều: `adapter → application → domain`. Không viết lại QHĐ hiện có;
refactor `plan.cpp` theo từng bước và giữ output/behavior tương đương trước khi thêm rule mới.
