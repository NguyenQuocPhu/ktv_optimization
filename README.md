# KTV Optimizer

Phần code hiện tại gồm nền tảng dữ liệu, learning offline và optimization V0.
Tài liệu kiến trúc phía dưới mô tả context và hướng phát triển dài hạn; đó
không phải toàn bộ pipeline đã được implement.

Tài liệu review theo đúng code đang chạy, gồm data flow từng snapshot, contract
từng module và các giả định nghiệp vụ cần xác nhận:
[`docs/PIPELINE_DATA_FLOW_REVIEW.md`](docs/PIPELINE_DATA_FLOW_REVIEW.md).

## Cấu trúc code hiện tại

```text
src/ktv_optimizer/
├── domain/
│   ├── job.py
│   ├── visit.py
│   ├── technician.py
│   └── system_state.py
├── data/
│   ├── sources/administrative_boundaries/
│   ├── sources/qos_maintenance/
│   ├── mappers/
│   └── validators/
├── features/
│   ├── job_features.py
│   ├── historical_features.py
│   └── feature_registry.py
├── learning/offline/
│   ├── datasets/
│   ├── duration_model/
│   └── lateness_model/
├── optimization/
│   ├── clustering.py
│   ├── compatibility.py
│   ├── scoring.py
│   ├── assignment.py
│   ├── routing.py
│   └── optimizer.py
├── pipeline/
│   ├── checkpoint.py
│   ├── output.py
│   └── service.py
└── application/
    ├── build_dataset.py
    ├── run_offline_evaluation.py
    ├── run_v0_optimization.py
    ├── process_snapshot.py
    └── demo_server.py
```

Nguồn runtime hiện tại:

```text
data/QOS_MAINTENANCE_utf8.csv
data/QOS_MAINT_CHECKIN_INFO_utf8.csv
data/boundary_2026-07-31.geojson
<daily shift roster CSV - chờ team data cung cấp>
```

Workbook `METADATA_20260730.xlsx` chỉ được dùng làm tài liệu schema. Code trong
package không đọc các file Excel.

`QOS_MAINTENANCE` hiện có các `CHECKLIST_ID` lặp do khác
`SERVICES_LIST`. Mapper gộp chúng thành một checklist canonical trước khi nối
với check-in để tránh merge nhiều-nhiều.

Chạy smoke build:

```bash
PYTHONPATH=src /home/nguyenquocphu/.venv/bin/python \
  -m ktv_optimizer.application.build_dataset \
  --data-dir data \
  --output artifacts/datasets/qos_offline_dataset.csv \
  --limit 10000
```

Bỏ `--limit` để build toàn bộ CSV. Chạy baseline evaluation:

```bash
PYTHONPATH=src /home/nguyenquocphu/.venv/bin/python \
  -m ktv_optimizer.application.run_offline_evaluation \
  --dataset artifacts/datasets/qos_offline_dataset.csv
```

## Optimization V0

Hai luồng được hỗ trợ:

```text
Chưa phân công
  → candidate KTV cùng chi nhánh
  → compatibility graph
  → weighted greedy assignment
  → routing

Đã phân công
  → giữ nguyên EMP_ACCOUNT
  → routing
```

Ba mode:

| Mode | Hard compatibility | Cluster key |
|---|---|---|
| `task_location` | Cùng branch, hỗ trợ CASE_TYPE, trong bán kính | CASE_TYPE + phường/xã |
| `task` | Cùng branch, hỗ trợ CASE_TYPE | CASE_TYPE |
| `location` | Cùng branch, trong bán kính | phường/xã |

`OBJ_LOCATION` được match với `ten_xa` và `tinh_tp` trong GeoJSON. Tọa độ trả
về là centroid đại diện của polygon phường/xã, không phải tọa độ chính xác của
nhà khách hàng. Địa chỉ không match được vẫn có thể chạy mode `task` với
missing-distance penalty; hai mode có location sẽ không tạo cạnh tương thích.

Shift roster CSV tối thiểu cần:

```text
EMP_ACCOUNT
BRANCH_NAME
```

Các cột tùy chọn:

```text
LATITUDE
LONGITUDE
SHIFT_START
SHIFT_END
SUPPORTED_CASE_TYPES
EXISTING_WORKLOAD_MINUTES
```

Chạy bằng fixture kiểm thử:

```bash
PYTHONPATH=src /home/nguyenquocphu/.venv/bin/python \
  -m ktv_optimizer.application.run_v0_optimization \
  --data-dir data \
  --boundary data/boundary_2026-07-31.geojson \
  --shift-roster utils/fixtures/shift_roster_v0.csv \
  --mode task_location \
  --planning-time 2026-06-30T06:00:00 \
  --output-dir artifacts/optimization_v0
```

Output CSV:

```text
jobs.csv
candidate_edges.csv
assignments.csv
unassigned.csv
routes.csv
```

V0 dùng khoảng cách chim bay, greedy assignment và nearest-neighbour routing.
Các interface đã tách để sau này thay bằng road ETA, GAP/CP-SAT,
best/regret insertion và ALNS. `CASE_TYPE` trong CSV hiện chỉ có một giá trị
`MAINTENANCE`, vì vậy mode theo task chỉ thực sự có ý nghĩa khi dữ liệu tương
lai có loại tác vụ chi tiết hơn.

## Multi-snapshot V1

V1 nhận các **batch sự kiện checklist** theo thứ tự thời gian. Mỗi dòng là trạng
thái mới nhất được gửi cho một checklist; file không cần lặp lại toàn bộ job đang
active. Pipeline đọc checkpoint hiện tại, upsert event, optimize lại rồi ghi
state mới vào hai CSV nhỏ. Không cần database hay API.

Quy tắc vòng đời tối giản:

- checklist active mới → `ADDED`;
- checklist active đã có → `UPDATED` nếu dữ liệu thay đổi;
- checklist không có trong batch mới → giữ nguyên từ checkpoint trước;
- status `Đóng checklist` hoặc `Đã xử lý` → `COMPLETED` và loại khỏi active plan;
- terminal event gửi lặp sau khi job đã đóng → bỏ qua để không đếm hai lần.

```text
manifest.csv → normalize → diff/conflict → optimizer V0
             → data/runtime/optimizer_state.csv
             → data/runtime/technician_state.csv
             → artifacts/snapshot_replay_v1/<SNAPSHOT_ID>/
```

Manifest gồm bốn cột; đường dẫn tương đối được resolve từ thư mục chứa manifest:

```csv
SNAPSHOT_ID,SNAPSHOT_TIME,MAINTENANCE_CSV,SHIFT_ROSTER_CSV
S001,2026-08-01 08:00:00,snapshot_001.csv,roster.csv
S002,2026-08-01 09:00:00,snapshot_002.csv,roster.csv
```

Chạy fixture năm snapshot:

```bash
PYTHONPATH=src /home/nguyenquocphu/.venv/bin/python \
  -m ktv_optimizer.application.run_snapshot_replay \
  --manifest utils/fixtures/snapshots_v1/manifest.csv \
  --boundary utils/fixtures/snapshots_v1/boundary.geojson \
  --state-file data/runtime/optimizer_state.csv \
  --technician-state-file data/runtime/technician_state.csv \
  --output-dir artifacts/snapshot_replay_v1 \
  --mode task_location \
  --reset-state
```

