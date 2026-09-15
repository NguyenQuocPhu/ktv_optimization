"""Đóng vai team data realtime: đọc luồng sự kiện, giữ trạng thái hiện tại, trả ``RouteRequest``.

Chỉ để chạy thử routing khi chưa có hệ thống thật; không deploy. Luồng sự kiện do
``events.py`` sinh từ export QOS. Bảng loại tác vụ lấy từ file nghiệp vụ; cách suy ra
hạn check-in và hạn hoàn tất khi export thiếu giờ hẹn là giả định tạm (docs/BUSINESS_RULES.md).

Trạng thái tại T chỉ dựng từ sự kiện có ``at ≤ T``, nên không biết trước status cuối:
- Job mở từ JOB_CREATED tới JOB_CLOSED.
- Job IN_PROGRESS khi đã CHECKIN mà chưa CHECKOUT. KTV không ở hai nơi cùng lúc:
  check-in job khác thì lượt trước (thiếu checkout) coi như kết thúc.
- Vị trí KTV là tọa độ mới nhất từ GPS, CHECKIN hoặc CHECKOUT.
- ``advance_to(..., collect=True)`` báo mọi thay đổi của job đang mở, để frontend biết
  KTV nào cần xếp lại. GPS chỉ cập nhật vị trí nên không báo.

Như một consumer thật: đọc tuần tự từ offset, lưu snapshot đầu mỗi ngày để tua lùi nhanh.
Chỉ dùng thư viện chuẩn.
"""

from __future__ import annotations

import json
from bisect import bisect_right
from dataclasses import dataclass, field, replace
from datetime import datetime, time, timedelta
from pathlib import Path

from ktv_routing.contract import (
    GeoPoint,
    JobFilter,
    JobInput,
    JobState,
    LocationFix,
    RouteRequest,
    TechnicianInput,
    WorkloadQuery,
)

EVENT_FORMAT = "ktv-events/1"
EVENT_TYPES = ("JOB_CREATED", "CHECKIN", "CHECKOUT", "JOB_CLOSED", "GPS")  # Cũng là thứ tự khi trùng giờ.

CHECKIN_BEFORE_END = "CHECKIN_BEFORE_END"  # Check-in trước mốc hẹn cuối (B).
DONE_SAME_APPOINTMENT_DAY = "DONE_SAME_APPOINTMENT_DAY"  # Hoàn tất trong ngày hẹn.
DONE_SAME_CREATED_DAY = "DONE_SAME_CREATED_DAY"  # Hoàn tất trong ngày tạo phiếu.
DONE_WITHIN_MONTH = "DONE_WITHIN_MONTH"  # Hoàn tất trong tháng.


@dataclass(frozen=True, slots=True)
class TaskType:
    group: str  # Nhóm tác vụ.
    window_minutes: int | None  # SLA mốc hẹn A→B.
    on_time: str  # Yêu cầu đúng hẹn.
    priority: int  # Ưu tiên trong ngày, 1 = gấp nhất.


# Sheet "#Bảng KPI, SLA Tác vụ" của file nghiệp vụ. Khóa là CASE_TYPE viết hoa.
TASK_TYPES: dict[str, TaskType] = {
    "TRIỂN KHAI MỚI (NET, COMBO..)": TaskType("Triển khai", 120, CHECKIN_BEFORE_END, 3),
    "BOX, CAM ONLY": TaskType("Triển khai", 120, CHECKIN_BEFORE_END, 3),
    "SWAP": TaskType("Triển khai", 60, CHECKIN_BEFORE_END, 3),
    "GSAFE": TaskType("Triển khai", None, DONE_SAME_APPOINTMENT_DAY, 3),
    "GIAO THIẾT BỊ CAM": TaskType("Triển khai", 60, DONE_SAME_APPOINTMENT_DAY, 3),
    "VẬT LÝ": TaskType("Bảo trì", 60, CHECKIN_BEFORE_END, 1),
    "LOGIC": TaskType("Bảo trì", 60, CHECKIN_BEFORE_END, 2),
    "THU HỒI THIẾT BỊ": TaskType("Thu hồi", None, DONE_WITHIN_MONTH, 4),
    "THU BILL TRẢ TRƯỚC, SAU": TaskType("Thu bill", None, DONE_WITHIN_MONTH, 4),
    "PHIẾU ONSITE": TaskType("CSKH chủ động", None, DONE_WITHIN_MONTH, 2),
    "NGƯNG KẾT NỐI 4H": TaskType("CSKH chủ động", None, DONE_SAME_CREATED_DAY, 2),
    "MẠNG CHẬP CHỜN, SUY HAO CAO": TaskType("CSKH chủ động", None, DONE_SAME_CREATED_DAY, 4),
    # Export QOS chỉ có MAINTENANCE, không rõ Vật lý hay Logic, không có giờ khách hẹn.
    # [GIẢ ĐỊNH] ưu tiên 2; hạn check-in = CREATE_DATE + 24 giờ (mốc khớp FLAG_ON_TIME 90%).
    "MAINTENANCE": TaskType("Bảo trì", 24 * 60, CHECKIN_BEFORE_END, 2),
}
DEFAULT_PRIORITY = 3  # CASE_TYPE không có trong bảng: không hạn, ưu tiên 3. [GIẢ ĐỊNH]


