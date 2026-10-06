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

## 2. Tasks (5 nhóm bắt buộc: trien_khai, bao_tri, thu_hoi, hoa_don, onsite + `cscd` không bắt buộc từ workbook API (4); rỗng = `[]`)

| Field | Bắt buộc | Giá trị hợp lệ | Vi phạm thì sao |
|---|---|---|---|
| `task_id, task_group_id/name, task_type_id/name` | ✔ | khớp sheet 05 mới. `trien_khai_net=3, trien_khai_box=4, vat_ly=1, logic=2`. Sub `swap/gsafe/giao_cam` nằm dưới `box` theo `init_status/tasktype` | 400 nếu sai nhóm; `task_type_name` lạ → 400 |
| `task_sub_id/name` | – | `0` / `""` nếu không có | Cho qua |
| `task_status_id/name` | ✔ | Prototype chỉ xử lý `6` và `10`: `6=ROUTABLE`, `10=CURRENT` | Mã khác `6/10` được coi `EXCLUDED`, không xếp tuyến. Nếu `10` không khớp `staff.current_task`, không route như task chờ và ghi cảnh báo |
| `sla.{sla_minutes, priority_in_day}` | ✔ | `sla_minutes`: int > 0 hoặc null. `priority`: 1–4 | 400 nếu sai. Phải khớp bảng 05 |
| `appointment` | – | `""` hoặc `"YYYY-MM-DD HH:mm:ss"`. `""` = AI tự xếp | 400 nếu sai format |
| `create_date, complete_date` | – | Cùng format trên. `""` = chưa có ngày | `complete_date` = ngày hoàn tất ca vụ **trước đó** (ngày thu bill kỳ trước, workbook (3)) — KHÔNG loại task (7.8); chỉ `hoa_don` dùng cho mục E (7.17). `create_date` dùng cho hạn "trong ngày tạo phiếu / trong tháng" |
| `location` | – | string tự do (địa chỉ staging khá bẩn) | Cho qua, chỉ hiển thị |
| `latlng` | ✔ | `"lat,lng"` VN hoặc `""` (= thiếu tọa độ → loại khỏi tuyến, Rule cứng 4) | 400 nếu format sai; `""` thì loại + vẫn đếm SLA |
| `handle_minutes` | – | số nguyên dương, `0` hoặc `null` | `0` / `null` nghĩa là dùng định mức theo task type. Giá trị âm hoặc sai kiểu là dữ liệu lỗi |
| `task_plots_id, staff_plots_id, staff_role, block_id` | ✔ | số nguyên; `staff_role`: 1 chính / 2 kiêm nhiệm / 3 hỗ trợ | Data giải thích `staff_plots_id=0 && staff_role=0` là KTV không thuộc lô cho task đó. Implementation hiện deferred case này và từ chối role 0; role 1/2/3 đi theo rule hiện tại. `task_plots_id=0` không tạo same-area/revisit group |
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
2. Data xác nhận `staff_plots_id=0` + `staff_role=0` nghĩa là KTV không thuộc lô nào cho task đó. Xử lý trường hợp này đang deferred; parser hiện không nhận role 0. `task_plots_id=0` không được xem là cùng lô với task 0 khác.
3. `staff.status=3` là off nên không sinh tuyến; `1` và `2` vẫn xử lý.
4. `complete_date` không loại task (việc xong/hủy theo `task_status_id`); `hoa_don` dùng nó cho mục E (7.17); `handle_minutes=0/null` dùng định mức.
5. Row task có cùng ID với `current_task` bổ sung dữ liệu cho việc đang làm. Nếu không có row khớp, dùng thông tin tối thiểu trong `current_task` và vị trí hiện tại của staff; không tạo stop trùng.
6. `contract_id` / `contract_no` được hiểu theo mô tả mới: ObjID và số hợp đồng; giữ trong task model nhưng không dùng trong scoring.
7. Envelope và key Kafka trong tài liệu là quy ước local prototype; khi có broker chỉ cập nhật adapter/config nếu contract nguồn khác.

## 6. Tiến độ implementation và plan tiếp theo

### Đã hoàn tất: Phase 1–5

1. **Contract/parser:** parse status/dates/contracts; `handle_minutes=0/null/""` dùng định mức; `staff_role` 1/2/3; row `current_task` trùng được chấp nhận. Catalog subtype/bảng số đầy đủ còn chờ chốt.
2. **Normalization:** status 6 vào tuyến; status 10 là current khi khớp `staff.current_task`; status khác bị loại; complete-date và tọa độ được lọc.
3. **SLA prep:** dùng `create_date` cho hạn theo ngày tạo/tháng khi có; fallback `planned_at`.
4. **Cluster/output:** QHĐ trước, chia cụm theo `leg_km > 2`, output `entry_type`, seq theo cluster; không sửa QHĐ.
5. **Local adapter:** đọc staging object/JSONL, sinh envelope prototype và ghi response OUT.

