"""Lõi routing: ``RouteRequest`` → ``RouteResponse``.

Mỗi KTV được xếp độc lập, chỉ trên job đã gán sẵn cho KTV đó; routing không gán lại việc.

Thứ tự chọn bằng quy hoạch động (QHĐ) theo rule nghiệp vụ trong ``rules.py``:
- Trạng thái = (tập job đã làm, job làm cuối). Mỗi trạng thái giữ các nhãn (giờ xong, tổng chi
  phí từng tầng) không bị nhãn nào khác cùng trạng thái hơn ở mọi mặt.
- Mở rộng nhãn = đi tiếp tới một job: tính giờ tới, chờ mốc hẹn, giờ xong và chi phí rule mềm.
  Rule cứng (giữ thứ tự tuyến cũ) chặn job chưa đủ điều kiện.
- Chọn nhãn đã đi hết job có khóa nhỏ nhất theo tầng rule, rồi lần ngược lấy thứ tự.
Quá ``max_exact_jobs`` job thì dùng heuristic: tham lam theo cùng khóa, rồi cải thiện bằng 2-opt.

Giờ: tới = xong điểm trước + phút chuyển; check-in = max(tới, mốc hẹn đầu); xong = check-in +
thời gian làm. Trễ hẹn khi check-in sau ``due_at``; trễ hoàn tất khi xong sau ``complete_by``.

Km và phút di chuyển lấy từ ``TravelModel`` (chim bay hoặc OSRM). Có mô hình thời gian học từ
lịch sử (``load_time_model``) thì thời gian làm lấy theo từng KTV, và phút giữa hai điểm tra
bảng khoảng chuyển job (gồm cả chờ, nghỉ trưa); km vẫn lấy từ ``TravelModel``.
"""

from __future__ import annotations

import json
from bisect import bisect_left
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from time import perf_counter

from .contract import (
    GeoPoint,
    Issue,
    JobInput,
    JobState,
    PlannedStop,
    PreviousRoute,
    RouteRequest,
    RouteResponse,
    RouteSummary,
    SequenceSource,
    StartSource,
    TechnicianInput,
    TechnicianRoute,
)
from .rules import SOFT_RULE_CODES, BusinessRules
from .travel import HaversineTravel, TravelMatrix, TravelModel, distance_km

# Thời gian làm tại chỗ V0 theo CASE_TYPE (phút). Đây là phần routing tự ước
# lượng; có mô hình thời gian học từ lịch sử thì dùng số học được.
DEFAULT_SERVICE_MINUTES: dict[str, float] = {
    "TRIỂN KHAI MỚI (NET, COMBO..)": 120,
    "BOX, CAM ONLY": 120,
    "SWAP": 60,
    "MAINTENANCE": 60,
    "VẬT LÝ": 60,
    "LOGIC": 60,
    "THU HỒI THIẾT BỊ": 15,
}
TIME_MODEL_FORMAT = "ktv-time-model/1"
_MAX_IMPROVE_JOBS = 40  # 2-opt tốn O(n³) mỗi vòng; nhiều job hơn thì chỉ dùng tham lam.

_RULE = {code: index for index, code in enumerate(SOFT_RULE_CODES)}
_LATE_CHECKIN = _RULE["LATE_CHECKIN"]
_LATE_MINUTES = _RULE["LATE_MINUTES"]
_LATE_COMPLETION = _RULE["LATE_COMPLETION"]
_AFTER_SHIFT = _RULE["AFTER_SHIFT"]
_KM = _RULE["KM"]
_TRAVEL_MINUTES = _RULE["TRAVEL_MINUTES"]
_AREA_REENTRY = _RULE["AREA_REENTRY"]
_PRIORITY_DELAY = _RULE["PRIORITY_DELAY"]
_FINISH = _RULE["FINISH"]


