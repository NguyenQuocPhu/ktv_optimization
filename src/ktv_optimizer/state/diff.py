"""Apply incremental checklist events to the active checkpoint."""

from __future__ import annotations

from dataclasses import replace

from ktv_optimizer.domain import (
    ACTIVE_CHECKLIST_STATUSES,
    COMPLETED_CHECKLIST_STATUSES,
)
from ktv_optimizer.optimization.models import OptimizationJob

from .snapshot import OptimizerCheckpoint, SnapshotChange


def _material_values(job: OptimizationJob) -> dict[str, object]:
    location = job.location
    return {
        "status": job.status,
        "branch_name": job.branch_name,
        "task_type": job.task_type,
        "address": job.address,
        "latitude": location.latitude if location else None,
        "longitude": location.longitude if location else None,
        "ward_code": job.ward_code,
        "assigned_technician": job.assigned_technician,
        "priority": job.priority,
        "service_minutes": job.service_minutes,
        "created_at": job.created_at,
        "due_at": job.due_at,
        "finished_at": job.finished_at,
        "on_time_flag": job.on_time_flag,
    }


def apply_job_events(
    previous: OptimizerCheckpoint,
    incoming_events: list[OptimizationJob],
) -> tuple[list[OptimizationJob], list[SnapshotChange], list[OptimizationJob]]:
    """Upsert active events, complete explicit terminal events, keep absences."""

    active_by_id = {
        record.job.checklist_id: record.job for record in previous.records
    }
    changes: list[SnapshotChange] = []
    completed: list[OptimizationJob] = []

    for event in incoming_events:
        job_id = event.checklist_id
        old_job = active_by_id.get(job_id)
        if event.status in COMPLETED_CHECKLIST_STATUSES:
            # Completion is idempotent: a repeated terminal event for a job no
            # longer active does not create another completion.
            if old_job is None:
                continue
            active_by_id.pop(job_id, None)
            # A terminal webhook may only contain status/outcome fields. Keep
            # the operational context already held in the active checkpoint.
            completed.append(
                replace(
                    event,
                    branch_name=event.branch_name or old_job.branch_name,
                    task_type=event.task_type or old_job.task_type,
                    address=event.address or old_job.address,
                    location=event.location or old_job.location,
                    ward_code=event.ward_code or old_job.ward_code,
                    ward_name=event.ward_name or old_job.ward_name,
                    province_name=(
                        event.province_name or old_job.province_name
                    ),
                    assigned_technician=(
                        event.assigned_technician
                        or old_job.assigned_technician
                    ),
                    created_at=event.created_at or old_job.created_at,
                    due_at=event.due_at or old_job.due_at,
                )
            )
            changes.append(
                SnapshotChange(
                    checklist_id=job_id,
                    change_type="COMPLETED",
                    changed_fields="status",
                    previous_status=old_job.status,
                    current_status=event.status,
                )
            )
            continue
        if event.status not in ACTIVE_CHECKLIST_STATUSES:
            continue

        if old_job is None:
            changes.append(
                SnapshotChange(job_id, "ADDED", "*", None, event.status)
            )
        else:
            old_values = _material_values(old_job)
            new_values = _material_values(event)
            changed_fields = sorted(
                key for key in old_values if old_values[key] != new_values[key]
            )
            if changed_fields:
                changes.append(
                    SnapshotChange(
                        checklist_id=job_id,
                        change_type="UPDATED",
                        changed_fields=",".join(changed_fields),
                        previous_status=old_job.status,
                        current_status=event.status,
                    )
                )
        active_by_id[job_id] = event

    active = sorted(active_by_id.values(), key=lambda job: job.checklist_id)
    return active, changes, completed
