# UML — KTV Routing

Sơ đồ theo code hiện tại. Repo chia làm 3 phần:
- `src/ktv_routing`: phần deploy;
- `simulator/ktv_simulator`: tạm đóng vai team data, không deploy;
- `research`: phân tích offline.

| Ký hiệu | Nghĩa |
|---|---|
| `<<frozen>>` | `@dataclass(frozen=True, slots=True)`: tạo xong không sửa được |
| `<<mutable>>` | `@dataclass` thường |
| `<<enum>>` | `str, Enum`: ra JSON là chuỗi |
| `<<interface>>` | `Protocol`: phía team khác hiện thực |
| `<<module>>` | file chỉ gồm hàm/hằng, vẽ thành một khối |

## 1. Luồng giữa các team

```mermaid
flowchart LR
    UI["Frontend<br/>chọn điều kiện lọc"]
    DATA["Team data / hệ thống checklist<br/>lọc, lấy job đã gán,<br/>vị trí KTV, ca làm"]
    SIM["simulator<br/>EventWorkloadProvider<br/>(đọc luồng sự kiện)"]
    subgraph ROUTING["Team routing: src/ktv_routing"]
        SVC["RoutingService"]
        PLAN["plan_routes"]
    end
    UI -- "WorkloadQuery<br/>planned_at + filter" --> SVC
    SVC -- "WorkloadQuery" --> DATA
    DATA -- "RouteRequest" --> SVC
    SVC --> PLAN
    PLAN -- "RouteResponse" --> UI
    SIM -. "tạm thay, cùng hợp đồng" .-> DATA
```

## 2. Module và chiều import

Mũi tên nghĩa là "import". Không có mũi tên nào đi từ phần deploy sang simulator hay research.

```mermaid
flowchart TB
    subgraph DEPLOY["src/ktv_routing — deploy, chỉ thư viện chuẩn"]
        contract["contract.py<br/>dataclass + JSON"]
        planner["planner.py<br/>xếp tuyến"]
        rules["rules.py<br/>rule nghiệp vụ, tầng, trọng số"]
        travel["travel.py<br/>km/phút: OSRM hoặc chim bay"]
        service["service.py<br/>query → data → planner"]
        cli["__main__.py<br/>request.json → response.json"]
    end
    subgraph SIMULATOR["simulator/ktv_simulator — không deploy"]
        events["events.py<br/>export QOS → luồng sự kiện (pandas)"]
        provider["provider.py<br/>luồng sự kiện → RouteRequest"]
        geocoding["geocoding.py<br/>địa chỉ → tâm phường/xã"]
        simcli["__main__.py<br/>--at, --replay, --serve"]
        convert["convert_xlsx.py<br/>xlsx → CSV"]
        fake["fake_boundary.py<br/>TẠM: boundary giả từ check-in"]
        web["web.py + web.html<br/>demo frontend + tua realtime"]
    end
    subgraph RESEARCH["research — offline"]
        timemodel["time_model.py<br/>học thời gian → JSON"]
        backtest["backtest_routing.py<br/>planner so với KTV thật"]
        qos["qos_data.py"]
        features["features.py"]
        baselines["baselines.py"]
        build["build_dataset.py"]
        evaluate["evaluate_baselines.py"]
    end
    OSRM[("OSRM /table<br/>public hoặc tự host")]
    planner --> contract
    planner --> rules
    planner --> travel
    cli --> rules
    travel --> contract
    travel -. "HTTP" .-> OSRM
    service --> contract
    service --> planner
    service --> travel
    cli --> contract
    cli --> planner
    cli --> travel
    simcli --> travel
    web --> travel
    geocoding --> contract
    provider --> contract
    events --> provider
    events --> geocoding
    simcli --> provider
    simcli --> planner
    fake --> geocoding
    fake --> events
    simcli --> web
    web --> provider
    web --> planner
    web --> contract
    build --> qos
    build --> features
    evaluate --> baselines
    timemodel --> planner
    timemodel --> events
    backtest --> timemodel
    backtest --> provider
    backtest --> planner
```

## 3. Hợp đồng vào (`contract.py`)

