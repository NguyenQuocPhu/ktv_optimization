"""CLI: run V0 assignment/routing from maintenance, boundary and shift CSV."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import pandas as pd

from ktv_optimizer.data.mappers import (
    maintenance_to_optimization_jobs,
    shift_roster_to_technicians,
)
from ktv_optimizer.data.sources.administrative_boundaries import (
    WardBoundaryIndex,
)
from ktv_optimizer.data.sources.qos_maintenance import QosMaintenanceCsvSource
from ktv_optimizer.optimization import CompatibilityMode, SimpleKtvOptimizer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument(
        "--boundary",
        type=Path,
        default=Path("data/boundary_2026-07-31.geojson"),
    )
    parser.add_argument("--shift-roster", type=Path, required=True)
    parser.add_argument(
        "--mode",
        choices=[mode.value for mode in CompatibilityMode],
        default=CompatibilityMode.TASK_LOCATION.value,
    )
    parser.add_argument("--max-distance-km", type=float, default=30)
    parser.add_argument("--average-speed-kmh", type=float, default=30)
    parser.add_argument(
        "--planning-time",
        help="ISO datetime; mặc định là thời điểm chạy.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/optimization_v0"),
    )
    return parser.parse_args()


def _route_rows(result) -> list[dict]:
    rows: list[dict] = []
    for route in result.routes:
        for stop in route.stops:
            rows.append(
                {
                    "TECHNICIAN_ID": route.technician_id,
                    "SEQUENCE": stop.sequence,
                    "CHECKLIST_ID": stop.job_id,
                    "LATITUDE": (
                        stop.location.latitude if stop.location else None
                    ),
                    "LONGITUDE": (
                        stop.location.longitude if stop.location else None
                    ),
                    "LEG_DISTANCE_KM": stop.leg_distance_km,
                    "ESTIMATED_ARRIVAL": stop.estimated_arrival,
                    "ESTIMATED_FINISH": stop.estimated_finish,
                    "ROUTE_TOTAL_DISTANCE_KM": route.total_distance_km,
                    "ROUTE_TOTAL_TRAVEL_MINUTES": route.total_travel_minutes,
                    "ROUTE_TOTAL_SERVICE_MINUTES": route.total_service_minutes,
                }
            )
    return rows


def _job_rows(jobs) -> list[dict]:
    return [
        {
            "CHECKLIST_ID": job.checklist_id,
            "CHECKLIST_STATUS": job.status,
            "BRANCH_NAME": job.branch_name,
            "CASE_TYPE": job.task_type,
            "OBJ_LOCATION": job.address,
            "LATITUDE": job.location.latitude if job.location else None,
            "LONGITUDE": job.location.longitude if job.location else None,
            "WARD_CODE": job.ward_code,
            "WARD_NAME": job.ward_name,
            "PROVINCE_NAME": job.province_name,
            "LOCATION_CONFIDENCE": job.location_confidence,
            "LOCATION_METHOD": job.location_method,
            "EMP_ACCOUNT": job.assigned_technician,
            "PRIORITY": job.priority,
            "SERVICE_MINUTES": job.service_minutes,
            "CREATED_AT": job.created_at,
            "DUE_AT": job.due_at,
        }
        for job in jobs
    ]


def main() -> int:
    args = parse_args()
    planning_time = (
        datetime.fromisoformat(args.planning_time)
        if args.planning_time
        else datetime.now()
    )

    maintenance = QosMaintenanceCsvSource(args.data_dir).read_maintenance(
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
    boundary_index = WardBoundaryIndex.from_geojson(args.boundary)
    jobs = maintenance_to_optimization_jobs(maintenance, boundary_index)
    roster = pd.read_csv(
        args.shift_roster,
        dtype={"EMP_ACCOUNT": "string", "BRANCH_NAME": "string"},
    )
    technicians = shift_roster_to_technicians(roster)

    optimizer = SimpleKtvOptimizer(
        mode=CompatibilityMode(args.mode),
        max_distance_km=args.max_distance_km,
        average_speed_kmh=args.average_speed_kmh,
    )
    result = optimizer.optimize(
        jobs,
        technicians,
        planning_time=planning_time,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(_job_rows(jobs)).to_csv(
        args.output_dir / "jobs.csv",
        index=False,
        encoding="utf-8-sig",
    )
    pd.DataFrame(
        [asdict(edge) for edge in result.candidate_edges]
    ).to_csv(
        args.output_dir / "candidate_edges.csv",
        index=False,
        encoding="utf-8-sig",
    )
    pd.DataFrame(
        [asdict(decision) for decision in result.assignments]
    ).to_csv(
        args.output_dir / "assignments.csv",
        index=False,
        encoding="utf-8-sig",
    )
    pd.DataFrame(
        [asdict(decision) for decision in result.unassigned]
    ).to_csv(
        args.output_dir / "unassigned.csv",
        index=False,
        encoding="utf-8-sig",
    )
    pd.DataFrame(_route_rows(result)).to_csv(
        args.output_dir / "routes.csv",
        index=False,
        encoding="utf-8-sig",
    )

    print(f"Mode: {result.mode.value}")
    print(f"Jobs: {len(jobs):,}")
    print(f"Technicians: {len(technicians):,}")
    print(f"Candidate edges: {len(result.candidate_edges):,}")
    print(f"Assignments: {len(result.assignments):,}")
    print(f"Unassigned: {len(result.unassigned):,}")
    print(f"Routes: {len(result.routes):,}")
    print(f"Output: {args.output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
