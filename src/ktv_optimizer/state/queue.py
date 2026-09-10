"""Build an explicit current/queued-job view from canonical state."""

from __future__ import annotations

from ktv_optimizer.optimization.models import OptimizationResult

from .availability import BUSY_JOB_STATUSES
from .snapshot import (
    OptimizerCheckpoint,
    Snapshot,
    TechnicianCheckpoint,
    TechnicianJobQueueItem,
    TechnicianWorkStatus,
)


def build_technician_job_queue(
    snapshot: Snapshot,
    checkpoint: OptimizerCheckpoint,
    technicians: TechnicianCheckpoint,
    optimization: OptimizationResult,
) -> tuple[TechnicianJobQueueItem, ...]:
    """Return every assigned active job, separating current work from queue."""

    technician_by_id = technicians.by_technician_id
    route_stop_by_job = {
        stop.job_id: stop
        for route in optimization.routes
        for stop in route.stops
    }
    items: list[TechnicianJobQueueItem] = []
    for record in checkpoint.records:
        technician_id = record.planned_technician
        if not technician_id:
            continue
        job = record.job
        technician = technician_by_id.get(technician_id)
        is_current = bool(
            technician and technician.current_job_id == job.checklist_id
        )
        if (
            is_current
            and technician is not None
            and technician.work_status is TechnicianWorkStatus.BUSY
        ):
            queue_status = "IN_PROGRESS"
            queue_position = 0
        elif job.status in BUSY_JOB_STATUSES:
            queue_status = "QUEUED_REVIEW"
            queue_position = None
        elif job.status == "Tạm dừng chờ xử lý":
            queue_status = "PAUSED"
            queue_position = None
        elif (
            is_current
            and technician is not None
            and technician.work_status is TechnicianWorkStatus.RESERVED
        ):
            queue_status = "NEXT"
            queue_position = record.route_order
        else:
            queue_status = "QUEUED"
            queue_position = record.route_order

        stop = route_stop_by_job.get(job.checklist_id)
        items.append(
            TechnicianJobQueueItem(
                snapshot_id=snapshot.snapshot_id,
                snapshot_time=snapshot.captured_at,
                technician_id=technician_id,
                checklist_id=job.checklist_id,
                queue_position=queue_position,
                queue_status=queue_status,
                checklist_status=job.status,
                assignment_source=record.assignment_source,
                estimated_arrival=(
                    stop.estimated_arrival if stop is not None else None
                ),
                estimated_finish=(
                    stop.estimated_finish if stop is not None else None
                ),
            )
        )
    return tuple(
        sorted(
            items,
            key=lambda item: (
                item.technician_id,
                item.queue_position is None,
                item.queue_position if item.queue_position is not None else 0,
                item.checklist_id,
            ),
        )
    )
