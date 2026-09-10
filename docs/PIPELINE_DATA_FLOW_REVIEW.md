# Review pipeline và data flow KTV Optimizer

> Tài liệu này mô tả **code đang chạy ở hiện tại**, không mô tả kiến trúc kỳ
> vọng trong tương lai. Mục tiêu là giúp review nghiệp vụ: dữ liệu nào đi vào,
> bị biến đổi ra sao, rule nào quyết định kết quả và state nào được giữ qua các
> snapshot.

## 1. Kết luận nhanh

Project hiện có ba luồng khác nhau, không nên xem chúng là một pipeline duy
nhất:

| Luồng | Entry point | Mục đích | Có dùng previous state? |
|---|---|---|---|
| Operational multi-snapshot | `application/process_snapshot.py` | Luồng chính để chạy từng batch checklist event | Có, checkpoint versioned và atomic |
| Stateless optimizer | `application/run_v0_optimization.py` | Chạy thử một lần, kiểm tra thuật toán | Không |
| Website user demo | `application/user_demo_server.py` | Trình diễn UX 1 KTV, 5→6 job, hoàn thành từng stop | Có state riêng của demo, **không** dùng operational reducer |

Ngoài ra còn có manifest replay để test nhiều snapshot, operational demo đầy
đủ event và một pipeline learning offline. Chúng được giải thích ở phần 11–13.

Luồng đáng dùng làm chuẩn nghiệp vụ hiện tại là:

```text
maintenance event batch + shift roster hiện tại + boundary GeoJSON
                              │
                              ▼
                    OperationalSnapshotProcessor
                              │
              map CSV ── load previous checkpoint
                              │
                              ▼
                        SnapshotReducer
           canonicalize → diff → availability → conflict
                              │
                              ▼
            compatibility graph → greedy assignment → route
                              │
                              ▼
                   output run + atomic checkpoint
```

Điểm quan trọng nhất: maintenance là **event/upsert feed**, không phải full-state
feed. Job vắng trong batch hiện tại vẫn được giữ từ checkpoint trước. Chỉ event
có status hoàn thành rõ ràng mới loại job khỏi active state.

---

## 2. Contract dữ liệu đầu vào

### 2.1. Maintenance checklist event batch

`OperationalSnapshotProcessor` chỉ đọc bảy cột sau:

| Cột | Ý nghĩa đang được dùng | Bắt buộc thực tế |
|---|---|---|
| `CHECKLIST_ID` | Khóa job để diff/link qua snapshot | Có |
| `CHECKLIST_STATUS` | Quyết định active, assignable, fixed hoặc busy | Có |
| `BRANCH_NAME` | Hard constraint Job–KTV phải cùng branch | Có |
| `CASE_TYPE` | Task type để policy, cluster và compatibility | Có |
| `OBJ_LOCATION` | Text dùng để match phường/xã | Có thể rỗng, nhưng mode location sẽ không gán được |
| `EMP_ACCOUNT` | Assignment đến từ hệ thống nguồn | Có thể rỗng tùy status |
| `CREATE_DATE` | Tính `due_at = create_at + SLA` | Có thể rỗng |

Các cột khác trong CSV gốc như `FINISH_DATE`, `FLAG_ON_TIME`, appointment hoặc
check-in **không đi vào online optimizer**.

Status được giữ trong operational V1:

| `CHECKLIST_STATUS` | Cách xử lý hiện tại |
|---|---|
| `Chưa phân công` | Job có thể được optimizer gán |
| `Đã phân công` | Giữ cứng `EMP_ACCOUNT` của hệ thống và routing |
| `Đang xử lý` | Gắn KTV là `BUSY`; không đưa vào assignment/routing mới |
| `Đã xử lý và đang theo dõi` | Cũng xem là `BUSY`; không routing |
| `Tạm dừng chờ xử lý` | Giữ KTV ở `RESERVED`; không routing |
| `Đóng checklist`, `Đã xử lý` | Explicit completion: kết thúc job và giải phóng KTV/queue |
| Mọi status khác | Không được mapper đưa vào reducer |

Một batch không cần chứa lại các checklist cũ. Vắng mặt có nghĩa là “không có
event mới”, không có nghĩa là hoàn thành. Completion được log riêng là
`COMPLETED` và được xuất trong `completed_jobs.csv` của run đó.

### 2.2. Shift roster

Hai cột bắt buộc:

| Cột | Ý nghĩa |
|---|---|
| `EMP_ACCOUNT` | Khóa KTV; phải duy nhất trong roster |
| `BRANCH_NAME` | Branch KTV đi ca |

Các cột tùy chọn:

| Cột | Default nếu thiếu | Cách dùng |
|---|---|---|
| `LATITUDE`, `LONGITUDE` | Không có location | Điểm xuất phát để tính Haversine và route |
| `SHIFT_START`, `SHIFT_END` | Không giới hạn theo ca | Suy ra `OFF_SHIFT` và tổng capacity |
| `SUPPORTED_CASE_TYPES` | Rỗng | Rỗng được hiểu là hỗ trợ **mọi** task |
| `EXISTING_WORKLOAD_MINUTES` | `0` | Tải ban đầu khi tính cost/capacity |
| `WORK_STATUS` | Tự suy ra | `IDLE/RESERVED/BUSY/UNAVAILABLE/OFF_SHIFT` |
| `CURRENT_JOB_ID` | Rỗng | Job hiện tại do nguồn runtime báo |
| `AVAILABLE_AT` | Rỗng | Thời điểm dự kiến KTV rảnh |

Roster phải chứa **tất cả KTV đi ca**, gồm cả KTV đang bận. KTV bận không biến
mất khỏi roster; trạng thái bận được lưu riêng trong technician state.

Hiện project chưa có roster thật. Các roster được sinh từ lịch sử maintenance
hoặc fixture chỉ là proxy để demo/test, không phải dữ liệu ca làm production.

### 2.3. Boundary GeoJSON

Mỗi feature cần tối thiểu:

