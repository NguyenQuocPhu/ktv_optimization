# KTV Optimizer — Working Memory

> Đọc file này trước khi tiếp tục project. Chỉ cập nhật khi có dữ liệu, giả
> định, quyết định kiến trúc hoặc kết quả kiểm chứng mới; giữ file ngắn.
> Khi build thêm tính năng, cập nhật tính năng/kết quả mới vào đầu file này.

## Update gần nhất

User demo map đổi tile nền từ `tile.openstreetmap.org` sang
`a.tile.openstreetmap.fr/hot` vì DNS WSL ánh xạ domain cũ về localhost; Leaflet,
marker nhiều màu và OSRM routing giữ nguyên. Tile thay thế đã kiểm tra HTTP 200.

Outcome cuối ngày V1 đã triển khai: operational event giữ thêm `FINISH_DATE` và
`FLAG_ON_TIME` xuyên mapper/checkpoint/completed output; snapshot actual SLA ưu
tiên YES/NO. `application.evaluate_day` gom mọi run SUCCESS theo ngày, dedupe
terminal checklist, tách COMPLETED/CANCELLED, enrich maintenance/INFO/GPS/roster
và xuất `daily_summary.json`, `checklist_outcomes.csv`, `travel_legs.csv`,
`technician_outcomes.csv` + latest pointer. GPS actual chỉ là estimate segment
5–120 km/h/gap≤15m trong checkout→next-checkin và luôn báo coverage. Source
outcome lệch ngày bị từ chối + đếm mismatch. Synthetic proof PASS: 2 job, SLA
50%, service40m, GPS3,892km/30m, wait10m, in-shift2/2, AI≤5s 2/2. CLI trên
5 real-ID snapshots PASS: completion3, SLA66,67%, AI≤5s 5/5; source lịch sử
lệch ngày3 được phát hiện, GPS đúng ngày không cover nên travel=null. Artifact:
`artifacts/daily_outcomes/2026-06-30__20260803T091735869510Z/`.

Re-audit `METADATA_20260730.xlsx`: `FLAG_ON_TIME` là nhãn chính thức
YES=đúng hẹn, NO=trễ, NA=hủy, INPROCESS=đang xử lý. Sau canonicalize 477.773
checklist: YES=388.150, NO=45.964, NA=43.611, INPROCESS=48; actual SLA label
coverage YES/NO=90,86%, rate đúng hẹn lịch sử=89,41%, không có ID xung đột flag.
GPS sample có 11.322 điểm/10 KTV/10 ngày, median interval 40,8s; bridge được
`EMPLOYEECODE → INFO.EMP_CODE` và `ACCOUNTEMP → maintenance.EMP_ACCOUNT`.
Trong cửa sổ sample có 92 khoảng checkout→next-checkin ≤4h; 85 (92,4%) có ≥2
GPS points. Có thể ước lượng actual travel cho subset này sau lọc gap/jitter/
speed outlier; không suy rộng toàn đội. Boundary chỉ cho ward/centroid, nên ưu
tiên INFO LAT_LNG_OUT/IN làm geofence đầu-cuối.

Real-checklist metric audit `utils/test_real_metrics_scenarios.py` PASS: tái dùng
canonical extract HNI_04, 9 checklist ID/branch/address/source KTV thật + 3 KTV
roster proxy; chỉ mô phỏng status, create/deadline và event order. 5 snapshot có
initial mixed, system override, start, urgent, route-stop #3 completion, manual
completion, late urgent completion, pause và add. Independent CSV recalculation
khớp 90/90 metric. Planned SLA 100→71,43→66,67→50→50%; actual completion SLA
S3=100%, S4=100%, S5=0%; distance 42,624→15,194 km; revisit xuất hiện đúng
S2/S3; mọi planning <0,001s. Artifact mới nhất:
`artifacts/real_metrics_test/real_metrics_20260803T082501376649/`.