`--reset-state` dùng khi replay lại toàn bộ manifest từ đầu. Nếu bỏ cờ này,
pipeline tiếp tục từ checkpoint hiện có. Snapshot cũ hơn checkpoint bị từ chối
để tránh vô tình ghi lùi state.

Checkpoint phân biệt hai field:

- `EMP_ACCOUNT`: assignment từ hệ thống trong snapshot nguồn.
- `PLANNED_EMP_ACCOUNT`: KTV thực tế trong plan hiện tại.

`SYSTEM_FIXED` là hard assignment. `OPTIMIZER_V0` từ snapshot trước là soft
incumbent ở V0; trong replay V1, assignment còn active được giữ như reservation.
KTV không còn trong roster thì job quay lại assignment pool.

Roster luôn chứa toàn bộ KTV đi ca, kể cả người đang bận. Runtime availability
được lưu riêng trong `technician_state.csv`:

```text
IDLE → RESERVED → BUSY → IDLE
             ↘ UNAVAILABLE
ngoài giờ ca → OFF_SHIFT
```

KTV `IDLE`, `RESERVED` hoặc `BUSY` đều có thể nhận thêm job vào queue; số lượng
do solver và shift capacity quyết định. Chỉ `UNAVAILABLE/OFF_SHIFT` bị loại khỏi
tập candidate. System-fixed cho KTV đang `BUSY` trở thành job chờ bình thường.
Nếu KTV `UNAVAILABLE/OFF_SHIFT`, pipeline ghi
`SYSTEM_ASSIGNED_TO_UNAVAILABLE_TECHNICIAN`. Các cột runtime tùy chọn:

```text
WORK_STATUS       # IDLE/RESERVED/BUSY/UNAVAILABLE/OFF_SHIFT
CURRENT_JOB_ID    # job đang làm/đang giữ nếu nguồn biết
AVAILABLE_AT      # thời điểm dự kiến rảnh
```

Nếu không có runtime columns, trạng thái được suy từ shift time, checklist hiện
tại và checkpoint trước. `Đang xử lý`/`Đã xử lý và đang theo dõi` được xem là
`BUSY`; `Đã phân công` và `Tạm dừng chờ xử lý` đặt KTV ở `RESERVED` nhưng KTV
vẫn có thể nhận thêm job chờ. Checklist
chỉ giải phóng reservation khi nhận explicit completion event; việc không xuất
hiện trong batch mới không làm job biến mất.

`technician_state.csv` có `PLANNED_JOB_COUNT` (tổng job gắn với KTV) và
`QUEUED_JOB_COUNT` (job chưa thực thi). Danh sách chi tiết được dựng từ
`optimizer_state.csv` và xuất thành `technician_job_queue.csv`; `CURRENT_JOB_ID`
là job đang chạy nếu `BUSY`, hoặc job kế tiếp nếu `RESERVED`.

Mỗi thư mục snapshot có:

```text
jobs.csv
completed_jobs.csv
changes.csv
conflicts.csv
assignments.csv
unassigned.csv
routes.csv
technicians.csv
technician_job_queue.csv
summary.json
```

Chạy regression nhanh:

```bash
PYTHONPATH=src /home/nguyenquocphu/.venv/bin/python utils/test_v0_optimizer.py
PYTHONPATH=src /home/nguyenquocphu/.venv/bin/python utils/test_multi_snapshot_pipeline.py
PYTHONPATH=src /home/nguyenquocphu/.venv/bin/python utils/test_real_metrics_scenarios.py
PYTHONPATH=src /home/nguyenquocphu/.venv/bin/python utils/test_daily_outcome_evaluator.py
```

## Operational single-snapshot V1

Entry point này nhận đúng một batch checklist event. Nó đọc checkpoint gần nhất,
merge event vào active state, optimize, ghi các file output và chỉ sau đó mới
atomically chuyển
`latest.json` sang state mới:

```bash
PYTHONPATH=src /home/nguyenquocphu/.venv/bin/python \
  -m ktv_optimizer.application.process_snapshot \
  --snapshot-id S001 \
  --snapshot-time 2026-08-01T08:00:00 \
  --maintenance utils/fixtures/snapshots_v1/snapshot_001.csv \
  --roster utils/fixtures/snapshots_v1/roster.csv \
  --boundary utils/fixtures/snapshots_v1/boundary.geojson \
  --runtime-dir data/runtime_v1 \
  --output-root artifacts/operational_runs \
  --mode task_location
```

Checkpoint được version hóa, không ghi đè state đang dùng:

```text
data/runtime_v1/
├── latest.json
├── checkpoints/<RUN_ID>/
│   ├── optimizer_state.csv
│   ├── technician_state.csv
│   └── checkpoint_metadata.json
└── staging/                    # version chưa commit nếu run bị ngắt
```

Hai CSV state được ghi trong staging rồi rename cả thư mục. Chỉ khi đủ hai CSV
và metadata, `latest.json` mới được replace. Vì vậy run lỗi không làm reader
đọc một cặp state nửa cũ/nửa mới.

Mỗi run có một thư mục trong `artifacts/operational_runs/<RUN_ID>/`, chứa output
CSV/summary như replay và `run_metadata.json`. Metadata ghi status, stage hiện
tại, thời gian chạy, config, đường dẫn/hash/size input, checkpoint trước/sau,
row counts, assignments, conflicts, routes, trạng thái KTV và traceback khi lỗi.

## Outcome cuối ngày V1

Snapshot report chỉ mô tả một lần tối ưu. Cuối ngày chạy evaluator để gom mọi
run `SUCCESS` của ngày, chống đếm trùng completion và tách actual outcome khỏi
plan còn lại ở snapshot cuối:

```bash
PYTHONPATH=src /home/nguyenquocphu/.venv/bin/python \
  -m ktv_optimizer.application.evaluate_day \
  --date 2026-06-30 \
  --runs-root artifacts/operational_runs \
  --maintenance data/QOS_MAINTENANCE_utf8.csv \
  --checkin data/QOS_MAINT_CHECKIN_INFO_utf8.csv \
  --gps data/sample_emp_coordinate.csv \
  --output-root artifacts/daily_outcomes
```

`--maintenance`, `--checkin`, `--gps` và `--roster` là tùy chọn. Nếu thiếu,
metric tương ứng trả `null`/coverage thấp thay vì tự suy thành 0. Maintenance
cuối ngày bổ sung `FLAG_ON_TIME` và `FINISH_DATE`; INFO bổ sung thời gian onsite;
GPS bổ sung distance/moving/wait giữa checkout job A và check-in job B; roster
có `SHIFT_START/SHIFT_END` dùng để xác nhận hoàn thành trong ca.

Mỗi lần chạy tạo một report bất biến và cập nhật pointer theo ngày:

```text
artifacts/daily_outcomes/
├── latest_2026-06-30.json
└── 2026-06-30__<TIMESTAMP>/
    ├── daily_summary.json
    ├── checklist_outcomes.csv
    ├── technician_outcomes.csv
    └── travel_legs.csv
```

SLA actual ưu tiên `FLAG_ON_TIME`: `YES/NO` vào mẫu số, `NA` là hủy và
`INPROCESS` chưa phải outcome. Nếu event không có flag, evaluator fallback lần
lượt sang `FINISH_DATE <= DUE_AT` rồi completion event time. Export cuối ngày
chỉ enrich khi `FINISH_DATE` cùng ngày completion event; mismatch được đếm ở
`source_outcome_date_mismatches` để tránh ghép nhầm/stale data.