- `properties.ma_xa`: mã phường/xã;
- `properties.ten_xa`: tên phường/xã;
- `properties.tinh_tp`: tỉnh/thành;
- geometry `Polygon` hoặc `MultiPolygon`.

Tọa độ trả về là **centroid polygon phường/xã**, không phải GPS nhà khách hàng.

### 2.4. Snapshot metadata

- `snapshot_id`: ID nghiệp vụ của lần chụp.
- `snapshot_time`: thời điểm dữ liệu có hiệu lực.
- `runtime_dir`: chuỗi checkpoint của đúng scope dữ liệu.
- mode: `task_location`, `task` hoặc `location`.

Snapshot cũ hơn checkpoint bị từ chối. Snapshot có timestamp bằng checkpoint
vẫn được chấp nhận. Hiện chưa có `scope_id`, idempotency key hoặc process lock.

---

## 3. Data flow operational theo từng bước

Entry point: [`process_snapshot.py`](../src/ktv_optimizer/application/process_snapshot.py).
Orchestrator: [`pipeline/service.py`](../src/ktv_optimizer/pipeline/service.py).

### Bước 1 — Khởi tạo run

`OperationalSnapshotProcessor.process(...)` tạo:

- `run_id = snapshot_id + timestamp lúc chạy`;
- thư mục output riêng cho run;
- `run_metadata.json` với status `RUNNING`, stage `INITIALIZE`.

Mỗi khi sang stage mới, metadata được ghi lại bằng file tạm rồi `replace`.

### Bước 2 — `VALIDATE_INPUT`

Pipeline kiểm tra ba path maintenance, roster, boundary có tồn tại hay không,
sau đó ghi SHA-256 và kích thước file.

Đây mới là **file validation**, chưa phải data validation. Các hàm
`validate_maintenance()` và `validate_checkins()` hiện chỉ được dùng ở offline
dataset builder, chưa được gọi ở operational pipeline.

### Bước 3 — `LOAD_INPUT`

- Maintenance được đọc dưới dạng string và chỉ lấy bảy cột ở phần 2.1.
- Roster được đọc toàn bộ bằng pandas.
- Số dòng raw được ghi vào metadata.

Thiếu một trong bảy cột maintenance sẽ làm pandas fail. Tuy nhiên ID rỗng,
branch rỗng, duplicate ID, GPS ngoài Việt Nam và status lạ chưa bị chặn tại đây.

### Bước 4 — `MAP_INPUT`

Ba mapper được gọi:

1. `maintenance_to_optimization_jobs()` tạo `OptimizationJob`.
2. `shift_roster_to_technicians()` tạo `TechnicianShift`.
3. `shift_roster_to_status_updates()` tạo `TechnicianStatusUpdate`.

Sau đó ghép thành một `Snapshot` immutable.

Chi tiết mapping maintenance:

```text
CHECKLIST_STATUS filter
  → trim/normalize text và parse CREATE_DATE
  → CASE_TYPE uppercase
  → lookup hard-coded TaskPolicy
  → OBJ_LOCATION match boundary
  → create OptimizationJob
```

Lưu ý: status đang được filter **trước** khi trim/normalize. Giá trị có khoảng
trắng thừa có thể bị loại trước khi được làm sạch.

Operational truyền `deduplicate=False`. Duplicate được chuyển tiếp sang state
layer, nơi `canonicalize_job_events()` log conflict và giữ event cuối theo thứ
tự CSV.

### Bước 5 — `LOAD_PREVIOUS_CHECKPOINT`

`VersionedCheckpointManager.load()` đọc `runtime_dir/latest.json`, rồi load đồng
thời:

- `optimizer_state.csv`: tất cả active job cùng plan lần trước;
- `technician_state.csv`: trạng thái runtime của KTV lần trước.

Nếu chưa có `latest.json`, cả hai checkpoint rỗng. Nếu pointer trỏ tới version
thiếu một trong ba file bắt buộc, run fail thay vì đọc state nửa vời.

### Bước 6 — `OPTIMIZE`: apply event vào previous active state

`SnapshotReducer.reduce(current, previous_jobs, previous_technicians)` là hàm
state transition chính. Nó không tự ghi file và xử lý theo thứ tự:

```text
6.1 reject out-of-order snapshot
6.2 canonicalize checklist events
6.3 apply events vào previous active jobs
6.4 derive trạng thái KTV trước tối ưu
6.5 resolve assignment cũ và system override
6.6 chọn KTV assignable + khóa reservation/current job cũ
6.7 gọi optimizer
6.8 tạo job checkpoint mới
6.9 tạo technician checkpoint mới
```

#### 6.1. Canonicalize và apply event

`canonicalize_job_events()` giữ event cuối nếu cùng checklist bị lặp trong một
batch, ghi `DUPLICATE_JOB_ID` và sort theo checklist ID.

`apply_job_events()` bắt đầu từ previous active checkpoint rồi xử lý từng event:

- active ID mới → `ADDED`;
- active ID đã có và material fields đổi → `UPDATED`;
- explicit terminal status của active ID → `COMPLETED`;
- checklist previous không xuất hiện → giữ nguyên;
- terminal event lặp cho job đã đóng → bỏ qua (idempotent);
- unchanged active event → không ghi vào `changes.csv`.

Các field được so sánh gồm status, branch, task, address, GPS, ward,
`assigned_technician`, priority, service duration, created/due time.

#### 6.2. Suy ra KTV state trước tối ưu

`derive_pre_optimization_states()` dùng bốn nguồn:

1. roster và shift time hiện tại;
2. runtime columns trong roster;
3. active job hiện tại đang claim KTV;
4. technician/job checkpoint trước.

Độ ưu tiên quyết định trạng thái, từ cao xuống thấp:

```text
ngoài thời gian ca
→ explicit OFF_SHIFT
→ explicit UNAVAILABLE
→ explicit BUSY
→ có job Đang xử lý/Theo dõi
→ có job Đã phân công/Tạm dừng/reservation optimizer cũ
→ explicit status còn lại
→ giữ UNAVAILABLE trước đó
→ giữ BUSY trước đó nếu current job vẫn active
→ IDLE
```