`summary.json.evaluation_metrics` đã có metric V1: planned/actual SLA,
distance, travel time, global/technician cluster visits, same-area revisit,
completed-in-shift, idle wait và AI planning time với target ≤5s. Planned SLA
tính toàn bộ active job có due; unassigned không được che khỏi denominator.
Capacity smoke S003 ra 15/16 = 93,75%, distance/travel/wait=0 do cùng location,
1 global cluster/3 KTV-cluster visits, revisit=0, actual completed=0 và planning
~0,001s. Artifact mới nhất:
`artifacts/capacity_overflow_test/capacity_20260803T081140342440/`.

Capacity-overflow smoke test `utils/test_capacity_overflow_e2e.py` PASS: 3 KTV,
ca 300 phút, maintenance 60 phút/job; event batch 6→6→4 tạo active
6→12→16. Assignment cân bằng 2/2/2 → 4/4/4 → 5/5/5; `CAP-J16` unassigned
đúng reason `SHIFT_CAPACITY_EXCEEDED`, mỗi KTV 300/300 phút và checkpoint chain
S001→S002→S003 hợp lệ; assignment cũ không bị đổi.

Operational checklist event/upsert V1 đã hoàn tất: job vắng trong batch mới giữ
previous active state; active event add/update, chỉ `Đóng checklist`/`Đã xử lý`
mới `COMPLETED` và release job/KTV. Terminal event lặp được bỏ qua. Mỗi run ghi
`completed_jobs.csv` (có completion event time) và thống kê
incoming/ADDED/UPDATED/COMPLETED trong
`summary.json` + metadata. Compile + multi-snapshot + multi-job event delta +
operational/demo + V0 + manifest replay đều PASS; regression có case cố ý không
gửi lại job cũ.

Synthetic operational queue test `utils/test_multi_job_queue_e2e.py` PASS:
2 KTV, 12h capacity, job 60 phút. S001 gửi 5 event → KTV-A=3/KTV-B=2;
S002 chỉ gửi 5 event mới nhưng active merge thành 10 → A=5/B=5, unassigned=0,
diff đúng 5 ADDED, checkpoint S002 nối S001 và giữ assignment 5 job cũ; mỗi KTV
state RESERVED, queue count=5. Artifact mẫu:
`artifacts/multi_job_queue_test/queue_20260802T061051129678/`.

Multi-job/KTV đã hoàn tất/pass: work status chỉ mô tả trạng thái hiện tại;
IDLE/RESERVED/BUSY nhận thêm theo solver/capacity, chỉ loại
UNAVAILABLE/OFF_SHIFT. `optimizer_state.csv` persist từng job/order;
`technician_state.csv` thêm `QUEUED_JOB_COUNT` (đọc ngược schema cũ), mỗi run có
`technician_job_queue.csv` với IN_PROGRESS/NEXT/QUEUED/PAUSED/QUEUED_REVIEW.
Bỏ limit 1 job và conflict giả khi BUSY nhận job chờ; chỉ nhiều BUSY mới conflict.
V0, multi-snapshot, operational demo và real-data 7-snapshot smoke đều PASS.

Docs review pipeline đã hoàn tất tại `docs/PIPELINE_DATA_FLOW_REVIEW.md`: mô tả
operational data flow từng stage, previous↔current reducer, input/output và
function contracts, optimizer/routing, user demo/offline flow. Audit P0 cần chốt:
full-snapshot scope, completion semantics, optimizer assignment ack/timeout,
roster/live GPS thật, customer GPS/road ETA và appointment/SLA/shift feasibility.

Completion GPS semantics đã hoàn tất/pass: khi hệ thống báo checklist xong,
current GPS KTV chuyển đúng tọa độ job; active route re-optimize từ điểm mới và
marker KTV di chuyển. Progress event lưu before/after GPS; snapshot kế tiếp kế
thừa location này, còn chọn lại ca/task mới reset về GPS đầu ca.

