"""Small contracts for sequential snapshot replay."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from ktv_optimizer.optimization.models import (
    OptimizationJob,
    OptimizationResult,
    TechnicianShift,
)


@dataclass(frozen=True, slots=True)
class Snapshot:
    snapshot_id: str  # ID ổn định của lần chụp dữ liệu.
    captured_at: datetime  # Thời điểm snapshot có hiệu lực.
    jobs: tuple[OptimizationJob, ...]  # Checklist events nhận ở lần chụp.
    technicians: tuple[TechnicianShift, ...]  # KTV đi ca tại thời điểm đó.
    technician_updates: tuple["TechnicianStatusUpdate", ...] = ()


class TechnicianWorkStatus(str, Enum):
    """Operational availability; roster membership is kept separately."""

    IDLE = "IDLE"
    RESERVED = "RESERVED"
    BUSY = "BUSY"
    UNAVAILABLE = "UNAVAILABLE"
    OFF_SHIFT = "OFF_SHIFT"


@dataclass(frozen=True, slots=True)
class TechnicianStatusUpdate:
    technician_id: str  # KTV được cập nhật trạng thái runtime.
    work_status: TechnicianWorkStatus | None = None  # None = tự suy ra.
    current_job_id: str | None = None  # Job đang làm/đã giữ nếu nguồn có.
    available_at: datetime | None = None  # Thời điểm dự kiến rảnh.


@dataclass(frozen=True, slots=True)
class StateRecord:
    snapshot_id: str  # Snapshot gần nhất chứa checklist.
    snapshot_time: datetime  # Thời điểm snapshot gần nhất.
    job: OptimizationJob  # Dữ liệu nguồn hiện tại của checklist.
    planned_technician: str | None  # KTV trong plan sau optimizer.
    assignment_source: str | None  # SYSTEM_FIXED hoặc OPTIMIZER_V0.
    route_order: int | None  # Thứ tự của job trên route hiện tại.
    updated_at: datetime  # Lần checkpoint record được cập nhật.


@dataclass(frozen=True, slots=True)
class OptimizerCheckpoint:
    records: tuple[StateRecord, ...] = ()  # Chỉ chứa checklist active.

    @property
    def by_job_id(self) -> dict[str, StateRecord]:
        return {record.job.checklist_id: record for record in self.records}


@dataclass(frozen=True, slots=True)
class TechnicianStateRecord:
    snapshot_id: str  # Snapshot gần nhất cập nhật KTV.
    snapshot_time: datetime  # Thời điểm trạng thái có hiệu lực.
    technician_id: str  # Account KTV trong roster.
    branch_name: str  # Chi nhánh đi ca.
    work_status: TechnicianWorkStatus  # IDLE/RESERVED/BUSY/...
    current_job_id: str | None  # Job hiện tại hoặc job kế tiếp đã giữ.
    available_at: datetime | None  # Dự kiến rảnh nếu tính được.
    planned_job_count: int  # Số job active đang gắn với KTV.
    latitude: float | None  # GPS hiện tại/đầu ca từ roster.
    longitude: float | None  # GPS hiện tại/đầu ca từ roster.
    updated_at: datetime  # Thời điểm ghi checkpoint.
    queued_job_count: int = 0  # Job chờ; không tính job đang thực thi.


@dataclass(frozen=True, slots=True)
class TechnicianCheckpoint:
    records: tuple[TechnicianStateRecord, ...] = ()

    @property
    def by_technician_id(self) -> dict[str, TechnicianStateRecord]:
        return {record.technician_id: record for record in self.records}


@dataclass(frozen=True, slots=True)
class SnapshotChange:
    checklist_id: str
    change_type: str  # ADDED, UPDATED hoặc COMPLETED.
    changed_fields: str  # Danh sách field thay đổi, ngăn bởi dấu phẩy.
    previous_status: str | None
    current_status: str | None


@dataclass(frozen=True, slots=True)
class SnapshotConflict:
    checklist_id: str | None
    code: str
    previous_value: str | None
    incoming_value: str | None
    resolution: str
    technician_id: str | None = None


@dataclass(frozen=True, slots=True)
class TechnicianJobQueueItem:
    snapshot_id: str  # Snapshot tạo queue hiện tại.
    snapshot_time: datetime  # Thời điểm queue có hiệu lực.
    technician_id: str  # KTV giữ job.
    checklist_id: str  # Job trong queue.
    queue_position: int | None  # 0=current; 1..N=route; None=chưa xếp tuyến.
    queue_status: str  # IN_PROGRESS/NEXT/QUEUED/PAUSED/QUEUED_REVIEW.
    checklist_status: str  # Status hiện hành sau khi apply event.
    assignment_source: str | None  # SYSTEM_FIXED/SYSTEM_ACTIVE/OPTIMIZER_V0.
    estimated_arrival: datetime | None = None  # ETA nếu job nằm trên route.
    estimated_finish: datetime | None = None  # Thời điểm dự kiến xong.


@dataclass(frozen=True, slots=True)
class SnapshotRunResult:
    snapshot: Snapshot
    changes: tuple[SnapshotChange, ...]
    conflicts: tuple[SnapshotConflict, ...]
    optimization: OptimizationResult
    checkpoint: OptimizerCheckpoint
    technician_checkpoint: TechnicianCheckpoint
    job_queue: tuple[TechnicianJobQueueItem, ...] = ()
    incoming_jobs: tuple[OptimizationJob, ...] = ()
    completed_jobs: tuple[OptimizationJob, ...] = ()
    planning_duration_seconds: float | None = None