```mermaid
classDiagram
    direction LR
    class WorkloadQuery {
        <<frozen>>
        +planned_at: datetime
        +filter: JobFilter
    }
    class JobFilter {
        <<frozen>>
        +case_types: tuple~str~
        +branch_names: tuple~str~
        +emp_accounts: tuple~str~
    }
    class WorkloadProvider {
        <<interface>>
        +fetch_workload(query: WorkloadQuery) RouteRequest
    }
    class RouteRequest {
        <<frozen>>
        +planned_at: datetime
        +technicians: tuple~TechnicianInput~
        +filter: JobFilter
    }
    class TechnicianInput {
        <<frozen>>
        +emp_account: str
        +jobs: tuple~JobInput~
        +last_location: LocationFix | None
        +shift_start: datetime | None
        +shift_end: datetime | None
        +previous_sequence: tuple~str~
    }
    class JobInput {
        <<frozen>>
        +job_id: str
        +state: JobState
        +location: GeoPoint | None
        +case_type: str | None
        +address: str | None
        +due_at: datetime | None
        +priority: int | None
        +started_at: datetime | None
        +appointment_start: datetime | None
        +complete_by: datetime | None
        +area: str | None
    }
    class LocationFix {
        <<frozen>>
        +location: GeoPoint
        +recorded_at: datetime
        +source: str
    }
    class GeoPoint {
        <<frozen>>
        +lat: float
        +lng: float
    }
    class JobState {
        <<enum>>
        PENDING
        IN_PROGRESS
    }
    WorkloadQuery *-- JobFilter
    WorkloadProvider ..> WorkloadQuery : nhận
    WorkloadProvider ..> RouteRequest : trả
    RouteRequest *-- JobFilter
    RouteRequest *-- "0..*" TechnicianInput
    TechnicianInput *-- "0..*" JobInput
    TechnicianInput *-- "0..1" LocationFix
    JobInput --> JobState
    JobInput *-- "0..1" GeoPoint
    LocationFix *-- GeoPoint
```

## 4. Hợp đồng ra (`contract.py`)

```mermaid
classDiagram
    direction LR
    class RouteResponse {
        <<frozen>>
        +planned_at: datetime
        +filter: JobFilter
        +routes: tuple~TechnicianRoute~
        +issues: tuple~Issue~
        +summary: RouteSummary
    }
    class TechnicianRoute {
        <<frozen>>
        +emp_account: str
        +start_at: datetime
        +start_location: GeoPoint | None
        +start_source: StartSource
        +in_progress_job_id: str | None
        +travel_source: str
        +sequence_source: SequenceSource
        +previous_route: PreviousRoute
        +score: dict
        +stops: tuple~PlannedStop~
        +total_km: float
        +total_travel_minutes: float
        +total_service_minutes: float
        +finish_at: datetime | None
    }
    class PlannedStop {
        <<frozen>>
        +sequence: int
        +job_id: str
        +location: GeoPoint
        +leg_km: float | None
        +leg_minutes: float | None
        +eta: datetime | None
        +wait_minutes: float | None
        +finish_at: datetime | None
        +due_at: datetime | None
        +late: bool | None
        +completion_late: bool | None
        +after_shift_end: bool | None
    }
    class Issue {
        <<frozen>>
        +code: str
        +emp_account: str | None
        +job_id: str | None
        +detail: str | None
    }
    class RouteSummary {
        <<frozen>>
        +technicians: int
        +jobs: int
        +pending_jobs: int
        +in_progress_jobs: int
        +routed_stops: int
        +stops_without_eta: int
        +late_stops: int
        +completion_late_stops: int
        +stops_after_shift_end: int
        +sla_evaluable_jobs: int
        +on_time_stops: int
        +on_time_rate_percent: float | None
        +total_km: float
        +travel_ms: float
        +planning_ms: float
    }
    class StartSource {
        <<enum>>
        IN_PROGRESS_JOB
        LAST_LOCATION
        STALE_LOCATION
        UNKNOWN
    }
    class SequenceSource {
        <<enum>>
        OPTIMAL
        APPROXIMATE
        HEURISTIC
    }
    class PreviousRoute {
        <<enum>>
        NONE
        KEPT
        CHANGED
    }
    RouteResponse *-- "0..*" TechnicianRoute
    RouteResponse *-- "0..*" Issue
    RouteResponse *-- RouteSummary
    TechnicianRoute *-- "0..*" PlannedStop
    TechnicianRoute --> StartSource
    TechnicianRoute --> SequenceSource
    TechnicianRoute --> PreviousRoute
```