Travel actual là GPS estimate có coverage, không phải road ETA: chỉ cộng segment
5–120 km/h, khoảng cách hai GPS point không quá 15 phút, nằm trong cửa sổ
checkout(A) → check-in(B). Boundary/phường xã chỉ dùng cho cluster và số lần quay
lại khu vực. `latest_snapshot_plan` được giữ riêng, tuyệt đối không cộng distance
của nhiều snapshot vì như vậy sẽ đếm trùng các tuyến đã re-plan.

## Website dành cho người dùng cuối

Website V1 cố ý dùng một KTV và dữ liệu TP.HCM đã chọn lọc để tuyến dễ quan sát.
Người dùng bấm một trong bốn nhóm `Triển khai`/`Bảo trì`/`Thu hồi`/`Ngẫu nhiên`;
backend gọi `SimpleKtvOptimizer` và `NearestNeighbourRouter` để tạo `USER-S001`
gồm 5 job thuộc ba cụm Quận 7, Quận 4 và Nhà Bè. Form bên phải thêm đúng một
checklist rồi tạo `USER-S002` gồm 6 job; node mới được đánh dấu và toàn tuyến
được tối ưu lại.

```bash
PYTHONPATH=src /home/nguyenquocphu/.venv/bin/python -B \
  -m ktv_optimizer.application.user_demo_server \
  --host 127.0.0.1 --port 8765 \
  --session-root artifacts/user_demo
```

Mở `http://127.0.0.1:8765`. Leaflet hiển thị OpenStreetMap; browser gọi OSRM để
bẻ từng leg theo đường bộ và tô màu theo cụm. Nếu API ngoài không phản hồi, UI
fallback sang đoạn Haversine để demo vẫn dùng được. Bản public có traffic lớn
cần tile provider và OSRM/road engine riêng, không nên coi public endpoint là
hạ tầng production có SLA.

Mỗi lần reset tạo session mới, không ghi đè lịch sử:

```text
artifacts/user_demo/<SESSION_ID>/
├── snapshots/
│   ├── USER-S001.csv       # 5 job ban đầu
│   └── USER-S002.csv       # full snapshot có thêm job thứ 6
├── plans/
│   ├── USER-S001.json      # tuyến trước khi thêm
│   └── USER-S002.json      # tuyến đã tối ưu lại
├── progress/
│   └── USER-S00x-R00y.json # event hoàn thành + active route mới
└── latest.json             # trỏ tới snapshot/plan mới nhất
```

Mỗi card trong **Thứ tự điểm dừng** có nút **Hoàn thành**. API đánh dấu job đã
xong, loại nó khỏi tập active và chạy lại route cho các điểm còn lại; marker,
đoạn đường, số job và thời gian trên UI cùng cập nhật. Đây là route-progress
revision `R00y`, không tạo full snapshot thứ ba. Nếu hoàn thành hết, map chỉ giữ
vị trí KTV và vẫn có thể nhận checklist từ snapshot tiếp theo.

Completion đồng thời cập nhật `current_location` của KTV bằng GPS của job vừa
xong. Marker KTV chuyển tới điểm đó và route còn lại xuất phát/re-optimize từ
location mới. Progress event lưu cả `technician_previous_location` và
`technician_current_location`; snapshot checklist kế tiếp kế thừa GPS này.

Đây là UI trình diễn luồng người dùng, tách biệt với Real-data Snapshot Lab và
demo operational nhiều event bên dưới.

## End-to-end multi-snapshot demo

Demo dùng Python standard library, không cần cài Streamlit hay map package:

```bash
PYTHONPATH=src /home/nguyenquocphu/.venv/bin/python \
  -m ktv_optimizer.application.demo_server \
  --host 127.0.0.1 --port 8765 \
  --session-root artifacts/demo_e2e
```

Mở `http://127.0.0.1:8765`. Server bootstrap `DEMO-S001` bằng đúng
`OperationalSnapshotProcessor`, không gọi thẳng optimizer. Mỗi thao tác thêm
checklist, manual replan hoặc hoàn thành job tạo một full snapshot CSV kế tiếp,
chạy diff/conflict/availability/optimizer, ghi output, commit checkpoint và cập
nhật `latest.json`.

Giao diện website gồm hai tab `Tổng quan`/`Danh sách công việc`. Tổng quan ưu tiên
card bốn chỉ số, CTA tối ưu, bản đồ tuyến lớn, snapshot editor gọn bên phải,
trạng thái KTV và route cards. Editor cho thêm/phân công/bắt đầu/tạm dừng/theo
dõi/hoàn thành/mở lại checklist hoặc thêm KTV mới vào roster; mọi thao tác đều
sinh full snapshot và chạy operational pipeline. Hướng dẫn nằm trong nút `?`; thông tin
diff/conflict/checkpoint mặc định thu gọn. Trên tablet/mobile, map và form tự xuống
một cột. Map demo chạy offline với nền khu vực, sông/đường minh họa, nhãn phường,
marker đánh số và legend màu theo KTV; đây vẫn là sơ đồ Haversine, không phải
road map hoặc ETA giao thông thật.

User chọn riêng hai khái niệm:

- Nhóm checklist mới: bảo trì, triển khai, thu hồi hoặc sinh ngẫu nhiên.
- Compatibility/cluster mode: `task_location`, `task` hoặc `location`.

`OBJ_LOCATION` synthetic được mapper qua GeoJSON bốn phường demo thành centroid
GPS. UI hiển thị previous/current snapshot, route overlay, KTV
`IDLE/RESERVED`, backlog và lý do, diff, assignment delta, conflict, checkpoint
chain, run metadata path và lịch sử snapshot. Nút “Hoàn thành” đóng checklist ở
snapshot mới, giải phóng KTV và cho backlog được xét lại.

Mỗi lần khởi động/reset tạo một session riêng, không xóa session cũ:

```text
artifacts/demo_e2e/<SESSION_ID>/
├── inputs/       # boundary, roster, DEMO-S001.csv ...
├── runtime/      # latest.json + checkpoint versions
└── runs/         # output và run_metadata.json từng snapshot
```

Input demo vẫn là synthetic. Đường nối dùng Haversine + nearest-neighbour V0,
chưa phải road route.

Smoke test mới:

```bash
PYTHONPATH=src /home/nguyenquocphu/.venv/bin/python \
  utils/test_operational_pipeline.py
```

### Real-data Snapshot Lab trên website

Trang này là bản trực quan của `utils/real_data_e2e.py`. Nó dùng CSV thật để
prepare job/roster proxy, nhưng cho chạy **từng snapshot bằng một click** thay vì
chạy liền cả bảy scenario trong terminal:

```bash
PYTHONPATH=src /home/nguyenquocphu/.venv/bin/python -B \
  -m ktv_optimizer.application.real_data_demo_server \
  --host 127.0.0.1 \
  --port 8765 \
  --session-root data/temp_real_replay/web_sessions
```

Mở `http://127.0.0.1:8765`, chọn branch/số job/mode rồi bấm **Chuẩn bị dữ
liệu**. Sau đó bấm snapshot đang sáng từ S001 đến S007. Mỗi click:

1. đọc full snapshot hiện tại và checkpoint trước;
2. chạy đủ validate → load → map → resolve state → optimize;
3. ghi output/run metadata;
4. commit checkpoint mới bằng `latest.json`;
5. hiện diff, assignment, conflict, backlog, route mẫu và file path trên web.

