# Core AI — Implementation Specification

> Phân rã triển khai + nhật ký từng phase. Phase 1–6 đã thực thi; Phase 7 đang làm (đọc IN xong,
> 7A state cache + API `replan` tiếp theo, produce OUT tạm hoãn). §2 và §10 giữ làm lịch sử lúc bắt đầu.
> QHĐ là lõi đã có; không thay thuật toán trong các bước dưới đây.

## 1. Phạm vi

### Trong phạm vi

- Nhận một payload công việc của một KTV.
- Parse/validate contract, chuẩn hoá status và dữ liệu staging.
- Áp SLA/rule hiện có, gọi route optimizer hiện có, dựng cụm và response theo workbook API mới.
- Chạy local bằng fixture/file trước khi có Kafka broker.
- Sau khi được cấp broker, consume IN → xử lý một message → produce OUT.
- Gateway API của team (`ktv_gateway`): Mobix gọi kích hoạt replan → Core tính → kết quả ra Kafka OUT (Phase 7).
- Batch đầu ngày là nhiều message KTV đi qua cùng pipeline.
- Replan tự động do thay đổi dữ liệu (hoàn tất/task mới/hẹn lại) và reoptimize do KTV không hài lòng là hai use case khác nhau; chưa được gộp thành "snapshot mới + trigger".

### Ngoài phạm vi của refactor này

- Thay QHĐ, đổi objective, đổi ngưỡng exact/heuristic, hoặc viết lại `dp.cpp`.
- Học ML/GBM, tune trọng số tự động, training/backtest runtime.
- Tách sáu bước thành sáu microservice hoặc sáu HTTP endpoint.
- Bot Gateway: hệ thống phía Mobix, không phải của team. Gateway của team là `ktv_gateway`.
- Lưu Oracle: Optimal Assign consume OUT và tự lưu. Core không giữ kho route phục vụ Mobix (Redis của
  gateway chỉ giữ state IN mới nhất + dedup, xem Phase 7).
- Break/OT mới ngoài phần break trưa đang có; nhiều khung giờ làm vẫn là phần chưa triển khai.
- Auto-insert task (Rule 5) trừ khi có spec riêng về cách chọn, feasibility và `insert_reason`.

## 2. Snapshot code lúc bắt đầu refactor (lịch sử)

> Mô tả code **trước** Phase 1, giữ để hiểu vì sao có các phase. Code hiện tại: `core/README.md`.

| File | Hiện làm gì | Phần cần giữ / thay đổi |
|---|---|---|
| `include/ktv/api.hpp`, `src/api.cpp` | DTO `Message/Staff/Task`, JSON parser/validator, TaskKind catalog, datetime | Giữ vai trò contract boundary; bổ sung field staging/API mới và bỏ lỗi duplicate current task |
| `include/ktv/rules.hpp`, `src/rules.cpp` | Tier rules, priority weights, config JSON; hiện có lunch-break config | Giữ nguyên objective và mặc định; QHĐ tiếp tục chấm route theo tier |
| `include/ktv/travel.hpp`, `src/travel.cpp` | Haversine/OSRM matrix, fallback đường chim bay × 1.3 | Giữ nguyên |
| `include/ktv/dp.hpp`, `src/dp.cpp` | QHĐ, nhãn Pareto, heuristic + 2-opt; working tree hiện đã có break như việc ảo | **Không đổi thuật toán**. Chỉ sửa khi có ticket thuật toán riêng |
| `include/ktv/plan.hpp`, `src/plan.cpp` | Orchestration hiện tại: lọc thiếu tọa độ, deadline, matrix, DP, response; output hiện gộp 1 cluster | Chia trách nhiệm nội bộ; giữ `plan()` làm entrypoint tương thích trong giai đoạn refactor |
| `src/cli/main.cpp` | CLI đọc JSONL/object, parse, gọi `plan()`, ghi response JSONL | File adapter prototype; transport Kafka sẽ thêm riêng, dùng chung adapter/envelope |
| `tests/test_api.cpp`, `test_plan.cpp`, `test_dp.cpp`, `test_travel.cpp` | Test parser, pipeline, QHĐ, OSRM local | Giữ các test hiện có; bổ sung test contract, normalization, cluster, Kafka-envelope prototype |
| `CMakeLists.txt` | Build static library `ktv`, CLI và 4 test | Thêm source/test khi tạo module mới |

Code hiện tại **chưa** parse `staff.status`, `create_date`, `complete_date`, `contract_id`, `contract_no`; parser strict sẽ từ chối chúng. Parser hiện từ chối `handle_minutes=0/null`, task role ngoài 1/2 và task đang làm trùng trong `tasks`. `plan()` chưa lọc theo `task_status_id`; clustering là một cụm duy nhất.

Catalog trong `task_kinds()` hiện cũng theo workbook cũ (ví dụ coi `swap` và `giao_thiet_bi_cam` là task type độc lập). Workbook mới mô tả `trien_khai_box` cùng các subtype; bước contract phải đồng bộ catalog theo đúng tên/ID/subtype của workbook mới, không lấy ID tạm từ dữ liệu giả.

**Blocker bảng số (chưa giải quyết):** workbook mới chưa có `task_type_id`/tên cho `thu_hoi`, `hoa_don`, `onsite`, và sheet 05 ghi rõ "cần chốt bảng số đầy đủ trước khi code". Ngoài ra `artifacts/fake/messages.jsonl` (5.332 message benchmark) đang dùng `box_cam_only`/`swap`/`giao_thiet_bi_cam` như type độc lập. Phase 1 chỉ **thêm** `trien_khai_box` (type 4) làm bí danh workbook mới và giữ các entry cũ để không phá benchmark; subtype SLA và bảng số đầy đủ vẫn chờ chốt.

Các thay đổi chưa commit trong `dp.*`, `plan.*`, `rules.*`, test và README có phần nghỉ trưa. Spec này coi đó là code hiện tại và không yêu cầu đảo ngược hay thay đổi thuật toán đó.

## 3. Quyết định contract cho prototype

### 3.1 Input message

Một message là một KTV. Payload staging `staff` + `tasks` được giữ ở root; envelope prototype cũng ở root:

```json
{
  "message_id": "local-00201964-20260819-day-start-01",
  "planned_at": "2026-08-19 07:30:00",
  "trigger": "DAY_START",
  "staff": {"...": "..."},
  "tasks": {"trien_khai": [], "bao_tri": [], "thu_hoi": [], "hoa_don": [], "onsite": []}
}
```

- `message_id`: ID duy nhất của lần yêu cầu; response echo bằng `trace_id`; prototype dùng `run_code = message_id`.
- `planned_at`: giờ Việt Nam dùng để lập tuyến; test/file fixture phải cho giá trị tường minh.
- `trigger`: `DAY_START | TASK_DONE | TASK_NEW | RESCHEDULE | REOPTIMIZE`.
- Các field envelope bắt buộc trên luồng Kafka; local adapter có thể tạo envelope cho file staging không có metadata.
- Kafka key đề xuất là `{date}:{staff_id}` để giữ thứ tự theo KTV; chưa là broker contract, chốt sau với producer/Infra.

### 3.2 Input DTO

**Staff**

- `staff_id`, `staff_account`: string; giữ nguyên leading zero.
- `latlng`: `"lat,lng"`; `available`: một hoặc nhiều khoảng `HH:mm-HH:mm`.
- `status`: `1/2` tiếp tục xử lý; `3` off → không sinh route.
- `plots[]`: giữ validation hiện tại; payload staging có plot role 1.
- `current_task`: nullable `{task_id, task_status_id, task_type_id}`.

**Task**

- Giữ các field hiện có trong `Task`.
- Thêm `create_date`, `complete_date` kiểu optional `Minutes` sau parse.
- Thêm `contract_id` optional số nguyên (ObjID), `contract_no` optional string; giữ để trace, không dùng trong score/route.
- `handle_minutes`: số dương dùng trực tiếp; `0`, `null` hoặc chuỗi rỗng dùng định mức `TaskKind`.
- `staff_role`: trong implementation hiện tại chỉ nhận `1/2/3` (chính/kiêm nhiệm/hỗ trợ) và xử lý theo rule đang có.
- Trường hợp `staff_plots_id=0 && staff_role=0` được data mô tả là KTV không thuộc lô cho task đó, nhưng **tạm ghi nhận, chưa implement**. Strict (`validate`) từ chối `staff_role=0`; nới lỏng (worker/`plan`) giữ task, giữ nguyên số 0, cảnh báo `STAFF_ROLE` — `staff_role` không dùng khi xếp tuyến nên không đổi route, không đổi rule. Câu hỏi đang chờ: `docs/DATA_QUESTIONS.md`.
- `task_plots_id=0`: lô task chưa xác định; không xem các task có plot 0 là cùng một lô.
- `complete_date != ""`: task hoàn tất, không đưa vào ứng viên tuyến.

**Task status — quy tắc prototype đã chốt**

| Mã | Phân loại nội bộ | Xử lý |
|---:|---|---|
| `6` | `ROUTABLE` | Đưa vào candidate list nếu chưa hoàn tất và có tọa độ |
| `10` | `CURRENT` | Chỉ là current khi khớp `staff.current_task.task_id`; không xếp thành stop thứ hai |
| Khác `6/10` | `EXCLUDED` | Xem như không cần xếp tuyến; loại khỏi candidate list |

Nếu task status 10 trùng `staff.current_task`, row task được phép tồn tại và được dùng bổ sung thông tin current; không còn là lỗi duplicate. Nếu status 10 không khớp `current_task`, không route row đó; ghi diagnostic nội bộ.

### 3.3 Kafka OUT prototype

Giữ response nghiệp vụ theo sheet 03/04, đổi `schedule[].type` thành `schedule[].entry_type`, bổ sung correlation metadata ở root:

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

- Một input tạo một output kể cả lỗi contract (`400`) hoặc không còn task route được (`422`).
- OSRM lỗi giữ behavior hiện tại: route fallback vẫn trả (`success=true`, `statuscode=424`).
- `task_role`, `priority`, `insert_reason` là optional trong workbook. Chưa phát ra giá trị giả: hiện không có insertion engine hoặc định nghĩa score từng task. Thêm khi có rule cụ thể.
- OUT topic: Optimal Assign consume để lưu Oracle; phía Mobix (Bot Gateway) tự lo đường đọc route. Core AI không giữ response store.
- Worker thêm mã `500` khi một message gây lỗi bất ngờ (vẫn commit, worker chạy tiếp).

## 4. Module/file specification

### 4.1 Module hiện hữu cần sửa

| File | Function/type | Trách nhiệm sau refactor |
|---|---|---|
| `include/ktv/api.hpp` | `Staff`, `Task`, `Message`, `Error`, `TaskKind` | Bổ sung DTO fields: `Staff.status`, `Task.create_date`, `Task.complete_date`, `Task.contract_id`, `Task.contract_no`. `Message` giữ envelope metadata. Không đưa thuật toán vào DTO |
| `src/api.cpp` | `parse_message`, internal `Reader`, `task_kinds`, `find_kind` | Parse/validate fields đã biết; chấp nhận task role 1–3, từ chối role 0 trong scope này; handle 0/null/`""`; parse dates/contracts; bỏ lỗi khi `current_task` row trùng ID; giữ strict rejection với field không nằm trong contract. Cập nhật catalog TaskKind/subtype theo workbook mới; không dùng các type ID cũ tự đặt |
| `include/ktv/plan.hpp` | `PlanResult`, `plan`, `error_response` | Giữ hàm `plan(...)` làm API nội bộ ổn định cho CLI và worker; không cho adapter gọi `dp::solve()` trực tiếp |
| `src/plan.cpp` | `deadlines`, `projected_sla`, `plan` | Điều phối normalize → deadline/problem construction → travel → `dp::solve` → cluster/output. Dùng `create_date` cho hạn theo ngày tạo; không sửa cách QHĐ chọn thứ tự |
| `src/cli/main.cpp` | CLI `plan/validate/print-rules` | File adapter prototype: đọc staging JSON một object hoặc JSONL qua `adapter/file`, gói qua `adapter/envelope`, gọi cùng `plan()`, ghi một response mỗi message. Không đặt nghiệp vụ status trong CLI |
| `include/ktv/adapter/*`, `src/adapter/*` | `Envelope` + `wrap_response` (dùng chung), `read_records` + `local_envelope` (file) | Ranh giới transport. Kafka sau này thêm adapter mới dùng chung `envelope` |
| `core/CMakeLists.txt` | target `ktv`, tests | Đăng ký source/test mới khi thêm normalization/cluster module |

