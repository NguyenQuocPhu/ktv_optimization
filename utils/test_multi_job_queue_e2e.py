#!/usr/bin/env python3
"""Run two operational snapshots proving that one KTV can queue many jobs."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ktv_optimizer.pipeline import OperationalSnapshotProcessor  # noqa: E402


MAINTENANCE_COLUMNS = [
    "CHECKLIST_ID",
    "CHECKLIST_STATUS",
    "BRANCH_NAME",
    "CASE_TYPE",
    "OBJ_LOCATION",
    "EMP_ACCOUNT",
    "CREATE_DATE",
]


def _job_rows(count: int) -> list[dict]:
    created_at = datetime(2026, 8, 3, 7)
    return [
        {
            "CHECKLIST_ID": f"QUEUE-J{index:02d}",
            "CHECKLIST_STATUS": "Chưa phân công",
            "BRANCH_NAME": "A",
            "CASE_TYPE": "MAINTENANCE",
            "OBJ_LOCATION": "Phường Demo, Tỉnh Demo",
            "EMP_ACCOUNT": None,
            "CREATE_DATE": (
                created_at + timedelta(minutes=index)
            ).isoformat(sep=" "),
        }
        for index in range(1, count + 1)
    ]


def _assignment_counts(run) -> Counter[str]:
    return Counter(
        assignment.technician_id
        for assignment in run.result.optimization.assignments
    )


def _assignment_map(run) -> dict[str, str]:
    return {
        assignment.job_id: assignment.technician_id
        for assignment in run.result.optimization.assignments
    }


def _snapshot_report(run) -> dict:
    counts = _assignment_counts(run)
    technicians = {
        item.technician_id: {
            "work_status": item.work_status.value,
            "current_job_id": item.current_job_id,
            "planned_job_count": item.planned_job_count,
            "queued_job_count": item.queued_job_count,
        }
        for item in run.result.technician_checkpoint.records
    }
    queues = {
        technician_id: [
            {
                "checklist_id": item.checklist_id,
                "position": item.queue_position,
                "queue_status": item.queue_status,
            }
            for item in run.result.job_queue
            if item.technician_id == technician_id
        ]
        for technician_id in sorted(counts)
    }
    return {
        "snapshot_id": run.result.snapshot.snapshot_id,
        "active_jobs": len(run.result.snapshot.jobs),
        "assignments": len(run.result.optimization.assignments),
        "unassigned": len(run.result.optimization.unassigned),
        "assignment_counts": dict(sorted(counts.items())),
        "technicians": technicians,
        "queues": queues,
        "checkpoint_id": run.checkpoint.checkpoint_id,
        "previous_checkpoint_id": run.checkpoint.previous_checkpoint_id,
        "output_dir": str(run.output_dir.resolve()),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "multi_job_queue_test",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    session_id = datetime.now().strftime("queue_%Y%m%dT%H%M%S%f")
    session = args.output_root / session_id
    inputs = session / "inputs"
    inputs.mkdir(parents=True)

    roster_path = inputs / "roster.csv"
    pd.DataFrame(
        [
            {
                "EMP_ACCOUNT": "KTV-A",
                "BRANCH_NAME": "A",
                "LATITUDE": 10.005,
                "LONGITUDE": 106.005,
                "SHIFT_START": "2026-08-03 06:00:00",
                "SHIFT_END": "2026-08-03 18:00:00",
                "SUPPORTED_CASE_TYPES": "MAINTENANCE",
                "EXISTING_WORKLOAD_MINUTES": 0,
            },
            {
                "EMP_ACCOUNT": "KTV-B",
                "BRANCH_NAME": "A",
                "LATITUDE": 10.005,
                "LONGITUDE": 106.005,
                "SHIFT_START": "2026-08-03 06:00:00",
                "SHIFT_END": "2026-08-03 18:00:00",
                "SUPPORTED_CASE_TYPES": "MAINTENANCE",
                "EXISTING_WORKLOAD_MINUTES": 0,
            },
        ]
    ).to_csv(roster_path, index=False, encoding="utf-8-sig")

    snapshot_1_path = inputs / "snapshot_001.csv"
    snapshot_2_path = inputs / "snapshot_002.csv"
    pd.DataFrame(_job_rows(5), columns=MAINTENANCE_COLUMNS).to_csv(
        snapshot_1_path, index=False, encoding="utf-8-sig"
    )
    # S002 only carries five new events. The previous five jobs must be kept
    # from the active checkpoint even though they are absent from this batch.
    pd.DataFrame(_job_rows(10)[5:], columns=MAINTENANCE_COLUMNS).to_csv(
        snapshot_2_path, index=False, encoding="utf-8-sig"
    )

    processor = OperationalSnapshotProcessor(
        boundary_path=(
            PROJECT_ROOT / "utils" / "fixtures" / "snapshots_v1"
            / "boundary.geojson"
        ),
        runtime_dir=session / "runtime",
        output_root=session / "runs",
    )
    first = processor.process(
        snapshot_id="QUEUE-S001",
        snapshot_time=datetime(2026, 8, 3, 8),
        maintenance_path=snapshot_1_path,
        roster_path=roster_path,
    )
    second = processor.process(
        snapshot_id="QUEUE-S002",
        snapshot_time=datetime(2026, 8, 3, 9),
        maintenance_path=snapshot_2_path,
        roster_path=roster_path,
    )

    first_report = _snapshot_report(first)
    second_report = _snapshot_report(second)
    first_assignments = _assignment_map(first)
    second_assignments = _assignment_map(second)
    assert first_report["assignments"] == 5
    assert first_report["unassigned"] == 0
    assert max(first_report["assignment_counts"].values()) > 1
    assert second_report["assignments"] == 10
    assert second_report["unassigned"] == 0
    assert all(
        count > 1 for count in second_report["assignment_counts"].values()
    )
    assert all(
        second_report["assignment_counts"][technician_id]
        > first_report["assignment_counts"].get(technician_id, 0)
        for technician_id in second_report["assignment_counts"]
    )
    assert all(
        second_assignments[job_id] == technician_id
        for job_id, technician_id in first_assignments.items()
    )
    assert second.checkpoint.previous_checkpoint_id == first.checkpoint.checkpoint_id

    report = {
        "status": "PASS",
        "assumption": {
            "technicians": 2,
            "shift_capacity_minutes_each": 720,
            "service_minutes_each_job": 60,
            "snapshot_1_event_rows": 5,
            "snapshot_2_event_rows": 5,
            "active_jobs_after_snapshot_2": 10,
            "new_jobs_in_snapshot_2": 5,
        },
        "previous_assignments_preserved": True,
        "snapshot_1": first_report,
        "snapshot_2": second_report,
        "session_dir": str(session.resolve()),
    }
    report_path = session / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Multi-job operational queue test: PASS")
    print(f"Report: {report_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