Completion action user web đã hoàn tất/pass: mỗi stop có nút Hoàn thành; backend
loại job khỏi active route, re-optimize, map/list/metrics biến mất đồng bộ, kể cả
0 job. Event lưu `progress/USER-S00x-R00y.json`; `latest.json` trỏ active plan,
không tạo thêm full snapshot ngoài S001/S002.

User-facing web V1 đã hoàn tất/pass: 1 KTV, chọn triển khai/bảo trì/thu hồi/random
thì optimizer thật tạo USER-S001 với 5 job/3 cụm; add checklist tạo USER-S002 với
6 job, node MỚI và re-route. Leaflet + OSM street tiles; OSRM road geometry đã
smoke `Ok`, fallback Haversine nếu mất mạng. Màu route theo cụm Q7/Q4/Nhà Bè.
Session ghi `artifacts/user_demo/<id>/{snapshots,plans,latest.json}`; tách khỏi lab.

Real-data Snapshot Lab đã hoàn tất: `real_data_demo_server.py` dùng trực tiếp
engine `real_data_e2e.py`; Prepare tạo workspace riêng, mỗi click chỉ chạy đúng
1/7 full snapshot và nối checkpoint previous→current. UI hiện source/prepare,
pipeline stage, diff/assignment/conflict, route mẫu và mọi file input/output.
HTTP smoke S001→S002, direct stateful S001→S007 và 3 regression suite đều PASS.

Cleanup 2026-08-01: đã dừng web demo và xóa toàn bộ output sinh lại được
(`artifacts/demo_e2e`, `artifacts/temp_demo_pipeline`, `data/temp_demo_snapshots`,
`data/temp_real_replay`, `__pycache__`) cùng generator demo nhỏ cũ. Giữ nguyên
mọi CSV/XLSX/GeoJSON nguồn, fixtures/test và code web; `artifacts/` để trống.

Real-data large replay đã implement/pass bằng `utils/real_data_e2e.py`
(`branches/prepare/run/inspect`). HNI_04: 17.873 row/17.145 checklist, roster
proxy 188 KTV, geocode 99,8075%; stress 3.000 + 600 job/7 snapshot PASS trong
9,388s, peak run ~303 MB. Output test cũ đã dọn; command tạo workspace mới trong
`data/temp_real_replay/`. Roster/GPS chỉ là proxy lịch sử. Boundary/process cache
index và runtime dùng centroid GeoJSON nhẹ sinh từ boundary thật.

Snapshot editor demo đã hoàn tất và E2E pass: UI chọn Checklist/KTV; checklist hỗ
trợ add/assign/start/pause/follow-up/complete/reopen, KTV mới được append vào
`inputs/roster.csv`. Mỗi event sinh full snapshot, chạy operational pipeline và
atomic checkpoint; test đủ vòng đời S001→S011, HTTP smoke pass rồi reset sạch.

Map demo V3 đã polish và smoke-test: map offline/synthetic có nền khu vực, sông,
đường chính/phụ, nhãn phường, route casing/màu, marker start/job đánh số, legend
theo KTV và nhãn tự né mép. Không dùng map tile/API; vẫn là sơ đồ Haversine.

UI demo E2E V3 đã hoàn tất theo reference app nhưng ở dạng website: header/tabs,
summary 4 chỉ số + CTA xanh, map là trọng tâm, quick-add/KTV bên phải, route cards
phía dưới và tab danh sách việc. Prose bị cắt tối đa; help nằm trong nút `?`, audit
ẩn mặc định. Layout dùng `minmax(0, ...)`, breakpoint 920/620px; JS syntax, HTTP
serve và toàn bộ regression pass. Backend/pipeline giữ nguyên.