`rules.hpp/.cpp`, `travel.hpp/.cpp`, `dp.hpp/.cpp` giữ contract và hành vi hiện có, trừ việc đưa thông tin area vào `Problem` chỉ khi interface hiện tại không thể biểu diễn rule plot 0 đúng. Không thay `solve`, `objective`, nhãn, tier order hoặc heuristic.

### 4.2 Module mới

| File đề xuất | Function/type | Input → output | Trách nhiệm |
|---|---|---|---|
| `include/ktv/normalization.hpp`, `src/normalization.cpp` | `NormalizedWorklist`, `normalize_worklist(const Message&)` | `Message` → candidates + current context + excluded counts | Áp status 6/10/other, `complete_date`, `current_task` duplicate, thiếu tọa độ và `task_plots_id=0`. Không xử lý quan hệ `staff_plots_id=0 && staff_role=0` trong scope này. Không tính score, không gọi OSRM, không đổi thứ tự |
| `include/ktv/sla.hpp`, `src/sla.cpp` | `Deadlines`, `resolve_deadlines(...)`, `projected_sla(...)` | task + TaskKind + `planned_at` → opens/due/complete_by và nhãn dự báo | Tách deadline/projected-SLA logic đang nằm trong `plan.cpp`; priority weight vẫn lấy từ `Rules`, không tạo pre-sort score mới |
| `include/ktv/cluster.hpp`, `src/cluster.cpp` | `Cluster`, `cluster_route(...)` | Dãy stops đã xếp + plot metadata → cụm theo route order | Tách phần hiện đang tạo đúng một cluster trong `plan.cpp`; không thay thứ tự do QHĐ trả |

Không cần tạo một class riêng cho mỗi “service”. `plan()` hiện là function façade của pipeline. Batch và Reoptimize không có thuật toán riêng; chúng gọi cùng `plan()` qua adapter/use-case boundary.

### 4.3 Sáu capability đã nêu map vào code

| Capability | Module/entrypoint | Ghi chú |
|---|---|---|
| Nhận payload + chuẩn hoá | `parse_message()` → `normalize_worklist()` | Parse contract tách khỏi quyết định status/lifecycle |
| Rule KPI/SLA/ưu tiên | `resolve_deadlines()`, `projected_sla()`, `Rules`, `dp::solve()` | SLA thành deadline; thứ tự vẫn do objective QHĐ theo tier hiện có quyết định |
| Gom cụm | `cluster_route()` | Hậu xử lý stops; không chạy optimizer khác |
| Tuyến có giờ hẹn | `plan()` → travel matrix → `dp::solve()` | QHĐ hiện có xử lý appointment/late/shift/lunch break |
| Batch đầu ngày | input fan-out + worker loop | N message một KTV; mỗi record gọi cùng `plan()`, không tạo batch solver/API riêng |
| Replan tự động | OA gửi worklist mới sau event; cùng pipeline `plan()` | Dữ liệu đổi là nguyên nhân tính lại; không cần ý định/tuỳ chọn của KTV |
| Reoptimize do KTV | Use case riêng, bàn sau khi nối Kafka (Phase 8); có context/ý định của user | Không coi là replan thường; contract và tiêu chí chấp nhận kết quả chốt ở Phase 8 |

### 4.4 Adapter Kafka

Kafka worker là adapter riêng (`src/kafka/main.cpp` + `kafka/config.*`, `kafka/consumer.*`), không gắn Kafka vào `api.cpp`, `plan.cpp` hoặc `dp.cpp`. Các bước 1–3 đã làm; bước 4 (produce OUT) tạm hoãn, xem Phase 7:

1. Consume một IN message.
2. Parse/validate; lỗi dữ liệu tạo OUT lỗi và không retry vô hạn.
3. Gọi cùng pipeline `plan()`.
4. Produce OUT; chỉ sau khi producer xác nhận mới commit input offset.
5. Lỗi tạm thời của runtime/broker retry; policy DLQ và max attempts chốt với Infra.

Worker xử lý ít nhất một lần (at-least-once); downstream phải deduplicate theo `run_code/message_id`. Chưa hứa end-to-end exactly-once.

## 5. Dependency graph

```text
CLI local (cli/main.cpp) ──────┐
Kafka worker (kafka/main.cpp) ─┤
Gateway replan (Phase 7A) ─────┴─> parse_message (api)
                                      │
                                      ▼
                               plan (pipeline façade)
                                      │
                  ┌───────────────────┴──────────────────┐
                  ▼                                      ▼
        normalize_worklist                    resolve_deadlines / Rules
                  │                                      │
                  └──────── candidates + Problem ───────┘
                                      │
                               travel matrix
                                      │
                                 dp::solve
                                      │ ordered Visits
                                      ▼
                         cluster_route + response mapping
                                      │
                             JSON response / OUT
```

- `dp` chỉ phụ thuộc `Problem`, `Rules`, `Matrix`; không biết JSON, Kafka hay cluster DTO.
- `normalization` phụ thuộc contract types; không phụ thuộc transport, OSRM hoặc DP.
- `cluster` nhận stops sau solve; không phụ thuộc Kafka/HTTP và không sửa QHĐ order.
- Adapter chỉ decode/encode/transport. Quyết định status và route nằm trong domain/pipeline.
- Gateway API (`ktv_gateway`) là app riêng trong repo, gọi cùng `plan()`; không thêm HTTP API cho từng stage.

## 6. Invariants

1. Mỗi input là đúng một KTV; `staff_id` là string và giữ số 0 đầu.
2. Chỉ status 6 trở thành candidate; status 10 chỉ là current theo `staff.current_task`; mọi status khác bị loại.
3. `complete_date` có giá trị luôn loại task, kể cả status 6.
4. `staff.current_task` không bao giờ xuất hiện lần thứ hai như một TASK stop. Nếu row task cùng ID có mặt, chỉ enrich current context.
5. Task status/plot filtering diễn ra trước khi dựng `Problem` và trước khi gọi OSRM.
6. Task thiếu tọa độ không vào DP. `staff_role` hợp lệ là 1/2/3; số khác (VD 0) chỉ lọt qua ở chế độ nới lỏng kèm cảnh báo và **không được diễn giải** thành role khác (không dùng khi xếp tuyến).
7. `task_plots_id=0` không tạo same-area bits với task khác plot 0; không gây AREA_REENTRY giả.
8. Thứ tự TASK trong response giữ nguyên thứ tự trả từ DP. Break/idle không làm đổi route order.
9. `tasks_total` là số TASK stops đã route; BREAK/IDLE và task bị loại không được đếm.
10. Tất cả thời điểm dùng local Vietnam time theo format contract; test truyền `planned_at` xác định, không phụ thuộc clock máy.
11. Batch và reoptimize gọi chung pipeline. Reoptimize cần snapshot input mới; Core AI không tự truy xuất trạng thái cũ.
12. QHĐ tiers, priority weights, dominance, exact/heuristic thresholds và lunch-break logic không thay đổi trong refactor.

## 7. Output/cluster rules cần giữ

- `schedule[].entry_type`: `TASK`, `IDLE`, `BREAK`; đổi từ key hiện tại `type` theo workbook mới.
- Clusterer chỉ hậu xử lý route. Mốc chia cụm ban đầu theo kế hoạch `core/README.md`: leg giữa hai TASK liên tiếp dài hơn 2 km mở cụm mới; tên cụm lấy từ plot khi có.
- `task_plots_id=0` không phải một plot chung. Task đó vẫn được nhóm theo leg distance, tên cụm dùng nhãn chưa xác định thay vì tên lô bịa.
- Chặng vào cụm là leg tới TASK đầu cụm; internal distance là các leg còn lại của cụm. Break/Idle gắn vào timeline và không tạo cụm.
- `revisit_count` giữ nghĩa số lần quay lại khu vực/plot đã rời; plot 0 không tham gia đếm.
- Cần reset `seq` trong từng `schedule[]` theo sheet output (sequence trong cụm); thứ tự tổng thể thể hiện bằng `cluster_seg` rồi schedule.
- Formula score float cho từng `priority` chưa có trong core rules; không lấy bừa cost của QHĐ làm score mới. Optional `priority/task_role/insert_reason` được để absent cho đến khi có formula/insertion spec.

## 8. Test specification

### Bộ test hiện tại (chạy `ctest` trong `core/build`)

| Test | Nội dung | Quy mô |
|---|---|---|
| `api` | parser contract: field mới, biến thể sai, mutation table | ~60 kiểm |
| `dp` | QHĐ so vét cạn + heuristic + biên | 600 bài đối chiếu vét cạn, 0,7 s |
| `plan` | một message end-to-end, nghỉ trưa, 422 | nhỏ |
| `travel` | OSRM local, ô null, fallback 424, ma trận méo → 424 | nhỏ |
| `normalization` | status/current/complete/location, ma trận status | nhỏ |
| `sla` | deadline matrix, projected_sla biên | ~40 kiểm |
| `cluster` | chia cụm, biên ngưỡng 2 km, nhiều cụm | nhỏ |
| `adapter` | object/JSONL/envelope, JSON hỏng | nhỏ |
| `pipeline` | ETA/SLA labels, ca làm, current, create_date, heuristic, 422, tất định | ~30 kịch bản |
| `cli` | binary thật: object/JSONL/`--at`/lỗi | end-to-end |
| `invariants` | 3.000 message sinh ngẫu nhiên (seeded) + benchmark 5.332 message (chỉ khi có `artifacts/fake/messages.jsonl`, không nằm trong git), kiểm bất biến và tất định | ~380.000 kiểm, 2,8 s |
| `gateway_store`, `gateway_seed`, `gateway_server` | store RAM, nạp file OUT, HTTP `worklist`/`healthz`/token | nhỏ |
| `gateway_redis` | `RedisStore` trên Redis thật (route, state/route/loc có version, dedup, TTL) (chỉ build khi có hiredis; không kết nối được thì bỏ qua) | nhỏ |
| `kafka_config` | đọc `.env`, env đè file, `KAFKA_USE_SASL`, validate | nhỏ; đọc cả biến môi trường thật |

`ktv_worker` chưa có test tự động (cần broker): kiểm bằng E2E Kafka local, xem kết quả Phase 7.

`invariants` chốt các bất biến: `tasks_total` = số TASK, task_id không lặp, `seq` liên tục theo cụm, thời gian không lùi, `cluster_seg` liên tục, `success/data/422` nhất quán, `routed` khớp normalization, cùng input cho cùng output.

### Test giữ nguyên

- `test_dp`: toàn bộ 300 bài random so QHĐ với exhaustive permutations; nghỉ trưa ở các bài hiện có; heuristics tạo hoán vị hợp lệ.
- `test_travel`: OSRM local matrix, asymmetric legs, null-cell fallback, OSRM failure `424`.
- `test_api` và `test_plan`: giữ behavior đang được test; chỉ đổi assertion tên `type` → `entry_type` khi output contract được chuyển.

### Test bổ sung

**Parser (`test_api`)**

- Parse `staff.status=1/2/3`; status sai kiểu/ngoài miền bị lỗi contract.
- Parse `create_date`, `complete_date` rỗng/đúng/sai format.
- Parse `contract_id` integer và `contract_no` string; vắng mặt vẫn hợp lệ.
- `handle_minutes=0`, `null`, `""` thành “dùng định mức”; số dương giữ nguyên; âm/sai kiểu lỗi.
- Task `staff_role=1/2/3` chấp nhận; `staff_role=0` và cặp `(staff_plots_id=0, staff_role=0)` bị reject trong scope hiện tại, có test ghi rõ deferred behavior.
- `current_task` trùng row task không còn lỗi duplicate; duplicate ID giữa hai task thường vẫn lỗi.
- Field chưa thuộc contract vẫn bị reject để bắt sai chính tả.

**Normalization (`test_normalization`)**

- Status 6 + chưa complete + có tọa độ → routable.
- Status 10 trùng current task → current context, không nằm trong candidates.
- Status 10 không trùng current task → excluded + diagnostic.
- Mọi status khác 6/10 (bao gồm 0/97) → excluded.
- `complete_date` có giá trị loại task status 6.
- Status 6 thiếu tọa độ → excluded; các task 6 khác vẫn giữ.
- Duplicate current row enrich current; không tạo hai stops.
- staff status 3 không tạo route; status 1/2 tiếp tục bình thường.

**Plan (`test_plan`)**