def job_deadlines(case_type: str | None, created_at: datetime) -> tuple[datetime | None, datetime | None, int]:
    """(hạn check-in, hạn hoàn tất, ưu tiên) theo loại tác vụ.

    Export không có giờ khách hẹn (mốc A) nên tạm lấy A = giờ tạo phiếu và ngày hẹn = ngày
    tạo phiếu. [GIẢ ĐỊNH]
    """

    task = TASK_TYPES.get((case_type or "").strip().upper())
    if task is None:
        return None, None, DEFAULT_PRIORITY
    if task.on_time == CHECKIN_BEFORE_END:
        return created_at + timedelta(minutes=task.window_minutes), None, task.priority
    if task.on_time in (DONE_SAME_APPOINTMENT_DAY, DONE_SAME_CREATED_DAY):
        return None, datetime.combine(created_at.date(), time(23, 59, 59)), task.priority
    next_month = (created_at.replace(day=28) + timedelta(days=4)).replace(day=1)
    return None, datetime.combine(next_month.date(), time()) - timedelta(seconds=1), task.priority


@dataclass(frozen=True, slots=True)
class BuildStats:
    open_onsite_jobs: int  # Job còn mở tại T, trước khi lọc.
    matched_jobs: int  # Còn lại sau điều kiện lọc.
    missing_technician: int  # Không có EMP_ACCOUNT nên không đưa vào request.
    without_location: int  # Không geocode được địa chỉ.
    in_progress_jobs: int
    technicians: int
    technicians_with_location: int
    events_applied: int  # Số sự kiện đã đọc tới T.


@dataclass(frozen=True, slots=True)
class Change:
    """Một thay đổi của job đang mở, kèm thông tin job để frontend lọc theo điều kiện.

    ``type`` là loại sự kiện gốc, hoặc VISIT_ENDED khi KTV check-in job khác làm kết
    thúc lượt đang mở (thiếu checkout) của job này.
    """

    at: datetime
    type: str
    job_id: str
    emp_account: str | None
    branch_name: str | None
    case_type: str | None


@dataclass(frozen=True, slots=True)
class _Job:
    job_id: str
    emp_account: str | None
    branch_name: str | None
    case_type: str | None
    address: str | None
    location: GeoPoint | None
    area: str | None  # Phường/xã khi geocode được.
    created_at: datetime
    started_at: datetime | None = None  # Giờ check-in của lượt đang mở.


@dataclass(slots=True)
class _State:
    clock: datetime = datetime.min  # Đã áp mọi sự kiện có at ≤ clock.
    offset: int = 0  # Vị trí byte của sự kiện kế tiếp chưa đọc.
    applied: int = 0
    jobs: dict[str, _Job] = field(default_factory=dict)  # Chỉ job còn mở.
    visiting: dict[str, str] = field(default_factory=dict)  # KTV → job đang check-in.
    locations: dict[str, LocationFix] = field(default_factory=dict)

    def copy(self) -> _State:
        # _Job và LocationFix bất biến nên chép dict là đủ.
        return _State(
            self.clock, self.offset, self.applied, dict(self.jobs), dict(self.visiting), dict(self.locations)
        )


def _point(value: dict | None) -> GeoPoint | None:
    return None if value is None else GeoPoint(float(value["lat"]), float(value["lng"]))