Mỗi lần Prepare tạo workspace mới tại
`data/temp_real_replay/web_sessions/<branch>_web_<timestamp>/`. Reset chỉ xóa
phiên khỏi UI, không xóa workspace cũ để còn audit. Bước Prepare có thể tốn
khoảng 20 giây và peak RAM cao vì phải đọc file boundary gốc; các snapshot sau
nhẹ hơn. Route hiện vẫn là Haversine + nearest-neighbour V0, chưa phải đường bộ.

Website synthetic cũ (`application/demo_server.py`) vẫn dùng riêng cho thao tác
thêm/sửa event tự do; Real-data Snapshot Lab dành cho việc nhìn và kiểm chứng
data flow cố định S001→S007 trên dữ liệu thật.

### Stress test nhiều snapshot bằng dữ liệu branch thật

`utils/real_data_e2e.py` tách thành ba bước. Liệt kê branch lớn trước:

```bash
/home/nguyenquocphu/.venv/bin/python utils/real_data_e2e.py \
  branches --top 20
```

Prepare một workspace mới. Lệnh dưới giữ toàn bộ row `HNI_04`, canonicalize
checklist, tạo pool 3.600 job thật và roster proxy cho toàn bộ KTV lịch sử:

```bash
/home/nguyenquocphu/.venv/bin/python utils/real_data_e2e.py prepare \
  --branch HNI_04 \
  --jobs 3000 \
  --add-jobs 600 \
  --output-dir data/temp_real_replay/hni04_large_v1
```

Chạy bảy full snapshot bằng operational processor. Console hiển thị từng stage;
JSONL, metadata và CSV audit được ghi trong workspace:

```bash
/home/nguyenquocphu/.venv/bin/python utils/real_data_e2e.py run \
  --workspace data/temp_real_replay/hni04_large_v1 \
  --mode task_location \
  --transition-count 30
```

Đọc lại data flow và kết quả validation:

```bash
/home/nguyenquocphu/.venv/bin/python utils/real_data_e2e.py inspect \
  --workspace data/temp_real_replay/hni04_large_v1
```

Mỗi workspace chỉ replay một lần để tránh vô tình nối checkpoint cũ. Muốn chạy
lại hoặc đổi branch/mode thì dùng tên workspace mới. `source_branch_all.csv`
giữ toàn bộ row branch; snapshot sử dụng checklist/GPS centroid thật nhưng
timeline status là scenario test. `roster_proxy_all.csv` được suy ra từ
`EMP_ACCOUNT` lịch sử và centroid của job gần đây, hoàn toàn không phải bằng
chứng KTV có ca hoặc GPS live.

Data flow của stress test:

```text
QOS_MAINTENANCE_utf8.csv + boundary GeoJSON
  → source_branch_all.csv + canonical jobs + roster proxy
  → REAL-S001 bootstrap/backlog
  → REAL-S002 add batch
  → REAL-S003 complete + refill backlog
  → REAL-S004 system assignment override/conflict
  → REAL-S005 start job/KTV BUSY
  → REAL-S006 thiếu KTV trong roster
  → REAL-S007 roster phục hồi + complete + replan
  → runs/* + checkpoints/* + scenario_summary.csv + validation.json
```

## Kiến trúc tham khảo

Đúng lúc này mình nghĩ nên **đóng băng một kiến trúc V1 đủ core**, không tiếp tục nhồi thêm các ý tưởng research như full RTV/column generation. Quan trọng là kiến trúc phải có **extension point**, để sau này thay từng module mà không đập cả hệ thống.

Mình đề xuất lấy pipeline này làm baseline:

[
\boxed{
State
\rightarrow
Spatial Candidate
\rightarrow
Compatibility
\rightarrow
Route\text{-}aware Estimation
\rightarrow
GAP/CP\text{-}SAT
\rightarrow
Routing
\rightarrow
ALNS/Repair
\rightarrow
Dispatch
\rightarrow
Outcome
}
]

và phía ngoài có:

[
Online\ Adaptation + Offline\ Evolution
]

---

# 1. Bảng thiết kế tổng thể

| #  | Module                             | Mục tiêu                                           | Input chính                                        | Xử lý / thuật toán                                                   | Output                                      | Hard constraint / nguyên tắc                    | Data cần log                            |
| -- | ---------------------------------- | -------------------------------------------------- | -------------------------------------------------- | -------------------------------------------------------------------- | ------------------------------------------- | ----------------------------------------------- | --------------------------------------- |
| 0  | **Realtime State Store**           | Tạo snapshot trạng thái hệ thống tại thời điểm (t) | Job, KTV, GPS, shift, SLA, traffic, route hiện tại | Đồng bộ event, normalize state                                       | `SystemState(t)`                            | Không dùng dữ liệu quá stale                    | `state_snapshot_id`, timestamp, version |
| 1  | **Replan Controller**              | Quyết định khi nào cần chạy optimizer              | Event mới + state                                  | Event trigger + threshold + cooldown                                 | `OptimizationRun`                           | Không replan liên tục gây route instability     | trigger, reason, run_id                 |
| 2  | **Spatial Candidate Generation**   | Giảm không gian Job–KTV                            | GPS KTV, GPS job, route hiện tại                   | R-Tree/H3 + current location + route corridor + ETA                  | Candidate edges                             | High recall, không lọc quá mạnh                 | candidate source, rank, distance, ETA   |
| 3  | **Compatibility Graph**            | Xóa những assignment không hợp lệ                  | Candidate edges + job/KTV attributes               | Skill, certificate, territory, status, shift, basic time feasibility | Sparse graph (E)                            | Hard rules phải loại edge                       | reject reason từng edge                 |
| 4  | **Route-aware Evaluator**          | Đánh giá hậu quả nếu (j) vào route (k)             | Current route + edge (j,k) + travel matrix         | Best insertion thử các position                                      | (\Delta travel,\Delta SLA,\Delta OT,\ldots) | Phải propagate time phía sau                    | best position + cost breakdown          |
| 5  | **Workload Estimator**             | Tính resource consumption của assignment           | Service duration + travel/insertion                | (a_{jk}=service+\Delta travel+\dots)                                 | Workload matrix (a_{jk})                    | Không dùng chỉ `1 job = 1 capacity`             | expected service/travel/workload        |
| 6  | **Global Assignment – GAP/CP-SAT** | Chọn Job → KTV trên toàn hệ thống                  | (E,c_{jk},a_{jk},H_k)                              | CP-SAT                                                               | Assignment ownership                        | Workload, compatibility, freeze, SLA hard rules | model size, solution, bound, runtime    |
| 7  | **Initial Routing**                | Xây sequence cụ thể cho từng KTV                   | Jobs đã assign / route hiện tại                    | Best/Regret-2 insertion                                              | Route ban đầu                               | Time window, frozen prefix                      | route trước/sau, ETA                    |
| 8  | **Cross-route Improvement**        | Sửa interaction location mà GAP chưa thấy          | Tất cả routes                                      | Relocate, swap, 2-opt*, ALNS                                         | Improved routes                             | Không phá hard constraint                       | accepted/rejected moves                 |
| 9  | **Safety / Stability Gate**        | Tránh optimizer tạo kế hoạch bất ổn                | New vs current route                               | Freeze horizon + stability rules                                     | Approved plan                               | Job gần thực hiện không được đổi tùy ý          | moved jobs, stability cost              |
| 10 | **Dispatch Engine**                | Chuyển plan thành hành động thật                   | Approved routes                                    | Publish assignment / sequence                                        | Dispatch commands                           | Idempotent, versioned                           | sent/acknowledged/failed                |
| 11 | **Execution Tracking**             | Biết thực tế xảy ra gì                             | GPS + job events                                   | Event processing                                                     | Actual state/outcome                        | Event ordering chính xác                        | arrival/start/finish/cancel             |
| 12 | **KPI / Reward Engine**            | Đo solution tốt/xấu                                | Planned vs actual                                  | SLA, travel, OT, workload balance, stability                         | KPI + reward                                | Metric definition cố định theo version          | component reward                        |
| 13 | **Online Adaptation**              | Tinh chỉnh soft policy                             | State + outcome + KPI                              | Bandit/RL/value model sau này                                        | (\theta_{t+1})                              | Không thay hard rules                           | policy old/new                          |
| 14 | **Offline Evolution**              | Học và benchmark policy mới                        | Full historical events                             | Replay, simulation, challenger policies                              | New champion policy                         | Safety gate trước production                    | experiment + simulation result          |