- Một task status 6 đi vào DP; task status 0/97 không xuất hiện ở schedule.
- Status 10 current không xuất hiện thành TASK; thời điểm start vẫn theo behavior hiện tại `planned_at + current_task_minutes`.
- Không còn task status 6 → response `422` theo contract không có việc route.
- create_date được dùng làm ngày cơ sở cho “hoàn tất trong ngày tạo/ trong tháng”; nếu thiếu thì fallback planned_at để giữ tính xác định.
- `handle_minutes=0/null` dùng đúng `TaskKind.handle_minutes`.
- Không đổi thứ tự, score objective, break placements trong fixture hiện tại ngoài thay đổi parser/filter/response contract.

**Cluster (`test_cluster`)**

- Split tại leg >2 km, không tạo cụm rỗng do inbound leg.
- TASK cùng cụm có center/radius/distance aggregation đúng; `task_plots_id=0` không bị xem là cùng plot với nhau.
- Break/Idle giữ đúng timeline và không tăng `task_count`/`cluster_count`.
- Ghép schedule sau clustering bảo toàn thứ tự TASK của DP.

**File end-to-end**

- Một JSON pretty-printed staging payload và JSONL nhiều dòng đều đọc được; fixture có cặp `(staff_plots_id=0, staff_role=0)` hiện phải trả lỗi validation cho tới khi phần deferred được đưa vào scope.
- Local envelope có `message_id/planned_at/trigger`; output echo `trace_id`, thêm `run_code/schema_version`, response body theo sheet 03/04.
- Staging fixture hiện có status 0/97/10 nên theo policy đã chốt sẽ không có candidate status 6; expected không có stop mới. Dùng fixture API sample/status 6 để test route bình thường.

## 9. Delivery phases

**Không triển khai cả refactor trong một lần.** Mỗi phase là một diff nhỏ có test/exit gate; chỉ chuyển phase sau khi gate trước đạt. Như vậy nếu output hoặc parser sai có thể khoanh vùng mà không phải debug đồng thời contract, clustering, transport và QHĐ.

| Phase | Phạm vi/file chính | Exit gate |
|---|---|---|
| 0. Baseline | Chạy suite hiện tại, ghi lại các thay đổi đang có (đặc biệt break trưa trong QHĐ) | `ctest` pass trước khi chỉnh logic |
| 1. Contract | `api.hpp/api.cpp`, `test_api.cpp` | Parser đúng các field mới; test contract pass; chưa thay route output |
| 2. Normalize | Tạo `normalization.hpp/.cpp`; nối vào `plan.cpp`; thêm `test_normalization.cpp` | Status/current/complete/coordinate filtering đúng; route order trên input cũ không đổi |
| 3. SLA/input prep | Tạo `sla.hpp/.cpp`; cập nhật `create_date`, `handle_minutes`; `test_plan.cpp` | Due/complete deadlines test được; `test_dp` không đổi và vẫn pass |
| 4. Cluster/output | Tạo `cluster.hpp/.cpp`; update response mapper trong `plan.cpp`; `test_cluster.cpp` | Cluster không đổi thứ tự TASK; output dùng `entry_type`; cluster/metrics test pass |
| 5. Local end-to-end | Mở rộng `main.cpp` để đọc JSON object staging và JSONL; sinh envelope local; ghi OUT prototype | Fixture route được ra response đúng schema; malformed input có response lỗi; CLI suite pass |
| 5.1. Local adapter hardening | Sửa edge cases CLI; thêm CLI end-to-end test | Giờ test xác định; một input lỗi cho đúng một output lỗi |
| 5.2. Pipeline readability | Tách cluster/output aggregation còn nằm trong `plan.cpp` thành helper/module có contract rõ | `plan()` đọc như orchestration; response/metrics giữ nguyên trên golden fixtures |
| 6. Gateway read model | Binary `ktv_gateway`, thư mục `gateway/*`; xem 6.1–6.2 | GET cho Mobix đọc bản mới nhất; không tính lại |
| 6.1 Dữ liệu vào | `gateway/store.*` + `gateway/seed.*`: `RouteStore`, `MemoryRouteStore`, loader OUT JSONL | unit test store + seed |
| 6.2 HTTP + binary | `gateway/server.*` + `gateway/main.cpp`: `GET /worklist/{staff_id}`, `/healthz`, `ktv_gateway --port --seed --token` | HTTP test + E2E `ktv_core plan → seed → GET` |
| 6.3 Redis store | `gateway/redis_store.*`: `RedisRouteStore`, key `{prefix}route|latest:...`, TTL 7 ngày, hiredis optional; CLI `--redis` | test trên Redis thật + demo xem key bằng `redis-cli` |
| 7. Mobix gọi API, trả từ cache | IN mới → worker tính → route cache Redis + Kafka OUT; Mobix `GET route` đọc cache, `GET replan` (vị trí mới) tính lại → cache + OUT → trả route | Xem 7.1–7.6 |
| 7.1 Đọc IN ✅ | `kafka/*`, `ktv_worker`: consume IN → `plan()` → stdout/file; hardening + `/healthz` `/readyz` | E2E Kafka local |
| 7.2–7.4 Cache + API | Redis state/route có version/loc/dedup; worker ghi cache; gateway `route` + `replan` | ghi cũ không đè mới; replan HIT/MISS đúng |
| 7.5–7.6 OUT + vận hành | Producer OUT cho cả worker và gateway (topic trống = bỏ qua); compose + README | mỗi lần tính đúng một OUT; worker commit sau delivery |
| Vận hành ✅ | `Dockerfile` (build kèm ctest), CI GitHub Actions, `/healthz` worker | image build được, CI xanh |
| 8. Reoptimize use case | Sau khi Kafka đã nối; chốt request/context và policy riêng cho thao tác KTV | Có input phân biệt với replan thường; kết quả/compare semantics được business duyệt |
| 9. Feedback/AI learning | Chỉ sau khi chốt nguồn feedback, DB/topic và versioning | Có log gợi ý ↔ kết quả thật; backtest/guardrail trước khi phát hành model/rules |

### Phase 0 — Baseline

- Chỉ chạy build/test hiện tại; không sửa code để “dọn” hoặc viết lại QHĐ.
- Ghi rõ working tree có thay đổi lunch-break trước khi model khác bắt đầu, để model không vô tình revert.
- Chụp response từ fixture API status 6 làm mốc pipeline hiện tại; mốc này dùng để phát hiện thay đổi không chủ ý, không dùng để đóng băng output cluster cũ sau phase 4.

### Phase 1 — Contract và parser  ✅ đã thực thi

- Sửa `Staff/Task` và `parse_message()` cho `staff.status`, `create_date`, `complete_date`, `contract_id`, `contract_no`.
- Cho `handle_minutes=0/null/""` thành không có thời lượng override; parser giữ task role 1/2/3, role 0 vẫn không hợp lệ trong scope này.
- Bỏ lỗi parser khi `staff.current_task.task_id` có row task cùng ID; duplicate ID giữa hai task thường vẫn là lỗi.
- Catalog: thêm `trien_khai_box` (type 4) theo workbook mới, giữ entry cũ cho benchmark; subtype/bảng số đầy đủ chờ chốt (xem blocker ở mục 2).
- Giữ validation strict cho field lạ; chỉ whitelist các field đã có trong contract/staging.

**Không** thêm filtering status vào parser. Parser decode contract; lifecycle filtering thuộc Phase 2.

**Kết quả:** `ctest` 4/4 pass. JSON mẫu workbook mới (`trien_khai_box`) validate + plan 200. `data/sample/Data staging.txt` giờ chỉ còn lỗi đúng ở `staff_role=0` (deferred); các field mới và current-task trùng đã parse được. Benchmark `artifacts/fake/messages.jsonl` vẫn plan đủ 5.332 message (không regression).

### Phase 2 — Normalization và eligibility  ✅ đã thực thi

- Thêm `normalize_worklist(const Message&)` trả candidates, current-task context và thống kê task bị loại.
- Áp policy cố định: status 6 candidate; status 10 current khi khớp `staff.current_task`; status khác excluded.
- `complete_date` có giá trị loại task; `staff.status=3` không sinh route; status 1/2 tiếp tục.
- Task status 10 trùng current được dùng làm thông tin bổ sung, không thành stop; current thiếu row vẫn dùng `current_task` + vị trí staff theo behavior hiện tại.
- Task thiếu tọa độ bị loại trước khi gọi OSRM. `(staff_plots_id=0, staff_role=0)` vẫn deferred và không có normalization rule trong phase này; validator không chấp nhận role 0.

**Thực thi:** file mới `include/ktv/normalization.hpp`, `src/normalization.cpp`, `tests/test_normalization.cpp`. Hằng `kStatusRoutable=6`, `kStatusCurrent=10`, `kStaffOff=3`. Thứ tự ưu tiên loại: off → row trùng current → status ≠ 6 → complete_date → thiếu tọa độ. `staff.status=3` trả `422` ("KTV đang off"). `plan.cpp` lấy candidates + current từ normalize; vẫn giữ `result.excluded` = số thiếu tọa độ. Thứ tự candidates giữ nguyên `message.tasks`.

**Kết quả:** `ctest` 5/5 pass (thêm test_normalization). Benchmark 5.332 message giữ nguyên phân bố (5202/130), JSON mẫu mới vẫn 200, staging vẫn chỉ lỗi `staff_role=0`. Không đổi `dp::solve`, objective hay thứ tự tuyến.

### Phase 3 — SLA và input cho QHĐ  ✅ đã thực thi

- Chuyển `deadlines()` và `projected_sla()` khỏi `plan.cpp` sang module `sla.*`, giữ nguyên semantics ngoài việc dùng `create_date` khi task rule yêu cầu hạn theo ngày tạo/tháng.
- `handle_minutes > 0` dùng input; 0/null/empty dùng `TaskKind.handle_minutes`.
- Giữ current task duration theo `Rules.current_task_minutes`; không đổi thành duration còn lại dựa trên task row nếu chưa có giờ bắt đầu/elapsed.
- `Rules` tiếp tục cung cấp tier/priority weight hiện tại; không tạo weighted sort hoặc sửa `dp::objective()`.

**Thực thi:** file mới `include/ktv/sla.hpp`, `src/sla.cpp`, `tests/test_sla.cpp`. Hàm `resolve_deadlines(task, kind, planned_at)` dùng `create_date` làm mốc cho "trong ngày tạo phiếu / trong tháng" (fallback `planned_at`); `projected_sla(checkin, done, due, complete_by, service, now, rules)` tách khỏi `Visit` cho gọn test. `start_of_day` chuyển thành inline trong `sla.hpp`; `plan.cpp` chỉ còn gọi module. `handle_minutes` giữ `value_or(kind.handle_minutes)` sẵn có.

**Kết quả:** `ctest` 6/6 pass (thêm test_sla). Benchmark 5.332 message giữ nguyên (5202/130), JSON mẫu mới vẫn 200. `dp::solve`/objective không đổi.

### Phase 4 — Clustering và output contract  ✅ đã thực thi

- Chạy QHĐ trước, cluster các TASK đã có thứ tự sau đó. Không cho clusterer reorder TASK.
- Chia cụm ban đầu theo kế hoạch `core/README.md` (leg liên tiếp `>2 km`); tên lấy từ plot khi có.
- `task_plots_id=0` không tham gia same-area/revisit và không nhóm tất cả plot 0 thành một cụm.
- Chốt riêng trước khi code phase này: leg threshold đo bằng OSRM `leg_km` hay Haversine; đây là khác biệt observable khi bật OSRM.
- Chuyển output `type` → `entry_type`; bảo toàn TASK/IDLE/BREAK chronology, sequence trong từng cluster theo contract; cập nhật `cluster_count`, distance summaries và `revisit_count`.
- Không phát `priority/task_role/insert_reason` tự suy diễn; chúng optional cho đến khi có công thức score/insert policy.

**Quyết định:** ngưỡng cắt dùng `leg_km` — chính là `v.km` lấy từ bảng `Matrix`/`visit()` hiện có, không dựng bảng mới và tự khớp `travel_source` (OSRM hay Haversine). Đã chốt với người dùng.

**Thực thi:** file mới `include/ktv/cluster.hpp`, `src/cluster.cpp` (`split_clusters`, `kClusterSplitKm = 2.0`), `tests/test_cluster.cpp`. `plan.cpp` gom rows IDLE/BREAK/TASK rồi cắt cụm sau QHĐ: TASK đầu luôn mở cụm (chặng vào không cắt), row IDLE/BREAK gắn vào cụm của TASK liền trước. `seq` đánh lại 1..n trong từng cụm; `entry_type` thay `type`; `cluster_count`, `travel_km_inbound/internal`, `task_count`, `handle_minutes`, `center/radius` tính theo cụm. `same_area` bỏ qua `task_plots_id=0` để không tính nhầm cùng khu vực. Tên cụm: plot 0 → "Khu vực chưa xác định".