## 5. Lõi routing (`planner.py`, `travel.py`, `service.py`, JSON)

```mermaid
classDiagram
    direction LR
    class RoutingService {
        +provider: WorkloadProvider
        +config: RoutingConfig
        +travel: TravelModel | None
        +plan(query: WorkloadQuery) RouteResponse
    }
    class TravelModel {
        <<interface>>
        +matrices(groups) list~TravelMatrix~
    }
    class TravelMatrix {
        <<frozen>>
        +km: tuple
        +minutes: tuple
        +source: str
        +note: str | None
    }
    class HaversineTravel {
        <<frozen>>
        +average_speed_kmh: float
        +minutes(km) float
        +matrix(points, note) TravelMatrix
        +matrices(groups) list~TravelMatrix~
    }
    class OsrmTravel {
        +base_url: str
        +profile: str
        +max_locations: int
        +timeout_seconds: float
        +min_interval_seconds: float
        +parallel_requests: int
        +fallback: HaversineTravel
        +calls: int
        +matrices(groups) list~TravelMatrix~
        -_solve(groups, batch) list~TravelMatrix~
        -_slice(group, position, distances, durations) TravelMatrix
        -_table(points) tuple
    }
    class OsrmError {
        <<exception>>
    }
    class travel {
        <<module>>
        +PUBLIC_OSRM_URL
        +distance_km(first, second) float
        +travel_model(kind, osrm_url, average_speed_kmh) TravelModel
    }
    class RoutingConfig {
        <<frozen>>
        +average_speed_kmh: float
        +location_max_age_minutes: float
        +default_service_minutes: float
        +service_minutes_by_case_type: dict
        +service_minutes_by_emp: dict
        +transition: TransitionTable | None
        +rules: BusinessRules
        +service_minutes(job: JobInput, emp_account) float
    }
    class BusinessRules {
        <<frozen>>
        +tiers: tuple~dict~
        +priority_weights: dict
        +default_priority_weight: float
        +max_exact_jobs: int
        +max_labels_per_state: int
        +previous_route_policy: str
        +reroute_min_gain: float
        +priority_weight(priority) float
        +objective_key(score) tuple
        +catalog() dict
    }
    class RuleInfo {
        <<frozen>>
        +code: str
        +name: str
        +unit: str
        +source: str
        +description: str
    }
    class rules_module {
        <<module>>
        +SOFT_RULES
        +HARD_RULES
        +DEFAULT_TIERS
        +DEFAULT_PRIORITY_WEIGHTS
        +rules_to_dict(rules) dict
        +rules_from_dict(data) BusinessRules
        +load_rules(path) BusinessRules
    }
    class _Problem {
        +solve(before) tuple
        +heuristic(before) list
        +improve(order, before) list
        +walk(order) tuple
        -step(mask, here, job, clock) tuple
        -extend(label, mask, here, job) _Label
        -keep(bucket, label) bool
    }
    class TransitionTable {
        <<frozen>>
        +km_edges: tuple
        +minutes: tuple
        +minutes_by_hour: dict
        +leg_minutes(km, depart_at) float
    }
    class planner {
        <<module>>
        +DEFAULT_SERVICE_MINUTES: dict
        +TIME_MODEL_FORMAT
        +plan_routes(request, config, travel) RouteResponse
        +time_model_config(model, config) RoutingConfig
        +load_time_model(path, config) RoutingConfig
        -resolve_start() điểm và giờ xuất phát
        -sequence() QHĐ theo rule, giữ/đổi tuyến cũ, tính ETA từng điểm
    }
    class contract {
        <<module>>
        +to_json_dict(value) dict
        +query_from_dict(data) WorkloadQuery
        +request_from_dict(data) RouteRequest
    }
    class ContractError {
        <<exception>>
    }
    class WorkloadProvider {
        <<interface>>
    }
    RoutingService --> WorkloadProvider : gọi fetch_workload
    RoutingService --> RoutingConfig
    RoutingService --> TravelModel
    RoutingService ..> planner : gọi plan_routes
    RoutingService ..> ContractError : filter/planned_at lệch
    planner ..> RoutingConfig
    RoutingConfig *-- "0..1" TransitionTable : mô hình thời gian
    RoutingConfig *-- BusinessRules : rule nghiệp vụ
    BusinessRules ..> RuleInfo : mô tả rule
    rules_module ..> BusinessRules : đọc/ghi JSON
    planner ..> _Problem : mỗi KTV một bài QHĐ
    _Problem ..> BusinessRules : tầng, trọng số, giới hạn
    planner ..> TransitionTable : load_time_model tạo
    planner ..> TravelModel : 1 lần cho mọi KTV
    TravelModel ..> TravelMatrix : trả mỗi KTV một ma trận
    HaversineTravel ..|> TravelModel
    OsrmTravel ..|> TravelModel
    OsrmTravel --> HaversineTravel : dự phòng khi lỗi
    OsrmTravel ..> OsrmError : bắt nội bộ, không ném ra ngoài
    travel ..> OsrmTravel : travel_model tạo
    contract ..> ContractError : JSON sai hợp đồng
```