Demo E2E nhiều snapshot đã implement và verify: bootstrap S001 qua operational
pipeline; add/complete/replan tạo full snapshot CSV kế tiếp, chạy
diff/conflict/availability/optimizer, atomic checkpoint + metadata. UI hiển thị
plan/route trước-sau, KTV status, backlog/reason, diff, assignment delta,
conflict và checkpoint history. Nhóm job mới và compatibility mode là hai
control riêng; random sinh event mới, không lọc mất job active. Session synthetic
được lưu ở `artifacts/demo_e2e/<SESSION_ID>/`, không còn chỉ giữ trong RAM.

Operational CSV V1 đã implement và verify: `application/process_snapshot.py`
xử lý đúng một full snapshot; `pipeline/` ghi state vào checkpoint versioned rồi
mới atomically switch `latest.json`. Mỗi run có `run_metadata.json` gồm
stage/status/timing/config/input SHA-256/counts/checkpoint chain/error traceback.
Demo localhost `application/demo_server.py` cho chọn bảo trì/triển khai/thu hồi/
random + 3 mode cluster. Test `utils/test_operational_pipeline.py` pass cả
checkpoint lỗi không đổi latest, failed metadata, S001→S004 add→backlog→complete
→reassign. Route vẫn là Haversine + nearest-neighbour V0, chưa phải đường bộ.

Audit workbook mô tả tác vụ bản đầy đủ: có 17 loại tác vụ; rule cao nhất là
SLA/KPI + khung hẹn A–B, sau đó cluster/road route và event replan. Core hiện
mới partial. P0 còn thiếu: taxonomy→policy thật (CASE_TYPE hiện chỉ
MAINTENANCE), appointment window, roster/GPS thật, travel-time đường bộ và
kiểm tra route/SLA/shift feasibility; chưa có replan gain gate hay KPI predicted
SLA. Offline duration/lateness baseline chưa nối vào optimizer.

Kiểm chứng theo 477.773 `CHECKLIST_ID`: chỉ 252/43.611 (0,578%) checklist
`Đóng checklist` có `CHECKOUT_DATE` hợp lệ. 302.682/302.947 checklist có
checkout mang status `Đã xử lý`. Vì vậy không đồng nhất `Đóng checklist` với
KTV checkout/hoàn thành thực địa.

Availability V1 giữ toàn bộ KTV đi ca trong roster và checkpoint
`IDLE/RESERVED/BUSY/UNAVAILABLE/OFF_SHIFT`. Multi-job queue thay rule 1 job/KTV:
IDLE/RESERVED/BUSY có thể nhận thêm theo solver/capacity; chỉ
UNAVAILABLE/OFF_SHIFT bị loại. System-fixed cho KTV BUSY là queue hợp lệ, không
còn conflict giả; nhiều BUSY đồng thời mới conflict. Không xóa KTV bận khỏi roster.

## Mục tiêu

Gợi ý/gán checklist cho KTV đi ca và tạo thứ tự tuyến. Ưu tiên: đúng SLA/hẹn →
gom khu vực → giảm di chuyển → cân bằng tải. README là context dài hạn, không
phải pipeline đã implement đầy đủ.

## Data đang có

- `QOS_MAINTENANCE_utf8.csv`: 502.210 dòng, 477.773 checklist.
- `QOS_MAINT_CHECKIN_INFO_utf8.csv`: 521.003 dòng, 477.773 checklist.
- `boundary_2026-07-31.geojson`: 3.321 polygon phường/xã, dùng centroid.
- `sample_emp_coordinate.csv`: 11.322 GPS của 10 KTV; `CREATEBY == ACCOUNTEMP`
  100%.
- Workbook mô tả tác vụ chỉ là tài liệu; runtime dùng CSV/GeoJSON.
- Venv: `/home/nguyenquocphu/.venv`.

## Phát hiện bắt buộc nhớ

- Maintenance có 24.240 ID lặp (24.437 dòng dư), chỉ khác `SERVICES_LIST`;
  canonicalize trước khi join.