**Kết quả:** `ctest` 7/7 pass (thêm test_cluster). Benchmark 5.332 message giữ nguyên (5202/130); JSON mẫu mới tách 2 cụm, `seq` reset theo cụm. `dp::solve`/objective không đổi.

### Phase 5 — Local end-to-end adapter  ✅ đã thực thi

- CLI đọc một pretty-printed object (payload staging) hoặc nhiều dòng JSONL.
- Nếu file staging thiếu envelope, nhận metadata local tường minh (message ID, planned_at, trigger); test không dùng đồng hồ hiện tại ngầm định.
- Ghi một JSON response cho mỗi input, theo OUT prototype trong mục 3.3.
- Giữ nguyên `data/sample/Data staging.txt`; với current policy, fixture đó có role 0 và status 0/97 nên không phải happy-path route fixture. Test nguyên file để xác nhận role 0 bị validation từ chối; tạo fixture test riêng từ staging với role 1/2/3 để xác nhận status 0/97 bị loại; dùng fixture API status 6 hợp lệ cho đường route.

**Thực thi:** file mới `include/ktv/adapter.hpp`, `src/adapter.cpp` (`read_records`, `local_envelope`, `wrap_response`), `tests/test_adapter.cpp`. `main.cpp` dùng adapter: đọc cả object pretty lẫn JSONL, tự sinh envelope khi thiếu (`message_id=local-<n>`, `trigger=DAY_START`, `planned_at=--at` hoặc giờ máy), ghi OUT prototype (thêm `message_id/run_code/trigger/planned_at/schema_version`). Thêm cờ `--at "YYYY-MM-DD HH:mm:ss"` để test xác định giờ. `validate`/`print-rules` giữ nguyên.

**Kết quả:** `ctest` 8/8 pass (thêm test_adapter). Kiểm chứng CLI: staging `Data staging.txt` (pretty object) → 1 record, `400` role 0, envelope đúng; JSON mẫu mới → `200`; dòng JSON hỏng → `400` + `data: null`; staging đổi role 0→2 → `422` (status 0/97 bị loại); benchmark JSONL 5.332 record giữ nguyên (5202/130).

### Phase 5.1 — Local adapter hardening  ✅ đã thực thi

Review Phase 5 phát hiện hai trường hợp chưa đúng với mục tiêu test local:

- `--at` sai format hiện silently fallback về giờ máy; phải trả lỗi tham số thay vì làm test không xác định.
- Nếu một pretty-printed object nhiều dòng bị hỏng JSON, `read_records()` fallback line-by-line và phát một lỗi cho mỗi dòng. Một input object phải tạo đúng một error response.

Thêm CTest gọi CLI với: object hợp lệ + envelope; object staging không có envelope và `--at`; JSONL nhiều record; một dòng JSONL hỏng; một pretty object hỏng; `--at` hỏng. Kiểm tra đúng số output, correlation fields và status code. Đây là sửa adapter/test, không đổi parser nghiệp vụ hay planner.

**Thực thi:** `main.cpp` báo lỗi và thoát khi `--at` sai thay vì dùng giờ máy. `adapter.cpp read_records()` đọc cả file như một JSON trước; chỉ coi là JSONL khi có ít nhất một dòng tự parse được; nếu không dòng nào parse được thì cả nội dung là một input lỗi. File rỗng trả 0 record. Thêm `tests/test_cli.cpp` chạy binary `ktv_core` thật trên fixture tạm và `add_test(cli ... $<TARGET_FILE:ktv_core>)`.

**Kết quả:** `ctest` 9/9 pass (thêm test_cli). Kiểm chứng: pretty object hỏng → 1 output `400` (trước là 3); `--at` sai → thoát mã 2, không fallback; benchmark 5.332 record giữ nguyên (5202/130); JSON mẫu mới vẫn `200`.

### Phase 5.2 — Pipeline readability  ✅ đã thực thi

**Mục tiêu:** `plan()` đọc như một chuỗi điều phối; việc gom cụm + hình học + tổng hợp số liệu nằm trong module `cluster`. `cluster.cpp` hiện chỉ có `split_clusters`; phần còn lại (phân TASK vào cụm, center/radius, tên theo plot, inbound/internal/handle, dựng schedule) còn nằm rải trong `plan.cpp`.

**Không làm:** đổi thứ tự TASK, ETA, ngưỡng cắt 2 km, công thức `same_area`/revisit, hay bất kỳ field output nào. Không tạo class/service/interface mới ngoài struct dữ liệu.

#### Vai trò IDLE/BREAK (và vì sao hợp đồng cụm chỉ có TASK)

`IDLE` (chờ tới khung hẹn) và `BREAK` (nghỉ trưa) **không quyết định biên cụm**. Biên cụm chỉ do `leg_km` giữa hai TASK liên tiếp. IDLE/BREAK chỉ là dòng timeline hiển thị và cộng vào `idle_minutes`/`break_minutes` (tính trong `plan.cpp`). Vì vậy module `cluster` **không nhận** IDLE/BREAK: nếu đưa chúng vào hợp đồng, module vừa chia cụm vừa dựng timeline — trộn hai trách nhiệm.

Hệ quả: việc gán IDLE/BREAK vào cụm nào là chuyện serialize của `plan.cpp`. Quy tắc giữ nguyên như hiện tại: dòng non-TASK thuộc cụm của TASK liền trước; nếu đứng trước TASK đầu thì thuộc cụm 1.

#### Contract module `cluster` (TASK-only)

```cpp
// Một TASK đã xếp kèm dữ liệu để tóm tắt cụm. Không giờ, không IDLE/BREAK.
struct TaskStop {
    const Task* task = nullptr;
    double leg_km = 0;          // chặng tới TASK này
    double service_minutes = 0;
};

struct ClusterSummary {
    int seg = 0;                // 1-based
    int first_task = 0;         // chỉ số TASK đầu trong dãy (để map timeline)
    int task_count = 0;
    std::string code;           // "CL-<seg>"
    std::string name;
    Point center;
    double radius_km = 0;
    double travel_km_inbound = 0, travel_km_internal = 0, handle_minutes = 0;
};

std::vector<ClusterSummary> summarize_clusters(const std::vector<TaskStop>& stops,
                                               const std::vector<Plot>& staff_plots,
                                               double split_km = kClusterSplitKm);

std::vector<ClusterSpan> split_clusters(const std::vector<double>& legs_km, double threshold_km);  // giữ nguyên
```

`summarize_clusters` thuần, không JSON; dùng `split_clusters` nội bộ cho biên và `distance_km` cho radius. Trách nhiệm:
- TASK đầu mở cụm; TASK sau mở cụm mới khi `leg_km > split_km`.
- `center` = trung bình lat/lng các TASK; `radius_km` = max `distance_km(center, task)`.
- `travel_km_inbound` = leg TASK đầu cụm; `travel_km_internal` = tổng leg các TASK còn lại (số chưa làm tròn; làm tròn khi serialize).
- `handle_minutes` = tổng service các TASK; `task_count` = số TASK.
- `first_task` cho phép `plan.cpp` map mỗi dòng timeline về cụm.
- `name` = "Cluster N — " + nhãn plot duy nhất theo thứ tự xuất hiện; plot 0 → "Khu vực chưa xác định"; có tên lô của KTV thì dùng tên, ngược lại "Lô <id>".
- `code` = "CL-N", `seg` = N.

#### `plan.cpp` sau refactor

Giữ: normalize → staff off → candidates → shift/start → dựng `Problem` → travel matrix → `dp::solve`.

Vòng lặp sau `solve` vẫn dựng timeline (`rows` ojson + `row_task` ordinal) và cộng `idle`/`rest` như hiện tại, nhưng tách thêm một mảng `std::vector<TaskStop> stops` (chỉ TASK). Sau đó:
1. `auto summaries = summarize_clusters(stops, staff.plots);`
2. Dựng `cluster_of_task` từ `summaries` (theo `seg`/`first_task`/`task_count`), rồi gán `rows` vào cụm theo quy tắc "TASK liền trước", đánh `seq` lại theo cụm (giống code hiện tại).
3. Serialize `clusters` từ summaries + schedule; `cluster_count = summaries.size()`.

Gói bước 2–3 vào một helper nội bộ `assemble_clusters(rows, row_task, summaries, staff)` để `plan()` ngắn. Helper `plot_label` chuyển vào `cluster.cpp`; `hhmm`, `latlng`, `round_to` ở lại `plan.cpp`.

#### Các bước thực hiện

1. Thêm `TaskStop`, `ClusterSummary`, `summarize_clusters` vào `cluster.*` (thuần, chưa nối).
2. Trong `plan.cpp` dựng `stops` bên cạnh vòng lặp timeline; gọi `summarize_clusters`.
3. Thay đoạn dựng clusters bằng helper `assemble_clusters`; giữ nguyên thứ tự field JSON.
4. Build + chạy toàn bộ `ctest`; sửa lệch parity.
5. Thêm unit test `summarize_clusters` + test gán timeline ở mức JSON.
6. Cập nhật `core/README.md`.

#### Chiến lược parity

- Dùng lưới test hiện có: `plan`, `cluster`, `pipeline`, `invariants` (380k kiểm) assert giá trị cụm/metrics.
- `test_invariants` phải giữ `5202/130` và mọi bất biến (tasks_total, seq theo cụm, thời gian, task_id không lặp).
- Soát output `api_sample` trước/sau: `cluster_count`, `task_count`, `travel_km_inbound/internal`, `center`, `radius_m`, tên cụm, `seq`.

#### Test chi tiết

`summarize_clusters` (unit, không JSON):

| # | Kịch bản | Kỳ vọng |
|---|---|---|
| 1 | 1 TASK | 1 cụm, inbound = leg, internal = 0 |
| 2 | 2 TASK, leg 2 = 2.0 | 1 cụm |
| 3 | 2 TASK, leg 2 = 2.0001 | 2 cụm |
| 4 | 3 TASK gần–xa–sát | spans [0,1) và [1,3); task_count 1/2; internal cụm 2 = leg TASK 3 |
| 5 | center/radius | center = trung bình tọa độ; radius = max khoảng cách tới center |
| 6 | tên 2 plot | nhãn theo lần xuất hiện, nối " · " |
| 7 | `task_plots_id=0` | "Khu vực chưa xác định" |
| 8 | plot trùng `staff_plots` | dùng tên lô; không tên → "Lô <id>" |
| 9 | `first_task` | offset đúng để map timeline |

Gán timeline + serialize (mức `plan` JSON, giữ trong `test_cluster`/`test_pipeline`):

| # | Kịch bản | Kỳ vọng |
|---|---|---|
| 10 | IDLE trước TASK đầu | IDLE thuộc cụm 1, `task_count` = 1 |
| 11 | BREAK giữa TASK cụm cũ và TASK mở cụm mới | BREAK thuộc cụm cũ (TASK liền trước) |
| 12 | IDLE/BREAK | không tăng `task_count`/`cluster_count`; `idle_minutes`/`break_minutes` khớp |
| 13 | `seq` theo cụm | mỗi cụm bắt đầu 1, liên tục; thứ tự TASK không đổi |

#### Exit gate

- `ctest` 11/11 pass, gồm unit test `summarize_clusters` mới.
- `test_invariants` ~380k kiểm pass, benchmark giữ `5202/130`.
- Output `api_sample` và fixture `plan`/`pipeline` không đổi giá trị.
- Không sửa `dp.cpp`, `rules.cpp`, `travel.cpp`; `split_clusters` giữ hành vi.

#### Rủi ro và rollback

- Lệch làm tròn inbound/internal hoặc thứ tự field JSON → test exact bắt được.
- Gán timeline sai cụm → test #10–#13 và `test_cluster` JSON bắt được.
- Nếu diff phình: giữ `summarize_clusters` làm helper nội bộ `plan.cpp` thay vì tách file. Rollback = revert `cluster.*` + `plan.cpp`.

**Kết quả:** `cluster.hpp/.cpp` có `TaskStop`, `ClusterSummary`, `summarize_clusters`; `plan.cpp` bỏ `plot_label`, dựng `stops` TASK và gọi `summarize_clusters`, gắn timeline + serialize như cũ. `ctest` 11/11 pass; `invariants` ~380k kiểm giữ `5202/130`; output `api_sample` giữ nguyên hình dạng cụm. Không sửa `dp.cpp`/`rules.cpp`/`travel.cpp`.

### Phase 6 — Gateway read model (đọc cho Mobix)

