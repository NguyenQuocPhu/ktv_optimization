"""Sequential reducer: checkpoint + checklist events -> new checkpoint."""

from __future__ import annotations

from datetime import datetime
from time import perf_counter

from ktv_optimizer.optimization import SimpleKtvOptimizer

from .availability import (
    build_post_optimization_states,
    derive_pre_optimization_states,
)
from .conflicts import canonicalize_job_events, resolve_previous_assignments
from .csv_store import CsvStateStore, CsvTechnicianStateStore
from .diff import apply_job_events
from .queue import build_technician_job_queue
from .snapshot import (
    OptimizerCheckpoint,
    Snapshot,
    SnapshotRunResult,
    StateRecord,
    TechnicianCheckpoint,
    TechnicianWorkStatus,
)


class SnapshotReducer:
    """Apply checklist event batches and checkpoint the latest active plan."""

    def __init__(
        self,
        optimizer: SimpleKtvOptimizer,
        state_store: CsvStateStore | None = None,
        technician_state_store: CsvTechnicianStateStore | None = None,
    ) -> None:
        self.optimizer = optimizer
        self.state_store = state_store
        self.technician_state_store = technician_state_store or (
            CsvTechnicianStateStore(
                state_store.path.with_name("technician_state.csv")
            )
            if state_store is not None
            else None
        )

    def process(self, snapshot: Snapshot) -> SnapshotRunResult:
        """Reduce and persist through the legacy two-file CSV stores."""

        if self.state_store is None or self.technician_state_store is None:
            raise ValueError("process() cần CSV store; dùng reduce() nếu tự commit")
        run = self.reduce(
            snapshot,
            self.state_store.load(),
            self.technician_state_store.load(),
        )
        self.state_store.save(run.checkpoint)
        self.technician_state_store.save(run.technician_checkpoint)
        return run

    def reduce(
        self,
        snapshot: Snapshot,
        previous: OptimizerCheckpoint | None = None,
        previous_technicians: TechnicianCheckpoint | None = None,
    ) -> SnapshotRunResult:
        """Pure state transition; the caller chooses how to commit it."""

        planning_started = perf_counter()
        previous = previous or OptimizerCheckpoint()
        previous_technicians = previous_technicians or TechnicianCheckpoint()
        previous_times = [record.snapshot_time for record in previous.records]
        previous_times.extend(
            record.snapshot_time for record in previous_technicians.records
        )
        if previous_times and snapshot.captured_at < max(previous_times):
            raise ValueError(
                "Snapshot cũ hơn checkpoint hiện tại; dùng --reset-state khi "
                "muốn replay lại từ đầu"
            )
        incoming_events, duplicate_conflicts = canonicalize_job_events(
            snapshot.jobs
        )
        jobs, changes, completed_jobs = apply_job_events(
            previous, incoming_events
        )
        canonical_snapshot = Snapshot(
            snapshot_id=snapshot.snapshot_id,
            captured_at=snapshot.captured_at,
            jobs=tuple(jobs),
            technicians=snapshot.technicians,
            technician_updates=snapshot.technician_updates,
        )
        pre_technician_state, availability_conflicts = (
            derive_pre_optimization_states(
                canonical_snapshot,
                previous,
                previous_technicians,
            )
        )
        incumbents, assignment_conflicts = resolve_previous_assignments(
            previous,
            jobs,
            snapshot.technicians,
        )
        pre_by_id = pre_technician_state.by_technician_id
        locked_assignments = {
            job_id: technician_id
            for job_id, technician_id in incumbents.items()
            if pre_by_id[technician_id].work_status
            in {TechnicianWorkStatus.RESERVED, TechnicianWorkStatus.BUSY}
        }
        assignable_technician_ids = {
            record.technician_id
            for record in pre_technician_state.records
            if record.work_status
            not in {
                TechnicianWorkStatus.UNAVAILABLE,
                TechnicianWorkStatus.OFF_SHIFT,
            }
        }
        available_at_by_technician = {
            record.technician_id: record.available_at
            for record in pre_technician_state.records
            if record.work_status is TechnicianWorkStatus.BUSY
            and record.available_at is not None
        }
        optimization = self.optimizer.optimize(
            jobs,
            list(snapshot.technicians),
            planning_time=snapshot.captured_at,
            incumbent_assignments=incumbents,
            assignable_technician_ids=assignable_technician_ids,
            locked_assignments=locked_assignments,
            max_new_jobs_per_technician=None,
            technician_available_at=available_at_by_technician,
        )
        checkpoint = self._checkpoint(
            snapshot,
            jobs,
            optimization,
            updated_at=datetime.now(),
        )
        technician_checkpoint = build_post_optimization_states(
            canonical_snapshot,
            pre_technician_state,
            optimization,
        )
        job_queue = build_technician_job_queue(
            canonical_snapshot,
            checkpoint,
            technician_checkpoint,
            optimization,
        )
        return SnapshotRunResult(
            snapshot=canonical_snapshot,
            changes=tuple(changes),
            conflicts=tuple(
                [
                    *duplicate_conflicts,
                    *availability_conflicts,
                    *assignment_conflicts,
                ]
            ),
            optimization=optimization,
            checkpoint=checkpoint,
            technician_checkpoint=technician_checkpoint,
            job_queue=job_queue,
            incoming_jobs=tuple(incoming_events),
            completed_jobs=tuple(completed_jobs),
            planning_duration_seconds=round(
                perf_counter() - planning_started, 6
            ),
        )

    @staticmethod
    def _checkpoint(
        snapshot: Snapshot,
        jobs,
        optimization,
        *,
        updated_at: datetime,
    ) -> OptimizerCheckpoint:
        assignment_by_job = {
            item.job_id: item for item in optimization.assignments
        }
        route_order = {
            stop.job_id: stop.sequence
            for route in optimization.routes
            for stop in route.stops
        }
        records = []
        for job in jobs:
            assignment = assignment_by_job.get(job.checklist_id)
            system_active = (
                assignment is None
                and job.assigned_technician is not None
                and job.status
                in {
                    "Đang xử lý",
                    "Đã xử lý và đang theo dõi",
                    "Tạm dừng chờ xử lý",
                }
            )
            records.append(
                StateRecord(
                    snapshot_id=snapshot.snapshot_id,
                    snapshot_time=snapshot.captured_at,
                    job=job,
                    planned_technician=(
                        assignment.technician_id
                        if assignment
                        else job.assigned_technician
                        if system_active
                        else None
                    ),
                    assignment_source=(
                        assignment.source
                        if assignment
                        else "SYSTEM_ACTIVE"
                        if system_active
                        else None
                    ),
                    route_order=route_order.get(job.checklist_id),
                    updated_at=updated_at,
                )
            )
        return OptimizerCheckpoint(tuple(records))
