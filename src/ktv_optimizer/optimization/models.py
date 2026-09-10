"""Input/output contracts shared by V0 optimization modules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from ktv_optimizer.domain import GeoPoint


class CompatibilityMode(str, Enum):
    """Cách tạo cluster và kiểm tra compatibility cho Job–KTV."""

    TASK_LOCATION = "task_location"
    TASK = "task"
    LOCATION = "location"


@dataclass(frozen=True, slots=True)
class OptimizationJob:
    checklist_id: str  # ID checklist cần gán hoặc routing.
    status: str  # Chưa phân công hoặc Đã phân công.
    branch_name: str  # Chi nhánh sở hữu checklist.
    task_type: str  # Loại tác vụ, hiện lấy từ CASE_TYPE.
    address: str | None  # OBJ_LOCATION gốc của khách hàng.
    location: GeoPoint | None  # GPS đại diện suy ra từ phường/xã.
    ward_code: str | None = None  # Mã phường/xã match từ GeoJSON.
    ward_name: str | None = None  # Tên phường/xã match từ GeoJSON.
    province_name: str | None = None  # Tỉnh/thành của boundary đã match.
    location_confidence: float | None = None  # Độ tin cậy của address match.
    location_method: str | None = None  # exact_substring hoặc fuzzy_segment.
    assigned_technician: str | None = None  # KTV hệ thống đã gán, nếu có.
    priority: int = 3  # 1 là ưu tiên cao nhất.
    service_minutes: float = 60.0  # Thời lượng xử lý dự kiến.
    created_at: datetime | None = None  # Thời điểm checklist phát sinh.
    due_at: datetime | None = None  # Hạn SLA dự kiến nếu suy ra được.
    finished_at: datetime | None = None  # FINISH_DATE thực tế từ event nguồn.
    on_time_flag: str | None = None  # YES/NO/NA/INPROCESS từ hệ thống nguồn.


@dataclass(frozen=True, slots=True)
class TechnicianShift:
    technician_id: str  # Account KTV.
    branch_name: str  # Chi nhánh KTV đi ca trong ngày.
    current_location: GeoPoint | None  # GPS hiện tại hoặc điểm đầu ca.
    shift_start: datetime | None = None  # Thời điểm bắt đầu ca.
    shift_end: datetime | None = None  # Thời điểm kết thúc ca.
    supported_task_types: frozenset[str] = frozenset()  # Rỗng = hỗ trợ mọi task.
    existing_workload_minutes: float = 0.0  # Tải đã có trước lần tối ưu này.


@dataclass(frozen=True, slots=True)
class CandidateEdge:
    job_id: str  # Checklist trên cạnh candidate.
    technician_id: str  # KTV trên cạnh candidate.
    cluster_key: str  # Cụm task/location của checklist.
    branch_ok: bool  # Job và KTV có cùng chi nhánh.
    task_ok: bool  # KTV có hỗ trợ CASE_TYPE của job.
    location_ok: bool  # Job nằm trong bán kính cho phép.
    compatible: bool  # Kết quả hard filter theo mode.
    distance_km: float | None  # Khoảng cách chim bay từ KTV đến job.
    reject_reason: str | None = None  # Lý do loại cạnh.


@dataclass(frozen=True, slots=True)
class AssignmentDecision:
    job_id: str  # Checklist được quyết định.
    technician_id: str  # KTV nhận checklist.
    source: str  # SYSTEM_FIXED hoặc OPTIMIZER_V0.
    cluster_key: str  # Cụm dùng trong lần gán.
    distance_cost: float  # Thành phần cost do khoảng cách.
    load_cost: float  # Thành phần cost do tải hiện tại.
    cluster_cost: float  # Bonus/penalty do gom cùng cluster.
    assignment_cost: float  # Tổng cost tại thời điểm chọn.
    distance_km: float | None  # Khoảng cách KTV–Job lúc đánh giá.
    stability_cost: float = 0.0  # Âm nếu giữ KTV từ snapshot trước.


@dataclass(frozen=True, slots=True)
class UnassignedDecision:
    job_id: str  # Checklist chưa gán được.
    reason: str  # Không có KTV/candidate/location/task phù hợp.


@dataclass(frozen=True, slots=True)
class RouteStop:
    sequence: int  # Thứ tự job trong tuyến, bắt đầu từ 1.
    job_id: str  # Checklist tại điểm dừng.
    location: GeoPoint | None  # GPS điểm dừng.
    leg_distance_km: float | None  # Khoảng cách từ điểm trước.
    estimated_arrival: datetime | None  # ETA theo tốc độ V0.
    estimated_finish: datetime | None  # ETA cộng thời lượng xử lý.


@dataclass(frozen=True, slots=True)
class TechnicianRoute:
    technician_id: str  # KTV sở hữu tuyến.
    stops: tuple[RouteStop, ...]  # Danh sách điểm theo thứ tự.
    total_distance_km: float  # Tổng khoảng cách chim bay.
    total_travel_minutes: float  # Tổng thời gian di chuyển ước tính.
    total_service_minutes: float  # Tổng thời lượng xử lý.


@dataclass(frozen=True, slots=True)
class OptimizationResult:
    mode: CompatibilityMode  # Option compatibility đã sử dụng.
    candidate_edges: tuple[CandidateEdge, ...]  # Compatibility graph.
    assignments: tuple[AssignmentDecision, ...]  # Fixed + gán mới.
    unassigned: tuple[UnassignedDecision, ...]  # Job không gán được.
    routes: tuple[TechnicianRoute, ...]  # Tuyến theo từng KTV.