**Bối cảnh.** Luồng ghi của ta là Kafka: Core consume IN → produce OUT; các consumer độc lập đọc OUT (Optimal Assign lưu Oracle, gateway giữ cache). Gateway **chỉ đọc**, không tính lại. Trước khi có Kafka, gateway lấy dữ liệu từ **file OUT** (`--seed`); khi có Kafka chỉ thay loader bằng consumer — HTTP và store không đổi. Đây là gateway của ta; bot gateway là việc của team khác.

**Phạm vi:** thêm binary `ktv_gateway` trong `core/`, đặt trong thư mục riêng:
```
core/include/ktv/gateway/{store,seed,redis_store,server}.hpp
core/src/gateway/{store,seed,redis_store,server,main}.cpp
core/tests/test_gateway_*.cpp
```
**Không làm:** Kafka, reoptimize, auth thật, `include_map`, endpoint kéo lô, `/metrics`. Không expose stage nội bộ. Không sửa core pipeline.

Chia thành 3 phase nhỏ:

| Phase | Nội dung | Exit gate |
|---|---|---|
| 6.1 Dữ liệu vào ✅ | `gateway/store.*`: `RouteStore` (`put/get/get_latest/size`) + `MemoryRouteStore` (map + mutex). `gateway/seed.*`: đọc OUT JSONL → index `(staff_id, date)`, bỏ dòng hỏng | unit test store + seed; chưa HTTP |
| 6.2 HTTP + binary ✅ | `gateway/server.*`: `GET /api/v1/worklist/{staff_id}?date=`, `GET /healthz`, token tĩnh tùy chọn. `gateway/main.cpp`: `ktv_gateway --port --seed --token`. E2E `ktv_core plan → seed → GET` | HTTP test cổng tạm + toàn bộ `ctest` pass; demo chạy |
| 6.3 Redis store ✅ | `gateway/redis_store.*`: `RedisRouteStore` cùng interface; key `{prefix}route:{staff}:{date}` + `{prefix}latest:{staff}`, TTL 7 ngày; CLI thêm `--redis HOST:PORT --redis-password --redis-prefix`; hiredis optional qua pkg-config | test trên Redis thật (skip nếu không có), demo seed → `redis-cli` xem key, restart không seed vẫn đọc |

**Kết quả 6.1:** `core/include/ktv/gateway/store.hpp`, `seed.hpp`; `core/src/gateway/store.cpp`, `seed.cpp`; `tests/test_gateway_store.cpp`, `test_gateway_seed.cpp`; lib `gateway` trong CMake. `ctest` 13/13 pass. Store thread-safe (mutex), `get_latest` theo date tăng dần; seed bỏ dòng hỏng/thiếu `planned_at`/`data.staff_id`/staff rỗng, cùng key thì bản sau thắng. Không sửa core pipeline.

**Hành vi chốt cho 6.2:**
- 200 JSON bản mới nhất; thiếu → `202 {"retry_after":5}`; token sai → 401.
- `date` bỏ trống → bản mới nhất của staff đó.
- `/healthz` → 200 `{"ok":true,"entries":N}`.
- GET **không** tính, không gọi `plan()`.

**Vai trò sau này:** phần đọc route của gateway (`RouteStore`, `GET worklist`, seed từ file OUT) là **đồ nghề dev**, không phải đường production. Đường production là Phase 7: Mobix gọi API kích hoạt → ta plan → đẩy Kafka OUT; phía Mobix tự lo cách đọc route. Redis + HTTP server của gateway được tái dùng cho state cache và endpoint `replan`.

**Kết quả 6.2:** `core/include/ktv/gateway/server.hpp`, `core/src/gateway/server.cpp`, `core/src/gateway/main.cpp` (binary `ktv_gateway`), `tests/test_gateway_server.cpp`. `ctest` 14/14 pass. Demo E2E: sinh 300 message → 294 dòng OUT → seed 120 key `(staff, date)`; `/healthz` 200; thiếu token 401; có token trả đúng route; staff lạ 202 `retry_after`. GET không gọi `plan()`, core pipeline không đổi.

**Kết quả 6.3:** `core/include/ktv/gateway/redis_store.hpp`, `core/src/gateway/redis_store.cpp`, `tests/test_gateway_redis.cpp`. hiredis phát hiện qua pkg-config; không có hiredis thì store Redis tắt, build vẫn chạy (`KTV_WITH_REDIS`). Key `{prefix}route:{staff}:{date}` (TTL 7 ngày) + `{prefix}latest:{staff}`; `get_latest` đọc latest rồi route; ghi ngày cũ không kéo latest lùi. `ctest` 15/15 pass (test Redis chạy trên Redis 7.4.9 local). Demo: seed 120 bản → 240 key, TTL ~604800s; restart gateway **không seed** vẫn trả route (dữ liệu nằm Redis). Key demo đã dọn sạch.

**Ghi chú:** `artifacts/fake/responses.jsonl` là format cũ (`type`, không envelope, không `planned_at`) → seed sẽ bỏ qua mọi dòng; phải sinh lại:

```bash
core/build/ktv_core plan artifacts/fake/messages.jsonl --out artifacts/fake/responses_v2.jsonl
```

`artifacts/` không được git track nên không cần commit. In-memory: 1 instance, restart mất cache → seed lại.

### Phase 7 — Mobix gọi API của team: trả route từ cache + đẩy Kafka OUT

Cập nhật hướng **2026-10-01** (thay bản 2026-09-30 "HTTP không trả route"). API draft cũ
[`docs/MOBIX-REPLAN-API-DRAFT.md`](../docs/MOBIX-REPLAN-API-DRAFT.md) còn ghi "202, không trả route" → sửa theo mục này khi chốt.

#### Context

- Bot Gateway là hệ thống phía Mobix. API của team là `ktv_gateway` (expose API + Redis).
- Mobix gọi API của ta và **nhận route trong HTTP response, lấy từ cache Redis**.
- Mỗi lần tính tuyến xong ra **hai nhánh**: (1) ghi **route cache** (Redis) để trả Mobix; (2) **produce Kafka OUT**
  (Optimal Assign lưu Oracle, consumer khác).
- State của KTV = message IN mới nhất (snapshot trọn gói 1 KTV). Kafka không tra on-demand → giữ trong Redis.

#### Luồng

```text
           ┌──────────── T1: có IN mới (ktv_worker) ────────────┐
Kafka IN ──┤ state ← IN · plan() · ghi route cache · produce OUT │──▶ commit IN (sau khi OUT xác nhận)
           └────────────────────────────────────────────────────┘
                     │ Redis: state / route / loc / dedup │
           ┌──────────── T2: Mobix replan (ktv_gateway) ────────┐
Mobix ─────┤ state + latlng Mobix · trùng dedup? → trả cache    │──▶ 200 route
 replan    │ chưa: plan() · ghi route cache · produce OUT       │
           └────────────────────────────────────────────────────┘
Mobix ── GET route ──▶ ktv_gateway ── đọc route cache ──▶ 200 route | 202 chưa có
```

- **T1 (IN mới)**: worker ghi state, tính luôn (để lúc Mobix đọc đã có route), ghi route cache, produce OUT,
  rồi mới commit IN. OUT lỗi → không commit → message được đọc lại, tính lại ra cùng kết quả (ghi cache idempotent).
- **T2 (Mobix replan)**: gateway đọc state, thay vị trí KTV bằng `latlng` Mobix gửi, tính, ghi cache, produce OUT,
  trả route. Trùng dedup (state và vị trí không đổi) → trả route đang cache, không tính, không OUT.
- **Đọc**: chỉ đọc cache, không tính.

#### Redis (một instance chung cho worker + gateway)

| Khóa | Giá trị | Ai ghi | TTL |
|---|---|---|---|
| `ktv:state:{staff}` | message IN mới nhất + `version` (`planned_at`, rồi offset) | worker | 2 ngày |
| `ktv:route:{staff}:{date}`, `ktv:latest:{staff}` (đã có, Phase 6.3) | response đã gói (giống OUT) + `based_on` {state version, `latlng_at`} | worker, gateway | 2 ngày |
| `ktv:loc:{staff}` | vị trí Mobix gửi gần nhất {`latlng`, `latlng_at`} | gateway | 1 ngày |
| `ktv:dedup:{staff}` | fingerprint lần tính cuối = state version + `latlng` làm tròn 4 chữ số (~11 m) | gateway | 1 ngày |

**Tranh chấp** (worker và gateway, nhiều replica, cùng một KTV): route chỉ ghi đè khi `based_on` **mới hơn**
(state version lớn hơn; bằng thì `latlng_at` mới hơn) — một Lua script trong Redis, không khóa. Nhờ vậy gateway
tính chậm trên state cũ không ghi đè được route worker vừa tính từ IN mới. State cũng chỉ ghi đè khi version mới hơn.

#### API (`ktv_gateway`)

```
GET /api/v1/staff/{staff_id}/route?date=YYYY-MM-DD      # đọc cache (date bỏ trống = mới nhất)
  200 <route>  ·  202 {"retry_after":5} chưa có  ·  401

GET /api/v1/staff/{staff_id}/replan?latlng=21.02,105.79&latlng_at=2026-10-01 09:20:00
  200 <route mới>  (header X-Cache: HIT khi trùng dedup, MISS khi vừa tính)
  404 chưa có state IN của KTV  ·  400 sai tham số  ·  401
Authorization: Bearer <token>       Cache-Control: no-store
```

`<route>` = đúng JSON đẩy vào Kafka OUT (envelope + `data.clusters/metrics`), để Mobix và OA thấy cùng một thứ.

#### Code (dự kiến)

| Phần | File | Ghi chú |
|---|---|---|
| Redis ✅ | `gateway/redis_store.*` | `RedisRouteStore` đổi tên `RedisStore`, thêm state / route có version (Lua) / loc / dedup, không tạo class mới |
| Producer | `kafka/producer.{hpp,cpp}` | librdkafka, idempotent, key = `staff_id`, chờ delivery report. `KAFKA_TOPIC_OUT` trống → bỏ qua produce, log một lần (deploy được trước khi SYS cấp topic) |
| Tính + phát | `adapter/publish.{hpp,cpp}` | Một hàm dùng chung cho T1 và T2: `plan()` → ghi route cache → produce OUT. Không đặt trong `plan`/`dp` |
| Worker | `kafka/main.cpp` | Thêm `--redis HOST:PORT`, `--redis-prefix`; luồng T1 |
| Gateway | `gateway/server.*`, `gateway/main.cpp` | Endpoint `route` + `replan`; link thêm `ktv` + `kafka`; thêm `--env/--rules/--osrm` |

#### Sub-phase

| Phase | Nội dung | Gate |
|---|---|---|
| 7.1 ✅ | `ktv_worker` đọc IN + hardening + `/healthz` `/readyz` (kết quả bên dưới) | E2E Kafka local |
| 7.2 ✅ | Redis: state + route có version (Lua) + loc + dedup (kết quả bên dưới) | test trên Redis thật: ghi cũ không đè mới, TTL đúng |
| 7.3 | Worker T1: IN → state → `plan()` → route cache | E2E: đẩy IN → đọc được route trong Redis; IN cũ không đè IN mới |
| 7.4 | Gateway: `GET route` + `GET replan` | HTTP test: replan trả route mới; gọi lại cùng vị trí → HIT; đổi vị trí → MISS; không có state → 404 |
| 7.5 | Producer OUT, nối vào T1 và T2 | E2E Kafka local: mỗi lần tính ra đúng một OUT; worker chỉ commit sau delivery; topic trống → chạy bình thường không OUT |
| 7.6 | Vận hành: compose chạy worker + gateway + Redis, `/healthz` gateway kiểm Redis, README | demo end-to-end bằng compose |
| sau | JWT thay token tĩnh, rate limit, OUT thêm `location`/`latlng` mỗi TASK (cần OA đồng ý) | |

#### Quyết định cần chốt (đang theo đề xuất)

1. **IN mới thì tính luôn** (T1) — đề xuất **có**, để cache luôn có route khi Mobix đọc và OA có OUT từ đầu ngày.
2. **State theo `staff`** (ngày nằm trong payload) — **đã chốt 2026-10-01**; route vẫn theo `(staff, date)` như Phase 6.3.
   Hệ quả đã biết: IN của ngày mai đến khi KTV còn đang chạy hôm nay sẽ thay state; `replan` sau đó tính trên IN mới.
3. **Nhớ vị trí Mobix gửi** để lần tính từ IN sau dùng lại nếu `latlng_at` trong 60 phút gần đây — đề xuất **có**
   [giả định 60 phút]; không thì tuyến tính từ IN quay về vị trí cũ trong payload.
