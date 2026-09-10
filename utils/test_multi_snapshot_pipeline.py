#!/usr/bin/env python3
"""Assertions for CSV-backed sequential snapshot V1; no pytest required."""

from __future__ import annotations

import csv
import sys
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ktv_optimizer.domain import GeoPoint  # noqa: E402
from ktv_optimizer.optimization import (  # noqa: E402
    CompatibilityMode,
    OptimizationJob,
    SimpleKtvOptimizer,
    TechnicianShift,
)
from ktv_optimizer.state import (  # noqa: E402
    CsvStateStore,
    Snapshot,
    SnapshotReducer,
    TechnicianStatusUpdate,
    TechnicianWorkStatus,
)


def job(
    checklist_id: str,
    *,
    status: str = "Chưa phân công",
    assigned: str | None = None,
) -> OptimizationJob:
    return OptimizationJob(
        checklist_id=checklist_id,
        status=status,
        branch_name="A",
        task_type="MAINTENANCE",
        address="Demo",
        location=GeoPoint(10.0, 106.0),
        ward_code="001",
        assigned_technician=assigned,
    )


def roster(
    t1_location: GeoPoint,
    *,
    include_t1: bool = True,
) -> tuple[TechnicianShift, ...]:
    technicians = []
    if include_t1:
        technicians.append(
            TechnicianShift("T1", "A", t1_location)
        )
    technicians.append(
        TechnicianShift("T2", "A", GeoPoint(10.0, 106.0))
    )
    return tuple(technicians)