Vì claim của job được xét trước explicit `IDLE`, roster không thể vô tình báo
rảnh cho người vẫn có active job.

GPS technician state lấy từ **roster hiện tại**. Code operational chưa cập nhật
GPS KTV sang vị trí job khi hoàn thành, cũng không fallback GPS từ checkpoint cũ
nếu roster mới thiếu tọa độ.

#### 6.3. Resolve assignment cũ

`resolve_previous_assignments()` áp dụng:

- `Đã phân công`: assignment từ hệ thống thắng;
- thiếu `EMP_ACCOUNT`: ghi conflict và không gán;
- KTV hệ thống không có trong roster: giữ thông tin nguồn nhưng không route;
- hệ thống đổi KTV so với plan cũ: log override, dùng KTV hệ thống;
- assignment optimizer cũ chỉ còn hợp lệ nếu KTV vẫn có trong roster.

Trong `SimpleKtvOptimizer` thuần, incumbent là soft bonus. Nhưng trong
operational reducer, job `Chưa phân công` đã được optimizer giữ từ snapshot
trước làm KTV chuyển `RESERVED`, rồi assignment đó được đưa vào
`locked_assignments`. Vì vậy trên thực tế nó được **giữ cứng qua snapshot** cho
tới khi job/status/roster thay đổi.

#### 6.4. Chọn tập KTV để gán

KTV có pre-state `IDLE`, `RESERVED` hoặc `BUSY` đều được đưa vào graph. Job mới
được thêm vào queue của KTV; chỉ `UNAVAILABLE` và `OFF_SHIFT` bị loại.

Operational reducer không đặt giới hạn số job mới/KTV. Solver tiếp tục gán cho
tới khi hết candidate hoặc vượt shift capacity. Reservation cũ vẫn được giữ
cứng, nhưng không ngăn KTV nhận thêm job chờ.

#### 6.5. Chạy optimizer và routing

Chi tiết ở phần 7. Kết quả gồm graph trong RAM, assignment, unassigned và route.

#### 6.6. Tạo post-state

Job checkpoint chỉ chứa active jobs. Với `Đang xử lý`, `Theo dõi`, `Tạm dừng`
không có optimizer decision nhưng có `EMP_ACCOUNT`, source được ghi là
`SYSTEM_ACTIVE`.

Technician post-state:

- giữ `OFF_SHIFT`, `UNAVAILABLE`, `BUSY`;
- nếu còn job trong plan thì `RESERVED`;
- không còn job thì `IDLE`;
- `current_job_id` là busy job hoặc stop đầu route;
- `available_at` của `RESERVED` là estimated finish cuối route nếu tính được;
- `planned_job_count` là tổng active job gắn với KTV;
- `queued_job_count` là số job chưa thực thi: bằng tổng job nếu `RESERVED`, hoặc
  không tính current job nếu `BUSY`;
- queue chi tiết phân biệt `IN_PROGRESS`, `NEXT`, `QUEUED`, `PAUSED` và
  `QUEUED_REVIEW`.

### Bước 7 — `WRITE_OUTPUT`

Output được ghi trước khi commit checkpoint. Vì vậy nếu commit fail, thư mục run
có thể đã có CSV nhưng `run_metadata.status = FAILED`; consumer phải kiểm tra
metadata, không chỉ kiểm tra file có tồn tại.

### Bước 8 — `COMMIT_CHECKPOINT`

Hai state CSV và metadata được ghi vào `runtime_dir/staging/<checkpoint_id>/`.
Sau đó:

1. rename cả staging directory sang `checkpoints/<checkpoint_id>/`;
2. atomically replace `latest.json` để version mới thành current.

Nếu fail trước bước 2, latest cũ vẫn dùng được. Có thể còn orphan checkpoint/
staging để audit hoặc dọn sau. Atomicity này bảo vệ file state, nhưng chưa bảo
vệ hai process chạy đồng thời vì chưa có lock.

---

## 4. Previous state và current state merge như thế nào?

Ví dụ tối giản:

```text
Previous S001
  J01: Chưa phân công → optimizer gán KTV-A
  J02: Đã phân công  → SYSTEM_FIXED KTV-B

Event batch S002
  J02: Đang xử lý, EMP_ACCOUNT=KTV-B
  J03: Chưa phân công
```

Kết quả hiện tại:

1. `J01` không có event mới nhưng vẫn active; reservation KTV-A được giữ.
2. `J02` updated, KTV-B được suy là `BUSY`; job giữ `SYSTEM_ACTIVE` nhưng không
   nằm trong route mới.
3. `J03` added; KTV `IDLE/RESERVED/BUSY` còn capacity đều có thể nhận vào
   queue, còn `UNAVAILABLE/OFF_SHIFT` bị loại.
4. Checkpoint S002 là active state sau merge và vẫn trỏ
   `previous_checkpoint_id` về S001.

Nếu S003 không nhắc tới J01, J01 vẫn active. Muốn kết thúc cần gửi:

- `J01, Đóng checklist` hoặc `J01, Đã xử lý`;
- J01 thành `COMPLETED`;
- reservation của KTV-A được giải phóng nếu không còn claim khác;
- run đó ghi J01 vào `completed_jobs.csv`.

Hai nguyên tắc cần giữ khi cấp input:

1. Batch phải được gửi theo thứ tự thời gian.
2. Mỗi checklist phải có event active ban đầu và terminal event khi hoàn thành.

---

## 5. Bảng trạng thái KTV

| Work status | Ý nghĩa code hiện tại | Nhận job mới? | Khi nào thoát trạng thái? |
|---|---|---:|---|
| `IDLE` | Có trong ca và không có claim active | Có | Khi được gán hoặc có runtime update |
| `RESERVED` | Có một hoặc nhiều job chờ, chưa có job đang thực thi | Có, thêm vào queue | Khi bắt đầu job, hết queue hoặc runtime update |
| `BUSY` | Đang thực thi một job; có thể có thêm job chờ | Có, thêm vào queue | Current job không active nữa hoặc runtime update |
| `UNAVAILABLE` | Nghỉ tạm/không thể nhận việc | Không | Cần runtime update khác; nếu không, state được giữ |
| `OFF_SHIFT` | Snapshot ngoài `[SHIFT_START, SHIFT_END)` | Không | Snapshot sau nằm trong giờ ca và không có explicit off-shift |