4. **Đường đọc**: `GET /api/v1/staff/{id}/route` thay `GET /api/v1/worklist/{id}` (cùng họ với `replan`; Mobix
   chưa tích hợp nên đổi được) — đề xuất **đổi**, bỏ đường cũ.
5. **`replan` trả route ngay trong response** (tính mất vài ms, OSRM tối đa ~3 s) — đề xuất **có**, thay vì 202 rồi đọc lại.
6. **Produce OUT lỗi ở gateway**: vẫn trả route cho Mobix, log + đếm ở `/healthz` — đề xuất **có**. Ở worker thì
   không commit IN để thử lại.
7. **Nội dung OUT = nội dung route cache** — đề xuất **có**, một JSON cho cả hai nhánh.

Còn mở (không chặn code): tên topic OUT + quyền WRITE (SYS); envelope thật của OA (chờ message đầu tiên);
key của message IN (nếu OA không key theo `staff_id`, hai message của cùng KTV có thể nằm khác partition —
version của state xử lý được thứ tự đến).

**Kết quả 7.2 — Redis (2026-10-01):** `RedisRouteStore` → `RedisStore` (`gateway/redis_store.*`), vẫn là
`RouteStore` nên seed/GET cũ chạy y nguyên. State, route, vị trí lưu dạng **hash** `{json, v}`; `v` = version
là dãy số nguyên cách nhau dấu cách, so từ trái sang (thiếu = 0, mỗi số < 2^53). Một Lua script
(`EVAL`, nguyên tử) chỉ ghi khi version **mới hơn hẳn** — bằng nhau không ghi — và cập nhật `latest` trong cùng
script (trước đây `GET` rồi `SET` riêng, hai replica có thể kéo `latest` lùi). Khóa route dạng chuỗi từ trước 7.2
bị xóa rồi ghi lại (không lỗi WRONGTYPE); đọc khóa cũ trả "chưa có". `put()` không version (seed) luôn ghi đè.
Version do bên gọi dựng (7.3/7.4), đề xuất: state `{planned_at yyyymmddHHMMSS, offset}`, route
`{state..., latlng_at yyyymmddHHMMSS hoặc 0}`, vị trí `{latlng_at}`; dedup là chuỗi ghi đè thẳng. TTL: state
2 ngày, route/latest 2 ngày (người dùng chốt 2026-10-01, trước là 7), loc/dedup 1 ngày (`Config`). Kiểm: `test_gateway_redis` trên Redis thật 7.4 (Docker `ktv-redis`) và 6.0 (apt) — IN đến
trễ/bằng không đè, route chậm trên state cũ không đè, `latest` không lùi, khóa dạng cũ, loc, dedup, TTL từng
loại, hai kết nối ghi song song 400 version → còn bản lớn nhất; làm hỏng phép so trong Lua → 8 chỗ FAIL. Test
không còn nuốt ngoại lệ thành SKIP (chỉ lỗi kết nối mới SKIP). `ctest` 16/16.

**Kết quả bước 1 — đọc IN (2026-09-30):** `core/include/ktv/kafka/{config,consumer}.hpp`,
`core/src/kafka/{config,consumer,main}.cpp`, `tests/test_kafka_config.cpp`; binary `ktv_worker`
chỉ build khi có librdkafka (`KTV_WITH_KAFKA`, thiếu thì tắt nhưng ctest vẫn xanh). Config từ
`.env` (biến môi trường thật đè file; xem `.env.example`): `SASL_PLAINTEXT` + `PLAIN` theo hướng
dẫn SYS cho queue cluster. Consume IN → `parse_message`/`plan()` → response ra stdout/`--out`;
`enable.auto.commit=false`, commit từng message sau khi ghi xong. Envelope đọc từ body
(`local_envelope`); header được log ra stderr để soi khi có message thật từ Optimal Assign.
`ctest` 16/16. Kiểm thử E2E trên Kafka local (compose): produce 1 message vào topic → worker
trả response `200` đúng, chạy lại cùng group không đọc lại (offset đã commit).

**Hardening worker (2026-09-30, sau review):** giờ lập tuyến lấy lại cho từng message (trước đây đứng
ở giờ khởi động; `--at` vẫn cố định cho test); thiếu `message_id` → `topic-partition-offset` (trước là
`local-<n>`, trùng giữa các lần chạy/replica — `local_envelope` nhận `fallback_id`); lỗi của một
message → response `500` rồi commit, không chết worker/kẹt partition; ghi response lỗi → thoát,
**không** commit; `--out` không mở được → thoát mã 2, mở kiểu ghi nối; SIGINT/SIGTERM → xong message
đang xử lý rồi đóng consumer; `poll()` chỉ ném với lỗi nặng (fatal, auth, quyền, topic không có), lỗi
mạng/broker thì log và thử lại. CMake link `RDKAFKA_LINK_LIBRARIES` + include dir `PUBLIC` (trỏ được bản
librdkafka tự build). E2E Kafka local: thiếu envelope, JSON hỏng (400), giờ nhảy theo phút giữa hai
message, `/dev/full` không commit rồi chạy lại đọc đúng message đó, OSRM trả ma trận sai kiểu → 500
và worker chạy tiếp, tắt/bật broker giữa chừng, SIGTERM thoát sạch. OSRM trả ma trận méo giờ lùi về
chim bay (`424`) ngay trong `travel.cpp`, không còn lên tới `500`.

**Vận hành (2026-09-30):** `ktv_worker --health-port N` mở `GET /healthz` (luồng riêng): `200` khi vòng
poll còn chạy trong 60 giây, `503` khi treo; body có `processed`, `by_status`, `last_message_at`,
`uptime_s`. Consumer lag để công cụ Kafka đo. HTTP của worker và gateway tắt `SO_REUSEPORT` (mặc định
của httplib) để trùng cổng là báo lỗi thay vì hai process cùng nghe. `Dockerfile` hai tầng (build chạy
luôn `ctest`, image chạy ~125 MB, user không phải root, mặc định chạy worker với `/healthz` ở 8081);
`.dockerignore` chỉ gửi `core/`. CI GitHub Actions: build + `ctest` có Redis service, và `docker build`.
Kiểm: image build + ctest 16/16 trong image; container worker đọc Kafka local, `/healthz` 200, `docker stop`
thoát sạch; trùng cổng health/gateway → thoát lỗi.

**Parse nới lỏng cho dữ liệu thật (2026-09-30, người dùng duyệt):** team data khó liên lạc, mô tả chưa đủ,
nên staging/prod phải chạy được trên dữ liệu lệch hợp đồng và ghi lại câu hỏi. `parse_message(data, errors,
&warnings)`: chỉ `400` khi không xếp được (JSON hỏng, `staff` hỏng: ID/account/tọa độ/ca làm, `tasks` không
phải object). Lệch mà vẫn xếp được → cảnh báo có mã (`UNKNOWN_FIELD`, `TASK_GROUPS`, `STAFF_ROLE`,
`CATALOG_MISMATCH`, `UNKNOWN_TASK_TYPE`, `TASK_GROUP`, `STAFF_STATUS`, `STAFF_PLOTS`, `CURRENT_TASK`,
`PLANNED_AT`); một task hỏng hoặc trùng ID → bỏ riêng task đó (`TASK_DROPPED`). Loại việc ngoài danh mục
dùng `kind_or_default` (60 phút, không hạn theo loại). Cảnh báo **không** vào OUT (OUT contract đang hoãn,
OA có thể parse strict): worker log loại mới một lần kèm `message_id`, đếm ở `/healthz` `data_issues`
(`issue_key` bỏ chỉ số mảng, tối đa 200 loại); `ktv_core plan` in bảng cảnh báo; `ktv_core validate` giữ
strict. Không truyền `warnings` = strict y như cũ (mọi test cũ giữ nguyên). Mã + giả định + câu hỏi:
`docs/DATA_QUESTIONS.md`. Kiểm: `test_api` thêm từng mã (nới lỏng vẫn xếp, strict vẫn lỗi, loại ngoài danh
mục plan ra `200`); staging mẫu `validate` 400 → `plan` 422 (status 0/97) với 2 cảnh báo `STAFF_ROLE`;
benchmark 5.332 message giữ 5202/130, không cảnh báo.

### Phase 8 — Reoptimize do KTV yêu cầu (bàn sau khi nối Kafka)

**Không đồng nhất với replan tự động.** Replan tự động xử lý thay đổi dữ liệu nguồn (task hoàn tất/mới/hẹn lại). Reoptimize là hành động chủ động của KTV sau khi đã xem một route và muốn chọn một cách tối ưu khác.

Đã thống nhất:

1. KTV chọn mode tối ưu khi yêu cầu reoptimize.
2. Nếu có route mới hợp lệ, route đó thay route đang xem dù metrics không tốt hơn; metrics vẫn có thể trả để giải thích/trace.

Chưa thống nhất và **không chọn owner trong tài liệu này**:

- Thành phần nào cung cấp/lấy snapshot mới nhất.
- Ai sở hữu hoặc gửi route baseline mà KTV đang xem (`base_run_code`/route cũ).
- Gateway, Optimal Assign hay flow khác tạo và chuyển command tới Core.

Không mặc định Gateway/OA/Core là nơi lấy snapshot; phải chốt với owner quy trình trước khi định nghĩa payload hay transport. Core hiện stateless nên không thể tự đọc baseline/snapshot cũ.

Với cùng snapshot + cùng rules/mode + cùng travel matrix, QHĐ hiện tại deterministic nên cho cùng route. Mode là ý định mới của KTV; nếu mode và data không đổi, reoptimize có thể không tạo thứ tự mới. Không thêm randomness.

**Còn phải chốt trước khi code logic mode:** workbook/PDF nêu default 70% SLA + 30% distance, SLA 100%, distance 100%; QHĐ hiện tại dùng objective theo tier. Cần định nghĩa rõ từng mode ánh xạ vào objective ra sao. Default 70/30 không được tự quy đổi ngầm; nếu cần sửa objective QHĐ thì trình bày/duyệt riêng trước khi đổi `dp.cpp`.

Phase 8 bắt đầu bằng design gate cho request/response, owner snapshot/baseline và mode semantics. Sau đó mới thêm application function/use case `reoptimize(...)` riêng, gọi chung normalization/SLA/travel/QHĐ/cluster pipeline. Không định nghĩa candidate payload/API trước khi các owner được chốt.

**Luồng reoptimize xuyên hệ thống (dự kiến, theo hướng Phase 7):** Mobix → `ktv_gateway` (API reoptimize, kèm mode) → lấy state cache + baseline (owner baseline chốt ở Phase 8) → use case reoptimize → Kafka OUT → phía Mobix đọc.

### Phase 9 — Feedback/AI learning

- Chỉ sau khi chốt nguồn check-in/out/thứ tự thực tế, storage/topic và versioning.
- Log suggestion/run + rule/model version; nối kết quả thật; backtest/guardrail trước khi cập nhật model hoặc trọng số.

### Handoff cho model triển khai

Giao **một phase mỗi lần**, không giao “implement toàn bộ architecture”. Với mỗi phase, yêu cầu model:

1. Chỉ sửa file nằm trong phase đó; không sửa `dp.cpp`/objective.
2. Chạy test hiện có + test phase tương ứng.
3. Báo file thay đổi, invariant đã giữ, test command/kết quả và điểm còn block.
4. Chờ review trước khi bắt đầu phase kế tiếp.

## 10. Đối chiếu plan ban đầu trong `docs/STAGING_DATA_SPEC.md`

