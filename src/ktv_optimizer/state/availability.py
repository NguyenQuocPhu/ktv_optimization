"""Derive KTV availability without removing busy people from the roster."""

from __future__ import annotations

from datetime import datetime

from ktv_optimizer.optimization.models import (
    OptimizationJob,
    OptimizationResult,
    TechnicianShift,
)

from .snapshot import (
    OptimizerCheckpoint,
    Snapshot,
    SnapshotConflict,
    TechnicianCheckpoint,
    TechnicianStateRecord,
    TechnicianWorkStatus,
)


BUSY_JOB_STATUSES = frozenset(
    {"Đang xử lý", "Đã xử lý và đang theo dõi"}
)
RESERVED_JOB_STATUSES = frozenset(
    {"Đã phân công", "Tạm dừng chờ xử lý"}
)


def _is_off_shift(shift: TechnicianShift, captured_at: datetime) -> bool:
    if shift.shift_start is not None and captured_at < shift.shift_start:
        return True
    return shift.shift_end is not None and captured_at >= shift.shift_end


def derive_pre_optimization_states(
    snapshot: Snapshot,
    previous_jobs: OptimizerCheckpoint,
    previous_technicians: TechnicianCheckpoint,
) -> tuple[TechnicianCheckpoint, list[SnapshotConflict]]:
    """Resolve runtime status before assigning new jobs."""

    roster_by_id = {
        technician.technician_id: technician
        for technician in snapshot.technicians
    }
    previous_tech_by_id = previous_technicians.by_technician_id
    job_by_id = {job.checklist_id: job for job in snapshot.jobs}
    conflicts: list[SnapshotConflict] = []

    updates = {}
    for update in snapshot.technician_updates:
        if update.technician_id in updates:
            conflicts.append(
                SnapshotConflict(
                    checklist_id=update.current_job_id,
                    code="DUPLICATE_TECHNICIAN_UPDATE",
                    previous_value=(
                        updates[update.technician_id].work_status.value
                        if updates[update.technician_id].work_status
                        is not None
                        else None
                    ),
                    incoming_value=(
                        update.work_status.value
                        if update.work_status is not None
                        else None
                    ),
                    resolution="KEEP_LAST_UPDATE",
                    technician_id=update.technician_id,
                )
            )
        updates[update.technician_id] = update
    for technician_id, update in updates.items():
        if technician_id not in roster_by_id:
            conflicts.append(
                SnapshotConflict(
                    checklist_id=update.current_job_id,
                    code="TECHNICIAN_UPDATE_NOT_IN_ROSTER",
                    previous_value=None,
                    incoming_value=(
                        update.work_status.value
                        if update.work_status is not None
                        else None
                    ),
                    resolution="IGNORE_UPDATE",
                    technician_id=technician_id,
                )
            )

    claims: dict[str, list[tuple[str, TechnicianWorkStatus]]] = {}
    for job in snapshot.jobs:
        technician_id = job.assigned_technician
        if not technician_id:
            continue
        if (
            technician_id not in roster_by_id
            and job.status in BUSY_JOB_STATUSES | RESERVED_JOB_STATUSES
            and job.status != "Đã phân công"
        ):
            conflicts.append(
                SnapshotConflict(
                    checklist_id=job.checklist_id,
                    code="ACTIVE_TECHNICIAN_NOT_IN_ROSTER",
                    previous_value=technician_id,
                    incoming_value=None,
                    resolution="KEEP_JOB_STATE_WITHOUT_TECHNICIAN_RUNTIME",
                    technician_id=technician_id,
                )
            )
        if job.status in BUSY_JOB_STATUSES:
            claims.setdefault(technician_id, []).append(
                (job.checklist_id, TechnicianWorkStatus.BUSY)
            )
        elif job.status in RESERVED_JOB_STATUSES:
            claims.setdefault(technician_id, []).append(
                (job.checklist_id, TechnicianWorkStatus.RESERVED)
            )

    for record in previous_jobs.records:
        job = job_by_id.get(record.job.checklist_id)
        technician_id = record.planned_technician
        if (
            job is not None
            and job.status == "Chưa phân công"
            and technician_id
            and record.assignment_source == "OPTIMIZER_V0"
        ):
            claims.setdefault(technician_id, []).append(
                (job.checklist_id, TechnicianWorkStatus.RESERVED)
            )

    records: list[TechnicianStateRecord] = []
    now = datetime.now()
    for technician in snapshot.technicians:
        technician_id = technician.technician_id
        update = updates.get(technician_id)
        previous = previous_tech_by_id.get(technician_id)
        technician_claims = list(dict.fromkeys(claims.get(technician_id, [])))
        claimed_ids = [job_id for job_id, _ in technician_claims]
        busy_claims = [
            job_id
            for job_id, status in technician_claims
            if status is TechnicianWorkStatus.BUSY
        ]

        explicit_status = update.work_status if update else None
        explicit_job = update.current_job_id if update else None
        previous_job = previous.current_job_id if previous else None
        previous_job_is_active = previous_job in job_by_id

        if _is_off_shift(technician, snapshot.captured_at):
            status = TechnicianWorkStatus.OFF_SHIFT
            current_job_id = explicit_job or previous_job
        elif explicit_status is TechnicianWorkStatus.OFF_SHIFT:
            status = TechnicianWorkStatus.OFF_SHIFT
            current_job_id = explicit_job or previous_job
        elif explicit_status is TechnicianWorkStatus.UNAVAILABLE:
            status = TechnicianWorkStatus.UNAVAILABLE
            current_job_id = explicit_job
        elif explicit_status is TechnicianWorkStatus.BUSY:
            status = TechnicianWorkStatus.BUSY
            current_job_id = explicit_job or (
                busy_claims[0] if busy_claims else previous_job
            )
        elif busy_claims:
            status = TechnicianWorkStatus.BUSY
            current_job_id = busy_claims[0]
        elif technician_claims:
            status = TechnicianWorkStatus.RESERVED
            current_job_id = (
                explicit_job
                if explicit_job in claimed_ids
                else previous_job
                if previous_job in claimed_ids
                else claimed_ids[0]
            )
        elif (
            explicit_status is not None
            and explicit_status is not TechnicianWorkStatus.OFF_SHIFT
        ):
            status = explicit_status
            current_job_id = explicit_job
        elif (
            previous is not None
            and previous.work_status is TechnicianWorkStatus.UNAVAILABLE
        ):
            status = TechnicianWorkStatus.UNAVAILABLE
            current_job_id = previous_job
        elif (
            previous is not None
            and previous.work_status is TechnicianWorkStatus.BUSY
            and previous_job_is_active
        ):
            status = TechnicianWorkStatus.BUSY
            current_job_id = previous_job
        else:
            status = TechnicianWorkStatus.IDLE
            current_job_id = None

        if len(busy_claims) > 1:
            conflicts.append(
                SnapshotConflict(
                    checklist_id=current_job_id,
                    code="TECHNICIAN_MULTIPLE_BUSY_JOBS",
                    previous_value=None,
                    incoming_value="|".join(sorted(busy_claims)),
                    resolution="KEEP_ONE_CURRENT_AND_FLAG_REMAINING",
                    technician_id=technician_id,
                )
            )
        if status in {
            TechnicianWorkStatus.UNAVAILABLE,
            TechnicianWorkStatus.OFF_SHIFT,
        }:
            for job_id, claim_status in technician_claims:
                claimed_job = job_by_id.get(job_id)
                if (
                    claim_status is TechnicianWorkStatus.RESERVED
                    and claimed_job is not None
                    and claimed_job.status in RESERVED_JOB_STATUSES
                ):
                    conflicts.append(
                        SnapshotConflict(
                            checklist_id=job_id,
                            code="SYSTEM_ASSIGNED_TO_UNAVAILABLE_TECHNICIAN",
                            previous_value=status.value,
                            incoming_value=job_id,
                            resolution="KEEP_SYSTEM_JOB_AND_REQUIRE_REVIEW",
                            technician_id=technician_id,
                        )
                    )

        location = technician.current_location
        available_at = (
            update.available_at
            if update and update.available_at is not None
            else previous.available_at
            if previous and status in {
                TechnicianWorkStatus.BUSY,
                TechnicianWorkStatus.RESERVED,
            }
            else None
        )
        records.append(
            TechnicianStateRecord(
                snapshot_id=snapshot.snapshot_id,
                snapshot_time=snapshot.captured_at,
                technician_id=technician_id,
                branch_name=technician.branch_name,
                work_status=status,
                current_job_id=current_job_id,
                available_at=available_at,
                planned_job_count=len(set(claimed_ids)),
                latitude=location.latitude if location else None,
                longitude=location.longitude if location else None,
                updated_at=now,
                queued_job_count=max(
                    0,
                    len(set(claimed_ids))
                    - (
                        1
                        if status is TechnicianWorkStatus.BUSY
                        and current_job_id in claimed_ids
                        else 0
                    ),
                ),
            )
        )
    return TechnicianCheckpoint(tuple(records)), conflicts