`shift_roster` là membership “ai đi ca”; `technician_state` là availability “ai
đang rảnh”. Không nên xóa KTV bận khỏi roster.

Một KTV vẫn chỉ có một `TechnicianStateRecord`, nhưng có thể có nhiều
`TechnicianJobQueueItem`:

| Queue status | Ý nghĩa |
|---|---|
| `IN_PROGRESS` | Job đang thực thi; `queue_position=0` |
| `NEXT` | Job kế tiếp của KTV `RESERVED` |
| `QUEUED` | Job chờ phía sau trong route |
| `PAUSED` | Job tạm dừng, vẫn giữ ownership nhưng chưa có route order |
| `QUEUED_REVIEW` | Nguồn báo nhiều job cùng busy; cần review job không được chọn làm current |

`ROUTE_ORDER` và `queue_position` được đánh số riêng trong từng KTV, không phải
thứ tự toàn hệ thống.

---

## 6. Conflict rules hiện có

Conflict chủ yếu là **audit log**; nhiều trường hợp hệ thống vẫn tiếp tục chạy.

| Code | Khi nào xảy ra | Resolution hiện tại |
|---|---|---|
| `DUPLICATE_JOB_ID` | Một checklist có nhiều row | Giữ row cuối |
| `DUPLICATE_TECHNICIAN_UPDATE` | Một KTV có nhiều runtime update | Giữ update cuối |
| `TECHNICIAN_UPDATE_NOT_IN_ROSTER` | Update cho KTV không đi ca | Bỏ update |
| `ACTIVE_TECHNICIAN_NOT_IN_ROSTER` | Active busy/paused job trỏ KTV ngoài roster | Giữ job state, không có technician runtime |
| `TECHNICIAN_MULTIPLE_BUSY_JOBS` | Một KTV có nhiều job cùng khai báo đang xử lý | Giữ một current job và flag các job còn lại |
| `SYSTEM_ASSIGNED_TO_UNAVAILABLE_TECHNICIAN` | System job trỏ người unavailable/off-shift | Giữ job và yêu cầu review |
| `SYSTEM_ASSIGNMENT_MISSING_EMP_ACCOUNT` | Status `Đã phân công` nhưng thiếu account | Để unassigned |
| `SYSTEM_TECHNICIAN_NOT_IN_ROSTER` | System-fixed KTV không có trong roster | System thắng nhưng không route |
| `SYSTEM_ASSIGNMENT_OVERRIDE` | System đổi KTV so với plan cũ | Dùng system assignment |
| `INCUMBENT_TECHNICIAN_NOT_IN_ROSTER` | KTV optimizer cũ mất khỏi roster | Trả job về assignment pool |

`conflicts.csv` không phải danh sách exception. Run chỉ fail khi input/schema/
timestamp/contract làm code không thể tiếp tục.

---

## 7. Optimization V0 xử lý cụ thể

### 7.1. Task policy

[`optimization/task_policy.py`](../src/ktv_optimizer/optimization/task_policy.py)
map `CASE_TYPE` sang:

- `priority`: 1 là cao nhất;
- `service_minutes`: thời lượng onsite mặc định;
- `sla_minutes`: cộng vào `CREATE_DATE` để tạo `due_at`;
- `onsite_required`: đã có trong policy nhưng chưa được graph/router sử dụng.

Policy hiện hard-code trong Python. Task không nhận diện được dùng priority 3,
60 phút, không có SLA.

### 7.2. Clustering

`location_key(job)` ưu tiên `WARD_CODE`; nếu thiếu thì dùng GPS làm tròn hai chữ
số thập phân; nếu vẫn thiếu thì `LOCATION:UNKNOWN`.

| Mode | Cluster key |
|---|---|
| `task_location` | `CASE_TYPE + ward/grid` |
| `task` | `CASE_TYPE` |
| `location` | `ward/grid` |

Cluster hiện chỉ tạo key và bonus trong cost. Nó không chạy một thuật toán phân
cụm độc lập như K-means/DBSCAN.

### 7.3. Compatibility graph

Mỗi cặp Job–KTV tạo một `CandidateEdge`:

- cùng branch là hard rule trong cả ba mode;
- task mode kiểm tra `SUPPORTED_CASE_TYPES`;
- location mode yêu cầu cả hai phía có GPS và Haversine ≤ bán kính;
- reject reason được ghi trên edge.

| Mode | Hard compatibility |
|---|---|
| `task_location` | branch + task + location |
| `task` | branch + task |
| `location` | branch + location |

Mode `task` cho phép thiếu GPS; scorer cộng missing-distance penalty. Graph của
operational run hiện không được ghi ra `candidate_edges.csv`, chỉ có count trong
summary. Stateless CLI có xuất file này.

### 7.4. Cost function

```text
total_cost
  = distance_km × 1
  + current_workload_hours × 5
  - 8 nếu KTV đã có cùng cluster
  - 10 nếu giữ incumbent
```

Nếu thiếu distance trong mode `task`, distance cost là 25. Các weight là V0
chưa được calibration từ dữ liệu/KPI.

### 7.5. Greedy assignment

`GreedyAssignmentSolver.solve()`:

1. sort job theo: có deadline trước → deadline sớm → priority nhỏ → created sớm
   → checklist ID;
2. lấy các compatible KTV;
3. loại candidate vượt capacity hoặc giới hạn job mới;
4. tính cost và chọn minimum; hòa cost thì technician ID nhỏ hơn thắng;
5. cộng `service_minutes` vào workload và tiếp tục job sau.

Capacity hiện là toàn bộ `SHIFT_END - SHIFT_START`, không phải số phút còn lại
từ `planning_time`. Workload không cộng travel, break hoặc return-to-depot.