| Ý trong plan ban đầu | Tình trạng hiện tại | Quyết định |
|---|---|---|
| Tách parse contract khỏi xử lý | Parser và normalization đã tách; `Message/Task` vẫn là typed contract model được domain dùng trực tiếp | Chưa tạo bản sao DomainTask/DTO thứ hai. Một nguồn staging chưa đủ lợi ích để phải mapping hai bộ struct; xem lại khi có nguồn/schema thứ hai |
| Normalization lifecycle/current/completion | Đã làm ở Phase 2; `staff_plots_id=0 && staff_role=0` bị deferred | Giữ policy status 6/10/khác; không tự bật role 0 |
| KPI/SLA/rule và route | SLA đã tách; QHĐ vẫn chấm theo tier rules | Không thêm scalar pre-sort score hoặc scorer thứ hai khi chưa có công thức nghiệp vụ |
| Gom cụm và output | ✅ Phase 4 + 5.2: `summarize_clusters` trong `cluster.cpp`, `plan.cpp` chỉ còn điều phối | Không thay optimizer |
| ProcessWorklist, batch | `plan()` là entrypoint dùng chung; một message = một KTV; batch là driver gọi `plan()` N lần | Không tạo service class. Batch chậm thì tối ưu **worker/parallelism/OSRM**, không viết lại `plan()`. Chỉ tách `plan_batch` khi batch cần **đánh đổi khác** (solver xấp xỉ, chia sẻ ma trận OSRM, deadline riêng), không phải khi chỉ cần nhanh hơn |
| Reoptimize | Chưa được implement; KTV chọn mode và route hợp lệ được thay route đang xem | Phase 8 (sau Kafka); owner của snapshot/baseline vẫn chưa chốt, không tự mặc định Gateway/OA/Core |
| Local adapter | ✅ Phase 5 + 5.1 hardening | CLI là harness chuẩn cho test local |
| Kafka | ✅ đọc IN (7.1) + hardening; produce OUT ⏸ tạm hoãn | Phase 7B khi có topic OUT và đã bàn với OA/Mobix |
| Gateway read model | ✅ Phase 6.1–6.3, nhưng là **đồ nghề dev** | Production dùng API kích hoạt Phase 7, không phải `GET worklist` |
| API cho Mobix | Gateway API của team (`ktv_gateway`) expose `replan`; chỉ trả `202`, kết quả đi Kafka OUT. Bot Gateway là phía Mobix | Phase 7A; không tạo API riêng cho normalization/rule/cluster/DP |
| Feedback để học model/weights | Chưa có nguồn feedback contract hoặc DB/topic | Phase 9; cần versioning và actual outcomes từ OA/Gateway |

### Luồng chạy cần nắm

```text
local file / Kafka record
  → read record + envelope
  → parse_message (api.cpp)
  → normalize_worklist (status/current/completion/location)
  → resolve_deadlines + build Problem (sla.cpp / plan.cpp)
  → travel matrix (travel.cpp)
  → dp::solve (dp.cpp; thứ tự)
  → cluster split + schedule/metrics output (cluster.cpp + plan.cpp)
  → business response JSON
  → local wrap_response / (7B) Kafka producer OUT
```

## 11. Decisions intentionally not made here

Đã biết (2026-09-30): queue dev `kafka-queue-dev-*`, SASL PLAIN, group theo quy ước `chatbot-ftel-*`, topic IN 3 partition (xem README gốc).

- Topic OUT, partition key OUT, retry/DLQ settings (Phase 7B, tạm hoãn).
- Envelope thật từ OA (header hay body, có `message_id/planned_at` không): chờ message đầu tiên.
- Hợp đồng dữ liệu còn hở (bảng số `task_type_id`, `staff_role = 0`, status ngoài 6/10…): tạm chạy bằng
  parse nới lỏng + sổ câu hỏi `docs/DATA_QUESTIONS.md`; chốt dần khi team data trả lời.
- Cách producer thông báo batch completion; hiện mỗi message là một KTV.
- Formula cho output `priority`, và tiêu chí `MAIN/INSERTED`/`insert_reason` khi bật Rule 5.
- OT nhiều cửa sổ, route giữ tuyến cũ, reoptimize threshold và batch-level API.

## 12. Brainstorm lên prod (ý tưởng, chưa phải kế hoạch)

> Danh sách mở, chưa ưu tiên, chưa cam kết. Mỗi ý có cách làm cụ thể để khi chọn thì biết bắt đầu từ đâu.
> Khi một ý được chọn thì chuyển thành phase có exit gate ở mục 9; ý nào đổi thuật toán/objective phải
> trình bày và duyệt trước (không sửa `dp.cpp` ngầm).

Số liệu gốc để so: benchmark 5.332 message, chim bay p95 < 1 ms, max ~130 ms (12 việc chính xác);
OSRM tự host p50 3 ms, p95 10 ms. Topic IN 3 partition. `max_exact_tasks = 12`, `max_labels = 32`,
2-opt chỉ tới 40 việc, `mask` 64 bit (tối đa 63 việc/KTV, hơn thì 422).

### 12.1 Scale thuật toán

**A1. Ngân sách thời gian mỗi lần gọi (anytime).** Thêm `Rules.time_budget_ms` (VD 300). `plan()` chạy
tham lam + 2-opt trước (vài ms) để luôn có lời giải; rồi chạy QHĐ, kiểm đồng hồ mỗi `mask` (vòng ngoài của
`exact()`), quá ngân sách thì bỏ và trả lời giải heuristic, `sequence_source = HEURISTIC`, kèm lý do
`TIME_BUDGET`. Lợi: nâng `max_exact_tasks` lên 14–15 mà không sợ đuôi dài. Test: bài 15 việc với ngân sách
1 ms phải trả heuristic hợp lệ; ngân sách lớn phải trùng vét cạn.

**A2. Cắt nhánh QHĐ bằng cận dưới.** Trước QHĐ đã có lời giải heuristic → khóa `K_heur`. Khi mở rộng nhãn,
nếu khóa tầng 1 hiện tại (số điểm trễ đã chắc chắn) đã lớn hơn `K_heur[0]` thì bỏ nhãn đó. Cận dưới rẻ:
việc chưa làm có `due` < `clock + leg nhỏ nhất tới nó` thì chắc chắn trễ → cộng vào. Không đổi kết quả
(chỉ bỏ nhãn không thể thắng), nên test vét cạn hiện có vẫn là cổng.

**A3. Heuristic tốt hơn cho KTV nhiều việc (> 12).** Hiện: tham lam + 2-opt 2 vòng. Thêm Or-opt (dời đoạn
1–3 việc sang chỗ khác) và relocate một việc, lặp tới khi không cải thiện hoặc hết ngân sách A1. Tùy chọn
sau đó: LNS (xóa ngẫu nhiên k việc rồi chèn lại tốt nhất, seed cố định để tất định). Đo trên benchmark:
số KTV `HEURISTIC` (126) có khóa tốt hơn bao nhiêu, thời gian p95.

**A4. QHĐ theo cụm cho KTV rất nhiều việc (30–63).** Cắt việc thành cụm địa lý trước (lưới ~2 km hoặc
theo `task_plots_id`), giải thứ tự giữa các cụm (ít phần tử → chính xác), rồi QHĐ trong từng cụm (≤ 12).
Nhược điểm: bỏ lỡ tuyến xen kẽ giữa cụm khi hạn gấp → chỉ dùng khi n > ngưỡng và so khóa với heuristic,
lấy cái tốt hơn.

**A5. Bộ nhớ QHĐ.** `table` là `vector<vector<int>>` kích thước `2^(n+1) × (n+1)`; ở n = 15 ~ 1 triệu ô
vector rỗng (~24 MB chỉ để header). Đổi sang mảng phẳng + offset (nhãn nằm trong một `vector<Label>`,
ô chỉ giữ `[begin, count]`) → ít cấp phát, cache tốt hơn, cần trước khi nâng ngưỡng. Parity: test `dp`.

**A6. Tính lại tăng dần khi replan.** Khi Mobix gọi replan chỉ vì vị trí đổi, phần lớn thứ tự cũ vẫn tốt.
Seed heuristic bằng thứ tự lần trước (lấy từ dedup/route cache ở 12.4), 2-opt từ đó → hội tụ nhanh và ít
"nhảy tuyến" làm KTV khó chịu. Liên quan Rule 4 (giữ tuyến cũ) ở Phase 8.

### 12.2 Scale hệ thống

**B1. Nhiều luồng trong một worker, giữ thứ tự theo KTV.** librdkafka giao message theo partition; mở
pool N luồng, băm `staff_id` (hoặc key Kafka) → luồng, mỗi luồng một hàng đợi → cùng KTV luôn xử lý tuần tự.
Commit: theo từng partition, chỉ commit offset liên tục đã xong (giữ `std::map<offset, done>` mỗi partition,
commit tới lỗ hổng đầu tiên). Hiện tại 1 luồng ~1 ms/message chim bay → ~1.000 msg/s; OSRM ~10 ms → ~100 msg/s
mỗi worker, đủ cho batch 6h với vài nghìn KTV. Chỉ làm khi đo lag thật cao.

**B2. Nhiều replica.** Số consumer có ích tối đa = số partition (IN hiện 3). Cần thêm thì xin SYS tăng
partition topic IN, hoặc dùng B1. Gateway (7A) stateless nhờ Redis → scale ngang sau load balancer tùy ý.

**B3. Batch đầu ngày.** OA đẩy vài nghìn message cùng lúc 6h. Đo: thời gian từ message đầu tới hết lag.
Nếu OSRM là nút cổ chai: giới hạn số request OSRM đồng thời (semaphore) để không làm sập OSRM, và bật
keep-alive (hiện `osrm_matrix` tạo `httplib::Client` mới mỗi lần → mỗi lần một TCP handshake ~vài ms;
giữ một client mỗi luồng).

**B4. Backpressure.** Nếu OSRM chậm hẳn (> 3 s timeout), mọi message trả 424 chim bay → vẫn chạy nhưng
chất lượng giảm. Thêm circuit breaker: 20 lần OSRM lỗi liên tiếp thì bỏ gọi OSRM 30 s (đi thẳng chim bay),
đỡ tốn 3 s timeout mỗi message; `/healthz` báo `osrm: "open"`.

### 12.3 Docker, môi trường, cấu hình

**C1. Một image, nhiều vai.** Image hiện có đủ `ktv_core`/`ktv_worker`/`ktv_gateway`. Deploy mỗi vai một
service với cùng image tag (gắn git SHA: `ktv-core:<sha>`), chỉ khác `command`. Không build riêng từng vai.

**C2. Compose cho staging.** `compose.prod.yaml` (hoặc k8s manifest) gồm: `worker` (replica 1–3),
`gateway` (replica ≥ 2), `redis` (có AOF, hoặc Redis công ty), `osrm` (volume dữ liệu bản đồ, chỉ mạng nội
bộ). Mọi cổng chỉ mở trong mạng nội bộ; gateway ra ngoài qua reverse proxy/ingress có TLS.

**C3. Cấu hình theo môi trường.** Giữ quy ước hiện có: biến môi trường đè `.env`. Tách 3 file mẫu
`.env.dev/.env.staging/.env.prod` (không chứa mật khẩu). Mật khẩu Kafka/Redis/token gateway lấy từ secret
của nền tảng (k8s Secret, Vault, hoặc `docker secret`), không để trong image hay git. Worker in `describe()`
lúc khởi động (đã không in password) để soát cấu hình.

**C4. Rules theo phiên bản.** `rules.json` là cấu hình nghiệp vụ, không nên đóng vào image: mount từ
ConfigMap/volume. Thêm field `version` trong rules và ghi vào mọi response/log (để biết tuyến nào tính bằng
bộ trọng số nào — cần cho learning 12.8). Đổi rules = đổi file + restart (hoặc SIGHUP reload, làm sau).

**C5. Healthcheck trong image.** Thêm `HEALTHCHECK` trong Dockerfile gọi `/healthz` (cần `curl` hoặc một
lệnh nhỏ trong `ktv_core`). Với k8s dùng `livenessProbe` → `/healthz`, `readinessProbe` → đã kết nối
Kafka/Redis.

**C6. Build tái lập.** Ghim base image theo digest, ghim phiên bản apt (librdkafka 1.8 hiện dùng). Khi cần
SCRAM thì tầng build tự build librdkafka 2.x (công thức ở README).

### 12.4 Redis

**D1. Khóa và TTL (7A).** `ktv:state:{staff}` = message IN mới nhất (TTL 2 ngày, hết ngày là vô nghĩa);
`ktv:dedup:{staff}` = fingerprint (TTL 1 ngày). Nếu cần tách theo ngày: `ktv:state:{staff}:{date}`.
Ghi state có điều kiện: chỉ ghi đè khi message mới hơn (so `planned_at` hoặc offset) để message đến trễ
không làm state lùi — dùng Lua script `if new > old then SET`.

**D2. Cache ma trận OSRM.** Nhiều KTV cùng chi nhánh đi qua cùng các điểm. Khóa theo cặp tọa độ đã làm tròn
5 chữ số (~1 m): `ktv:leg:{lat1,lng1}:{lat2,lng2}` → `km,phút`, TTL 7 ngày (bản đồ đổi chậm). Trước khi
gọi OSRM, `MGET` các cặp; thiếu mới gọi `/table` phần thiếu. Chỉ đáng làm nếu OSRM là nút cổ chai (đo trước).

**D3. Route cache cho Mobix (nếu sau này cần).** Kết quả `plan()` mới nhất: `ktv:route:{staff}:{date}`
— đã có `RedisStore` (Phase 6.3, 7.2). Theo hướng 2026-10-01 đây **là** nguồn trả route cho Mobix (Phase 7), nhưng hữu ích để A6 và để debug "lần trước trả gì".