---

# 2. Data flow cụ thể

Mình sẽ chuẩn hóa pipeline thành các object rõ ràng.

## `SystemState`

```text
SystemState
 ├─ timestamp
 ├─ active_jobs[]
 ├─ technicians[]
 ├─ technician_locations[]
 ├─ current_routes[]
 ├─ frozen_jobs[]
 ├─ traffic_state
 └─ policy_version
```

Không để mỗi module tự query database lung tung.

Một optimization run phải dựa trên **một snapshot nhất quán**.

---

# 3. Replan Controller

Không nên:

```text
có GPS mới
→ optimize
có GPS mới
→ optimize
có GPS mới
→ optimize
```

vì GPS có thể update vài giây một lần.

Nên có trigger:

### Event trigger

```text
NEW_JOB
JOB_CANCELLED
JOB_COMPLETED
KTV_UNAVAILABLE
SLA_CHANGED
CUSTOMER_RESCHEDULE
MAJOR_TRAFFIC_CHANGE
```

và periodic:

```text
mỗi X phút
```

---

Có thể có rule:

[
Replan=
CriticalEvent
\lor
Elapsed>T
\lor
ExpectedGain>\tau
]

Sau này AI có thể học chính:

[
\tau_{replan}
]

---

# 4. Spatial Candidate Generation

Đây không phải assignment.

Mục tiêu duy nhất:

> **Loại 90–99% cặp Job–KTV rõ ràng không đáng xem, nhưng không làm mất solution tốt.**

Input:

```text
KTV current position
KTV future route
Job position
ETA matrix
priority/SLA
```

Candidate nên là union:

[
C_k=
C_{current}
\cup
C_{route}
\cup
C_{urgent}
\cup
C_{rareSkill}
\cup
C_{incumbent}
]

---

### Output edge

```text
CandidateEdge
{
    job_id,
    technician_id,

    candidate_source,
    spatial_rank,

    geo_distance,
    estimated_eta
}
```

Ví dụ:

```text
J103 - K12
source = ROUTE_CORRIDOR
ETA = 8m
rank = 3
```

---

# 5. Compatibility Graph

Sau Spatial:

```text
100,000 possible pairs
↓
5,000 spatial candidates
```

Compatibility tiếp tục lọc.

Edge tồn tại khi:

[
Compatible(j,k)=1
]

### Hard filters V1

[
Skill_j\subseteq Skill_k
]

[
Cert_j\subseteq Cert_k
]

[
Territory(j,k)=true
]

[
ShiftFeasible(j,k)=true
]

[
JobStatusAllowsAssignment(j)=true
]

và basic time feasibility.

---

Output:

```text
CompatibleEdge
{
    job_id,
    technician_id,

    skill_ok,
    certification_ok,
    territory_ok,
    shift_ok,
    basic_time_ok
}
```

Những edge fail phải log:

```text
reject_reason = MISSING_CERTIFICATION
```

---

# 6. Route-aware Evaluator

Đây là module cực kỳ quan trọng.

KTV:

[
R_k=(A,B,C,D)
]

Job (j).

Thử:

[
j,A,B,C,D
]

[
A,j,B,C,D
]

[
A,B,j,C,D
]

...

Với position (p):

[
\Delta C_{jkp}
==============

C(R_k\oplus_pj)-C(R_k)
]

chọn:

[
p^*
===

\arg\min_p\Delta C_{jkp}
]

---

Output:

```text
RouteInsertionEstimate
{
    job_id,
    technician_id,

    best_position,

    delta_travel,
    delta_waiting,
    delta_lateness,
    delta_overtime,

    predicted_arrival,
    predicted_finish,

    insertion_feasible
}
```

---

# 7. Cost (c_{jk})

Từ module trên xây:

[
c_{jk}
======

M_{SLA}\Delta SLA
+
w_T\Delta Travel
+
w_O\Delta OT
+
w_B BalancePenalty
+
w_S StabilityPenalty
]

Ví dụ:

```text
J42 → K7

Δtravel      = 12
Δlateness    = 0
Δovertime    = 4
balance      = 2
stability    = 0

total cost   = 22
```

Không log mỗi `total=22`.

Phải log **components**.

---

# 8. Workload (a_{jk})

Tách khỏi cost.

Ví dụ:

[
a_{jk}
======

E(Service_j)
+
\Delta Travel_{jk}
+
AccessTime_j
]

Ví dụ:

```text
service       50m
travel         8m
access         5m
-----------------
workload      63m
```

Ý nghĩa:

[
a_{jk}=63
]

là KTV mất 63 phút capacity.

Trong khi:

[
c_{jk}=14
]

là assignment có optimization cost 14.

Hai thứ khác nhau.

---

# 9. GAP / CP-SAT core model

Biến:

[
x_{jk}\in{0,1}
]

và:

[
u_j\in{0,1}
]

`u_j=1` nếu job chưa assign.

---

### Assignment

[
\sum_{k\in C_j}x_{jk}+u_j=1
]

---

### Workload

[
Load_k
======

Load_k^{fixed}
+
\sum_j a_{jk}x_{jk}
]

[
Load_k\le Capacity_k+OT_k
]

---

### Overtime

[
0\le OT_k\le OT_k^{max}
]

---

### Compatibility

Không tạo variable cho incompatible pair.

Tức thay vì:

[
x_{jk}=0
]

có thể đơn giản không tạo nó.

Compatibility graph sparse giúp CP-SAT scale tốt hơn.

---

# 10. Objective V1

Mình sẽ tránh objective quá phức tạp lúc đầu.

Dùng:

[
\boxed{
\min
[
M_UU
+
M_{SLA}L
+
w_TT
+
w_OO
+
w_BB
+
w_SS
]
}
]

Trong đó:

* (U): unassigned priority-weighted jobs;
* (L): lateness;
* (T): travel;
* (O): overtime;
* (B): workload imbalance;
* (S): route instability.

Business priority:

[
\boxed{
Unassigned/SLA
\gg
Travel
\approx
Workload

>

Stability
}
]

hoặc lexicographic objective nếu cần strict priority.

---

# 11. Kết quả CP-SAT

Không chỉ:

```text
J1 → K3
```

Output cần:

```text
AssignmentSolution
{
    run_id,

    job_assignments[],

    workload_by_technician[],
    unassigned_jobs[],

    objective_value,
    best_bound,

    solver_status,
    solve_time_ms
}
```

Status:

