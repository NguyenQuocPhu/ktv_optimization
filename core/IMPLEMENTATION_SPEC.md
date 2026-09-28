# Core AI — Implementation Specification

> Phân rã triển khai dựa trên code C++ và contract đang có. Đây là tài liệu kế hoạch,
> chưa sửa source. QHĐ hiện tại là lõi đã có; không thay thuật toán trong các bước dưới đây.

## 1. Phạm vi

### Trong phạm vi

- Nhận một payload công việc của một KTV.
- Parse/validate contract, chuẩn hoá status và dữ liệu staging.
- Áp SLA/rule hiện có, gọi route optimizer hiện có, dựng cụm và response theo workbook API mới.
- Chạy local bằng fixture/file trước khi có Kafka broker.
- Sau khi được cấp broker, consume IN → xử lý một message → produce OUT.
- Batch đầu ngày là nhiều message KTV đi qua cùng pipeline.
- Replan tự động do thay đổi dữ liệu (hoàn tất/task mới/hẹn lại) và reoptimize do KTV không hài lòng là hai use case khác nhau; chưa được gộp thành "snapshot mới + trigger".

### Ngoài phạm vi của refactor này

- Thay QHĐ, đổi objective, đổi ngưỡng exact/heuristic, hoặc viết lại `dp.cpp`.
- Học ML/GBM, tune trọng số tự động, training/backtest runtime.
- Tách sáu bước thành sáu microservice hoặc sáu HTTP endpoint.
- HTTP API cho Mobix: Mobix gọi Bot Gateway; Core AI xử lý qua Kafka theo sơ đồ hiện tại.
- Lưu Oracle/Store trong Core AI. Hai consumer OUT độc lập thuộc Optimal Assign và Bot Gateway.
- Break/OT mới ngoài phần break trưa đang có; nhiều khung giờ làm vẫn là phần chưa triển khai.
- Auto-insert task (Rule 5) trừ khi có spec riêng về cách chọn, feasibility và `insert_reason`.

## 2. Snapshot code hiện tại

| File | Hiện làm gì | Phần cần giữ / thay đổi |
|---|---|---|
| `include/ktv/api.hpp`, `src/api.cpp` | DTO `Message/Staff/Task`, JSON parser/validator, TaskKind catalog, datetime | Giữ vai trò contract boundary; bổ sung field staging/API mới và bỏ lỗi duplicate current task |
| `include/ktv/rules.hpp`, `src/rules.cpp` | Tier rules, priority weights, config JSON; hiện có lunch-break config | Giữ nguyên objective và mặc định; QHĐ tiếp tục chấm route theo tier |
| `include/ktv/travel.hpp`, `src/travel.cpp` | Haversine/OSRM matrix, fallback đường chim bay × 1.3 | Giữ nguyên |
| `include/ktv/dp.hpp`, `src/dp.cpp` | QHĐ, nhãn Pareto, heuristic + 2-opt; working tree hiện đã có break như việc ảo | **Không đổi thuật toán**. Chỉ sửa khi có ticket thuật toán riêng |
| `include/ktv/plan.hpp`, `src/plan.cpp` | Orchestration hiện tại: lọc thiếu tọa độ, deadline, matrix, DP, response; output hiện gộp 1 cluster | Chia trách nhiệm nội bộ; giữ `plan()` làm entrypoint tương thích trong giai đoạn refactor |
| `src/main.cpp` | CLI đọc JSONL, parse, gọi `plan()`, ghi response JSONL | Dùng làm file adapter prototype; sau này thêm Kafka worker riêng |
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
- Trường hợp `staff_plots_id=0 && staff_role=0` được data mô tả là KTV không thuộc lô cho task đó, nhưng **tạm ghi nhận, chưa implement**. Hiện validation từ chối `staff_role=0`; không route theo giả định riêng và không đổi rule.
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
- OUT topic có hai consumer group độc lập: Optimal Assign lưu Oracle; Bot Gateway phục vụ Mobix. Core AI không giữ response store.

## 4. Module/file specification

### 4.1 Module hiện hữu cần sửa

