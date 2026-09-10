#!/usr/bin/env python3
"""Smoke test: 3 KTV fill five-hour shifts; job 16 must stay unassigned."""

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
PLANNING_TIME = datetime(2026, 8, 4, 8)
SERVICE_MINUTES = 60
JOBS_PER_TECHNICIAN = 5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "capacity_overflow_test",
    )
    return parser.parse_args()


def _job_rows() -> list[dict]:
    return [
        {
            "CHECKLIST_ID": f"CAP-J{index:02d}",
            "CHECKLIST_STATUS": "Chưa phân công",
            "BRANCH_NAME": "A",
            "CASE_TYPE": "MAINTENANCE",
            "OBJ_LOCATION": "Phường Demo, Tỉnh Demo",
            "EMP_ACCOUNT": None,
            "CREATE_DATE": (
                PLANNING_TIME - timedelta(minutes=30) + timedelta(seconds=index)
            ).isoformat(sep=" "),
        }
        for index in range(1, 17)
    ]


def _roster_rows() -> list[dict]:
    # Five-hour shift / 60 minutes per maintenance job = exactly five jobs.
    return [
        {
            "EMP_ACCOUNT": technician_id,
            "BRANCH_NAME": "A",
            "LATITUDE": 10.0100000008,
            "LONGITUDE": 106.0100000089,
            "SHIFT_START": "2026-08-04 08:00:00",
            "SHIFT_END": "2026-08-04 13:00:00",
            "SUPPORTED_CASE_TYPES": "MAINTENANCE",
            "EXISTING_WORKLOAD_MINUTES": 0,
        }
        for technician_id in ("KTV-A", "KTV-B", "KTV-C")
    ]


def _report(run) -> dict:
    result = run.result
    assignment_counts = Counter(
        item.technician_id for item in result.optimization.assignments
    )
    route_by_technician = {
        route.technician_id: route for route in result.optimization.routes
    }
    return {
        "snapshot_id": result.snapshot.snapshot_id,
        "incoming_events": len(result.incoming_jobs),
        "active_jobs": len(result.snapshot.jobs),
        "assignments": len(result.optimization.assignments),
        "unassigned": len(result.optimization.unassigned),
        "assignment_counts": dict(sorted(assignment_counts.items())),
        "unassigned_jobs": [
            {"checklist_id": item.job_id, "reason": item.reason}
            for item in result.optimization.unassigned
        ],
        "technicians": {
            item.technician_id: {
                "planned_job_count": item.planned_job_count,
                "queued_job_count": item.queued_job_count,
                "route_service_minutes": (
                    route_by_technician[item.technician_id]
                    .total_service_minutes
                ),
                "capacity_used_percent": round(
                    route_by_technician[item.technician_id]
                    .total_service_minutes
                    / (5 * 60)
                    * 100,
                    1,
                ),
            }
            for item in result.technician_checkpoint.records
        },
        "checkpoint_id": run.checkpoint.checkpoint_id,
        "previous_checkpoint_id": run.checkpoint.previous_checkpoint_id,
        "output_dir": str(run.output_dir.resolve()),
        "evaluation_metrics": run.metadata["counts"]["evaluation_metrics"],
    }


def _assignment_map(run) -> dict[str, str]:
    return {
        item.job_id: item.technician_id
        for item in run.result.optimization.assignments
    }