## 6. Một lần lập tuyến

```mermaid
sequenceDiagram
    actor User as Người dùng
    participant UI as Frontend
    participant SVC as RoutingService
    participant DATA as Team data hoặc simulator
    participant PLAN as plan_routes
    participant OSRM as OSRM /table
    User->>UI: chọn MAINTENANCE, chi nhánh HNI_04
    UI->>SVC: WorkloadQuery(planned_at, filter)
    SVC->>DATA: fetch_workload(query)
    DATA-->>SVC: RouteRequest: job đã gán, vị trí, ca làm
    SVC->>SVC: kiểm tra planned_at và filter khớp query
    SVC->>PLAN: plan_routes(request, config, travel)
    PLAN->>PLAN: từng KTV: chọn điểm và giờ xuất phát
    PLAN->>OSRM: tự host = mỗi KTV 1 request song song, public = gom ≤ 100 điểm
    OSRM-->>PLAN: ma trận mét và giây
    loop từng KTV
        PLAN->>PLAN: QHĐ trên (tập job đã làm, job cuối), so theo tầng rule
        PLAN->>PLAN: có tuyến cũ thì giữ, trừ khi tuyến mới tốt hơn rõ
        PLAN->>PLAN: tới = xong trước + leg_minutes, chờ mốc hẹn, trễ khi check-in sau hạn
    end
    PLAN-->>SVC: RouteResponse
    SVC-->>UI: tuyến, ETA, issue, summary
```

## 7. Simulator (`simulator/ktv_simulator`)

```mermaid
classDiagram
    direction LR
    class WorkloadProvider {
        <<interface>>
        +fetch_workload(query: WorkloadQuery) RouteRequest
    }
    class EventWorkloadProvider {
        +path: Path
        +shift: tuple | None
        +info: dict
        +clock: datetime
        +fetch_workload(query: WorkloadQuery) RouteRequest
        +build(query: WorkloadQuery) tuple
        +advance_to(at, collect) list~Change~
        +close() None
        -_read(offset) tuple | None
        -_apply(state, event, at, changes) None
    }
    class _State {
        <<mutable>>
        +clock: datetime
        +offset: int
        +applied: int
        +jobs: dict
        +visiting: dict
        +locations: dict
        +copy() _State
    }
    class _Job {
        <<frozen>>
        +job_id: str
        +emp_account: str | None
        +branch_name: str | None
        +case_type: str | None
        +address: str | None
        +location: GeoPoint | None
        +created_at: datetime
        +started_at: datetime | None
    }
    class Change {
        <<frozen>>
        +at: datetime
        +type: str
        +job_id: str
        +emp_account: str | None
        +branch_name: str | None
        +case_type: str | None
    }
    class BuildStats {
        <<frozen>>
        +open_onsite_jobs: int
        +matched_jobs: int
        +missing_technician: int
        +without_location: int
        +in_progress_jobs: int
        +technicians: int
        +technicians_with_location: int
        +events_applied: int
    }
    class provider_rules {
        <<module>>
        +EVENT_FORMAT
        +EVENT_TYPES
        +CASE_TYPE_RULES
        +REMOTE_CASE_TYPES
        +filter_changes(changes, job_filter) list~Change~
    }
    class events {
        <<module>>
        +OPEN_STATUSES
        +geocode(addresses, boundary_geojson, workers) dict
        +build_events(maintenance_csv, boundary_geojson) tuple
        +write_events(path, header, events) None
    }
    class web {
        <<module>>
        +catalog(provider, travel) dict
        +replay_step(provider, config, travel, query, to) dict
        +make_server(provider, config, travel, host, port) ThreadingHTTPServer
        +serve(provider, config, travel, host, port) None
    }
    class WardBoundaryIndex {
        +boundaries: tuple
        +from_geojson(path) WardBoundaryIndex
        +match(address, fuzzy_threshold) LocationMatch
    }
    class WardBoundary {
        <<frozen>>
        +ward_code: str
        +ward_name: str
        +province_name: str
        +point: GeoPoint
        +normalized_ward: str
        +normalized_province: str
    }
    class LocationMatch {
        <<frozen>>
        +address: str
        +ward_code: str
        +ward_name: str
        +province_name: str
        +point: GeoPoint
        +confidence: float
        +method: str
    }
    EventWorkloadProvider ..|> WorkloadProvider
    EventWorkloadProvider *-- "1..*" _State : hiện tại + snapshot mỗi ngày
    _State *-- "0..*" _Job : job đang mở
    EventWorkloadProvider ..> Change : advance_to trả
    EventWorkloadProvider ..> BuildStats : build trả kèm
    EventWorkloadProvider ..> provider_rules : giả định nghiệp vụ
    events ..> provider_rules : EVENT_FORMAT
    events ..> WardBoundaryIndex : geocode địa chỉ
    web --> EventWorkloadProvider
    web ..> Change : lọc ra KTV cần xếp lại
    WardBoundaryIndex *-- "0..*" WardBoundary
    WardBoundaryIndex ..> LocationMatch : match trả
```