**D4. Vận hành Redis.** Bật AOF (`appendonly yes` như compose), `maxmemory` + `volatile-lru` (mọi khóa đều
có TTL). Kích thước ước lượng: message IN ~5–20 KB × vài nghìn KTV → vài chục MB. Dùng Redis công ty nếu có
(Sentinel/Cluster); code hiện dùng hiredis một node → cần thêm cấu hình Sentinel nếu bắt buộc HA.

### 12.5 Store lâu dài (database)

**E1. Nhật ký tuyến đã gợi ý (bắt buộc cho learning).** Mỗi lần `plan()`: `run_code`, `message_id`,
`staff_id`, `planned_at`, `server_time`, `rules_version`, `travel_source`, `sequence_source`, hash input,
thứ tự task + ETA/giờ xong dự kiến từng task, metrics, cảnh báo dữ liệu, thời gian tính. Không cần full
payload trong DB — full payload ghi ra object storage (S3/MinIO) dạng JSONL nén theo ngày
(`s3://ktv-log/in/2026-10-01/part-*.jsonl.gz`), DB chỉ giữ con trỏ.

**E2. Chọn DB.** Truy vấn chủ yếu là phân tích (theo ngày/chi nhánh/KTV) → ClickHouse hoặc PostgreSQL có
partition theo ngày. Bắt đầu đơn giản: PostgreSQL, bảng `route_run` (1 dòng/lần tính) + `route_stop`
(1 dòng/task trong tuyến). Ghi không đồng bộ: worker đẩy vào một topic Kafka `ktv-audit` (hoặc file JSONL),
một job riêng nạp vào DB → không để DB chậm làm chậm đường realtime.

**E3. Giữ bao lâu.** Log chi tiết 90 ngày, tổng hợp theo ngày giữ lâu. Tọa độ khách/KTV là dữ liệu nhạy
cảm → che bớt (làm tròn 3 chữ số) trong bảng phân tích, bản đầy đủ chỉ trong object storage có phân quyền.

### 12.6 OSRM

**F1. Chạy ở đâu.** Một service nội bộ (2,6 GB RAM, CPU cho `/table`), 2 replica sau load balancer để
không phụ thuộc một máy. Dữ liệu bản đồ dựng sẵn thành image riêng `ktv-osrm:<ngày-bản-đồ>` → deploy như
code, rollback được.

**F2. Cập nhật bản đồ.** Job hàng tháng: tải `vietnam-latest.osm.pbf`, extract + contract (~5 phút, 13 GB
RAM lúc extract), build image mới, chạy smoke test (vài cặp tọa độ cố định so với bản cũ, lệch > 20% thì
dừng), rồi đổi tag.

**F3. Xe máy thay ô tô.** KTV đi xe máy; profile `car.lua` bỏ qua hẻm và cấm đường ngược với xe máy. Thử
profile tùy chỉnh (copy `car.lua`, cho phép `highway=path/footway` hẹp, tốc độ xe máy) và so km với lịch sử
check-in thật (BUSINESS_RULES Q21).

**F4. Giờ cao điểm.** OSRM không biết kẹt xe. Rẻ nhất: nhân hệ số thời gian theo khung giờ (VD 7–9h và
16:30–19h × 1,4), cấu hình trong `rules.json`, học hệ số từ dữ liệu thật (12.8).

### 12.7 Quan sát (observability)

**G1. Metrics.** `/healthz` hiện đã có bộ đếm. Thêm `/metrics` dạng Prometheus (text đơn giản, không cần
thư viện): `ktv_messages_total{status}`, `ktv_plan_ms` (histogram), `ktv_osrm_errors_total`,
`ktv_data_issues_total{code}`, `ktv_sequence_source_total{source}`. Consumer lag lấy bằng Kafka exporter
có sẵn, không tự tính.

**G2. Log có cấu trúc.** Hiện log là chữ tự do ra stderr. Đổi sang một dòng JSON mỗi sự kiện
(`{"ts","level","event":"message_done","message_id","staff_id","status","ms"}`) để đưa vào ELK/Loki và lọc
theo `message_id`. Không log tọa độ thô và token.

**G3. Cảnh báo.** Lag > N phút; tỉ lệ 400/500 > x%; `/healthz` 503; OSRM lỗi liên tục; `data_issues` xuất
hiện mã mới (loại dữ liệu lạ lần đầu) → báo kênh team.

**G4. Truy vết end-to-end.** Giữ `message_id`/`run_code` xuyên suốt IN → state → replan → OUT → OA. Khi
Mobix gọi replan, `X-Request-ID` ghi cùng `run_code` → từ một khiếu nại của KTV lần được payload, rules
version, ma trận travel đã dùng.

### 12.8 Học từ dữ liệu (ML) và LLM

Nguyên tắc: **không có mô hình nào nằm trên đường realtime nếu không tất định và rẻ.** QHĐ tất định;
mô hình học chỉ cập nhật tham số (bảng số trong `rules.json`), LLM chỉ ở ngoài đường tính tuyến.

**H1. Vòng dữ liệu cần có trước.** Hai nguồn phải nối được theo `task_id`:
(a) nhật ký gợi ý (E1): ta đã dự đoán thứ tự nào, ETA nào;
(b) kết quả thật: giờ check-in/checkout, thứ tự thực tế KTV đã đi — lấy từ OA/QOS (cần hỏi team data
nguồn nào, định kỳ hay topic). Bảng join `route_outcome(run_code, task_id, eta_pred, checkin_actual,
done_pred, done_actual, order_pred, order_actual)`. Không có (b) thì không học được gì.

**H2. Học thời gian (đã từng làm bằng Python, 2026-09-13).** Thời gian xử lý: median theo KTV (≥ 10 lượt)
→ theo loại → chung; thời gian di chuyển: bảng theo km × khung giờ. Kết quả trước: MAE thời gian làm
59 → 37 phút. Làm lại dưới dạng job offline (Python/SQL, không cần vào core) xuất `time_model.json`
có `version`; core đọc như một phần rules (thêm bảng tra `handle_minutes` theo KTV/loại). Job chạy tuần,
chỉ phát hành khi backtest không tệ hơn bản đang chạy.

**H3. Tinh chỉnh trọng số rule.** Trọng số tầng 3 (km, phút trễ, quay lại khu vực…) hiện là giả định.
Offline: cho QHĐ chạy lại các ngày cũ với nhiều bộ trọng số (grid nhỏ hoặc Bayesian optimization),
chấm bằng mô phỏng theo dữ liệu thật (số trễ, km), chọn bộ tốt nhất; ràng buộc: không bao giờ tăng số
việc trễ để đổi lấy km (tầng 1 thắng). Gọi core qua CLI `ktv_core plan --rules x.json` (không cần binding).

**H4. Học "KTV thực sự đi thế nào".** Backtest cũ: gần nhất đoán đúng việc kế tiếp 45%, QHĐ 37%. Có thể
thêm một rule tầng 3 phản ánh thói quen (VD phạt đổi hướng/quay đầu) với trọng số học từ H1. Đây là thay
objective → trình bày và duyệt trước.

**H5. Chỗ LLM hợp lý (ngoài đường tính).**
- *Giải thích tuyến cho KTV/điều phối*: đầu vào là response + chi phí từng rule (đã có trong QHĐ), LLM viết
  1–2 câu tiếng Việt "đi A trước vì hẹn 10h, B cùng khu với C". Chạy bất đồng bộ hoặc khi người dùng bấm xem,
  không chặn replan. Có mẫu câu cố định làm fallback.
- *Phân loại phản hồi*: nếu app có ô "vì sao không đi theo gợi ý", LLM gắn nhãn lý do (khách đổi giờ, đường
  cấm, kẹt xe, thiếu vật tư) → thành feature cho H3/H4.
- *Trợ lý sổ câu hỏi dữ liệu*: đọc `data_issues` + mẫu payload, soạn nháp câu hỏi gửi team data kèm ví dụ.
- Cần: bảng lưu prompt/response/model version cho mọi lần gọi (audit), che PII (tên, SĐT, địa chỉ) trước khi
  gửi ra ngoài, hạn mức chi phí, và đánh giá thủ công một tập mẫu trước khi bật.

**H6. Không làm.** Không để LLM chọn thứ tự tuyến trực tiếp: không tất định, khó kiểm chứng, chậm, và
không đảm bảo ràng buộc cứng (hẹn, ca, nghỉ trưa) mà QHĐ đang bảo đảm.

**H7. Phát hành mô hình/trọng số an toàn.** Mỗi bản `rules.json`/`time_model.json` có version; chạy
**shadow** (tính song song bản mới, chỉ log, không gửi) vài ngày, so số trễ dự báo/km/ETA error với bản
đang chạy; đạt thì bật cho 1 chi nhánh (theo `staff_location`), rồi toàn bộ. Rollback = trỏ lại version cũ.

### 12.9 Độ tin cậy

**I1. Replay.** Vì OUT/IN là Kafka có offset, có thể chạy lại một khoảng thời gian bằng group mới +
`auto.offset.reset` hoặc reset offset tới timestamp — dùng khi sửa bug tính sai. Cần `run_code` tất định
theo input để downstream nhận ra bản tính lại (đã có: `run_code = message_id`).

**I2. DLQ.** Message gây 500 hiện được commit và bỏ qua. Ghi nguyên payload + lỗi vào topic
`ktv-dlq` (hoặc file) để tra và phát lại sau khi sửa.

**I3. Chống bão replan.** Mobix có thể gọi dồn (GPS cập nhật liên tục). Ngoài dedup: làm tròn `latlng`
(~50 m) trong fingerprint để vị trí xê dịch nhỏ không tính lại; giới hạn 1 lần tính / KTV / 30 s
(Redis `SET ktv:lock:{staff} NX EX 30`), lần gọi trong khoảng đó trả 202 và đánh dấu "tính lại sau".

**I4. Thời gian.** Mọi mốc giờ đang là giờ VN không múi giờ. Server prod phải chạy UTC chuẩn
(`vietnam_now` cộng 7 giờ từ `time()` — không phụ thuộc TZ máy, tốt); cần NTP để `server_time` và
"giờ lập tuyến" không lệch.

### 12.10 Bảo mật

**J1.** Gateway: token thật (JWT của hệ thống Mobix/SSO, kiểm chữ ký + `staff_id` trong token khớp path),
không dùng token tĩnh `--token` như hiện tại. **J2.** Kafka: mỗi môi trường một tài khoản, quyền tối thiểu
(READ IN, WRITE OUT). **J3.** Redis: có mật khẩu (đã có `--redis-password`), không mở ra ngoài mạng nội bộ.
**J4.** Log/DB: không lưu tọa độ và địa chỉ đầy đủ quá thời hạn cần (E3). **J5.** Image quét lỗ hổng trong
CI (Trivy) trước khi deploy.

### 12.11 Kiểm thử và phát hành

**K1. Golden set từ dữ liệu thật.** Khi có message thật từ OA: lấy mẫu vài trăm message (ẩn danh), commit
vào repo làm fixture; mỗi thay đổi core phải cho kết quả giống hệt, hoặc diff được giải thích. Thay cho
benchmark giả không nằm trong git.

**K2. Test tải.** Script đẩy N nghìn message vào Kafka local (đã có cách ở README) và đo thời gian tới hết
lag, p95 `plan_ms`, CPU OSRM → biết cần bao nhiêu replica trước giờ batch 6h.

**K3. Canary.** Deploy bản mới cho 1 replica worker (cùng group) → nhận ~1/3 partition; so `/metrics` với bản
cũ vài giờ rồi mới thay hết.

### 12.12 Tính năng sản phẩm (sau khi luồng chính ổn)

- **Tuyến thay thế**: trả thêm 1–2 thứ tự khác gần tốt nhất (lấy từ các nhãn còn lại của QHĐ ở trạng thái
  cuối, hoặc chạy lại với một rule tắt) để KTV chọn — nối vào Phase 8 reoptimize.
- **Đường đi trên bản đồ**: gọi OSRM `/route` cho các chặng đã chọn, trả polyline (BUSINESS_RULES Q26);
  chỉ gọi khi app cần hiển thị, không gọi trong `plan()`.
- **Giải thích bằng số**: chi phí từng rule của tuyến chọn so với tuyến "gần nhất trước" — cho điều phối
  thấy vì sao không đi điểm gần nhất.
- **Cảnh báo quá tải**: KTV có tổng thời gian vượt ca → báo sớm cho OA để chia lại việc (routing chỉ báo,
  không gán lại — đúng phạm vi team).
