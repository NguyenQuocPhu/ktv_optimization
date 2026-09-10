"""Replay ordered checklist-event snapshots with CSV optimizer state."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path

import pandas as pd

from ktv_optimizer.data.mappers import (
    maintenance_to_optimization_jobs,
    shift_roster_to_status_updates,
    shift_roster_to_technicians,
)
from ktv_optimizer.data.mappers.optimization_mapper import (
    EVENT_TRACKING_STATUSES,
)
from ktv_optimizer.data.sources.administrative_boundaries import (
    WardBoundaryIndex,
)
from ktv_optimizer.optimization import (
    AssignmentDecision,
    CompatibilityMode,
    SimpleKtvOptimizer,
    UnassignedDecision,
)
from ktv_optimizer.pipeline.output import snapshot_summary
from ktv_optimizer.state import (
    CsvStateStore,
    CsvTechnicianStateStore,
    Snapshot,
    SnapshotReducer,
)
from ktv_optimizer.state.snapshot import (
    SnapshotChange,
    SnapshotConflict,
    TechnicianJobQueueItem,
    TechnicianStateRecord,
)


MANIFEST_COLUMNS = {
    "SNAPSHOT_ID",
    "SNAPSHOT_TIME",
    "MAINTENANCE_CSV",
    "SHIFT_ROSTER_CSV",
}
MAINTENANCE_COLUMNS = [
    "CHECKLIST_ID",
    "CHECKLIST_STATUS",
    "BRANCH_NAME",
    "CASE_TYPE",
    "OBJ_LOCATION",
    "EMP_ACCOUNT",
    "CREATE_DATE",
    "FINISH_DATE",
    "FLAG_ON_TIME",
]
JOB_OUTPUT_COLUMNS = [
    "CHECKLIST_ID",
    "CHECKLIST_STATUS",
    "BRANCH_NAME",
    "CASE_TYPE",
    "OBJ_LOCATION",
    "LATITUDE",
    "LONGITUDE",
    "EMP_ACCOUNT",
    "CREATED_AT",
    "DUE_AT",
    "FINISH_DATE",
    "FLAG_ON_TIME",
    "WARD_CODE",
    "WARD_NAME",
    "PROVINCE_NAME",
]
ROUTE_OUTPUT_COLUMNS = [
    "TECHNICIAN_ID",
    "SEQUENCE",
    "CHECKLIST_ID",
    "LATITUDE",
    "LONGITUDE",
    "LEG_DISTANCE_KM",
    "ESTIMATED_ARRIVAL",
    "ESTIMATED_FINISH",
    "ROUTE_TOTAL_DISTANCE_KM",
    "ROUTE_TOTAL_TRAVEL_MINUTES",
    "ROUTE_TOTAL_SERVICE_MINUTES",
]
COMPLETED_JOB_OUTPUT_COLUMNS = [
    "CHECKLIST_ID",
    "COMPLETION_STATUS",
    "COMPLETION_EVENT_TIME",
    "BRANCH_NAME",
    "CASE_TYPE",
    "OBJ_LOCATION",
    "EMP_ACCOUNT",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--boundary",
        type=Path,
        default=Path("data/boundary_2026-07-31.geojson"),
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path("data/runtime/optimizer_state.csv"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/snapshot_replay_v1"),
    )
    parser.add_argument(
        "--technician-state-file",
        type=Path,
        default=Path("data/runtime/technician_state.csv"),
    )
    parser.add_argument(
        "--mode",
        choices=[mode.value for mode in CompatibilityMode],
        default=CompatibilityMode.TASK_LOCATION.value,
    )
    parser.add_argument("--max-distance-km", type=float, default=30)
    parser.add_argument("--average-speed-kmh", type=float, default=30)
    parser.add_argument(
        "--reset-state",
        action="store_true",
        help="Xóa checkpoint trước khi replay manifest từ đầu.",
    )
    return parser.parse_args()


def _resolve_path(value: object, manifest_dir: Path) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else manifest_dir / path


def _safe_snapshot_dir(snapshot_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", snapshot_id).strip("._")
    if not safe:
        raise ValueError("SNAPSHOT_ID không tạo được tên thư mục hợp lệ")
    return safe


def _route_rows(result) -> list[dict]:
    return [
        {
            "TECHNICIAN_ID": route.technician_id,
            "SEQUENCE": stop.sequence,
            "CHECKLIST_ID": stop.job_id,
            "LATITUDE": stop.location.latitude if stop.location else None,
            "LONGITUDE": stop.location.longitude if stop.location else None,
            "LEG_DISTANCE_KM": stop.leg_distance_km,
            "ESTIMATED_ARRIVAL": stop.estimated_arrival,
            "ESTIMATED_FINISH": stop.estimated_finish,
            "ROUTE_TOTAL_DISTANCE_KM": route.total_distance_km,
            "ROUTE_TOTAL_TRAVEL_MINUTES": route.total_travel_minutes,
            "ROUTE_TOTAL_SERVICE_MINUTES": route.total_service_minutes,
        }
        for route in result.routes
        for stop in route.stops
    ]


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
            "EMP_ACCOUNT": job.assigned_technician,
            "CREATED_AT": job.created_at,
            "DUE_AT": job.due_at,
            "FINISH_DATE": job.finished_at,
            "FLAG_ON_TIME": job.on_time_flag,
            "WARD_CODE": job.ward_code,
            "WARD_NAME": job.ward_name,
            "PROVINCE_NAME": job.province_name,
        }
        for job in jobs
    ]


def _write_dataclasses(path: Path, values, data_class) -> None:
    columns = [field.name for field in fields(data_class)]
    pd.DataFrame([asdict(value) for value in values], columns=columns).to_csv(
        path, index=False, encoding="utf-8-sig"
    )


def _write_snapshot_output(output_dir: Path, run) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        _job_rows(run.snapshot.jobs), columns=JOB_OUTPUT_COLUMNS
    ).to_csv(
        output_dir / "jobs.csv", index=False, encoding="utf-8-sig"
    )
    completed_jobs = [
        {
            "CHECKLIST_ID": job.checklist_id,
            "COMPLETION_STATUS": job.status,
            "COMPLETION_EVENT_TIME": run.snapshot.captured_at,
            "BRANCH_NAME": job.branch_name,
            "CASE_TYPE": job.task_type,
            "OBJ_LOCATION": job.address,
            "EMP_ACCOUNT": job.assigned_technician,
        }
        for job in run.completed_jobs
    ]
    pd.DataFrame(
        completed_jobs, columns=COMPLETED_JOB_OUTPUT_COLUMNS
    ).to_csv(
        output_dir / "completed_jobs.csv",
        index=False,
        encoding="utf-8-sig",
    )
    _write_dataclasses(
        output_dir / "changes.csv", run.changes, SnapshotChange
    )
    _write_dataclasses(
        output_dir / "conflicts.csv", run.conflicts, SnapshotConflict
    )
    _write_dataclasses(
        output_dir / "assignments.csv",
        run.optimization.assignments,
        AssignmentDecision,
    )
    _write_dataclasses(
        output_dir / "unassigned.csv",
        run.optimization.unassigned,
        UnassignedDecision,
    )
    _write_dataclasses(
        output_dir / "technicians.csv",
        run.technician_checkpoint.records,
        TechnicianStateRecord,
    )
    _write_dataclasses(
        output_dir / "technician_job_queue.csv",
        run.job_queue,
        TechnicianJobQueueItem,
    )
    pd.DataFrame(
        _route_rows(run.optimization), columns=ROUTE_OUTPUT_COLUMNS
    ).to_csv(
        output_dir / "routes.csv", index=False, encoding="utf-8-sig"
    )
    summary = snapshot_summary(run)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_manifest(path: Path) -> pd.DataFrame:
    manifest = pd.read_csv(path, encoding="utf-8-sig", dtype="string")
    missing = sorted(MANIFEST_COLUMNS - set(manifest.columns))
    if missing:
        raise ValueError("Manifest thiếu cột: " + ", ".join(missing))
    duplicated_ids = manifest["SNAPSHOT_ID"].duplicated(keep=False)
    if duplicated_ids.any():
        values = sorted(manifest.loc[duplicated_ids, "SNAPSHOT_ID"].unique())
        raise ValueError("SNAPSHOT_ID bị trùng: " + ", ".join(values))
    manifest["_SNAPSHOT_TIME"] = pd.to_datetime(
        manifest["SNAPSHOT_TIME"], errors="raise"
    )
    return manifest.sort_values(
        ["_SNAPSHOT_TIME", "SNAPSHOT_ID"], kind="stable"
    )


def main() -> int:
    args = parse_args()
    manifest = _load_manifest(args.manifest)
    manifest_dir = args.manifest.resolve().parent
    boundary_index = WardBoundaryIndex.from_geojson(args.boundary)
    store = CsvStateStore(args.state_file)
    technician_store = CsvTechnicianStateStore(args.technician_state_file)
    if args.reset_state:
        store.clear()
        technician_store.clear()
    reducer = SnapshotReducer(
        SimpleKtvOptimizer(
            mode=CompatibilityMode(args.mode),
            max_distance_km=args.max_distance_km,
            average_speed_kmh=args.average_speed_kmh,
        ),
        store,
        technician_store,
    )

    summaries = []
    for _, row in manifest.iterrows():
        snapshot_id = str(row["SNAPSHOT_ID"])
        captured_at = row["_SNAPSHOT_TIME"].to_pydatetime()
        maintenance_path = _resolve_path(
            row["MAINTENANCE_CSV"], manifest_dir
        )
        roster_path = _resolve_path(row["SHIFT_ROSTER_CSV"], manifest_dir)
        available_columns = set(
            pd.read_csv(
                maintenance_path, encoding="utf-8-sig", nrows=0
            ).columns
        )
        required_columns = set(MAINTENANCE_COLUMNS) - {
            "FINISH_DATE",
            "FLAG_ON_TIME",
        }
        missing_columns = sorted(required_columns - available_columns)
        if missing_columns:
            raise ValueError(
                "Maintenance event thiếu cột: " + ", ".join(missing_columns)
            )
        maintenance = pd.read_csv(
            maintenance_path,
            encoding="utf-8-sig",
            dtype="string",
            keep_default_na=False,
            usecols=[
                column
                for column in MAINTENANCE_COLUMNS
                if column in available_columns
            ],
        )
        roster = pd.read_csv(roster_path, encoding="utf-8-sig")
        jobs = maintenance_to_optimization_jobs(
            maintenance,
            boundary_index,
            deduplicate=False,
            statuses=EVENT_TRACKING_STATUSES,
        )
        technicians = shift_roster_to_technicians(roster)
        technician_updates = shift_roster_to_status_updates(roster)
        run = reducer.process(
            Snapshot(
                snapshot_id=snapshot_id,
                captured_at=captured_at,
                jobs=tuple(jobs),
                technicians=tuple(technicians),
                technician_updates=tuple(technician_updates),
            )
        )
        snapshot_output = args.output_dir / _safe_snapshot_dir(snapshot_id)
        _write_snapshot_output(snapshot_output, run)
        summary = json.loads(
            (snapshot_output / "summary.json").read_text(encoding="utf-8")
        )
        summaries.append(summary)
        print(
            f"{snapshot_id}: active={summary['active_jobs']}, "
            f"assigned={summary['assignments']}, "
            f"unassigned={summary['unassigned']}, "
            f"changes={summary['changes']}, "
            f"conflicts={summary['conflicts']}"
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "replay_summary.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"State: {args.state_file.resolve()}")
    print(f"Technician state: {args.technician_state_file.resolve()}")
    print(f"Output: {args.output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
