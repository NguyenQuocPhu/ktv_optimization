#!/usr/bin/env python3
"""Fast assertions for V0 flow semantics; no external test framework needed."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ktv_optimizer.domain import GeoPoint  # noqa: E402
from ktv_optimizer.optimization import (  # noqa: E402
    CompatibilityMode,
    OptimizationJob,
    SimpleKtvOptimizer,
    TechnicianShift,
)


def demo_jobs() -> list[OptimizationJob]:
    return [
        OptimizationJob(
            checklist_id="J_UNASSIGNED_GPS",
            status="Chưa phân công",
            branch_name="A",
            task_type="MAINTENANCE",
            address="Xa demo",
            location=GeoPoint(10.01, 106.01),
            ward_code="001",
        ),
        OptimizationJob(
            checklist_id="J_UNASSIGNED_NO_GPS",
            status="Chưa phân công",
            branch_name="A",
            task_type="MAINTENANCE",
            address=None,
            location=None,
        ),
        OptimizationJob(
            checklist_id="J_FIXED",
            status="Đã phân công",
            branch_name="A",
            task_type="MAINTENANCE",
            address="Phường demo",
            location=GeoPoint(10.02, 106.02),
            assigned_technician="T2",
        ),
    ]


def demo_technicians() -> list[TechnicianShift]:
    return [
        TechnicianShift(
            technician_id="T1",
            branch_name="A",
            current_location=GeoPoint(10.0, 106.0),
            supported_task_types=frozenset({"MAINTENANCE"}),
        ),
        TechnicianShift(
            technician_id="T2",
            branch_name="A",
            current_location=GeoPoint(10.02, 106.02),
            supported_task_types=frozenset({"MAINTENANCE"}),
        ),
    ]


def main() -> int:
    now = datetime(2026, 6, 30, 6)
    task_location = SimpleKtvOptimizer(
        mode=CompatibilityMode.TASK_LOCATION,
        max_distance_km=30,
    ).optimize(demo_jobs(), demo_technicians(), planning_time=now)
    task_only = SimpleKtvOptimizer(
        mode=CompatibilityMode.TASK,
        max_distance_km=30,
    ).optimize(demo_jobs(), demo_technicians(), planning_time=now)

    fixed = next(
        decision
        for decision in task_location.assignments
        if decision.job_id == "J_FIXED"
    )
    assert fixed.technician_id == "T2"
    assert fixed.source == "SYSTEM_FIXED"

    task_location_unassigned = {
        decision.job_id for decision in task_location.unassigned
    }
    assert "J_UNASSIGNED_NO_GPS" in task_location_unassigned

    task_only_assigned = {
        decision.job_id for decision in task_only.assignments
    }
    assert "J_UNASSIGNED_NO_GPS" in task_only_assigned
    assert {route.technician_id for route in task_only.routes}

    print("V0 optimizer assertions: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