Đây là greedy theo thứ tự, không đảm bảo global optimum.

### 7.6. Hai loại assignment

- `Chưa phân công`: vào greedy assignment.
- `Đã phân công`: tạo `SYSTEM_FIXED`, không qua hard compatibility, radius,
  capacity hoặc availability check của solver; chỉ cần KTV còn trong roster để
  route.

Các status busy/follow-up/paused được theo dõi trong state nhưng không vào danh
sách assignment của optimizer.

### 7.7. Routing

`NearestNeighbourRouter` được đặt tên nearest-neighbour nhưng rank hiện tại là:

```text
due_at sớm → priority cao → khoảng cách gần → checklist ID
```

Vì deadline đứng trước distance, đây chính xác hơn là **SLA-first greedy route**.

ETA:

```text
leg_minutes = Haversine_km / average_speed_kmh × 60
arrival = previous_finish + leg_minutes
finish = arrival + service_minutes
```

Chưa có road network, traffic, appointment window, turn restriction, break,
shift-end feasibility, route insertion hay local search. Nếu một stop thiếu GPS,
ETA của stop đó và các stop sau có thể trở thành rỗng.

---

## 8. Function/module contract của core pipeline

### Application và pipeline

| Function/class | Input | Xử lý | Output/side effect |
|---|---|---|---|
| `process_snapshot.main()` | CLI args | Tạo config và gọi processor đúng một snapshot | In summary, output run, commit state |
| `OperationalSnapshotProcessor.process()` | ID/time + 3 input paths | Chạy toàn bộ stage operational | `OperationalRun`; output directory; checkpoint mới |
| `snapshot_summary()` | `SnapshotRunResult` | Đếm job/KTV/change/conflict/route/status | `dict` cho `summary.json` và metadata |
| `write_snapshot_output()` | output dir + result | Serialize stable CSV/JSON contract | Chín CSV và một summary JSON |
| `VersionedCheckpointManager.latest()` | runtime dir | Đọc pointer và verify đủ file | `CheckpointRef` hoặc `None` |
| `VersionedCheckpointManager.load()` | latest pointer | Deserialize hai state CSV | previous job state + KTV state + ref |
| `VersionedCheckpointManager.commit()` | result state | Stage, rename version, switch pointer | `CheckpointRef` mới |

### Data layer

| Function/class | Input | Xử lý | Output |
|---|---|---|---|
| `normalize_maintenance_frame()` | maintenance DataFrame | Trim text, sentinel date→`NaT`, parse số/date | DataFrame đã chuẩn hóa |
| `maintenance_to_optimization_jobs()` | maintenance + boundary | Filter status, normalize, policy, geocode | `list[OptimizationJob]` |
| `shift_roster_to_technicians()` | roster DataFrame | Parse GPS/shift/task/workload | `list[TechnicianShift]` |
| `shift_roster_to_status_updates()` | roster DataFrame | Parse runtime status/job/available time | `list[TechnicianStatusUpdate]` |
| `WardBoundaryIndex.from_geojson()` | GeoJSON path | Tính centroid, index ward/province | Boundary index trong RAM |
| `WardBoundaryIndex.match()` | address text | Exact segment, sau đó fuzzy ≥ 0.86 | `LocationMatch` hoặc `None` |

### State layer

| Function/class | Input | Xử lý | Output |
|---|---|---|---|
| `canonicalize_job_events()` | mapped events | Duplicate keep-last | canonical events + conflicts |
| `apply_job_events()` | previous checkpoint + events | Upsert/complete, giữ job vắng mặt | active jobs + ADDED/UPDATED/COMPLETED |
| `derive_pre_optimization_states()` | current snapshot + 2 previous state | Merge roster/runtime/job claims/history | pre-KTV state + conflicts |
| `resolve_previous_assignments()` | previous jobs + current jobs/roster | System override + incumbent validity | incumbent map + conflicts |
| `SnapshotReducer.reduce()` | current snapshot + previous state | Pure state transition + optimizer | `SnapshotRunResult` |
| `SnapshotReducer.process()` | snapshot + legacy CSV stores | `reduce()` rồi save hai file tuần tự | Result; dùng cho manifest replay |
| `build_post_optimization_states()` | snapshot + pre-state + result | Apply plan vào KTV availability | technician checkpoint mới |
| `build_technician_job_queue()` | job/KTV checkpoint + route | Tách current job khỏi các job chờ | queue đầy đủ theo từng KTV |
| `CsvStateStore` | job checkpoint | Serialize/deserialize 23 cột job/plan | `optimizer_state.csv` |
| `CsvTechnicianStateStore` | KTV checkpoint | Serialize/deserialize KTV runtime | `technician_state.csv` |

### Optimization layer

| Function/class | Input | Xử lý | Output |
|---|---|---|---|
| `get_task_policy()` | task type | Lookup policy V0 | priority/service/SLA |
| `job_cluster_key()` | job + mode | Tạo deterministic cluster key | string key |
| `CompatibilityGraphBuilder.build()` | jobs × KTV | Hard filter và distance | tất cả candidate/rejected edges |
| `AssignmentScorer.score()` | edge + workload/context | Tách từng cost component | `ScoreBreakdown` |
| `GreedyAssignmentSolver.solve()` | jobs, KTV, graph | Greedy assign theo SLA/priority | assignments + unassigned |
| `NearestNeighbourRouter.build_route()` | một KTV + các job | SLA-first route + Haversine ETA | `TechnicianRoute` |
| `NearestNeighbourRouter.build_routes()` | mọi assignment | Nhóm theo KTV và route từng nhóm | list route |
| `SimpleKtvOptimizer.optimize()` | jobs + KTV + state hints | Fixed/locked + graph + assign + route | `OptimizationResult` |

---

## 9. Output của một operational run

```text
artifacts/operational_runs/<RUN_ID>/
├── jobs.csv
├── completed_jobs.csv
├── changes.csv
├── conflicts.csv
├── assignments.csv
├── unassigned.csv
├── technicians.csv
├── technician_job_queue.csv
├── routes.csv
├── summary.json
└── run_metadata.json
```

