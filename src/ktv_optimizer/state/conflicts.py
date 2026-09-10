"""Minimal, explainable conflict rules for multi-snapshot V1."""

from __future__ import annotations

from ktv_optimizer.optimization.models import OptimizationJob, TechnicianShift

from .snapshot import OptimizerCheckpoint, SnapshotConflict


def canonicalize_job_events(
    jobs: tuple[OptimizationJob, ...],
) -> tuple[list[OptimizationJob], list[SnapshotConflict]]:
    """Keep the last event for each checklist without dropping completion."""

    by_id: dict[str, OptimizationJob] = {}
    conflicts: list[SnapshotConflict] = []
    for job in jobs:
        previous = by_id.get(job.checklist_id)
        if previous is not None:
            conflicts.append(
                SnapshotConflict(
                    checklist_id=job.checklist_id,
                    code="DUPLICATE_JOB_ID",
                    previous_value=previous.status,
                    incoming_value=job.status,
                    resolution="KEEP_LAST_EVENT",
                )
            )
        by_id[job.checklist_id] = job
    return sorted(by_id.values(), key=lambda job: job.checklist_id), conflicts


def resolve_previous_assignments(
    previous: OptimizerCheckpoint,
    jobs: list[OptimizationJob],
    technicians: tuple[TechnicianShift, ...],
) -> tuple[dict[str, str], list[SnapshotConflict]]:
    """Build soft incumbents and report hard system overrides."""

    previous_by_id = previous.by_job_id
    technician_ids = {item.technician_id for item in technicians}
    incumbents: dict[str, str] = {}
    conflicts: list[SnapshotConflict] = []

    for job in jobs:
        old = previous_by_id.get(job.checklist_id)
        if job.status == "Đã phân công":
            if not job.assigned_technician:
                conflicts.append(
                    SnapshotConflict(
                        job.checklist_id,
                        "SYSTEM_ASSIGNMENT_MISSING_EMP_ACCOUNT",
                        old.planned_technician if old else None,
                        None,
                        "LEAVE_UNASSIGNED",
                    )
                )
            elif job.assigned_technician not in technician_ids:
                conflicts.append(
                    SnapshotConflict(
                        job.checklist_id,
                        "SYSTEM_TECHNICIAN_NOT_IN_ROSTER",
                        old.planned_technician if old else None,
                        job.assigned_technician,
                        "SYSTEM_WINS_BUT_JOB_CANNOT_BE_ROUTED",
                    )
                )
            elif old and old.planned_technician != job.assigned_technician:
                conflicts.append(
                    SnapshotConflict(
                        job.checklist_id,
                        "SYSTEM_ASSIGNMENT_OVERRIDE",
                        old.planned_technician,
                        job.assigned_technician,
                        "USE_SYSTEM_ASSIGNMENT",
                    )
                )
            continue

        if not old or old.assignment_source != "OPTIMIZER_V0":
            continue
        incumbent = old.planned_technician
        if incumbent and incumbent in technician_ids:
            incumbents[job.checklist_id] = incumbent
        elif incumbent:
            conflicts.append(
                SnapshotConflict(
                    job.checklist_id,
                    "INCUMBENT_TECHNICIAN_NOT_IN_ROSTER",
                    incumbent,
                    None,
                    "RETURN_JOB_TO_ASSIGNMENT_POOL",
                )
            )
    return incumbents, conflicts