```text
OPTIMAL
FEASIBLE
INFEASIBLE
UNKNOWN
```

Realtime có thể chấp nhận:

```text
FEASIBLE
```

nếu time budget hết.

---

# 12. Time budget

Ví dụ V1:

```text
Candidate generation   100–300 ms
Compatibility           50–200 ms
Insertion estimation   200–800 ms
CP-SAT                  500–2000 ms
Routing                 100–500 ms
ALNS                    500–1500 ms
----------------------------------
Target                  ~2–5 s
```

Đây chỉ nên được coi là **design budget ban đầu**, không phải benchmark guarantee. Runtime thật phải đo trên size dữ liệu của bạn.

---

# 13. Initial Routing

Sau CP-SAT:

```text
K1 owns:
J1 J4 J8 J13
```

Chưa có nghĩa sequence là:

```text
J1 → J4 → J8 → J13
```

Routing layer dùng:

### Existing route

Giữ frozen prefix.

### New jobs

Dùng Regret-2 insertion.

Ví dụ:

Job J8:

```text
best insertion    cost = 4
second best       cost = 25
regret            = 21
```

J8 có regret cao → insert sớm.

---

# 14. Frozen prefix

Ví dụ KTV:

```text
Current → J1 → J2 → J3 → J4
```

Trong đó:

```text
Current → J1 → J2
```

đã:

* khách xác nhận;
* KTV đang đi;
* hoặc gần thời điểm thực hiện.

Đóng băng:

```text
[FROZEN]
Current → J1 → J2
```

optimizer chỉ được sửa:

```text
J3 → J4 → ...
```

Điều này cực kỳ quan trọng với production.

---

# 15. Cross-route Improvement

Sau routing:

```text
K1: A → B → C → D
K2: E → F → G
```

Ta không tin assignment 100%.

Thử:

### Relocate

[
C:K1\rightarrow K2
]

### Swap

[
C\leftrightarrow F
]

### 2-opt

tối ưu thứ tự trong route.

### 2-opt*

đổi đoạn giữa hai route.

---

# 16. ALNS layer

V1 có thể bắt đầu:

```text
relocate
swap
2-opt
2-opt*
```

sau đó thêm ALNS.

ALNS:

```text
Current solution
      ↓
Destroy
      ↓
Remove một nhóm jobs
      ↓
Repair
      ↓
Reinsert globally
      ↓
Accept / reject
```

Destroy strategies sau này:

```text
Random removal
Worst-cost removal
Spatial cluster removal
Late-job removal
Overloaded-tech removal
```

Repair:

```text
Best insertion
Regret-2
Regret-3
```

---

# 17. Safety / Stability Gate

Đừng để optimizer thấy:

```text
cost mới tốt hơn 0.5%
```

rồi:

```text
đổi 15 KTV
```

Nên có:

[
Gain > StabilityCost
]

Ví dụ:

```text
move 1 chưa dispatch   low penalty
move appointment confirmed  high penalty
move KTV already travelling  forbidden
```

---

# 18. Dispatch

Mỗi optimization result nên có version:

```text
plan_version = 1728
```

Dispatch:

```text
KTV12:
route version 1728

J10
↓
J17
↓
J32
```

KTV device gửi acknowledgment:

```text
ACK plan 1728
```

Nếu optimizer chạy tiếp:

```text
plan 1729
```

tránh race condition.

---

# 19. Outcome loop

Đây là phần khiến hệ thống trở thành **closed-loop system**.

```text
Dispatch
   ↓
Actual execution
   ↓
GPS / events
   ↓
Outcome
   ↓
State update
   ↓
Replan
```

Không phải:

```text
Optimize một lần
→ xong
```

---

# 20. Event model

Mình rất khuyên event-driven.

Ví dụ:

```text
JOB_CREATED
JOB_ASSIGNED
KTV_DISPATCHED
KTV_ARRIVED
SERVICE_STARTED
SERVICE_COMPLETED
JOB_CANCELLED
JOB_REASSIGNED
KTV_UNAVAILABLE
```

Mỗi event:

```text
event_id
event_type
event_time
received_at
entity_id
payload
```

---

# 21. KPI layer

Phải tách KPI khỏi solver.

### Service

[
SLAHitRate
]

[
AverageLateness
]

[
P95Lateness
]

### Mobility

[
TravelTime
]

[
TravelDistance
]

### Workforce

[
Utilization_k
]

[
Overtime
]

[
LoadVariance
]

### Stability

```text
reassignment count
route changes
customer reschedules
```

### Solver

```text
solve_time
objective
optimality gap
candidate graph size
```

---

# 22. Online Learning — chỉ là extension hook V1

Core optimizer phải chạy được hoàn toàn **không có AI**.

Sau này thêm:

```text
State
 ↓
Optimizer θ
 ↓
Dispatch
 ↓
Outcome
 ↓
Reward
 ↓
AI
 ↓
θ'
```

AI được phép sửa:

```text
w_travel
w_balance
w_stability
candidate_radius
replan_threshold
```

Không được sửa:

```text
skill rule
certification
legal constraint
hard SLA
frozen job
```

---

# 23. Future Value Network

Cũng nên thiết kế interface từ bây giờ, nhưng V1 có thể trả:

[
V(s)=0
]

Sau này:

```text
FutureValueService
```

input:

```text
region
time
technician future position
remaining shift
future demand forecast
technician skill
```

output:

[
V(s')
]

và cost trở thành:

[
c'_{jk}
=======

c_{jk}
+
\gamma V(s'_{jk})
]

Không cần thay CP-SAT.

Đây là extension point rất đẹp.

---

# 24. Bundle/RTV cũng để extension point

V1:

[
Job\rightarrow KTV
]

Sau này có thể thay:

```text
Compatibility Graph
```

bằng:

```text
Job-Job Shareability
        ↓
Candidate Bundles
        ↓
Bundle-KTV Graph
```

Assignment engine vẫn có thể là CP-SAT.

Nghĩa là kiến trúc đừng hard-code entity phải luôn là single job.

Có thể abstraction:

```text
AssignableUnit
```

V1:

```text
AssignableUnit = Job
```

V2:

```text
AssignableUnit = Bundle<Job>
```

Đây là một design decision mình rất khuyên.

---

# 25. Interface giữa các module

Có thể chuẩn hóa:

```text
SystemState
   ↓
CandidateGenerator
   ↓
CandidateEdge[]
   ↓
CompatibilityEvaluator
   ↓
CompatibleEdge[]
   ↓
RouteImpactEstimator
   ↓
AssignmentOption[]
   ↓
AssignmentSolver
   ↓
AssignmentPlan
   ↓
RoutingEngine
   ↓
RoutePlan
   ↓
RouteImprover
   ↓
FinalPlan
```

---

# 26. `AssignmentOption` nên là object trung tâm

Mình sẽ thiết kế gần như:

```text
AssignmentOption {
    job_id
    technician_id

    best_insertion_position

    predicted_travel_delta
    predicted_service_time
    predicted_lateness
    predicted_overtime

    workload_consumption

    travel_cost
    sla_cost
    overtime_cost
    workload_cost
    stability_cost
    future_value_cost

    total_cost
}
```

Đây chính là interface giữa:

[
Routing\ estimation
]

và:

[
CP\text{-}SAT
]

---

# 27. Các database/table core

Mình sẽ chưa over-engineer, chỉ cần:

```text
jobs
technicians
technician_skills

job_events
technician_state_events
gps_events

optimization_runs
optimization_candidates
compatibility_edges
assignment_options

assignment_decisions

route_plans
route_stops

dispatch_events
job_outcomes

policy_versions
```

Sau này:

```text
experience_buffer
offline_experiments
```

---

# 28. `optimization_runs`

Cực quan trọng:

```text
optimization_run_id

started_at
finished_at

trigger
state_snapshot_id

policy_version
optimizer_version

candidate_count
compatible_edge_count
variable_count

solver_status
solve_time
objective
best_bound
```

Sau này mọi quyết định phải truy ngược được về:

[
run_id
]

---

# 29. Monitoring hệ thống

Mình sẽ chia 3 dashboard.

### Business

```text
SLA hit rate
jobs completed
unassigned
overtime
travel
```

### Optimizer

```text
candidate/job
compatible/job
CP-SAT runtime
optimality gap
ALNS improvement
```

### System

```text
event lag
GPS freshness
routing API latency
dispatch failures
```

---

# 30. Fallback strategy

System optimization production bắt buộc phải có fallback.

Ví dụ:

```text
CP-SAT timeout
       ↓
return best feasible solution
```

Nếu không có:

```text
use previous plan
```

Nếu previous plan invalid:

```text
greedy safe assignment
```

Nếu routing service chết:

```text
cached ETA / geo-distance approximation
```

Không nên để:

```text
optimizer fail
→ dispatch system fail
```

---

# 31. Các version mình sẽ chia

### V0 — Baseline

```text
R-Tree
→ Compatibility
→ greedy
→ simple routing
```

Dùng để benchmark.

---

### V1 — Core production candidate

```text
R-Tree / route corridor
→ Compatibility
→ Route-aware cost
→ GAP / CP-SAT
→ Regret routing
→ relocate/swap/2-opt
```

**Đây là thứ mình nghĩ bạn nên implement trước.**

---

### V1.5

```text
+ ALNS
+ adaptive replan
+ better workload prediction
```

---

### V2

```text
+ Future Value NN
+ demand forecasting
+ online policy adaptation
```

---

### V3

```text
+ Job shareability
+ Bundles / RTV
```

---

### Research path

```text
Column Generation
Branch-and-Price
Stochastic Optimization
RL + OR
```

---

# 32. Toàn bộ hệ thống nếu vẽ thành một pipeline

```text
                        ┌────────────────────┐
                        │ External / Events  │
                        │ GPS / Job / Traffic│
                        └──────────┬─────────┘
                                   ▼
                        ┌────────────────────┐
                        │ Realtime State     │
                        │ Snapshot           │
                        └──────────┬─────────┘
                                   │
                           Replan Trigger
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────┐
│                    OPTIMIZATION CORE                    │
│                                                         │
│  Spatial Candidate                                      │
│       ↓                                                 │
│  Compatibility Graph                                    │
│       ↓                                                 │
│  Route-aware Evaluation                                 │
│       ↓                                                 │
│  Cost c_jk + Workload a_jk                              │
│       ↓                                                 │
│  GAP / CP-SAT                                           │
│       ↓                                                 │
│  Initial Routing                                        │
│       ↓                                                 │
│  ALNS / Cross-route Repair                              │
│       ↓                                                 │
│  Stability / Safety Gate                                │
└──────────────────────────┬──────────────────────────────┘
                           ▼
                       Dispatch
                           │
                           ▼
                     Real Execution
                           │
          ┌────────────────┼─────────────────┐
          ▼                ▼                 ▼
         GPS             Job event          KPI
          │                │                 │
          └────────────────┴─────────────────┘
                           │
                           ▼
                      State Update
                           │
                           └───────→ next optimization
```

---

# 33. Và có hai vòng học bên ngoài

```text
                    PRODUCTION LOOP
                          │
                          ▼
                  Historical Outcome
                    /             \
                   /               \
                  ▼                 ▼
          Online Adaptation    Offline Evolution
          fast / safe          replay / simulate
                  \                 /
                   \               /
                    ▼             ▼
                      Policy θ
                          │
                          ▼
                    Optimizer Core
```

---

# 34. Điểm mình sẽ đóng đinh trong design document

Có 5 nguyên tắc kiến trúc nên ghi ngay từ đầu:

**1. Optimization phải hoạt động được khi AI = OFF.**

**2. Hard constraints không được biến thành learned rules.**

**3. Location không chỉ xuất hiện ở candidate search, mà còn ở route-aware cost và route improvement.**

**4. Assignment không được coi là kết quả cuối; routing có quyền sửa assignment thông qua cross-route moves.**

**5. Mọi decision phải reproducible:**

[
State+Policy+SolverVersion
\rightarrow
Decision
]

Phải truy ngược được.

---

## Bản V1 mình sẽ chốt cho team

[
\boxed{
\begin{aligned}
&\text{Realtime State}\
&\downarrow\
&\text{R-Tree + Route Corridor}\
&\downarrow\
&\text{Compatibility Graph}\
&\downarrow\
&\text{Best-Insertion Route Impact}\
&\downarrow\
&\text{Cost }c_{jk}+\text{ Workload }a_{jk}\
&\downarrow\
&\textbf{GAP / CP-SAT}\
&\downarrow\
&\text{Regret-2 Routing}\
&\downarrow\
&\text{Relocate + Swap + 2-opt + 2-opt*}\
&\downarrow\
&\text{Stability Gate}\
&\downarrow\
&\text{Dispatch}\
&\downarrow\
&\text{Outcome / KPI / Replan}
\end{aligned}}
]

Đây là kiến trúc mình thấy **đủ core để bắt đầu implement**, nhưng đồng thời đã để sẵn vị trí cho **ALNS, Future Value NN, Bundling/RTV, Online Learning và Column Generation** sau này mà không cần thiết kế lại toàn bộ hệ thống.



Chính xác hơn một chút: `AssignmentScorer.score()` không tạo compatibility graph mà tính trọng số cho từng edge đã được graph xác nhận là hợp lệ.

Toàn bộ thuật toán hiện tại đi qua các hàm sau:

```text
Job + KTV
→ tạo cluster
→ tạo compatibility graph
→ tính cost từng edge
→ greedy chọn Job–KTV
→ routing
→ cập nhật state và queue
```

## 1. `job_cluster_key()`

File: [clustering.py](/home/nguyenquocphu/fpt/fpt_ktv_optimization/src/ktv_optimizer/optimization/clustering.py)

Input:

```python
job
mode
```

Output là cluster của job:

```text
task_location → TASK:MAINTENANCE|WARD:001
task          → TASK:MAINTENANCE
location      → WARD:001
```

Hàm này không gán KTV, chỉ xác định job thuộc cụm nào.

---

## 2. `CompatibilityGraphBuilder.build()`

File: [compatibility.py](/home/nguyenquocphu/fpt/fpt_ktv_optimization/src/ktv_optimizer/optimization/compatibility.py)

Tạo mọi cặp:

```text
Job × KTV → CandidateEdge
```

Mỗi edge chứa:

```python
CandidateEdge(
    job_id="J01",
    technician_id="KTV-A",
    branch_ok=True,
    task_ok=True,
    location_ok=True,
    compatible=True,
    distance_km=4.2,
    reject_reason=None,
)
```

Hard constraint:

```text
task_location → branch + task + location
task          → branch + task
location      → branch + location
```

Nếu không hợp lệ:

```python
compatible = False
reject_reason = "BRANCH_MISMATCH"
```

hoặc:

```text
TASK_NOT_SUPPORTED
MISSING_LOCATION
OUTSIDE_DISTANCE_RADIUS
```

Đây mới là hàm xây compatibility graph.

---

## 3. `AssignmentScorer.score()`

File: [scoring.py](/home/nguyenquocphu/fpt/fpt_ktv_optimization/src/ktv_optimizer/optimization/scoring.py)

Chỉ chạy trên các edge `compatible=True`.

Input:

```python
edge
workload_minutes
technician_has_cluster
is_incumbent
```

Output:

```python
ScoreBreakdown(
    distance_cost=...,
    load_cost=...,
    cluster_cost=...,
    stability_cost=...,
    total_cost=...,
)
```

Công thức:

```text
total
= distance
+ workload
- same-cluster bonus
- incumbent bonus
```

Đoạn bạn hỏi:

```python
is_incumbent=(
    (incumbent_assignments or {}).get(job.checklist_id)
    == edge.technician_id
)
```

chỉ cung cấp một biến boolean cho scorer tính `stability_cost`.

---

## 4. `GreedyAssignmentSolver.solve()`

File: [assignment.py](/home/nguyenquocphu/fpt/fpt_ktv_optimization/src/ktv_optimizer/optimization/assignment.py)

Đây là hàm thực sự chọn KTV.

### Bước 1: sắp xếp job

```python
due_at có trước
→ due_at sớm
→ priority cao
→ created_at sớm
→ checklist_id
```

### Bước 2: lấy candidate edge hợp lệ

```python
candidates = compatible_by_job[job.checklist_id]
```

### Bước 3: kiểm tra capacity

```python
projected_workload = current_workload + job.service_minutes
```

Nếu vượt ca:

```text
SHIFT_CAPACITY_EXCEEDED
```

### Bước 4: tính cost từng candidate

```python
score = self.scorer.score(...)
```

### Bước 5: chọn cost nhỏ nhất

```python
_, technician_id, edge, score = min(scored)
```

Nếu hai KTV bằng cost thì technician ID nhỏ hơn thắng.

### Bước 6: cập nhật workload và cluster

```python
workloads[technician_id] += job.service_minutes
clusters[technician_id].add(edge.cluster_key)
```

Sau đó mới xét job tiếp theo.

---

## 5. `SimpleKtvOptimizer.optimize()`

File: [optimizer.py](/home/nguyenquocphu/fpt/fpt_ktv_optimization/src/ktv_optimizer/optimization/optimizer.py)

Đây là hàm điều phối toàn bộ thuật toán.

Nó chia job thành:

```python
assignable = status == "Chưa phân công"
fixed      = status == "Đã phân công"
```

Sau đó xử lý:

### System-fixed

```text
Đã phân công + EMP_ACCOUNT
→ giữ nguyên KTV hệ thống
→ không chạy scorer
```

### Locked assignment

```text
Job optimizer đã giữ từ snapshot trước
→ giữ thẳng KTV
→ không so sánh lại candidate
```

### Job chưa phân công còn lại

```text
build compatibility graph
→ greedy assignment
→ routing
```

Nó còn khởi tạo workload từ:

- Workload roster.
- Job active đang xử lý.
- Job tạm dừng.
- System-fixed.
- Locked assignment.

---

## 6. `_capacity_minutes()`

Trong [assignment.py](/home/nguyenquocphu/fpt/fpt_ktv_optimization/src/ktv_optimizer/optimization/assignment.py).

Tính capacity KTV:

```python
capacity = SHIFT_END - SHIFT_START
```

Ví dụ:

```text
06:00 → 18:00 = 720 phút
```

Nếu KTV đã có 660 phút và job mới 120 phút:

```text
660 + 120 = 780 > 720
→ reject
```

Nếu roster không có giờ ca thì capacity là `None`, tức không áp giới hạn này.

---

## 7. `NearestNeighbourRouter.build_route()`

File: [routing.py](/home/nguyenquocphu/fpt/fpt_ktv_optimization/src/ktv_optimizer/optimization/routing.py)

Sau khi biết KTV có những job nào, router chọn thứ tự:

```text
due_at sớm
→ priority cao
→ khoảng cách gần
→ checklist_id
```

Sau đó tính:

```text
travel_minutes
= Haversine distance / average_speed × 60

estimated_arrival
= previous_finish + travel_minutes

estimated_finish
= estimated_arrival + service_minutes
```

Nếu KTV đang `BUSY` và có `AVAILABLE_AT`, queue mới bắt đầu từ thời điểm đó.

---

## 8. `SnapshotReducer.reduce()`

File: [reducer.py](/home/nguyenquocphu/fpt/fpt_ktv_optimization/src/ktv_optimizer/state/reducer.py)

Đây là lớp bao ngoài optimizer để xử lý nhiều snapshot:

```text
previous checkpoint + current snapshot
→ canonicalize
→ diff
→ derive KTV status
→ resolve incumbent/system override
→ gọi SimpleKtvOptimizer
→ tạo job state
→ tạo technician state
→ tạo job queue
```

Nó quyết định KTV nào được tham gia:

```text
IDLE        → được nhận
RESERVED    → được nhận thêm vào queue
BUSY        → được nhận thêm vào queue
UNAVAILABLE → không được nhận
OFF_SHIFT   → không được nhận
```

---

## 9. `build_post_optimization_states()`

File: [availability.py](/home/nguyenquocphu/fpt/fpt_ktv_optimization/src/ktv_optimizer/state/availability.py)

Sau khi optimizer chạy xong, hàm này cập nhật:

```text
WORK_STATUS
CURRENT_JOB_ID
AVAILABLE_AT
PLANNED_JOB_COUNT
QUEUED_JOB_COUNT
```

Ví dụ:

```text
KTV-A:
WORK_STATUS=RESERVED
CURRENT_JOB_ID=J01
PLANNED_JOB_COUNT=5
QUEUED_JOB_COUNT=5
```

Nếu J01 bắt đầu:

```text
WORK_STATUS=BUSY
CURRENT_JOB_ID=J01
PLANNED_JOB_COUNT=5
QUEUED_JOB_COUNT=4
```

---

## 10. `build_technician_job_queue()`

File: [queue.py](/home/nguyenquocphu/fpt/fpt_ktv_optimization/src/ktv_optimizer/state/queue.py)

Chuyển assignment, route và technician state thành queue dễ đọc:

```text
J01 → IN_PROGRESS, position 0
J02 → NEXT, position 1
J03 → QUEUED, position 2
J04 → PAUSED, chưa có position
```

Kết quả được xuất vào:

```text
technician_job_queue.csv
```

Tóm lại:

```text
job_cluster_key()
    tạo cụm

CompatibilityGraphBuilder.build()
    quyết định edge nào hợp lệ

AssignmentScorer.score()
    tính cost của edge hợp lệ

GreedyAssignmentSolver.solve()
    chọn edge có cost thấp nhất

SimpleKtvOptimizer.optimize()
    điều phối fixed + locked + assign + route

NearestNeighbourRouter
    sắp thứ tự tuyến

SnapshotReducer
    nối previous/current snapshot

build_post_optimization_states()
    cập nhật trạng thái KTV

build_technician_job_queue()
    tạo queue nhiều job/KTV
```

Hiện chỉ assignment có hàm cost rõ ràng. Routing đang dùng thứ tự ưu tiên dạng tuple, chưa có một route objective tổng hợp riêng.