| File | Nội dung đúng cần hiểu |
|---|---|
| `jobs.csv` | Canonical active jobs; `EMP_ACCOUNT` là assignment nguồn, không phải plan optimizer |
| `completed_jobs.csv` | Completion hợp lệ trong riêng run; giữ event time và `FINISH_DATE`/`FLAG_ON_TIME` nếu event nguồn có |
| `changes.csv` | ADDED/UPDATED/COMPLETED so với checkpoint trước |
| `conflicts.csv` | Các bất nhất và resolution đã áp dụng |
| `assignments.csv` | Plan thật của run: `SYSTEM_FIXED` + locked + optimizer mới |
| `unassigned.csv` | Job optimizer/fixed không gán được và reason |
| `technicians.csv` | KTV post-state dùng để vận hành/audit run |
| `technician_job_queue.csv` | Toàn bộ current/next/queued/paused job theo KTV |
| `routes.csv` | Stop order và Haversine ETA theo KTV |
| `summary.json` | Count tổng hợp, gồm incoming/add/update/complete |
| `run_metadata.json` | Stage/status/timing/config/hash/checkpoint/error |

Các số `active_jobs`, `added_jobs`, `updated_jobs` và `completed_jobs` là số đo
chính xác theo event contract/state của pipeline. `COMPLETION_EVENT_TIME` là lúc
batch được hệ thống nhận, không tự coi là thời điểm KTV hoàn tất thực địa.
`FINISH_DATE`/`FLAG_ON_TIME` mới là outcome ưu tiên; event time chỉ là fallback
khi nguồn chưa gửi outcome chi tiết.

`summary.json.evaluation_metrics` hiện có bộ metric V1:

| Metric | Cách tính hiện tại |
|---|---|
| `planned_sla_on_time_rate_percent` | Job có due time và route finish đúng hạn / toàn bộ active job có due time; unassigned tính là chưa đạt |
| `completed_sla_on_time_rate_percent` | Ưu tiên `FLAG_ON_TIME=YES/NO`; fallback `FINISH_DATE` hoặc event time so với due |
| `total_distance_km` | Tổng Haversine distance của mọi route |
| `total_travel_minutes` | Tổng travel ETA theo tốc độ V0 |
| `unique_clusters` | Số cluster khác nhau trên toàn plan |
| `technician_cluster_visits` | Tổng số cặp KTV–cluster phải phục vụ |
| `same_area_revisit_count` | Số lần route rời cluster rồi quay lại cluster đó |
| `completed_jobs_in_shift` | Completion có EMP_ACCOUNT và event time nằm trong shift |
| `total/average_wait_between_jobs_minutes` | Idle wait ngoài travel giữa hai stop; V0 chưa có appointment wait nên thường bằng 0 |
| `ai_planning_seconds` | Thời gian reducer merge state + assign + route |
| `ai_planning_target_met` | `ai_planning_seconds <= 5` |

Regression nghiệp vụ bằng checklist thật nằm tại
`utils/test_real_metrics_scenarios.py`. Test tái dùng canonical extract HNI_04,
chạy năm event snapshot và tính lại 18 metric/snapshot trực tiếp từ
`optimizer_state.csv`, `assignments.csv`, `routes.csv`, `completed_jobs.csv` và
roster. `metrics_audit.csv` chỉ PASS khi cả 90 giá trị khớp `summary.json`.

### Báo cáo actual cuối ngày

`DailyOutcomeEvaluator` quét `run_metadata.json` có `status=SUCCESS` và
`snapshot_time` thuộc ngày cần báo cáo. `completed_jobs.csv` của các run được
concat, sort theo event time rồi deduplicate `CHECKLIST_ID`, vì retry terminal
event không được làm tăng KPI.

```text
completed_jobs của ngày
  → maintenance cuối ngày: FLAG_ON_TIME/FINISH_DATE
  → INFO: canonical check-in/check-out của đúng ngày
  → GPS: EMP_CODE → ACCOUNT, trace trong checkout(A)→checkin(B)
  → roster: outcome time nằm trong SHIFT_START/SHIFT_END
  → daily_summary + checklist/technician/travel CSV
```

Không merge outcome lịch sử chỉ vì trùng checklist ID. `FINISH_DATE` nguồn phải
cùng ngày completion event; `NA + Đóng checklist` là ngoại lệ hủy hợp lệ không
có finish time. Mismatch được giữ event pipeline và tăng
`source_outcome_date_mismatches`.

Output cuối ngày:

- `checklist_outcomes.csv`: một dòng/terminal checklist, loại
  COMPLETED/CANCELLED, SLA source, outcome time, KTV, cluster, visit và in-shift;
- `travel_legs.csv`: một dòng cho hai job liên tiếp của cùng KTV, GPS coverage,
  distance, moving time, wait estimate hoặc reason không tính được;
- `technician_outcomes.csv`: KPI theo KTV;
- `daily_summary.json`: SLA, completed/cancelled, travel/cluster/revisit/shift,
  planning avg/p95/max/≤5s và plan còn lại ở snapshot cuối.

State được dùng cho snapshot kế tiếp nằm ở:

```text
data/runtime_v1/
├── latest.json
└── checkpoints/<RUN_ID>/
    ├── optimizer_state.csv
    ├── technician_state.csv
    └── checkpoint_metadata.json
```

Trong `optimizer_state.csv`:

- `EMP_ACCOUNT`: system/source assignment;
- `PLANNED_EMP_ACCOUNT`: KTV trong plan hiện tại;
- `ASSIGNMENT_SOURCE`: `SYSTEM_FIXED`, `SYSTEM_ACTIVE` hoặc `OPTIMIZER_V0`;
- `ROUTE_ORDER`: thứ tự hiện tại nếu job được route.

