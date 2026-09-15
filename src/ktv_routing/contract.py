"""Hợp đồng dữ liệu giữa routing và các team khác.

Luồng: UI chọn điều kiện lọc → ``WorkloadQuery`` gửi team data → team data trả
``RouteRequest`` (job đã gán sẵn cho KTV) → routing trả ``RouteResponse``.

Module này chỉ có dataclass và phần đọc/ghi JSON, không có thuật toán. JSON
dùng đúng tên field của dataclass; field lạ bị từ chối để bắt lỗi gõ sai sớm.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Protocol


class ContractError(ValueError):
    """Dữ liệu không đúng hợp đồng; message ghi rõ đường dẫn field lỗi."""


class JobState(str, Enum):
    PENDING = "PENDING"  # KTV còn phải tới làm.
    IN_PROGRESS = "IN_PROGRESS"  # KTV đang làm tại hiện trường.


class StartSource(str, Enum):
    IN_PROGRESS_JOB = "IN_PROGRESS_JOB"  # Xuất phát tại job đang làm, khi làm xong.
    LAST_LOCATION = "LAST_LOCATION"  # Vị trí mới nhất, còn trong ngưỡng tuổi.
    STALE_LOCATION = "STALE_LOCATION"  # Vị trí quá cũ: vẫn dùng, kèm issue.
    UNKNOWN = "UNKNOWN"  # Không biết KTV ở đâu: tuyến không có ETA.


class SequenceSource(str, Enum):
    OPTIMAL = "OPTIMAL"  # QHĐ tìm được thứ tự tốt nhất theo rule nghiệp vụ.
    APPROXIMATE = "APPROXIMATE"  # QHĐ phải cắt bớt nhãn: gần tối ưu.
    HEURISTIC = "HEURISTIC"  # Quá nhiều job cho QHĐ: tham lam theo rule + 2-opt.


class PreviousRoute(str, Enum):
    NONE = "NONE"  # Request không có previous_sequence.
    KEPT = "KEPT"  # Giữ thứ tự tuyến cũ, chỉ chèn job mới.
    CHANGED = "CHANGED"  # Đổi thứ tự vì tuyến mới tốt hơn rõ.


@dataclass(frozen=True, slots=True)
class GeoPoint:
    lat: float
    lng: float


# ---------------------------------------------------------------- vào


@dataclass(frozen=True, slots=True)
class JobFilter:
    """Điều kiện lọc UI chọn, gửi kèm query cho team data.

    Danh sách rỗng = không lọc theo tiêu chí đó. Các tiêu chí kết hợp bằng AND,
    các giá trị trong cùng một danh sách kết hợp bằng OR.
    """

    case_types: tuple[str, ...] = ()  # VD ("MAINTENANCE",).
    branch_names: tuple[str, ...] = ()  # VD ("HNI_04",).
    emp_accounts: tuple[str, ...] = ()  # Chỉ lấy việc của các KTV này.


@dataclass(frozen=True, slots=True)
class WorkloadQuery:
    """UI → routing → team data: cần job của ai, tại thời điểm nào."""

    planned_at: datetime  # Thời điểm lập tuyến.
    filter: JobFilter = JobFilter()


@dataclass(frozen=True, slots=True)
class JobInput:
    job_id: str  # CHECKLIST_ID.
    state: JobState
    location: GeoPoint | None  # None nếu team data chưa có tọa độ.
    case_type: str | None = None  # Loại tác vụ; routing dùng để ước lượng thời gian làm.
    address: str | None = None  # Chỉ để hiển thị/truy vết.
    due_at: datetime | None = None  # Hạn check-in (mốc hẹn cuối B): check-in sau mốc này là trễ hẹn.
    priority: int | None = None  # Ưu tiên trong ngày: 1 gấp nhất … 4.
    started_at: datetime | None = None  # Lúc bắt đầu làm, với IN_PROGRESS.
    appointment_start: datetime | None = None  # Mốc hẹn đầu (A): tới sớm hơn thì chờ tới A.
    complete_by: datetime | None = None  # Hạn hoàn tất (trong ngày hẹn, ngày tạo phiếu, trong tháng).
    area: str | None = None  # Khu vực/cụm, để hạn chế quay lại khu vực đã rời.


@dataclass(frozen=True, slots=True)
class LocationFix:
    location: GeoPoint
    recorded_at: datetime
    source: str = "GPS"  # Nguồn vị trí: GPS, LAST_FINISHED_JOB...


@dataclass(frozen=True, slots=True)
class TechnicianInput:
    emp_account: str
    jobs: tuple[JobInput, ...]  # Job đã gán cho KTV, còn cần xử lý.
    last_location: LocationFix | None = None
    shift_start: datetime | None = None
    shift_end: datetime | None = None
    previous_sequence: tuple[str, ...] = ()  # Thứ tự routing gợi ý lần trước (job_id), để giữ tuyến ổn định.


@dataclass(frozen=True, slots=True)
class RouteRequest:
    """Team data → routing: danh sách job đã gán của từng KTV."""

    planned_at: datetime
    technicians: tuple[TechnicianInput, ...]
    filter: JobFilter = JobFilter()  # Điều kiện đã áp dụng, gửi lại để truy vết.


class WorkloadProvider(Protocol):
    """Phía team data: nhận query có điều kiện lọc, trả job đã gán cho KTV."""

    def fetch_workload(self, query: WorkloadQuery) -> RouteRequest: ...


# ---------------------------------------------------------------- ra


@dataclass(frozen=True, slots=True)
class Issue:
    code: str  # VD JOB_LOCATION_UNKNOWN, STALE_TECHNICIAN_LOCATION.
    emp_account: str | None = None
    job_id: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class PlannedStop:
    sequence: int  # Thứ tự làm, bắt đầu từ 1.
    job_id: str
    location: GeoPoint
    leg_km: float | None  # Km từ điểm trước (theo travel_source); None nếu không rõ điểm đầu.
    leg_minutes: float | None  # Phút từ lúc xong điểm trước tới lúc tới điểm này.
    eta: datetime | None  # Giờ tới dự kiến.
    wait_minutes: float | None  # Chờ tới mốc hẹn đầu; check-in = eta + wait_minutes.
    finish_at: datetime | None  # Giờ làm xong = check-in + thời gian làm.
    due_at: datetime | None  # Hạn check-in.
    late: bool | None  # Check-in sau due_at (trễ hẹn); None nếu thiếu giờ hoặc hạn.
    completion_late: bool | None  # Làm xong sau complete_by; None nếu thiếu giờ hoặc hạn.
    after_shift_end: bool | None


@dataclass(frozen=True, slots=True)
class TechnicianRoute:
    emp_account: str
    start_at: datetime  # Lúc KTV rảnh để đi điểm đầu tiên.
    start_location: GeoPoint | None
    start_source: StartSource
    in_progress_job_id: str | None
    travel_source: str  # OSRM (km/phút đường bộ) hoặc HAVERSINE (chim bay).
    sequence_source: SequenceSource  # Thứ tự do QHĐ tối ưu, gần đúng hay heuristic.
    previous_route: PreviousRoute  # Có giữ thứ tự tuyến cũ không.
    score: dict[str, float]  # Chi phí từng rule mềm của thứ tự đã chọn (xem rules.py); rỗng nếu không rõ điểm đầu.
    stops: tuple[PlannedStop, ...]
    total_km: float
    total_travel_minutes: float
    total_service_minutes: float
    finish_at: datetime | None  # Lúc xong điểm cuối.


@dataclass(frozen=True, slots=True)
class RouteSummary:
    technicians: int  # Số tuyến trả về.
    jobs: int  # Tổng job nhận được.
    pending_jobs: int
    in_progress_jobs: int
    routed_stops: int
    stops_without_eta: int
    late_stops: int  # Điểm check-in trễ hẹn.
    completion_late_stops: int  # Điểm làm xong sau hạn hoàn tất.
    stops_after_shift_end: int
    sla_evaluable_jobs: int  # Job PENDING có due_at, kể cả job không xếp được.
    on_time_stops: int
    on_time_rate_percent: float | None
    total_km: float
    travel_ms: float  # Thời gian lấy ma trận km/phút (gọi OSRM nếu dùng).
    planning_ms: float  # Tổng thời gian xếp tuyến, đã gồm travel_ms.


@dataclass(frozen=True, slots=True)
class RouteResponse:
    planned_at: datetime
    filter: JobFilter
    routes: tuple[TechnicianRoute, ...]
    issues: tuple[Issue, ...]
    summary: RouteSummary


# ---------------------------------------------------------------- JSON


def to_json_dict(value: Any) -> Any:
    """Dataclass hợp đồng → dict/list thuần để ``json.dumps``."""

    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: to_json_dict(getattr(value, item.name))
            for item in fields(value)
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (tuple, list)):
        return [to_json_dict(item) for item in value]
    return value


def _object(value: Any, path: str, allowed: set[str]) -> dict:
    if not isinstance(value, dict):
        raise ContractError(f"{path}: cần object JSON")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ContractError(
            f"{path}: field không có trong hợp đồng: {', '.join(unknown)}"
        )
    return value


def _list(value: Any, path: str) -> list:
    if not isinstance(value, list):
        raise ContractError(f"{path}: cần mảng JSON")
    return value


def _text(value: Any, path: str, *, required: bool = False) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ContractError(f"{path}: bắt buộc")
        return None
    if not isinstance(value, str):
        raise ContractError(f"{path}: cần chuỗi, nhận {value!r}")
    return value.strip()


def _datetime(
    value: Any, path: str, aware: bool | None, *, required: bool = False
) -> datetime | None:
    if value is None:
        if required:
            raise ContractError(f"{path}: bắt buộc")
        return None
    if not isinstance(value, str):
        raise ContractError(f"{path}: cần chuỗi ISO datetime, nhận {value!r}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ContractError(f"{path}: không phải ISO datetime: {value!r}") from None
    if aware is not None and (parsed.tzinfo is not None) != aware:
        raise ContractError(
            f"{path}: có/không có múi giờ phải thống nhất với planned_at"
        )
    return parsed


def _number(value: Any, path: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{path}: cần số, nhận {value!r}")
    if not low <= value <= high:
        raise ContractError(f"{path}: ngoài khoảng [{low}, {high}]: {value}")
    return float(value)


def _point(value: Any, path: str) -> GeoPoint | None:
    if value is None:
        return None
    data = _object(value, path, {"lat", "lng"})
    return GeoPoint(
        lat=_number(data.get("lat"), f"{path}.lat", -90, 90),
        lng=_number(data.get("lng"), f"{path}.lng", -180, 180),
    )


def _filter(value: Any, path: str) -> JobFilter:
    if value is None:
        return JobFilter()
    keys = ("case_types", "branch_names", "emp_accounts")
    data = _object(value, path, set(keys))
    return JobFilter(
        **{
            key: tuple(
                _text(item, f"{path}.{key}[{index}]", required=True)
                for index, item in enumerate(
                    _list(data.get(key, []), f"{path}.{key}")
                )
            )
            for key in keys
        }
    )


def _job(value: Any, path: str, aware: bool) -> JobInput:
    data = _object(
        value,
        path,
        {
            "job_id",
            "state",
            "location",
            "case_type",
            "address",
            "due_at",
            "priority",
            "started_at",
            "appointment_start",
            "complete_by",
            "area",
        },
    )
    try:
        state = JobState(data.get("state"))
    except (ValueError, TypeError):
        allowed = ", ".join(item.value for item in JobState)
        raise ContractError(
            f"{path}.state: cần một trong {allowed}, nhận {data.get('state')!r}"
        ) from None
    priority = data.get("priority")
    if priority is not None and (
        isinstance(priority, bool) or not isinstance(priority, int)
    ):
        raise ContractError(f"{path}.priority: cần số nguyên, nhận {priority!r}")
    due_at = _datetime(data.get("due_at"), f"{path}.due_at", aware)
    appointment_start = _datetime(data.get("appointment_start"), f"{path}.appointment_start", aware)
    if due_at is not None and appointment_start is not None and due_at < appointment_start:
        raise ContractError(f"{path}: due_at trước appointment_start")
    return JobInput(
        job_id=_text(data.get("job_id"), f"{path}.job_id", required=True),
        state=state,
        location=_point(data.get("location"), f"{path}.location"),
        case_type=_text(data.get("case_type"), f"{path}.case_type"),
        address=_text(data.get("address"), f"{path}.address"),
        due_at=due_at,
        priority=priority,
        started_at=_datetime(data.get("started_at"), f"{path}.started_at", aware),
        appointment_start=appointment_start,
        complete_by=_datetime(data.get("complete_by"), f"{path}.complete_by", aware),
        area=_text(data.get("area"), f"{path}.area"),
    )


def _location_fix(value: Any, path: str, aware: bool) -> LocationFix | None:
    if value is None:
        return None
    data = _object(value, path, {"location", "recorded_at", "source"})
    location = _point(data.get("location"), f"{path}.location")
    if location is None:
        raise ContractError(f"{path}.location: bắt buộc")
    return LocationFix(
        location=location,
        recorded_at=_datetime(
            data.get("recorded_at"), f"{path}.recorded_at", aware, required=True
        ),
        source=_text(data.get("source"), f"{path}.source") or "GPS",
    )


def _technician(value: Any, path: str, aware: bool) -> TechnicianInput:
    data = _object(
        value,
        path,
        {"emp_account", "jobs", "last_location", "shift_start", "shift_end", "previous_sequence"},
    )
    shift_start = _datetime(data.get("shift_start"), f"{path}.shift_start", aware)
    shift_end = _datetime(data.get("shift_end"), f"{path}.shift_end", aware)
    if shift_start is not None and shift_end is not None and shift_end < shift_start:
        raise ContractError(f"{path}: shift_end trước shift_start")
    return TechnicianInput(
        emp_account=_text(
            data.get("emp_account"), f"{path}.emp_account", required=True
        ),
        jobs=tuple(
            _job(item, f"{path}.jobs[{index}]", aware)
            for index, item in enumerate(_list(data.get("jobs"), f"{path}.jobs"))
        ),
        last_location=_location_fix(
            data.get("last_location"), f"{path}.last_location", aware
        ),
        shift_start=shift_start,
        shift_end=shift_end,
        previous_sequence=tuple(
            _text(item, f"{path}.previous_sequence[{index}]", required=True)
            for index, item in enumerate(
                _list(data.get("previous_sequence", []), f"{path}.previous_sequence")
            )
        ),
    )


def query_from_dict(data: Any) -> WorkloadQuery:
    data = _object(data, "query", {"planned_at", "filter"})
    return WorkloadQuery(
        planned_at=_datetime(
            data.get("planned_at"), "query.planned_at", None, required=True
        ),
        filter=_filter(data.get("filter"), "query.filter"),
    )


def request_from_dict(data: Any) -> RouteRequest:
    data = _object(data, "request", {"planned_at", "technicians", "filter"})
    planned_at = _datetime(
        data.get("planned_at"), "request.planned_at", None, required=True
    )
    aware = planned_at.tzinfo is not None
    technicians = _list(data.get("technicians"), "request.technicians")
    return RouteRequest(
        planned_at=planned_at,
        technicians=tuple(
            _technician(item, f"request.technicians[{index}]", aware)
            for index, item in enumerate(technicians)
        ),
        filter=_filter(data.get("filter"), "request.filter"),
    )