| File | Function/type | Trách nhiệm sau refactor |
|---|---|---|
| `include/ktv/api.hpp` | `Staff`, `Task`, `Message`, `Error`, `TaskKind` | Bổ sung DTO fields: `Staff.status`, `Task.create_date`, `Task.complete_date`, `Task.contract_id`, `Task.contract_no`. `Message` giữ envelope metadata. Không đưa thuật toán vào DTO |
| `src/api.cpp` | `parse_message`, internal `Reader`, `task_kinds`, `find_kind` | Parse/validate fields đã biết; chấp nhận task role 1–3, từ chối role 0 trong scope này; handle 0/null/`""`; parse dates/contracts; bỏ lỗi khi `current_task` row trùng ID; giữ strict rejection với field không nằm trong contract. Cập nhật catalog TaskKind/subtype theo workbook mới; không dùng các type ID cũ tự đặt |
| `include/ktv/plan.hpp` | `PlanResult`, `plan`, `error_response` | Giữ hàm `plan(...)` làm API nội bộ ổn định cho CLI và worker; không cho adapter gọi `dp::solve()` trực tiếp |
| `src/plan.cpp` | `deadlines`, `projected_sla`, `plan` | Điều phối normalize → deadline/problem construction → travel → `dp::solve` → cluster/output. Dùng `create_date` cho hạn theo ngày tạo; không sửa cách QHĐ chọn thứ tự |
| `src/main.cpp` | CLI `plan/validate/print-rules` | File adapter prototype: đọc staging JSON một object hoặc JSONL, tạo envelope local nếu chưa có, gọi cùng `plan()`, ghi một response mỗi message. Không đặt nghiệp vụ status trong CLI |
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
| Reoptimize do KTV | Use case riêng, có context của lần gợi ý và ý định/điều kiện user | Không coi là replan thường; contract và tiêu chí chấp nhận kết quả phải chốt ở Phase 6 |

### 4.4 Adapter về sau

Khi có broker/config client, thêm một Kafka worker adapter riêng (ví dụ `src/kafka_worker.cpp`) thay vì gắn Kafka vào `api.cpp`, `plan.cpp` hoặc `dp.cpp`:

1. Consume một IN message.
2. Parse/validate; lỗi dữ liệu tạo OUT lỗi và không retry vô hạn.
3. Gọi cùng pipeline `plan()`.
4. Produce OUT; chỉ sau khi producer xác nhận mới commit input offset.
5. Lỗi tạm thời của runtime/broker retry; policy DLQ và max attempts chốt với Infra.

Worker xử lý ít nhất một lần (at-least-once); downstream phải deduplicate theo `run_code/message_id`. Chưa hứa end-to-end exactly-once.

## 5. Dependency graph