Trong `technician_state.csv`, `PLANNED_JOB_COUNT` là tổng job active và
`QUEUED_JOB_COUNT` là số job chưa thực thi. Queue không cần một store thứ ba:
danh sách đầy đủ được persist theo từng job trong `optimizer_state.csv` và được
dựng lại bằng `build_technician_job_queue()`.

---

## 10. Error handling và observability

Mỗi run ghi stage hiện tại:

```text
INITIALIZE → VALIDATE_INPUT → LOAD_INPUT → MAP_INPUT
→ LOAD_PREVIOUS_CHECKPOINT → OPTIMIZE → WRITE_OUTPUT
→ COMMIT_CHECKPOINT → COMPLETED
```

Khi exception:

- metadata chuyển `FAILED` và giữ stage xảy ra lỗi;
- ghi error type, message và traceback;
- không switch latest checkpoint;
- exception được ném lại để CLI trả exit code lỗi.

Hiện chưa có structured log tập trung, metric exporter, alert, run registry,
retry policy hoặc lock chống concurrent writer. `print(stage)` và các file JSON
là observability hiện có.

---

## 11. Các entrypoint khác và phạm vi dùng

### `run_snapshot_replay.py`

Đọc manifest, sort snapshot theo time rồi gọi `SnapshotReducer.process()`.
Nó dùng hai CSV state legacy và save tuần tự, không có version directory/pointer
atomic. Dùng cho regression/replay fixture; không nên là operational entrypoint.

### `run_v0_optimization.py`

Chạy trực tiếp mapper → optimizer → năm output CSV. Không diff, conflict,
technician availability hay checkpoint. Dùng để debug graph/weights/route một
lần.

### `demo_server.py`

Đây là **operational demo**. Mỗi event add/assign/start/pause/follow-up/
complete/reopen/add KTV tạo full snapshot kế tiếp và gọi đúng
`OperationalSnapshotProcessor`. Dùng khi cần xem state transition end-to-end.

### `real_data_demo_server.py` và `utils/real_data_e2e.py`

Chuẩn bị một branch từ maintenance thật, dựng roster proxy và chạy từng scenario
snapshot. Dùng để stress/data-flow demo; roster/GPS sinh từ lịch sử không mang
ý nghĩa “KTV thực sự đi ca và đang ở đây”.

---

## 12. Website dành cho user hiện tại

[`user_demo_server.py`](../src/ktv_optimizer/application/user_demo_server.py)
cố ý đơn giản hóa UX:

```text
chọn nhóm tác vụ + mode
→ tạo USER-S001 gồm 5 job synthetic cho 1 KTV
→ SimpleKtvOptimizer chạy trực tiếp
→ website vẽ route
→ add checklist tạo USER-S002 gồm 6 job
→ complete stop: xóa job active, dời GPS KTV tới job, route lại
```

File demo được lưu ở:

```text
artifacts/user_demo/<SESSION_ID>/
├── snapshots/USER-S001.csv, USER-S002.csv
├── plans/USER-S001.json, USER-S002.json
├── progress/<SNAPSHOT>-Rxxx.json
└── latest.json
```

Phần browser dùng Leaflet/OpenStreetMap và thử gọi OSRM để **vẽ** đường bộ.
Assignment/ETA backend vẫn dùng Haversine V0.

Khác biệt bắt buộc nhớ giữa website user và operational core:

| Hành vi | User demo | Operational pipeline |
|---|---|---|
| Dữ liệu | Synthetic/curated | CSV snapshot + roster |
| Số KTV | 1 | N KTV |
| Previous state | Session riêng | Versioned checkpoint |
| Conflict/availability reducer | Không | Có |
| Một job mới/KTV/snapshot | Không giới hạn | Theo solver và shift capacity |
| Complete job | Xóa active và cập nhật GPS KTV ngay | Chỉ phản ứng ở snapshot sau; chưa tự cập nhật GPS |
| Road geometry | OSRM ở browser | Không |

Vì vậy website đẹp hiện tại là product-flow prototype, chưa phải frontend của
operational service.

---

## 13. Pipeline learning offline

Luồng này tách khỏi online optimizer:

```text
QOS_MAINTENANCE + QOS_MAINT_CHECKIN_INFO
→ normalize + validate
→ collapse maintenance 1 row/checklist
→ tạo visit duration/GPS features
→ inner join visit với checklist
→ technician historical features point-in-time
→ duration/lateness baselines
```

Chi tiết:

1. `OfflineDatasetBuilder.build()` đọc hai CSV và gọi validator.
2. Duplicate maintenance được collapse theo `CHECKLIST_ID`; `SERVICES_LIST`
   được union, `SOURCE_RECORD_COUNT` giữ multiplicity.
3. `SERVICE_DURATION_MINUTES = CHECKOUT_DATE - CHECKIN_DATE`; chỉ giữ `[0,1440]`.
4. GPS displacement dùng Haversine nếu bốn tọa độ trong biên Việt Nam.
5. Inner join với job context; visit không link được bị loại.
6. `IS_LATE`: `FLAG_ON_TIME=NO → 1`, `YES → 0`, khác → missing.
7. History feature dùng `shift(1).expanding()` để không nhìn record hiện tại/
   tương lai của chính KTV.
8. Duration baseline lấy median theo `SERVICES_LIST + EMP_LEVEL`.
9. Lateness baseline lấy smoothed rate theo `BRANCH_NAME + SERVICES_LIST`.

Các prediction offline hiện **chưa cấp** `service_minutes`, late risk hay weight
cho online optimizer. Online vẫn dùng `TaskPolicy` hard-code.

`domain.Job.is_finished` hiện chỉ kiểm tra `FINISH_DATE is not None` và trong
code có ghi chú cần sửa. Theo kiểm chứng dữ liệu trước đây, finish/check-out/
status đóng không đồng nghĩa hoàn toàn; không nên dùng property này làm rule
production ở thời điểm hiện tại.

---

## 14. Audit nghiệp vụ: chỗ cần xác nhận

### Mức P0 — phải chốt trước khi coi là pipeline thật