def build_post_optimization_states(
    snapshot: Snapshot,
    pre_state: TechnicianCheckpoint,
    optimization: OptimizationResult,
) -> TechnicianCheckpoint:
    """Apply assignment results and persist the resulting KTV availability."""

    jobs_by_technician: dict[str, set[str]] = {}
    busy_jobs_by_technician: dict[str, list[str]] = {}
    for assignment in optimization.assignments:
        jobs_by_technician.setdefault(assignment.technician_id, set()).add(
            assignment.job_id
        )
    for job in snapshot.jobs:
        if not job.assigned_technician:
            continue
        if job.status in BUSY_JOB_STATUSES:
            busy_jobs_by_technician.setdefault(
                job.assigned_technician, []
            ).append(job.checklist_id)
            jobs_by_technician.setdefault(
                job.assigned_technician, set()
            ).add(job.checklist_id)
        elif job.status == "Tạm dừng chờ xử lý":
            jobs_by_technician.setdefault(
                job.assigned_technician, set()
            ).add(job.checklist_id)

    route_finish = {
        route.technician_id: max(
            (
                stop.estimated_finish
                for stop in route.stops
                if stop.estimated_finish is not None
            ),
            default=None,
        )
        for route in optimization.routes
    }
    route_order = {
        route.technician_id: [stop.job_id for stop in route.stops]
        for route in optimization.routes
    }

    records: list[TechnicianStateRecord] = []
    now = datetime.now()
    for record in pre_state.records:
        job_ids = jobs_by_technician.get(record.technician_id, set())
        busy_ids = busy_jobs_by_technician.get(record.technician_id, [])
        if record.work_status is TechnicianWorkStatus.OFF_SHIFT:
            status = TechnicianWorkStatus.OFF_SHIFT
            current_job_id = record.current_job_id
        elif record.work_status is TechnicianWorkStatus.UNAVAILABLE:
            status = TechnicianWorkStatus.UNAVAILABLE
            current_job_id = record.current_job_id
        elif busy_ids:
            status = TechnicianWorkStatus.BUSY
            current_job_id = (
                record.current_job_id
                if record.current_job_id in busy_ids
                else busy_ids[0]
            )
        elif record.work_status is TechnicianWorkStatus.BUSY:
            status = TechnicianWorkStatus.BUSY
            current_job_id = record.current_job_id
        elif job_ids:
            status = TechnicianWorkStatus.RESERVED
            ordered = route_order.get(record.technician_id, [])
            current_job_id = next(
                (job_id for job_id in ordered if job_id in job_ids),
                sorted(job_ids)[0],
            )
        else:
            status = TechnicianWorkStatus.IDLE
            current_job_id = None

        records.append(
            TechnicianStateRecord(
                snapshot_id=snapshot.snapshot_id,
                snapshot_time=snapshot.captured_at,
                technician_id=record.technician_id,
                branch_name=record.branch_name,
                work_status=status,
                current_job_id=current_job_id,
                available_at=(
                    record.available_at
                    if status is TechnicianWorkStatus.BUSY
                    else route_finish.get(record.technician_id)
                    if status is TechnicianWorkStatus.RESERVED
                    else None
                ),
                planned_job_count=len(job_ids),
                latitude=record.latitude,
                longitude=record.longitude,
                updated_at=now,
                queued_job_count=max(
                    0,
                    len(job_ids)
                    - (
                        1
                        if status is TechnicianWorkStatus.BUSY
                        and current_job_id in job_ids
                        else 0
                    ),
                ),
            )
        )
    return TechnicianCheckpoint(tuple(records))
