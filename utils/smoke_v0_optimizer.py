#!/usr/bin/env python3
"""Smoke test V0 optimizer on current active checklists and a demo roster."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ktv_optimizer.data.mappers import (  # noqa: E402
    maintenance_to_optimization_jobs,
    shift_roster_to_technicians,
)
from ktv_optimizer.data.sources.administrative_boundaries import (  # noqa: E402
    WardBoundaryIndex,
)
from ktv_optimizer.data.sources.qos_maintenance import (  # noqa: E402
    QosMaintenanceCsvSource,
)
from ktv_optimizer.optimization import (  # noqa: E402
    CompatibilityMode,
    SimpleKtvOptimizer,
)


def build_demo_roster(jobs) -> pd.DataFrame:
    """Create test-only shift rows; this is not production roster data."""

    location_by_job = {job.checklist_id: job.location for job in jobs}
    rows = [
        # System-fixed KTVs must remain attached to their assigned jobs.
        ("LANPN.GIANGVT1", "LAN", "1481839496", 0),
        ("SFPRO.PHUBQ2", "HCM_01", "1483632816", 60),
        ("HTHTI.TIENTN4", "HTH", "1484520256", 120),
        # Candidate KTVs for currently unassigned jobs.
        ("HYN02.VANNH2", "HYN", "1483947506", 60),
        ("HYN02.DEMO2", "HYN", "1483947506", 0),
        ("FTI_HCM.AGI", "FTI_HCM", "1480302956", 0),
        ("FTI_HCM.DNI", "FTI_HCM", "1483410286", 120),
        ("HNI05.DEMO", "HNI_05", "1482626336", 0),
    ]
    result: list[dict] = []
    for account, branch, anchor_job_id, workload in rows:
        point = location_by_job.get(anchor_job_id)
        result.append(
            {
                "EMP_ACCOUNT": account,
                "BRANCH_NAME": branch,
                "LATITUDE": point.latitude if point else None,
                "LONGITUDE": point.longitude if point else None,
                "SHIFT_START": "2026-06-30 06:00:00",
                "SHIFT_END": "2026-06-30 18:00:00",
                "SUPPORTED_CASE_TYPES": "MAINTENANCE",
                "EXISTING_WORKLOAD_MINUTES": workload,
            }
        )
    return pd.DataFrame(result)


def main() -> int:
    source = QosMaintenanceCsvSource(PROJECT_ROOT / "data")
    maintenance = source.read_maintenance(
        usecols=[
            "CHECKLIST_ID",
            "CHECKLIST_STATUS",
            "BRANCH_NAME",
            "CASE_TYPE",
            "OBJ_LOCATION",
            "EMP_ACCOUNT",
            "CREATE_DATE",
        ]
    )
    boundaries = WardBoundaryIndex.from_geojson(
        PROJECT_ROOT / "data" / "boundary_2026-07-31.geojson"
    )
    jobs = maintenance_to_optimization_jobs(maintenance, boundaries)
    technicians = shift_roster_to_technicians(build_demo_roster(jobs))

    for mode in CompatibilityMode:
        result = SimpleKtvOptimizer(
            mode=mode,
            max_distance_km=30,
        ).optimize(
            jobs,
            technicians,
            planning_time=datetime(2026, 6, 30, 6),
        )
        print(f"\n=== {mode.value} ===")
        for decision in result.assignments:
            print(
                decision.source,
                decision.job_id,
                "->",
                decision.technician_id,
                f"cost={decision.assignment_cost:.3f}",
            )
        for decision in result.unassigned:
            print("UNASSIGNED", decision.job_id, decision.reason)
        for route in result.routes:
            sequence = " -> ".join(stop.job_id for stop in route.stops)
            print(
                "ROUTE",
                route.technician_id,
                sequence,
                f"distance={route.total_distance_km:.3f}km",
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