1. **Scope checkpoint**

   Cần xác định một runtime dir đại diện toàn công ty, một branch hay một ca.
   Event feed không xóa job vắng mặt, nhưng scope vẫn cần rõ để roster, branch
   constraint và vận hành checkpoint không bị trộn.

2. **Các terminal status khác completion**

   V1 chỉ coi `Đóng checklist` và `Đã xử lý` là hoàn thành. Nếu nguồn có hủy,
   chuyển branch hoặc invalid checklist thì cần thêm event/rule riêng.

3. **Assignment optimizer có được hệ thống nguồn acknowledge hay không**

   Job `Chưa phân công` đã gán được giữ như reservation qua snapshot, dù nguồn
   vẫn báo chưa phân công. Cần timeout/ack/reject rule để tránh giữ vô hạn.

4. **Roster và live location thật**

   Không thể quyết định availability/branch/distance production bằng roster proxy
   từ maintenance lịch sử.

5. **GPS job và travel time**

   Ward centroid đủ để demo/gom vùng, chưa đủ để route tới khách hàng. ETA chim
   bay/tốc độ cố định không phải thời gian đường bộ.

6. **Appointment/SLA/shift feasibility**

   Route chưa dùng khung hẹn, chưa kiểm tra stop kết thúc trước shift end và chưa
   dự đoán KPI trễ.

### Mức P1 — pipeline chạy được nhưng rule có thể sai nghiệp vụ

1. `Tạm dừng chờ xử lý` nằm trong queue với status `PAUSED`; KTV vẫn có thể nhận
   việc khác. Cần chốt lúc nào paused job được đưa lại vào route.
2. `Đã xử lý và đang theo dõi` đang là current `BUSY`, nhưng KTV vẫn có thể nhận
   thêm job chờ. Cần xác nhận theo dõi có thật sự là công việc đang thực thi.
3. Số job/KTV hiện chỉ bị chặn bởi capacity toàn ca; chưa có giới hạn queue theo
   policy, số stop hoặc thời gian còn lại.
4. System-fixed bypass compatibility, capacity và availability. Code có log
   conflict nhưng vẫn giữ assignment.
5. `SUPPORTED_CASE_TYPES` rỗng nghĩa là hỗ trợ mọi task. Với data thiếu, rule
   này permissive thay vì fail-safe.
6. Capacity dùng toàn ca, không dùng thời gian còn lại; travel không nằm trong
   workload.
7. Duplicate checklist giữ row cuối theo file, không theo event time/version.
8. Status được filter trước normalize; whitespace/casing khác chuẩn có thể làm
   mất job.
9. Active status như busy/paused thiếu `EMP_ACCOUNT` có thể nằm trong checkpoint
   nhưng không xuất hiện trong `unassigned.csv` vì optimizer chỉ xử lý hai status.
10. GPS technician checkpoint lấy roster mới; roster thiếu GPS làm mất vị trí
    đã biết. Operational completion chưa dời GPS tới job vừa xong.
11. `BRANCH_NAME` so sánh exact string, chưa có branch ID/canonical mapping.
12. Equal-time snapshot được chấp nhận và chưa có idempotency/concurrency lock.

### Mức P2 — chất lượng tối ưu

- Weight chưa calibration và các đại lượng chưa normalize.
- Greedy có thể tạo nghiệm kém global optimum.
- Cluster chỉ là key/bonus, chưa phải spatial clustering thật.
- Routing không có insertion, 2-opt/ALNS/CP-SAT.
- Incumbent workload chưa được seed đầy đủ; chủ yếu được khóa/stability bonus.
- `onsite_required=False` chưa làm thay đổi compatibility/routing.
- Operational output không persist toàn bộ candidate edge nên audit graph khó.

---

## 15. Checklist để duyệt nghiệp vụ

| Câu hỏi cần business trả lời | Default code hiện tại |
|---|---|
| Runtime/checkpoint thuộc toàn hệ thống hay branch/ca? | Không có scope key; coi toàn checkpoint là một scope |
| Job mất khỏi feed có đồng nghĩa hoàn thành? | Không; job được giữ cho tới explicit terminal event |
| Status nào hoàn thành job? | `Đóng checklist`, `Đã xử lý` → `COMPLETED` |
| Optimizer assignment có giữ đến khi nguồn acknowledge? | Có, gần như giữ cứng |
| Reservation có timeout không? | Không |
| Tạm dừng có khóa KTV không? | Không; job ở `PAUSED`, KTV vẫn nhận thêm queue |
| Theo dõi sau xử lý có khóa KTV không? | Không chặn job chờ, nhưng vẫn đánh dấu current `BUSY` |
| System-fixed vi phạm ca/khoảng cách thì reject hay giữ? | Giữ và log conflict |
| KTV không có skill data được hiểu thế nào? | Hỗ trợ mọi task |
| Một lần replan cho KTV nhận tối đa bao nhiêu job mới? | Không giới hạn cứng; theo solver/capacity |
| Hoàn thành cập nhật GPS từ nguồn nào? | Operational chưa làm; user demo dùng GPS job |
| Route cần tối ưu SLA, hẹn hay distance trước? | Due time → priority → distance |
| ETA dùng road/traffic hay chim bay? | Chim bay, tốc độ cố định |

Nếu duyệt xong bảng này, phần lớn thay đổi tiếp theo chỉ nằm trong policy/state/
optimizer module, không cần phá contract pipeline và checkpoint hiện tại.

---

## 16. Đánh giá tổng thể

Khung hiện tại đã đủ để chứng minh một pipeline end-to-end tuần tự:

- đọc checklist event batch;
- upsert vào previous active state;
- phát hiện ADDED/UPDATED/COMPLETED và conflict;
- quản lý KTV availability;
- gán và route;
- ghi output có audit;
- commit hai state atomically.

Nó chưa phải production dispatch engine vì nguồn roster/GPS thật, các event hủy/
chuyển vùng, checkpoint scope, appointment/SLA feasibility và road routing chưa
được chốt. Phần nên giữ là module boundary và data contract; phần cần thay dần là
business policy, online validation, assignment solver và road router.