```text
Local file adapter (main.cpp) ─┐
Future Kafka worker adapter ───┴─> parse_message (api)
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
- Gateway HTTP nằm ngoài Core AI. Không thêm HTTP API cho từng stage.

## 6. Invariants

1. Mỗi input là đúng một KTV; `staff_id` là string và giữ số 0 đầu.
2. Chỉ status 6 trở thành candidate; status 10 chỉ là current theo `staff.current_task`; mọi status khác bị loại.
3. `complete_date` có giá trị luôn loại task, kể cả status 6.
4. `staff.current_task` không bao giờ xuất hiện lần thứ hai như một TASK stop. Nếu row task cùng ID có mặt, chỉ enrich current context.
5. Task status/plot filtering diễn ra trước khi dựng `Problem` và trước khi gọi OSRM.
6. Task thiếu tọa độ không vào DP. `staff_role` chỉ nhận 1/2/3 trong scope hiện tại; cặp `staff_plots_id=0 && staff_role=0` chưa hỗ trợ và không được diễn giải thành role khác.
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
| 6. Reoptimize use case | Chốt request/context và policy riêng cho thao tác KTV; test local, chưa cần broker | Có input phân biệt với replan thường; kết quả/compare semantics được business duyệt |
| 7. Kafka worker | Chỉ bắt đầu khi có broker/config/topic contract; dispatch message vào replan hoặc Reoptimize | Produce OUT thành công trước commit IN; retry/replay không làm sai correlation/run_code |
| 8. Gateway/API | Ngoài Core AI; phối hợp Bot Gateway/Optimal Assign | GET cho Mobix đọc bản mới nhất; nút reoptimize gửi đúng command/context Phase 6 |
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

### Phase 5.2 — Pipeline readability (khuyến nghị trước Kafka)

Hiện `cluster.cpp` chỉ chia `ClusterSpan`; `plan.cpp` vẫn tạo schedule JSON, tính center/radius, ghép tên plot, phân TASK vào cluster và gom metrics. Điều này chạy đúng nhưng ranh giới module chưa rõ như plan ban đầu.

- Tách một helper thuần nhận ordered stop data + plot metadata và trả cluster outputs/aggregates.
- `plan()` giữ phần điều phối: normalize → SLA/Problem → travel → `dp::solve()` → cluster/output → metrics/envelope.
- Không đổi `dp::solve`, thứ tự, ETA, score hay ngưỡng cluster; dùng golden test hiện có và test nhiều cụm để chứng minh parity.
- Chỉ tạo struct dữ liệu trung gian nhỏ nếu cần; không tạo service class/interface/factory riêng cho một implementation.

**Lợi:** luồng xử lý dễ đọc, cluster có thể test độc lập. **Hại:** phải di chuyển một khối response lớn, có nguy cơ lệch field/metrics. Khuyến nghị làm thành diff riêng sau hardening CLI, trước Kafka; nếu diff phình hoặc khó giữ output parity thì để lại và chỉ cải thiện tên helper.

### Phase 6 — Reoptimize do KTV yêu cầu (design gate trước khi code)

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

Phase 6 bắt đầu bằng design gate cho request/response, owner snapshot/baseline và mode semantics. Sau đó mới thêm application function/use case `reoptimize(...)` riêng, gọi chung normalization/SLA/travel/QHĐ/cluster pipeline. Không định nghĩa candidate payload/API trước khi các owner được chốt.

### Phase 7 — Kafka adapter

- Chỉ thực hiện khi có broker access, auth, consumer group, partition/key và client library chuẩn của công ty.
- Worker dispatch record tự động vào replan; record reoptimize đi vào use case Phase 6 sau khi contract và owner của input được chốt.
- Consume mỗi record; produce response OUT rồi mới commit offset. Xử lý at-least-once, correlation theo `message_id`, `run_code` ổn định theo command.
- Một message vẫn là một KTV. Batch đầu ngày là throughput của nhiều record; không tạo payload/solver batch khác.
- Lỗi dữ liệu trả lỗi OUT; retry/DLQ chỉ áp dụng lỗi transport/runtime theo policy Infra.

### Phase 8 — Gateway/API

- Ngoài Core AI; phối hợp Bot Gateway/Optimal Assign.
- Gateway sở hữu HTTP đọc route mới nhất cho Mobix và là một phần của luồng nhận thao tác reoptimize.
- Owner cung cấp snapshot/baseline cho reoptimize chưa được quyết định; Phase 6 phải chốt việc này. Không expose từng bước pipeline thành API.

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
| Gom cụm và output | Đã có split theo leg, nhưng cluster aggregation/schedule JSON vẫn nằm trong `plan.cpp` | Thêm Phase 5.2 vì giúp hiểu luồng; không phải thay optimizer |
| ProcessWorklist, batch | `plan()` là entrypoint dùng chung; một message = một KTV | Không tạo service class riêng. Batch là nhiều record |
| Reoptimize | Chưa được implement; KTV chọn mode và route hợp lệ được thay route đang xem | Phase 6 riêng; owner của snapshot/baseline vẫn chưa chốt, không tự mặc định Gateway/OA/Core |
| Local adapter | Đã làm Phase 5; có hai edge cases CLI đã tái hiện | Phase 5.1 hardening trước khi coi local CLI là harness chuẩn |
| Kafka + hai consumer OUT | Chưa làm, thiếu broker details | Phase 7 sau khi Infra cấp contract; không viết adapter giả broker |
| API cho Mobix | Không thuộc Core AI theo sơ đồ đã chốt | Bot Gateway expose HTTP; không tạo API riêng cho normalization/rule/cluster/DP |
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
  → local wrap_response / Kafka producer OUT
```

## 11. Decisions intentionally not made here

- Kafka broker/security, group IDs, partition count, retry/DLQ settings và xác nhận key.
- Cách producer thông báo batch completion; hiện mỗi message là một KTV.
- Formula cho output `priority`, và tiêu chí `MAIN/INSERTED`/`insert_reason` khi bật Rule 5.
- OT nhiều cửa sổ, route giữ tuyến cũ, reoptimize threshold và batch-level API.