@dataclass(frozen=True, slots=True)
class TransitionTable:
    """Phút từ lúc xong job trước tới lúc check-in job sau, học từ lịch sử KTV.

    Gồm cả di chuyển lẫn chờ khách, nghỉ trưa... Tra theo km chim bay giữa hai điểm
    và giờ rời điểm trước. Cột ``i`` ứng với ``km_edges[i-1] < km ≤ km_edges[i]``.
    """

    km_edges: tuple[float, ...]
    minutes: tuple[float, ...]  # Dùng cho mọi giờ không có trong minutes_by_hour.
    minutes_by_hour: dict[int, tuple[float, ...]] = field(default_factory=dict)

    def leg_minutes(self, km: float, depart_at: datetime) -> float:
        row = self.minutes_by_hour.get(depart_at.hour, self.minutes)
        return row[bisect_left(self.km_edges, km)]


@dataclass(frozen=True, slots=True)
class RoutingConfig:
    average_speed_kmh: float = 30.0  # Đổi km chim bay ra phút khi không có đường bộ.
    location_max_age_minutes: float = 240.0  # Vị trí cũ hơn ngưỡng là stale.
    default_service_minutes: float = 60.0  # CASE_TYPE không có trong bảng.
    service_minutes_by_case_type: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_SERVICE_MINUTES)
    )
    # KTV → CASE_TYPE → phút; ưu tiên hơn bảng theo CASE_TYPE.
    service_minutes_by_emp: dict[str, dict[str, float]] = field(default_factory=dict)
    # None: phút giữa hai điểm lấy từ TravelModel.
    transition: TransitionTable | None = None
    rules: BusinessRules = field(default_factory=BusinessRules)

    def __post_init__(self) -> None:
        if self.average_speed_kmh <= 0:
            raise ValueError("average_speed_kmh phải > 0")

    def service_minutes(self, job: JobInput, emp_account: str | None = None) -> float:
        key = (job.case_type or "").strip().upper()
        personal = self.service_minutes_by_emp.get(emp_account or "")
        if personal and key in personal:
            return personal[key]
        return self.service_minutes_by_case_type.get(
            key, self.default_service_minutes
        )