def _report(changes: list[Change] | None, at: datetime, kind: str, job: _Job) -> None:
    if changes is not None:
        changes.append(Change(at, kind, job.job_id, job.emp_account, job.branch_name, job.case_type))


def _wanted(job_filter: JobFilter) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    return (
        frozenset(value.strip().upper() for value in job_filter.case_types),
        frozenset(value.strip() for value in job_filter.branch_names),
        frozenset(value.strip() for value in job_filter.emp_accounts),
    )


def _matches(wanted, case_type: str | None, branch_name: str | None, emp_account: str | None) -> bool:
    case_types, branch_names, emp_accounts = wanted
    return (
        (not case_types or case_type in case_types)
        and (not branch_names or branch_name in branch_names)
        and (not emp_accounts or emp_account in emp_accounts)
    )


def filter_changes(changes: list[Change], job_filter: JobFilter) -> list[Change]:
    wanted = _wanted(job_filter)
    return [
        change for change in changes if _matches(wanted, change.case_type, change.branch_name, change.emp_account)
    ]


class EventWorkloadProvider:
    """``WorkloadProvider`` đọc luồng sự kiện: tua tới ``planned_at`` rồi dựng ``RouteRequest``."""

    def __init__(self, events_path: str | Path, *, shift: tuple[time, time] | None = None) -> None:
        self.path = Path(events_path)
        self.shift = shift  # Ca làm áp cho mọi KTV; None = không biết ca.
        self._file = self.path.open("rb")
        try:
            header = json.loads(self._file.readline() or b"null")
        except ValueError:
            header = None
        if not isinstance(header, dict) or header.get("format") != EVENT_FORMAT:
            self._file.close()
            raise ValueError(
                f"{self.path}: không phải luồng sự kiện {EVENT_FORMAT} (tạo bằng python -m ktv_simulator.events)"
            )
        self.info = header
        self._position = self._file.tell()
        self._start = _State(offset=self._position)
        self._state = self._start.copy()
        self._checkpoints: list[_State] = []  # Snapshot đầu mỗi ngày, sắp theo clock.
        self._peeked: tuple[int, tuple[dict, datetime, int]] | None = None

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> EventWorkloadProvider:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def clock(self) -> datetime:
        return self._state.clock

    def advance_to(self, at: datetime, *, collect: bool = False) -> list[Change]:
        """Áp mọi sự kiện có ``at ≤ T``; T lùi thì khôi phục snapshot gần nhất rồi đọc tiếp.

        ``collect=True`` trả về các sự kiện job vừa áp (GPS không tính).
        """

        if at.tzinfo is not None:
            raise ValueError("Luồng sự kiện dùng giờ địa phương không múi giờ: planned_at phải naive")
        if at < self._state.clock:
            index = bisect_right([snapshot.clock for snapshot in self._checkpoints], at)
            self._state = (self._checkpoints[index - 1] if index else self._start).copy()
        state = self._state
        changes: list[Change] = []
        while (item := self._read(state.offset)) is not None:
            event, event_at, next_offset = item
            if event_at > at:
                break
            midnight = datetime.combine(event_at.date(), time()) - timedelta(microseconds=1)
            if state.applied and midnight >= state.clock and (
                not self._checkpoints or self._checkpoints[-1].clock < midnight
            ):
                snapshot = state.copy()
                snapshot.clock = midnight
                self._checkpoints.append(snapshot)
            self._apply(state, event, event_at, changes if collect else None)
            state.offset = next_offset
            state.applied += 1
        state.clock = at
        return changes

    def _read(self, offset: int) -> tuple[dict, datetime, int] | None:
        if self._peeked is not None and self._peeked[0] == offset:
            return self._peeked[1]
        if self._position != offset:
            self._file.seek(offset)
        line = self._file.readline()
        self._position = offset + len(line)
        if not line:
            return None
        event = json.loads(line)
        item = (event, datetime.fromisoformat(event["at"]), self._position)
        self._peeked = (offset, item)
        return item

    def _apply(self, state: _State, event: dict, at: datetime, changes: list[Change] | None) -> None:
        kind = event.get("type")
        emp_account = event.get("emp_account")
        if kind == "GPS":
            point = _point(event.get("location"))
            if emp_account and point is not None:
                state.locations[emp_account] = LocationFix(point, at, "GPS")
            return

        job_id = event.get("job_id")
        jobs = state.jobs
        job = jobs.get(job_id)
        if kind == "JOB_CREATED":
            job = _Job(
                job_id=job_id,
                emp_account=emp_account,
                branch_name=event.get("branch_name"),
                case_type=event.get("case_type"),
                address=event.get("address"),
                location=_point(event.get("location")),
                area=event.get("area"),
                created_at=at,
            )
            jobs[job_id] = job
        elif kind in ("CHECKIN", "CHECKOUT"):
            previous = state.visiting.pop(emp_account, None) if emp_account else None
            if previous not in (None, job_id):
                if kind == "CHECKOUT":
                    state.visiting[emp_account] = previous  # Checkout job khác: lượt đang mở vẫn giữ.
                elif previous in jobs:
                    # Lượt trước thiếu checkout kết thúc. Báo riêng vì job này có thể khớp bộ
                    # lọc trong khi job vừa check-in thì không.
                    ended = jobs[previous]
                    jobs[previous] = replace(ended, started_at=None)
                    _report(changes, at, "VISIT_ENDED", ended)
            if job is not None:
                jobs[job_id] = replace(job, started_at=at if kind == "CHECKIN" else None)
                if kind == "CHECKIN" and emp_account:
                    state.visiting[emp_account] = job_id
            point = _point(event.get("location")) or (job.location if job is not None else None)
            if emp_account and point is not None:
                state.locations[emp_account] = LocationFix(point, at, kind)
        elif kind == "JOB_CLOSED":
            jobs.pop(job_id, None)
            if emp_account and state.visiting.get(emp_account) == job_id:
                del state.visiting[emp_account]
        else:
            raise ValueError(f"{self.path}: loại sự kiện không hợp lệ {kind!r} tại byte {state.offset}")
        if job is not None:  # None: job ngoài luồng (đã đóng hoặc chưa tạo), không job mở nào đổi.
            _report(changes, at, kind, job)

    def fetch_workload(self, query: WorkloadQuery) -> RouteRequest:
        return self.build(query)[0]

    def build(self, query: WorkloadQuery) -> tuple[RouteRequest, BuildStats]:
        self.advance_to(query.planned_at)
        state = self._state
        wanted = _wanted(query.filter)
        open_onsite = matched = missing_technician = without_location = in_progress = 0
        by_technician: dict[str, list[JobInput]] = {}
        for job in state.jobs.values():
            open_onsite += 1
            if not _matches(wanted, job.case_type, job.branch_name, job.emp_account):
                continue
            matched += 1
            if job.emp_account is None:
                missing_technician += 1
                continue
            without_location += job.location is None
            in_progress += job.started_at is not None
            due_at, complete_by, priority = job_deadlines(job.case_type, job.created_at)
            by_technician.setdefault(job.emp_account, []).append(
                JobInput(
                    job_id=job.job_id,
                    state=JobState.IN_PROGRESS if job.started_at is not None else JobState.PENDING,
                    location=job.location,
                    case_type=job.case_type,
                    address=job.address,
                    due_at=due_at,
                    priority=priority,
                    started_at=job.started_at,
                    complete_by=complete_by,
                    area=job.area,
                )
            )

        shift_start = shift_end = None
        if self.shift is not None:
            day = query.planned_at.date()
            shift_start = datetime.combine(day, self.shift[0])
            shift_end = datetime.combine(day, self.shift[1])
        technicians = tuple(
            TechnicianInput(
                emp_account=account,
                jobs=tuple(sorted(items, key=lambda item: item.job_id)),
                last_location=state.locations.get(account),
                shift_start=shift_start,
                shift_end=shift_end,
            )
            for account, items in sorted(by_technician.items())
        )
        stats = BuildStats(
            open_onsite_jobs=open_onsite,
            matched_jobs=matched,
            missing_technician=missing_technician,
            without_location=without_location,
            in_progress_jobs=in_progress,
            technicians=len(technicians),
            technicians_with_location=sum(item.last_location is not None for item in technicians),
            events_applied=state.applied,
        )
        return RouteRequest(planned_at=query.planned_at, technicians=technicians, filter=query.filter), stats