`provider_rules` là tên vẽ cho nhóm hằng số và hàm lọc ở đầu `provider.py`; `events` và `web` là hai file module. Không file nào là class.

### Một nhịp realtime (`web.replay_step`, dùng cho `--replay` và nút ▶ Chạy)

```mermaid
sequenceDiagram
    participant UI as Web demo hoặc --replay
    participant DATA as EventWorkloadProvider
    participant FILE as events_2026-06.jsonl
    participant PLAN as plan_routes
    UI->>DATA: advance_to(T), advance_to(T + bước, collect)
    DATA->>FILE: đọc tiếp từ offset
    DATA-->>UI: Change của job đang mở, gồm VISIT_ENDED
    UI->>UI: lọc theo filter, lấy KTV bị ảnh hưởng
    alt có KTV bị ảnh hưởng
        UI->>DATA: build(WorkloadQuery(T + bước, filter + emp_accounts))
        DATA-->>UI: RouteRequest của riêng các KTV đó
        UI->>PLAN: plan_routes(request)
        PLAN-->>UI: tuyến mới, ghép vào bảng
    else không có
        UI->>UI: giữ nguyên mọi tuyến
    end
```

## 8. Research (`research/`)

```mermaid
classDiagram
    direction LR
    class QosCsvSource {
        +maintenance_path: Path
        +checkins_path: Path
        +read_maintenance(nrows) DataFrame
        +read_checkins(nrows) DataFrame
    }
    class ValidationReport {
        <<frozen>>
        +dataset: str
        +row_count: int
        +issues: tuple~ValidationIssue~
        +is_valid: bool
        +to_frame() DataFrame
    }
    class ValidationIssue {
        <<frozen>>
        +severity: str
        +code: str
        +count: int
        +message: str
    }
    class OfflineDatasetBuilder {
        +source: QosCsvSource
        +max_service_minutes: float
        +build(nrows) DatasetBuildResult
    }
    class DatasetBuildResult {
        <<frozen>>
        +dataset: DataFrame
        +maintenance_validation: ValidationReport
        +checkin_validation: ValidationReport
    }
    class FeatureRegistry {
        <<mutable>>
        +builders: dict
        +register(name, builder) None
        +build(name, frame) DataFrame
    }
    class DurationBaseline {
        <<mutable>>
        +group_columns: tuple
        +global_median_: float | None
        +lookup_: DataFrame | None
        +fit(frame) DurationBaseline
        +predict(frame) Series
    }
    class LatenessBaseline {
        <<mutable>>
        +group_columns: tuple
        +smoothing: float
        +global_rate_: float | None
        +lookup_: DataFrame | None
        +fit(frame) LatenessBaseline
        +predict_proba(frame) Series
    }
    class time_model {
        <<module>>
        +KM_EDGES
        +load_visits(maintenance_csv, checkins_csv) DataFrame
        +load_transitions(visits) DataFrame
        +fit_time_model(visits, transitions, start, end) dict
        +evaluate_time_model(model, visits, transitions, start, end) dict
    }
    class backtest_routing {
        <<module>>
        +next_job_decisions(provider, transitions) tuple
        +day_replays(visits, configs, travel) tuple
        +follow_order(jobs, emp_account, origin, at, config, travel) TechnicianRoute
        +summarize_decisions(frame, skipped) dict
        +summarize_days(days, config_names) dict
        +summarize_etas(etas) dict
    }
    backtest_routing ..> time_model : load_visits, load_transitions
    OfflineDatasetBuilder --> QosCsvSource
    OfflineDatasetBuilder ..> DatasetBuildResult : trả
    DatasetBuildResult *-- "2" ValidationReport
    ValidationReport *-- "0..*" ValidationIssue
```