def main() -> int:
    args = parse_args()
    session = args.output_root / datetime.now().strftime(
        "capacity_%Y%m%dT%H%M%S%f"
    )
    input_dir = session / "inputs"
    input_dir.mkdir(parents=True)
    roster_path = input_dir / "roster.csv"
    pd.DataFrame(_roster_rows()).to_csv(
        roster_path, index=False, encoding="utf-8-sig"
    )

    rows = _job_rows()
    event_batches = (rows[:6], rows[6:12], rows[12:])
    event_paths = []
    for index, batch in enumerate(event_batches, start=1):
        path = input_dir / f"events_{index:03d}.csv"
        pd.DataFrame(batch, columns=MAINTENANCE_COLUMNS).to_csv(
            path, index=False, encoding="utf-8-sig"
        )
        event_paths.append(path)

    processor = OperationalSnapshotProcessor(
        boundary_path=(
            PROJECT_ROOT
            / "utils"
            / "fixtures"
            / "snapshots_v1"
            / "boundary.geojson"
        ),
        runtime_dir=session / "runtime",
        output_root=session / "runs",
    )
    runs = [
        processor.process(
            snapshot_id=f"CAP-S{index:03d}",
            # All batches belong to one planning instant. This makes the
            # five-hour capacity and route horizon directly comparable.
            snapshot_time=PLANNING_TIME,
            maintenance_path=event_path,
            roster_path=roster_path,
        )
        for index, event_path in enumerate(event_paths, start=1)
    ]
    reports = [_report(run) for run in runs]
    final = reports[-1]

    assert [item["incoming_events"] for item in reports] == [6, 6, 4]
    assert [item["active_jobs"] for item in reports] == [6, 12, 16]
    assert [item["assignments"] for item in reports] == [6, 12, 15]
    assert [item["unassigned"] for item in reports] == [0, 0, 1]
    assert final["assignment_counts"] == {
        "KTV-A": JOBS_PER_TECHNICIAN,
        "KTV-B": JOBS_PER_TECHNICIAN,
        "KTV-C": JOBS_PER_TECHNICIAN,
    }
    assert final["unassigned_jobs"] == [
        {
            "checklist_id": "CAP-J16",
            "reason": "SHIFT_CAPACITY_EXCEEDED",
        }
    ]
    assert all(
        technician["planned_job_count"] == JOBS_PER_TECHNICIAN
        and technician["route_service_minutes"] == 300
        and technician["capacity_used_percent"] == 100.0
        for technician in final["technicians"].values()
    )
    assert all(
        current["previous_checkpoint_id"] == previous["checkpoint_id"]
        for previous, current in zip(reports, reports[1:])
    )
    assignment_maps = [_assignment_map(run) for run in runs]
    assert all(
        current[job_id] == technician_id
        for previous, current in zip(assignment_maps, assignment_maps[1:])
        for job_id, technician_id in previous.items()
    )
    metrics = final["evaluation_metrics"]
    assert metrics["planned_sla_on_time_rate_percent"] == 93.75
    assert metrics["planned_sla_on_time_jobs"] == 15
    assert metrics["planned_sla_evaluable_jobs"] == 16
    assert metrics["total_distance_km"] == 0.0
    assert metrics["total_travel_minutes"] == 0.0
    assert metrics["unique_clusters"] == 1
    assert metrics["technician_cluster_visits"] == 3
    assert metrics["same_area_revisit_count"] == 0
    assert metrics["completed_jobs_in_shift"] == 0
    assert metrics["total_wait_between_jobs_minutes"] == 0.0
    assert metrics["ai_planning_target_met"] is True

    report = {
        "status": "PASS",
        "assumptions": {
            "technicians": 3,
            "jobs": 16,
            "shift_minutes_each": 300,
            "service_minutes_each_job": SERVICE_MINUTES,
            "max_jobs_by_capacity_each": JOBS_PER_TECHNICIAN,
            "event_batch_sizes": [6, 6, 4],
        },
        "checks": {
            "balanced_5_jobs_each": True,
            "job_16_unassigned": True,
            "job_16_reason": "SHIFT_CAPACITY_EXCEEDED",
            "checkpoint_chain_valid": True,
            "previous_assignments_preserved": True,
        },
        "snapshots": reports,
        "session_dir": str(session.resolve()),
    }
    report_path = session / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("Capacity overflow multi-snapshot smoke test: PASS")
    print(f"Report: {report_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