def main() -> int:
    with TemporaryDirectory() as temporary_dir:
        state_path = Path(temporary_dir) / "optimizer_state.csv"
        store = CsvStateStore(state_path)
        reducer = SnapshotReducer(
            SimpleKtvOptimizer(
                mode=CompatibilityMode.TASK_LOCATION,
                max_distance_km=30,
            ),
            store,
        )

        first = reducer.process(
            Snapshot(
                "S001",
                datetime(2026, 8, 1, 8),
                (job("J_KEEP"),),
                roster(GeoPoint(10.0, 106.0)),
            )
        )
        first_assignment = first.optimization.assignments[0]
        assert first_assignment.technician_id == "T1"
        assert {change.change_type for change in first.changes} == {"ADDED"}
        first_tech = first.technician_checkpoint.by_technician_id
        assert first_tech["T1"].work_status is TechnicianWorkStatus.RESERVED
        assert first_tech["T1"].current_job_id == "J_KEEP"
        assert first_tech["T2"].work_status is TechnicianWorkStatus.IDLE

        second = reducer.process(
            Snapshot(
                "S002",
                datetime(2026, 8, 1, 9),
                # J_KEEP is deliberately absent. Input is an event batch, so
                # absence must keep the active job from the previous state.
                (job("J_NEW"), job("J_NEW")),
                roster(GeoPoint(10.01, 106.01)),
            )
        )
        second_by_job = {
            item.job_id: item for item in second.optimization.assignments
        }
        assert second_by_job["J_KEEP"].technician_id == "T1"
        assert second_by_job["J_KEEP"].stability_cost < 0
        assert {job.checklist_id for job in second.snapshot.jobs} == {
            "J_KEEP",
            "J_NEW",
        }
        assert not any(
            item.checklist_id == "J_KEEP" for item in second.changes
        )
        second_tech = second.technician_checkpoint.by_technician_id
        assert second_tech["T1"].current_job_id == "J_KEEP"
        assert second_tech["T1"].planned_job_count == 2
        assert second_tech["T1"].queued_job_count == 2
        assert second_tech["T2"].work_status is TechnicianWorkStatus.IDLE
        assert [item.queue_status for item in second.job_queue] == [
            "NEXT",
            "QUEUED",
        ]
        assert any(
            item.code == "DUPLICATE_JOB_ID" for item in second.conflicts
        )
        assert any(
            item.checklist_id == "J_NEW" and item.change_type == "ADDED"
            for item in second.changes
        )

        third = reducer.process(
            Snapshot(
                "S003",
                datetime(2026, 8, 1, 10),
                (
                    job("J_KEEP", status="Đã phân công", assigned="T2"),
                    job("J_NEW", status="Đóng checklist", assigned="T1"),
                ),
                roster(GeoPoint(10.01, 106.01)),
            )
        )
        third_assignment = third.optimization.assignments[0]
        assert third_assignment.technician_id == "T2"
        assert third_assignment.source == "SYSTEM_FIXED"
        third_tech = third.technician_checkpoint.by_technician_id
        assert third_tech["T1"].work_status is TechnicianWorkStatus.IDLE
        assert third_tech["T2"].work_status is TechnicianWorkStatus.RESERVED
        assert any(
            item.code == "SYSTEM_ASSIGNMENT_OVERRIDE"
            for item in third.conflicts
        )
        assert any(
            item.checklist_id == "J_NEW"
            and item.change_type == "COMPLETED"
            for item in third.changes
        )

        checkpoint = store.load()
        assert len(checkpoint.records) == 1
        assert checkpoint.records[0].planned_technician == "T2"
        assert checkpoint.records[0].assignment_source == "SYSTEM_FIXED"
        with state_path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert rows[0]["EMP_ACCOUNT"] == "T2"
        assert rows[0]["PLANNED_EMP_ACCOUNT"] == "T2"

        fourth = reducer.process(
            Snapshot(
                "S004",
                datetime(2026, 8, 1, 11),
                (
                    job(
                        "J_KEEP",
                        status="Đang xử lý",
                        assigned="T2",
                    ),
                    job("J_FREE"),
                ),
                roster(GeoPoint(10.0, 106.0)),
            )
        )
        assert fourth.optimization.assignments[0].technician_id == "T2"
        fourth_tech = fourth.technician_checkpoint.by_technician_id
        assert fourth_tech["T2"].work_status is TechnicianWorkStatus.BUSY
        assert fourth_tech["T2"].current_job_id == "J_KEEP"
        assert fourth_tech["T2"].planned_job_count == 2
        assert fourth_tech["T2"].queued_job_count == 1
        assert fourth_tech["T1"].work_status is TechnicianWorkStatus.IDLE
        assert [item.queue_status for item in fourth.job_queue] == [
            "IN_PROGRESS",
            "QUEUED",
        ]
        fifth = reducer.process(
            Snapshot(
                "S005",
                datetime(2026, 8, 1, 12),
                (
                    job("J_FREE"),
                    job("J_KEEP", status="Đóng checklist", assigned="T2"),
                ),
                roster(GeoPoint(10.0, 106.0), include_t1=False),
            )
        )
        assert fifth.optimization.assignments[0].technician_id == "T2"
        fifth_tech = fifth.technician_checkpoint.by_technician_id
        assert "T1" not in fifth_tech
        assert fifth_tech["T2"].work_status is TechnicianWorkStatus.RESERVED
        assert fifth_tech["T2"].current_job_id == "J_FREE"
        assert not any(
            item.code == "INCUMBENT_TECHNICIAN_NOT_IN_ROSTER"
            for item in fifth.conflicts
        )

        sixth = reducer.process(
            Snapshot(
                "S006",
                datetime(2026, 8, 1, 13),
                (
                    job(
                        "J_SYSTEM",
                        status="Đã phân công",
                        assigned="T2",
                    ),
                    job("J_A"),
                    job("J_B"),
                    job("J_FREE", status="Đóng checklist", assigned="T2"),
                ),
                roster(GeoPoint(10.0, 106.0)),
                (
                    TechnicianStatusUpdate(
                        technician_id="T2",
                        work_status=TechnicianWorkStatus.BUSY,
                        current_job_id="J_EXTERNAL",
                    ),
                ),
            )
        )
        sixth_assignment = sixth.optimization.assignments[0]
        assert sixth_assignment.technician_id == "T2"
        assert sixth_assignment.source == "SYSTEM_FIXED"
        optimizer_assignments = [
            item
            for item in sixth.optimization.assignments
            if item.source == "OPTIMIZER_V0"
        ]
        assert len(optimizer_assignments) == 2
        assert not sixth.optimization.unassigned
        assert (
            sixth.technician_checkpoint.by_technician_id["T2"].work_status
            is TechnicianWorkStatus.BUSY
        )
        assert (
            sixth.technician_checkpoint.by_technician_id["T2"].queued_job_count
            == 2
        )
        assert not any(
            item.code == "SYSTEM_ASSIGNED_TO_BUSY_TECHNICIAN"
            for item in sixth.conflicts
        )

        seventh = reducer.process(
            Snapshot(
                "S007",
                datetime(2026, 8, 1, 14),
                (
                    job("J_SYS_1", status="Đã phân công", assigned="T1"),
                    job("J_SYS_2", status="Đã phân công", assigned="T1"),
                    job("J_A", status="Đóng checklist", assigned="T2"),
                    job("J_B", status="Đóng checklist", assigned="T1"),
                    job(
                        "J_SYSTEM",
                        status="Đóng checklist",
                        assigned="T2",
                    ),
                ),
                roster(GeoPoint(10.0, 106.0)),
            )
        )
        assert len(seventh.optimization.assignments) == 2
        assert {
            item.technician_id for item in seventh.optimization.assignments
        } == {"T1"}
        seventh_t1 = seventh.technician_checkpoint.by_technician_id["T1"]
        assert seventh_t1.work_status is TechnicianWorkStatus.RESERVED
        assert seventh_t1.planned_job_count == 2
        assert seventh_t1.queued_job_count == 2
        assert [item.queue_status for item in seventh.job_queue] == [
            "NEXT",
            "QUEUED",
        ]

        try:
            reducer.process(
                Snapshot(
                    "STALE",
                    datetime(2026, 8, 1, 12, 30),
                    (job("J_FREE"),),
                    roster(GeoPoint(10.0, 106.0)),
                )
            )
        except ValueError as error:
            assert "cũ hơn checkpoint" in str(error)
        else:
            raise AssertionError("Stale snapshot must be rejected")

    print("Multi-snapshot CSV pipeline assertions: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