## 9. File → nội dung

| File | Class / hàm chính |
|---|---|
| `src/ktv_routing/contract.py` | `WorkloadQuery`, `JobFilter`, `RouteRequest`, `TechnicianInput`, `JobInput`, `LocationFix`, `GeoPoint`, `JobState`, `WorkloadProvider`, `RouteResponse`, `TechnicianRoute`, `PlannedStop`, `Issue`, `RouteSummary`, `StartSource`, `ContractError`, `to_json_dict`, `query_from_dict`, `request_from_dict` |
| `src/ktv_routing/rules.py` | `BusinessRules`, `RuleInfo`, `SOFT_RULES`, `HARD_RULES`, `rules_to_dict`, `rules_from_dict`, `load_rules` — rule nghiệp vụ, tầng, trọng số |
| `src/ktv_routing/planner.py` | `RoutingConfig`, `TransitionTable`, `plan_routes` (QHĐ qua `_Problem`), `load_time_model`, `time_model_config` |
| `src/ktv_routing/travel.py` | `TravelModel`, `TravelMatrix`, `HaversineTravel`, `OsrmTravel`, `distance_km`, `travel_model` |
| `src/ktv_routing/service.py` | `RoutingService` |
| `simulator/ktv_simulator/events.py` | `build_events`, `write_events`, `geocode` + CLI — export QOS → luồng sự kiện JSONL (pandas) |
| `simulator/ktv_simulator/provider.py` | `EventWorkloadProvider`, `Change`, `BuildStats`, `filter_changes`, giả định SLA |
| `simulator/ktv_simulator/geocoding.py` | `WardBoundaryIndex`, `WardBoundary`, `LocationMatch` |
| `simulator/ktv_simulator/fake_boundary.py` | TẠM: `ward_and_province`, `build` — boundary giả từ check-in |
| `simulator/ktv_simulator/convert_xlsx.py` | `convert`, `read_shared_strings` — workbook → CSV UTF-8 |
| `simulator/ktv_simulator/web.py` + `web.html` | `make_server`, `serve`, `catalog`, `replay_step` — web demo: `GET /api/options`, `POST /api/plan`, `POST /api/replay` |
| `research/time_model.py` | `load_visits`, `load_transitions`, `fit_time_model`, `evaluate_time_model` + CLI — học thời gian làm và khoảng chuyển job |
| `research/backtest_routing.py` | `next_job_decisions`, `day_replays`, `follow_order` + CLI — planner so với KTV thật, sai số ETA |
| `research/qos_data.py` | `QosCsvSource`, chuẩn hóa CSV, `ValidationReport`, `ValidationIssue` |
| `research/features.py` | `FeatureRegistry`, `build_job_features`, `add_technician_history_features` |
| `research/baselines.py` | `DurationBaseline`, `LatenessBaseline` |
| `research/build_dataset.py` | `OfflineDatasetBuilder`, `DatasetBuildResult` + CLI |

## 10. Đã bỏ so với bản trước

| Phần cũ | Lý do |
|---|---|
| `domain/checklist_status.py`, `state/events.py` (8 status, vòng đời, event) | Việc của hệ thống checklist; routing chỉ cần `PENDING` / `IN_PROGRESS` |
| `storage/` SQLite, `pipeline/`, `evaluation/` | Việc của team database / BI; routing không lưu trạng thái |
| `optimization/assignment`, `compatibility`, `scoring`, `optimizer`, `domain/job`… | Routing không gán việc |
| 3 web demo trong `application/` | Việc của frontend |