def _minutes(value: object, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError(f"{path}: cần số phút ≥ 0")
    return float(value)


def _minutes_row(value: object, path: str, size: int) -> tuple[float, ...]:
    if not isinstance(value, list) or len(value) != size:
        raise ValueError(f"{path}: cần danh sách {size} số phút")
    return tuple(_minutes(item, f"{path}[{index}]") for index, item in enumerate(value))


def time_model_config(model: object, config: RoutingConfig | None = None) -> RoutingConfig:
    """Áp mô hình thời gian (dict JSON do ``research/time_model.py`` sinh) lên config.

    Bảng theo CASE_TYPE của mô hình ghi đè bảng mặc định; CASE_TYPE mô hình chưa
    học vẫn giữ số mặc định.
    """

    config = config or RoutingConfig()
    if not isinstance(model, dict) or model.get("format") != TIME_MODEL_FORMAT:
        raise ValueError(f"cần object JSON có format = {TIME_MODEL_FORMAT}")
    service = model.get("service_minutes")
    transition = model.get("transition_minutes")
    if not isinstance(service, dict) or not isinstance(transition, dict):
        raise ValueError("cần service_minutes và transition_minutes")

    by_case_type = {
        str(case_type).strip().upper(): _minutes(value, f"service_minutes.by_case_type.{case_type}")
        for case_type, value in (service.get("by_case_type") or {}).items()
    }
    by_emp: dict[str, dict[str, float]] = {}
    for account, values in (service.get("by_emp") or {}).items():
        if not isinstance(values, dict):
            raise ValueError(f"service_minutes.by_emp.{account}: cần object CASE_TYPE → phút")
        by_emp[str(account)] = {
            str(case_type).strip().upper(): _minutes(value, f"service_minutes.by_emp.{account}.{case_type}")
            for case_type, value in values.items()
        }

    edges_value = transition.get("km_edges")
    if not isinstance(edges_value, list) or not edges_value:
        raise ValueError("transition_minutes.km_edges: cần danh sách km tăng dần")
    edges = tuple(_minutes(value, "transition_minutes.km_edges") for value in edges_value)
    if any(later <= earlier for earlier, later in zip(edges, edges[1:])):
        raise ValueError("transition_minutes.km_edges: cần tăng dần")
    size = len(edges) + 1
    by_hour: dict[int, tuple[float, ...]] = {}
    for hour, row in (transition.get("by_hour") or {}).items():
        if not str(hour).isdigit() or not 0 <= int(hour) <= 23:
            raise ValueError(f"transition_minutes.by_hour.{hour}: giờ phải là 0..23")
        by_hour[int(hour)] = _minutes_row(row, f"transition_minutes.by_hour.{hour}", size)

    return replace(
        config,
        default_service_minutes=_minutes(service.get("default"), "service_minutes.default"),
        service_minutes_by_case_type={**config.service_minutes_by_case_type, **by_case_type},
        service_minutes_by_emp=by_emp,
        transition=TransitionTable(
            km_edges=edges,
            minutes=_minutes_row(transition.get("minutes"), "transition_minutes.minutes", size),
            minutes_by_hour=by_hour,
        ),
    )


def load_time_model(path: str | Path, config: RoutingConfig | None = None) -> RoutingConfig:
    try:
        with Path(path).open(encoding="utf-8") as handle:
            model = json.load(handle)
        return time_model_config(model, config)
    except ValueError as error:  # Gồm cả JSON hỏng.
        raise ValueError(f"{path}: {error}") from None


@dataclass(frozen=True, slots=True)
class _Start:
    at: datetime
    location: GeoPoint | None
    source: StartSource
    in_progress_job_id: str | None = None


def plan_routes(
    request: RouteRequest,
    config: RoutingConfig | None = None,
    travel: TravelModel | None = None,
) -> RouteResponse:
    config = config or RoutingConfig()
    travel = travel or HaversineTravel(config.average_speed_kmh)
    clock = perf_counter()
    issues: list[Issue] = []
    planned: list[tuple[TechnicianInput, list[JobInput], _Start]] = []
    owner_by_job: dict[str, str] = {}
    seen_technicians: set[str] = set()
    job_count = pending_count = in_progress_count = sla_evaluable = 0

    for technician in sorted(request.technicians, key=lambda item: item.emp_account):
        account = technician.emp_account
        if account in seen_technicians:
            issues.append(Issue("DUPLICATE_TECHNICIAN", emp_account=account))
            continue
        seen_technicians.add(account)

        pending: list[JobInput] = []
        running: list[JobInput] = []
        for job in sorted(technician.jobs, key=lambda item: item.job_id):
            job_count += 1
            if job.job_id in owner_by_job:
                issues.append(
                    Issue(
                        "DUPLICATE_JOB",
                        emp_account=account,
                        job_id=job.job_id,
                        detail=f"first_emp_account={owner_by_job[job.job_id]}",
                    )
                )
                continue
            owner_by_job[job.job_id] = account
            if job.state is JobState.IN_PROGRESS:
                in_progress_count += 1
                running.append(job)
                continue
            pending_count += 1
            sla_evaluable += job.due_at is not None
            if job.location is None:
                issues.append(
                    Issue(
                        "JOB_LOCATION_UNKNOWN",
                        emp_account=account,
                        job_id=job.job_id,
                        detail=f"address={job.address}" if job.address else None,
                    )
                )
            else:
                pending.append(job)

        if not pending and not running:
            continue
        start = _resolve_start(technician, running, request.planned_at, config, issues)
        if (
            pending
            and technician.shift_end is not None
            and request.planned_at >= technician.shift_end
        ):
            issues.append(
                Issue(
                    "TECHNICIAN_OFF_SHIFT",
                    emp_account=account,
                    detail=f"shift_end={technician.shift_end.isoformat()}",
                )
            )
        planned.append((technician, pending, start))

    # Mỗi KTV một nhóm điểm: [điểm xuất phát nếu biết] + các job chờ.
    groups = [
        ([start.location] if start.location is not None else [])
        + [job.location for job in pending]
        for _, pending, start in planned
    ]
    travel_clock = perf_counter()
    matrices = travel.matrices(groups)
    travel_ms = (perf_counter() - travel_clock) * 1000

    routes: list[TechnicianRoute] = []
    for (technician, pending, start), matrix in zip(planned, matrices, strict=True):
        if matrix.note:
            issues.append(
                Issue(
                    "ROAD_DISTANCE_FALLBACK",
                    emp_account=technician.emp_account,
                    detail=matrix.note,
                )
            )
        routes.append(_sequence(technician, pending, start, config, matrix, issues))

    stops = [stop for route in routes for stop in route.stops]
    on_time = sum(stop.late is False for stop in stops)
    summary = RouteSummary(
        technicians=len(routes),
        jobs=job_count,
        pending_jobs=pending_count,
        in_progress_jobs=in_progress_count,
        routed_stops=len(stops),
        stops_without_eta=sum(stop.eta is None for stop in stops),
        late_stops=sum(stop.late is True for stop in stops),
        completion_late_stops=sum(stop.completion_late is True for stop in stops),
        stops_after_shift_end=sum(stop.after_shift_end is True for stop in stops),
        sla_evaluable_jobs=sla_evaluable,
        on_time_stops=on_time,
        on_time_rate_percent=(
            round(on_time / sla_evaluable * 100, 2) if sla_evaluable else None
        ),
        total_km=round(sum(route.total_km for route in routes), 3),
        travel_ms=round(travel_ms, 3),
        planning_ms=round((perf_counter() - clock) * 1000, 3),
    )
    return RouteResponse(
        planned_at=request.planned_at,
        filter=request.filter,
        routes=tuple(routes),
        issues=tuple(issues),
        summary=summary,
    )


def _resolve_start(
    technician: TechnicianInput,
    running: list[JobInput],
    planned_at: datetime,
    config: RoutingConfig,
    issues: list[Issue],
) -> _Start:
    """Điểm và giờ xuất phát: job đang làm → vị trí mới nhất → không rõ."""

    account = technician.emp_account
    start_at = max(planned_at, technician.shift_start or planned_at)
    in_progress_job_id = None
    if running:
        # Job bắt đầu gần nhất là nơi KTV đang đứng.
        running.sort(
            key=lambda job: (
                job.started_at is not None,
                job.started_at or planned_at,
                job.job_id,
            )
        )
        current = running[-1]
        in_progress_job_id = current.job_id
        if len(running) > 1:
            issues.append(
                Issue(
                    "MULTIPLE_IN_PROGRESS",
                    emp_account=account,
                    job_id=current.job_id,
                    detail="in_progress=" + "|".join(job.job_id for job in running),
                )
            )
        service = timedelta(minutes=config.service_minutes(current, account))
        if current.started_at is None:
            issues.append(
                Issue(
                    "IN_PROGRESS_START_UNKNOWN",
                    emp_account=account,
                    job_id=current.job_id,
                    detail="giả định bắt đầu tại planned_at",
                )
            )
            start_at = max(start_at, planned_at + service)
        else:
            start_at = max(start_at, current.started_at + service)
        if current.location is not None:
            return _Start(
                start_at,
                current.location,
                StartSource.IN_PROGRESS_JOB,
                in_progress_job_id,
            )
        issues.append(
            Issue(
                "JOB_LOCATION_UNKNOWN",
                emp_account=account,
                job_id=current.job_id,
                detail="job đang làm không có tọa độ",
            )
        )

    fix = technician.last_location
    if fix is None:
        issues.append(Issue("TECHNICIAN_LOCATION_UNKNOWN", emp_account=account))
        return _Start(start_at, None, StartSource.UNKNOWN, in_progress_job_id)
    if planned_at - fix.recorded_at > timedelta(
        minutes=config.location_max_age_minutes
    ):
        issues.append(
            Issue(
                "STALE_TECHNICIAN_LOCATION",
                emp_account=account,
                detail=(
                    f"source={fix.source}; "
                    f"recorded_at={fix.recorded_at.isoformat()}"
                ),
            )
        )
        return _Start(
            start_at, fix.location, StartSource.STALE_LOCATION, in_progress_job_id
        )
    return _Start(
        start_at, fix.location, StartSource.LAST_LOCATION, in_progress_job_id
    )


def _after(value: datetime | None, limit: datetime | None) -> bool | None:
    if value is None or limit is None:
        return None
    return value > limit


def _rounded(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


class _Label:
    """Một tuyến dở dang: giờ xong job cuối, tổng chi phí từng tầng, nhãn trước và job vừa thêm."""

    __slots__ = ("finish", "costs", "parent", "job")

    def __init__(self, finish: float, costs: tuple[float, ...], parent: _Label | None, job: int | None) -> None:
        self.finish = finish
        self.costs = costs
        self.parent = parent
        self.job = job


class _Problem:
    """Bài xếp thứ tự của một KTV. Giờ tính bằng phút kể từ ``start.at``."""

    def __init__(
        self,
        technician: TechnicianInput,
        jobs: list[JobInput],
        start: _Start,
        config: RoutingConfig,
        matrix: TravelMatrix,
    ) -> None:
        self.jobs = jobs
        self.config = config
        self.rules = config.rules
        self.matrix = matrix
        self.start = start
        self.known_start = start.location is not None
        self.offset = 1 if self.known_start else 0

        def minutes(value: datetime | None) -> float | None:
            return None if value is None else (value - start.at).total_seconds() / 60

        self.service = [config.service_minutes(job, technician.emp_account) for job in jobs]
        self.due = [minutes(job.due_at) for job in jobs]
        self.opens = [minutes(job.appointment_start) for job in jobs]
        self.complete_by = [minutes(job.complete_by) for job in jobs]
        self.shift_end = minutes(technician.shift_end)
        self.weight = [self.rules.priority_weight(job.priority) for job in jobs]
        self.area = [job.area for job in jobs]
        self.area_mask: dict[str, int] = {}
        for index, area in enumerate(self.area):
            if area is not None:
                self.area_mask[area] = self.area_mask.get(area, 0) | 1 << index
        points = ([start.location] if self.known_start else []) + [job.location for job in jobs]
        self.straight = (
            [[distance_km(first, second) for second in points] for first in points]
            if config.transition is not None and self.known_start
            else None
        )
        # Mỗi tầng: các cặp (chỉ số rule, trọng số) khác 0. FINISH lấy thẳng từ giờ xong.
        self.tier_terms = [
            [(_RULE[code], weight) for code, weight in tier.items() if weight and code != "FINISH"]
            for tier in self.rules.tiers
        ]
        self.finish_tier, self.finish_weight = next(
            ((index, tier["FINISH"]) for index, tier in enumerate(self.rules.tiers) if tier.get("FINISH")),
            (None, 0.0),
        )
        self.root = _Label(0.0, (0.0,) * len(self.tier_terms), None, None)

    def leg(self, here: int | None, job: int, clock: float) -> tuple[float, float]:
        """(km, phút) từ ``here`` (None = điểm xuất phát) tới ``job``, rời đi lúc ``clock``."""

        if here is None and not self.known_start:
            return 0.0, 0.0
        origin = 0 if here is None else self.offset + here
        target = self.offset + job
        km = self.matrix.km[origin][target]
        if self.straight is not None:
            depart = self.start.at + timedelta(minutes=clock)
            return km, self.config.transition.leg_minutes(self.straight[origin][target], depart)
        return km, self.matrix.minutes[origin][target]

    def step(self, mask: int, here: int | None, job: int, clock: float) -> tuple[float, float, float, float, float, list[float]]:
        """Đi từ ``here`` tới ``job``: (km, phút đi, giờ tới, giờ check-in, giờ xong, chi phí từng rule)."""

        km, travel = self.leg(here, job, clock)
        arrive = clock + travel
        opens = self.opens[job]
        checkin = opens if opens is not None and arrive < opens else arrive
        done = checkin + self.service[job]
        cost = [0.0] * len(SOFT_RULE_CODES)
        due = self.due[job]
        if due is not None and checkin > due:
            cost[_LATE_CHECKIN] = self.weight[job]
            cost[_LATE_MINUTES] = checkin - due
        limit = self.complete_by[job]
        if limit is not None and done > limit:
            cost[_LATE_COMPLETION] = 1.0
        if self.shift_end is not None and done > self.shift_end:
            cost[_AFTER_SHIFT] = 1.0
        cost[_KM] = km
        cost[_TRAVEL_MINUTES] = travel
        area = self.area[job]
        if area is not None and here is not None and self.area[here] != area and mask & self.area_mask[area]:
            cost[_AREA_REENTRY] = 1.0
        cost[_PRIORITY_DELAY] = self.weight[job] * checkin / 60
        return km, travel, arrive, checkin, done, cost

    def extend(self, label: _Label, mask: int, here: int | None, job: int) -> _Label:
        *_, done, cost = self.step(mask, here, job, label.finish)
        costs = tuple(
            total + sum(weight * cost[rule] for rule, weight in terms)
            for total, terms in zip(label.costs, self.tier_terms)
        )
        return _Label(done, costs, label, job)

    def key(self, label: _Label) -> tuple[float, ...]:
        if self.finish_tier is None:
            return label.costs
        return tuple(
            total + self.finish_weight * label.finish if index == self.finish_tier else total
            for index, total in enumerate(label.costs)
        )

    def keep(self, bucket: list[_Label], label: _Label) -> bool:
        """Thêm nhãn nếu không nhãn nào cùng trạng thái hơn nó ở mọi mặt; True nếu phải cắt bớt."""

        finish, costs = label.finish, label.costs
        for old in bucket:
            if old.finish <= finish and all(a <= b for a, b in zip(old.costs, costs)):
                return False
        bucket[:] = [
            old for old in bucket if not (finish <= old.finish and all(a <= b for a, b in zip(costs, old.costs)))
        ]
        bucket.append(label)
        if len(bucket) > self.rules.max_labels_per_state:
            bucket.sort(key=self.key)
            del bucket[self.rules.max_labels_per_state :]
            return True
        return False

    @staticmethod
    def allowed(before: list[int] | None, mask: int, job: int) -> bool:
        return before is None or before[job] & mask == before[job]

    def solve(self, before: list[int] | None) -> tuple[list[int], SequenceSource]:
        """QHĐ; ``before[k]`` là tập job (bitmask) phải làm trước job k."""

        n = len(self.jobs)
        if n == 0:
            return [], SequenceSource.OPTIMAL
        if n > self.rules.max_exact_jobs:
            return self.heuristic(before), SequenceSource.HEURISTIC
        table: list[list[list[_Label]] | None] = [None] * (1 << n)
        capped = False

        def bucket(mask: int, job: int) -> list[_Label]:
            row = table[mask]
            if row is None:
                row = table[mask] = [[] for _ in range(n)]
            return row[job]

        for job in range(n):
            if self.allowed(before, 0, job):
                capped |= self.keep(bucket(1 << job, job), self.extend(self.root, 0, None, job))
        full = (1 << n) - 1
        for mask in range(1, full):
            row = table[mask]
            if row is None:
                continue
            for here, labels in enumerate(row):
                for label in labels:
                    for job in range(n):
                        bit = 1 << job
                        if mask & bit or not self.allowed(before, mask, job):
                            continue
                        capped |= self.keep(bucket(mask | bit, job), self.extend(label, mask, here, job))
        finals = [label for labels in (table[full] or ()) for label in labels]
        if not finals:
            return self.heuristic(before), SequenceSource.HEURISTIC
        best: _Label | None = min(finals, key=self.key)
        order: list[int] = []
        while best is not None and best.job is not None:
            order.append(best.job)
            best = best.parent
        order.reverse()
        return order, SequenceSource.APPROXIMATE if capped else SequenceSource.OPTIMAL

    def heuristic(self, before: list[int] | None) -> list[int]:
        """Tham lam: mỗi bước chọn job làm khóa tăng ít nhất; sau đó cải thiện bằng 2-opt."""

        n = len(self.jobs)
        label, mask, here, order = self.root, 0, None, []
        while len(order) < n:
            best: tuple[tuple, _Label, int] | None = None
            for job in range(n):
                if mask >> job & 1 or not self.allowed(before, mask, job):
                    continue
                candidate = self.extend(label, mask, here, job)
                rank = (self.key(candidate), self.jobs[job].job_id)
                if best is None or rank < best[0]:
                    best = (rank, candidate, job)
            _, label, job = best
            order.append(job)
            mask |= 1 << job
            here = job
        return self.improve(order, before)

    def evaluate(self, order: list[int]) -> tuple[float, ...]:
        label, mask, here = self.root, 0, None
        for job in order:
            label = self.extend(label, mask, here, job)
            mask |= 1 << job
            here = job
        return self.key(label)

    def valid(self, order: list[int], before: list[int] | None) -> bool:
        mask = 0
        for job in order:
            if not self.allowed(before, mask, job):
                return False
            mask |= 1 << job
        return True

    def improve(self, order: list[int], before: list[int] | None) -> list[int]:
        """2-opt: đảo một đoạn nếu khóa nhỏ hơn và vẫn giữ thứ tự bắt buộc; tối đa 2 vòng."""

        n = len(order)
        if n < 3 or n > _MAX_IMPROVE_JOBS:
            return order
        best = self.evaluate(order)
        for _ in range(2):
            improved = False
            for i in range(n - 1):
                for j in range(i + 1, n):
                    candidate = order[:i] + order[i : j + 1][::-1] + order[j + 1 :]
                    if not self.valid(candidate, before):
                        continue
                    candidate_key = self.evaluate(candidate)
                    if candidate_key < best:
                        order, best, improved = candidate, candidate_key, True
            if not improved:
                break
        return order

    def walk(self, order: list[int]) -> tuple[list[tuple[int, float, float, float, float, float]], dict[str, float]]:
        """Tính lại từng điểm dừng và tổng chi phí từng rule của thứ tự đã chọn."""

        steps = []
        totals = [0.0] * len(SOFT_RULE_CODES)
        mask, here, clock = 0, None, 0.0
        for job in order:
            km, travel, arrive, checkin, done, cost = self.step(mask, here, job, clock)
            steps.append((job, km, travel, arrive, checkin, done))
            totals = [total + value for total, value in zip(totals, cost)]
            mask |= 1 << job
            here = job
            clock = done
        totals[_FINISH] = clock
        return steps, dict(zip(SOFT_RULE_CODES, totals))


def _clearly_better(new: tuple[float, ...], old: tuple[float, ...], min_gain: float) -> bool:
    """Tuyến mới tốt hơn ở một tầng trên, hoặc bằng ở các tầng trên và giảm tầng cuối ≥ min_gain."""

    if new[:-1] != old[:-1]:
        return new[:-1] < old[:-1]
    return old[-1] - new[-1] >= min_gain


def _sequence(
    technician: TechnicianInput,
    jobs: list[JobInput],
    start: _Start,
    config: RoutingConfig,
    matrix: TravelMatrix,
    issues: list[Issue],
) -> TechnicianRoute:
    """Chọn thứ tự bằng QHĐ theo rule nghiệp vụ rồi tính ETA từng điểm.

    Chỉ số trong ``matrix``: 0 là điểm xuất phát (nếu biết), tiếp theo là ``jobs``.
    """

    rules = config.rules
    problem = _Problem(technician, jobs, start, config, matrix)
    position = {job.job_id: index for index, job in enumerate(jobs)}
    previous = [job_id for job_id in dict.fromkeys(technician.previous_sequence) if job_id in position]
    previous_route = PreviousRoute.NONE
    if previous and rules.previous_route_policy != "IGNORE":
        before = [0] * len(jobs)
        done = 0
        for job_id in previous:
            before[position[job_id]] = done
            done |= 1 << position[job_id]
        order, source = problem.solve(before)
        previous_route = PreviousRoute.KEPT
        if rules.previous_route_policy == "IF_BETTER":
            free_order, free_source = problem.solve(None)
            if free_order != order and _clearly_better(
                rules.objective_key(problem.walk(free_order)[1]),
                rules.objective_key(problem.walk(order)[1]),
                rules.reroute_min_gain,
            ):
                order, source, previous_route = free_order, free_source, PreviousRoute.CHANGED
    else:
        order, source = problem.solve(None)
    if source is not SequenceSource.OPTIMAL:
        issues.append(
            Issue(
                "SEQUENCE_NOT_OPTIMAL",
                emp_account=technician.emp_account,
                detail=(
                    f"{len(jobs)} job > max_exact_jobs={rules.max_exact_jobs}: dùng heuristic"
                    if source is SequenceSource.HEURISTIC
                    else f"vượt max_labels_per_state={rules.max_labels_per_state}: kết quả gần đúng"
                ),
            )
        )

    steps, totals = problem.walk(order)
    stops: list[PlannedStop] = []
    total_km = total_travel = total_service = 0.0
    for sequence, (index, km, travel, arrive, checkin, done) in enumerate(steps, 1):
        job = jobs[index]
        total_service += problem.service[index]
        if problem.known_start:
            eta = start.at + timedelta(minutes=arrive)
            checked_in = start.at + timedelta(minutes=checkin)
            finish = start.at + timedelta(minutes=done)
            leg_km, leg_minutes, wait = km, travel, checkin - arrive
            total_km += km
            total_travel += travel
        else:  # Không biết điểm xuất phát: có thứ tự và km giữa các job, không có giờ.
            eta = checked_in = finish = wait = None
            leg_km = None if sequence == 1 else km
            leg_minutes = None if sequence == 1 else travel
        stops.append(
            PlannedStop(
                sequence=sequence,
                job_id=job.job_id,
                location=job.location,
                leg_km=_rounded(leg_km, 3),
                leg_minutes=_rounded(leg_minutes, 2),
                eta=eta,
                wait_minutes=_rounded(wait, 2),
                finish_at=finish,
                due_at=job.due_at,
                late=_after(checked_in, job.due_at),
                completion_late=_after(finish, job.complete_by),
                after_shift_end=_after(finish, technician.shift_end),
            )
        )

    return TechnicianRoute(
        emp_account=technician.emp_account,
        start_at=start.at,
        start_location=start.location,
        start_source=start.source,
        in_progress_job_id=start.in_progress_job_id,
        travel_source=matrix.source,
        sequence_source=source,
        previous_route=previous_route,
        score={code: round(value, 3) for code, value in totals.items()} if problem.known_start else {},
        stops=tuple(stops),
        total_km=round(total_km, 3),
        total_travel_minutes=round(total_travel, 2),
        total_service_minutes=round(total_service, 2),
        finish_at=stops[-1].finish_at if stops else start.at,
    )