- INFO là quan hệ `Checklist 1 → N Visit`: 39.087 ID lặp, tối đa 10 dòng.
  Không tự động coi mỗi dòng lặp là một visit thật; nhiều dòng là ping/check-in
  lặp. Ưu tiên record có checkout khi cùng `CHECKLIST_ID + EMP_CODE +
  CHECKIN_DATE`.
- `-1` trong date = missing/`NaT`.
- Không dùng riêng `FINISH_DATE is NaT` để xác định active: phần lớn là
  `Đóng checklist`.
- CSV maintenance là snapshot cuối, không có assignment/status event history.
  Không được giả định `EMP_ACCOUNT` đã tồn tại tại `CREATE_DATE`.
- Quan hệ Job–KTV khi tạo là optional; lịch sử có thể 0..N KTV.
- `CASE_TYPE` hiện chỉ có `MAINTENANCE`; task-mode chưa phân biệt loại việc.
- GPS check-in chỉ có sau khi KTV đến, không dùng cho assignment của chính job
  đó. Boundary centroid chỉ là GPS đại diện phường/xã, không phải nhà khách.

## Optimization V0 đã implement

```text
Chưa phân công → compatibility graph → weighted greedy assign → routing
Đã phân công   → giữ EMP_ACCOUNT → routing
```

Modes:

- `task_location`: branch + CASE_TYPE + bán kính; cluster task+phường/xã.
- `task`: branch + CASE_TYPE; cho phép thiếu GPS với penalty.
- `location`: branch + bán kính; cluster phường/xã.

Cost V0 = distance + workload + same-cluster bonus. Routing V0 =
nearest-neighbour, SLA-first, khoảng cách chim bay, tốc độ mặc định 30 km/h.
Chưa có road ETA/traffic/appointment, CP-SAT, insertion, ALNS.

Multi-snapshot V1 đã triển khai tuần tự cho checklist event batch, không dùng
SQLite/API/concurrency. `CsvStateStore` checkpoint active job + system/planned
assignment; `SnapshotReducer` upsert/complete, tạo diff/conflict, dùng
incumbent/cluster bonus, optimize rồi ghi state mới. CLI manifest:
`application/run_snapshot_replay.py`; test/fixture ở
`utils/test_multi_snapshot_pipeline.py`, `utils/fixtures/snapshots_v1/`.
Chưa có incremental insertion, frozen route hay race handling.

Roster thật chưa có. Contract tối thiểu: `EMP_ACCOUNT`, `BRANCH_NAME`; nên có
GPS, shift start/end, supported case types, existing workload. Runtime columns
tùy chọn: `WORK_STATUS`, `CURRENT_JOB_ID`, `AVAILABLE_AT`. Fixture:
`utils/fixtures/shift_roster_v0.csv`.

## Code quan trọng

- `src/ktv_optimizer/data/sources/administrative_boundaries/`: address → ward GPS.
- `src/ktv_optimizer/data/mappers/optimization_mapper.py`: CSV → optimizer input.
- `src/ktv_optimizer/optimization/`: cluster, graph, cost, assign, route.
- `src/ktv_optimizer/state/`: snapshot, diff/conflict, CSV checkpoint, reducer.
- `src/ktv_optimizer/application/run_v0_optimization.py`: CLI → 5 CSV.
- `application/run_snapshot_replay.py`: manifest snapshots → state + outputs.
- `utils/test_v0_optimizer.py`, `utils/test_multi_snapshot_pipeline.py`: tests.

V0 đã verify: 11 jobs, 8 demo KTV, 64 edges, 10 assignments, 1 job thiếu
location, 8 routes; compile/assertions pass.

## Nguyên tắc tiếp tục

- Tách hard compatibility khỏi learned/soft weights.
- Log cost components và reject reason; không chỉ log total.
- Chống future leakage bằng point-in-time data.
- Thuật toán mới phải thay được module V0, không đổi input/output contract.
- Khi có key mới, cập nhật file này và xóa chi tiết đã lỗi thời.