### Tiếp theo trước khi có broker

6. **Phase 5.1 — harden CLI (đã xong):** `--at` sai báo lỗi và thoát; pretty JSON hỏng tạo đúng một error response; thêm CTest chạy end-to-end CLI. `ctest` 9/9 pass; benchmark 5.332 record không đổi.
7. **Phase 5.2 — dễ đọc pipeline (đã xong):** `summarize_clusters` tách sang `cluster.*`; `plan.cpp` chỉ còn điều phối. Giữ nguyên TASK order/ETA/metrics; `ctest` 11/11 pass.

### Chờ dependency ngoài repo

8. **Phase 6 — Gateway read model (gateway của ta):** binary `ktv_gateway`, thư mục riêng `core/include/ktv/gateway/` + `core/src/gateway/`. **6.1 (đã xong)** `store` + `seed`; **6.2 (đã xong)** `server` + `main` (`GET /api/v1/worklist/{staff_id}`, `/healthz`); **6.3 (đã xong)** `RedisRouteStore` (hiredis optional, key `{prefix}route|latest:...`, TTL 7 ngày, CLI `--redis`). `ctest` 15/15; demo Redis: restart gateway không seed vẫn đọc được. Feeder tạm là file OUT; khi có Kafka chỉ thay bằng consumer.
9. **Phase 7 — Mobix kích hoạt replan:** Core expose `GET /api/v1/staff/{staff_id}/replan?latlng=&recorded_at=` (tái dùng gateway). Consumer nền giữ **message IN mới nhất mỗi staff** (state cache) + **dedup** (fingerprint) để không plan lại khi không đổi; `plan()` → **produce Kafka OUT**, HTTP chỉ trả `202` (không trả route cho Mobix). **7A** chạy được ngay với broker giả bằng file; **7B** thay bằng Kafka thật khi có broker. Không phân biệt trigger, không mode (mode là Phase 8).
10. **Phase 8 — Reoptimize do KTV yêu cầu:** bàn sau khi nối Kafka. Đã chốt KTV chọn mode và route mới hợp lệ thay route đang xem dù metrics không tốt hơn. Chưa chốt ai cung cấp/lấy snapshot mới nhất và route baseline; không mặc định Gateway/OA/Core. Không đồng nhất với replan tự động.
11. **Phase 9 — feedback/AI learning:** sau khi OA/Gateway chốt nguồn kết quả thật, version rule/model và guardrail backtest.

### Các mục được cố ý hoãn

- `staff_plots_id=0 && staff_role=0`: ý nghĩa data đã rõ nhưng implementation chưa hỗ trợ; parser hiện từ chối role 0.
- Đồng bộ đầy đủ task type/subtype: workbook mới thiếu/chưa nhất quán bảng số; không đoán thêm ID.
- Output `priority`, `task_role=INSERTED`, `insert_reason`: chưa có formula/insertion behavior đủ rõ.
- Tách DTO/domain thành hai bộ struct: chưa cần khi chỉ có một nguồn input.
- Tạo service class riêng cho ProcessWorklist/Batch/Reoptimize: ProcessWorklist đúng là một lần `plan()`, batch là nhiều lần; class riêng chỉ là bọc rỗng. Reoptimize cần thêm input/policy nên sẽ là **function** riêng ở Phase 8, không phải class.

### Bố cục code hiện tại (flat, cố ý)

```text
core/include/ktv/*.hpp            api, normalization, sla, rules, travel, dp, cluster, plan
core/include/ktv/adapter/         envelope (dùng chung), file (đọc object/JSONL)
core/include/ktv/gateway/         store, seed, redis_store, server
core/src/*.cpp                    hiện thực tương ứng
core/src/adapter/                 envelope.cpp, file.cpp
core/src/cli/main.cpp             binary ktv_core (một lần plan mỗi record)
core/src/gateway/                 main.cpp + store/seed/redis_store/server (binary ktv_gateway)
```

Sơ đồ nested `contract/application/domain/ports/adapters` là định hướng ban đầu; hiện **không tách** vì chỉ có một implementation và một nguồn input. Khi có transport thứ hai thật (Kafka), cân nhắc thêm một hàm application `process_record` dùng chung CLI + worker để tránh lặp parse→plan→wrap; vẫn là function, không phải class/microservice.

Luồng phụ thuộc: `adapter/CLI → plan (application façade) → domain (normalize, sla, dp, cluster, travel)`. Không viết lại QHĐ.
