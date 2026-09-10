#!/usr/bin/env python3
"""Prepare and stress-test multi-snapshot optimization with real branch rows.

The generated shift roster is a TEST PROXY inferred from historical
``EMP_ACCOUNT`` and the centroid of a recent job address. It must not be treated
as proof that a technician was on shift or at that coordinate.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ktv_optimizer.data.sources.administrative_boundaries import (  # noqa: E402
    WardBoundaryIndex,
)
from ktv_optimizer.optimization import CompatibilityMode  # noqa: E402
from ktv_optimizer.pipeline import (  # noqa: E402
    OperationalSnapshotProcessor,
    PipelineConfig,
)


PIPELINE_COLUMNS = [
    "CHECKLIST_ID",
    "CHECKLIST_STATUS",
    "BRANCH_NAME",
    "CASE_TYPE",
    "OBJ_LOCATION",
    "EMP_ACCOUNT",
    "CREATE_DATE",
]
ROSTER_COLUMNS = [
    "EMP_ACCOUNT",
    "BRANCH_NAME",
    "LATITUDE",
    "LONGITUDE",
    "SHIFT_START",
    "SHIFT_END",
    "SUPPORTED_CASE_TYPES",
    "EXISTING_WORKLOAD_MINUTES",
    "IS_SHIFT_ROSTER_PROXY",
    "LOCATION_SOURCE",
    "SOURCE_CHECKLIST_ID",
]


def _json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _non_empty_workspace(path: Path) -> bool:
    return path.exists() and any(path.iterdir())


def _read_branch(path: Path, branch: str) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for chunk in pd.read_csv(
        path,
        encoding="utf-8-sig",
        dtype="string",
        chunksize=75_000,
        low_memory=False,
    ):
        if "BRANCH_NAME" not in chunk:
            raise ValueError("Maintenance thiếu cột BRANCH_NAME")
        selected = chunk[chunk["BRANCH_NAME"].eq(branch)]
        if not selected.empty:
            parts.append(selected.copy())
    if not parts:
        raise ValueError(f"Không tìm thấy branch: {branch}")
    return pd.concat(parts, ignore_index=True)


def _canonicalize(frame: pd.DataFrame) -> pd.DataFrame:
    missing = sorted(set(PIPELINE_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError("Maintenance thiếu cột: " + ", ".join(missing))
    canonical = frame[PIPELINE_COLUMNS].copy()
    canonical["_HAS_ADDRESS"] = canonical["OBJ_LOCATION"].notna()
    canonical["_HAS_EMP"] = canonical["EMP_ACCOUNT"].notna()
    canonical["_CREATED_AT"] = pd.to_datetime(
        canonical["CREATE_DATE"], errors="coerce", format="mixed"
    )
    canonical = canonical.sort_values(
        ["CHECKLIST_ID", "_HAS_ADDRESS", "_HAS_EMP", "_CREATED_AT"],
        kind="stable",
    ).drop_duplicates("CHECKLIST_ID", keep="last")
    return canonical.reset_index(drop=True)


def _address_matches(
    frame: pd.DataFrame,
    boundary: WardBoundaryIndex,
) -> dict[str, Any]:
    addresses = sorted(
        {
            str(value).strip()
            for value in frame["OBJ_LOCATION"].dropna()
            if str(value).strip()
        }
    )
    return {address: boundary.match(address) for address in addresses}


def _match_for(row: pd.Series, matches: dict[str, Any]) -> Any:
    value = row.get("OBJ_LOCATION")
    if pd.isna(value):
        return None
    return matches.get(str(value).strip())


def _write_light_boundary(
    boundary: WardBoundaryIndex,
    path: Path,
) -> None:
    size = 0.00001
    features = []
    for item in boundary.boundaries:
        longitude = item.point.longitude
        latitude = item.point.latitude
        ring = [
            [longitude - size, latitude - size],
            [longitude + size, latitude - size],
            [longitude + size, latitude + size],
            [longitude - size, latitude + size],
            [longitude - size, latitude - size],
        ]
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "ma_xa": item.ward_code,
                    "ten_xa": item.ward_name,
                    "tinh_tp": item.province_name,
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [ring],
                },
            }
        )
    _json_write(path, {"type": "FeatureCollection", "features": features})


def _build_roster(
    branch_rows: pd.DataFrame,
    canonical: pd.DataFrame,
    matches: dict[str, Any],
    *,
    branch: str,
    snapshot_start: datetime,
) -> pd.DataFrame:
    technician_ids = sorted(
        {
            str(value).strip()
            for value in branch_rows["EMP_ACCOUNT"].dropna()
            if str(value).strip()
        }
    )
    located = []
    for _, row in canonical.iterrows():
        technician_id = row.get("EMP_ACCOUNT")
        if pd.isna(technician_id) or not str(technician_id).strip():
            continue
        match = _match_for(row, matches)
        if match is None:
            continue
        located.append(
            {
                "EMP_ACCOUNT": str(technician_id).strip(),
                "CHECKLIST_ID": str(row["CHECKLIST_ID"]),
                "CREATED_AT": row["_CREATED_AT"],
                "LATITUDE": match.point.latitude,
                "LONGITUDE": match.point.longitude,
            }
        )
    locations = pd.DataFrame(located)
    if locations.empty:
        raise ValueError("Không geocode được địa chỉ nào để tạo roster proxy")
    latest = (
        locations.sort_values("CREATED_AT", kind="stable")
        .drop_duplicates("EMP_ACCOUNT", keep="last")
        .set_index("EMP_ACCOUNT")
    )
    fallback_latitude = float(locations["LATITUDE"].median())
    fallback_longitude = float(locations["LONGITUDE"].median())
    shift_start = snapshot_start.replace(hour=6, minute=0, second=0)
    shift_end = shift_start + timedelta(hours=16)
    rows = []
    for technician_id in technician_ids:
        if technician_id in latest.index:
            location = latest.loc[technician_id]
            latitude = float(location["LATITUDE"])
            longitude = float(location["LONGITUDE"])
            source = "RECENT_HISTORICAL_JOB_WARD_CENTROID"
            source_job = str(location["CHECKLIST_ID"])
        else:
            latitude = fallback_latitude
            longitude = fallback_longitude
            source = "BRANCH_MEDIAN_WARD_CENTROID_FALLBACK"
            source_job = None
        rows.append(
            {
                "EMP_ACCOUNT": technician_id,
                "BRANCH_NAME": branch,
                "LATITUDE": latitude,
                "LONGITUDE": longitude,
                "SHIFT_START": shift_start.isoformat(sep=" "),
                "SHIFT_END": shift_end.isoformat(sep=" "),
                "SUPPORTED_CASE_TYPES": None,
                "EXISTING_WORKLOAD_MINUTES": 0,
                "IS_SHIFT_ROSTER_PROXY": True,
                "LOCATION_SOURCE": source,
                "SOURCE_CHECKLIST_ID": source_job,
            }
        )
    return pd.DataFrame(rows, columns=ROSTER_COLUMNS)


def prepare(args: argparse.Namespace) -> int:
    if args.jobs <= 0 or args.add_jobs < 0:
        raise ValueError("--jobs phải > 0 và --add-jobs phải >= 0")
    workspace = args.output_dir.resolve()
    if _non_empty_workspace(workspace):
        raise FileExistsError(
            f"Workspace đã có dữ liệu: {workspace}; hãy chọn thư mục mới"
        )
    inputs = workspace / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    print(f"[PREPARE] Đọc toàn bộ branch {args.branch}...")
    branch_rows = _read_branch(args.maintenance, args.branch)
    branch_rows.to_csv(
        inputs / "source_branch_all.csv",
        index=False,
        encoding="utf-8-sig",
    )
    canonical = _canonicalize(branch_rows)

    print("[PREPARE] Load địa giới và geocode địa chỉ thật...")
    boundary = WardBoundaryIndex.from_geojson(args.boundary)
    matches = _address_matches(canonical, boundary)
    canonical["_MATCH"] = canonical.apply(
        lambda row: _match_for(row, matches), axis=1
    )
    canonical["LATITUDE"] = canonical["_MATCH"].map(
        lambda match: match.point.latitude if match else None
    )
    canonical["LONGITUDE"] = canonical["_MATCH"].map(
        lambda match: match.point.longitude if match else None
    )
    canonical["WARD_CODE"] = canonical["_MATCH"].map(
        lambda match: match.ward_code if match else None
    )
    canonical["WARD_NAME"] = canonical["_MATCH"].map(
        lambda match: match.ward_name if match else None
    )
    canonical["LOCATION_METHOD"] = canonical["_MATCH"].map(
        lambda match: match.method if match else None
    )
    canonical["SOURCE_CHECKLIST_STATUS"] = canonical["CHECKLIST_STATUS"]
    canonical["SOURCE_EMP_ACCOUNT"] = canonical["EMP_ACCOUNT"]
    serializable = canonical.drop(
        columns=["_MATCH", "_HAS_ADDRESS", "_HAS_EMP", "_CREATED_AT"]
    )
    serializable.to_csv(
        inputs / "source_jobs_canonical.csv",
        index=False,
        encoding="utf-8-sig",
    )
    geocoded = canonical[canonical["_MATCH"].notna()].copy()
    required_pool = args.jobs + args.add_jobs
    if len(geocoded) < required_pool:
        raise ValueError(
            f"Branch chỉ có {len(geocoded):,} checklist geocode được; "
            f"cần {required_pool:,}"
        )
    pool = geocoded.sort_values(
        ["_CREATED_AT", "CHECKLIST_ID"], ascending=[False, True]
    ).head(required_pool)
    pool = pool[PIPELINE_COLUMNS].copy()
    source_lookup = canonical.set_index("CHECKLIST_ID")
    pool["SOURCE_CHECKLIST_STATUS"] = pool["CHECKLIST_ID"].map(
        source_lookup["CHECKLIST_STATUS"]
    )
    pool["SOURCE_EMP_ACCOUNT"] = pool["CHECKLIST_ID"].map(
        source_lookup["EMP_ACCOUNT"]
    )
    pool.to_csv(
        inputs / "scenario_job_pool.csv",
        index=False,
        encoding="utf-8-sig",
    )

    snapshot_start = datetime.fromisoformat(args.snapshot_start)
    roster = _build_roster(
        branch_rows,
        canonical,
        matches,
        branch=args.branch,
        snapshot_start=snapshot_start,
    )
    roster.to_csv(
        inputs / "roster_proxy_all.csv",
        index=False,
        encoding="utf-8-sig",
    )
    _write_light_boundary(boundary, inputs / "boundary_centroids.geojson")

    metadata = {
        "created_at": datetime.now().astimezone().isoformat(),
        "branch": args.branch,
        "snapshot_start": snapshot_start.isoformat(),
        "base_jobs": args.jobs,
        "add_jobs": args.add_jobs,
        "source_branch_rows": len(branch_rows),
        "source_unique_checklists": canonical["CHECKLIST_ID"].nunique(),
        "source_technicians": len(roster),
        "geocoded_checklists": len(geocoded),
        "geocode_rate": round(len(geocoded) / max(1, len(canonical)), 6),
        "maintenance_source": str(args.maintenance.resolve()),
        "boundary_source": str(args.boundary.resolve()),
        "roster_warning": (
            "TEST PROXY inferred from historical EMP_ACCOUNT and recent job "
            "ward centroid; not a real shift roster or live KTV GPS"
        ),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    _json_write(workspace / "prepare_metadata.json", metadata)
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    print(f"[PREPARE] Workspace: {workspace}")
    return 0


class ScenarioRunner:
    SCENARIOS = (
        ("REAL-S001", "BOOTSTRAP_LARGE_BACKLOG", "Nạp batch checklist thật."),
        ("REAL-S002", "ADD_JOB_BATCH", "Thêm một batch checklist mới."),
        ("REAL-S003", "COMPLETE_AND_REFILL", "Đóng job và xét lại backlog."),
        ("REAL-S004", "SYSTEM_ASSIGNMENT_OVERRIDE", "Nguồn ghi đè assignment."),
        ("REAL-S005", "START_JOBS_AND_MARK_BUSY", "Job bắt đầu, KTV chuyển BUSY."),
        ("REAL-S006", "ROSTER_SHORTAGE", "Một số KTV biến mất khỏi roster."),
        ("REAL-S007", "ROSTER_RECOVERY_AND_COMPLETE", "Roster phục hồi và replan."),
    )

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.workspace = args.workspace.resolve()
        self.inputs = self.workspace / "inputs"
        self.snapshots = self.workspace / "snapshots"
        self.rosters = self.workspace / "rosters"
        self.reports = self.workspace / "reports"
        self.runs = self.workspace / "runs"
        self.runtime = self.workspace / "runtime"
        self.log_path = self.reports / "scenario_log.jsonl"
        self.metadata = json.loads(
            (self.workspace / "prepare_metadata.json").read_text(
                encoding="utf-8"
            )
        )
        if (self.runtime / "latest.json").exists():
            raise FileExistsError(
                "Workspace đã chạy replay; hãy prepare một workspace mới"
            )
        self.pool = pd.read_csv(
            self.inputs / "scenario_job_pool.csv",
            encoding="utf-8-sig",
            dtype="string",
        )
        self.roster = pd.read_csv(
            self.inputs / "roster_proxy_all.csv",
            encoding="utf-8-sig",
        )
        self.base_jobs = int(self.metadata["base_jobs"])
        self.add_jobs = int(self.metadata["add_jobs"])
        self.current = self.pool.head(self.base_jobs).copy()
        self.current["CHECKLIST_STATUS"] = "Chưa phân công"
        self.current["EMP_ACCOUNT"] = pd.NA
        self.snapshot_start = datetime.fromisoformat(
            self.metadata["snapshot_start"]
        )
        self.processor = OperationalSnapshotProcessor(
            boundary_path=self.inputs / "boundary_centroids.geojson",
            runtime_dir=self.runtime,
            output_root=self.runs,
            config=PipelineConfig(
                mode=CompatibilityMode(args.mode),
                max_distance_km=args.max_distance_km,
                average_speed_kmh=args.average_speed_kmh,
            ),
        )
        self.summaries: list[dict[str, Any]] = []
        self.manifest: list[dict[str, Any]] = []
        self.last_run = None
        self.completed_ids: list[str] = []
        self.override_ids: list[str] = []
        self.override_technicians: dict[str, str] = {}
        self.started_ids: list[str] = []
        self.snapshots.mkdir(parents=True, exist_ok=True)
        self.rosters.mkdir(parents=True, exist_ok=True)
        self.reports.mkdir(parents=True, exist_ok=True)

    def log(self, event: str, **values: Any) -> None:
        record = {
            "logged_at": datetime.now().astimezone().isoformat(),
            "event": event,
            **values,
        }
        line = json.dumps(record, ensure_ascii=False)
        print(f"[E2E] {line}")
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def run_snapshot(
        self,
        sequence: int,
        scenario: str,
        description: str,
        *,
        roster: pd.DataFrame | None = None,
    ):
        snapshot_id = f"REAL-S{sequence:03d}"
        snapshot_time = self.snapshot_start + timedelta(hours=sequence - 1)
        snapshot_path = self.snapshots / f"{snapshot_id}.csv"
        roster_path = self.rosters / f"{snapshot_id}.csv"
        self.current.to_csv(
            snapshot_path, index=False, encoding="utf-8-sig"
        )
        active_roster = self.roster if roster is None else roster
        active_roster.to_csv(
            roster_path, index=False, encoding="utf-8-sig"
        )
        self.log(
            "SCENARIO_START",
            snapshot_id=snapshot_id,
            scenario=scenario,
            description=description,
            snapshot_rows=len(self.current),
            roster_rows=len(active_roster),
        )
        started = time.perf_counter()
        run = self.processor.process(
            snapshot_id=snapshot_id,
            snapshot_time=snapshot_time,
            maintenance_path=snapshot_path,
            roster_path=roster_path,
        )
        queue_path = run.output_dir / "technician_job_queue.csv"
        if not queue_path.is_file():
            raise AssertionError("Run thiếu technician_job_queue.csv")
        elapsed = round(time.perf_counter() - started, 3)
        counts = dict(run.metadata["counts"])
        if counts["job_queue_items"] < counts["assignments"]:
            raise AssertionError("Queue không chứa đủ assignment của run")
        summary = {
            "snapshot_id": snapshot_id,
            "snapshot_time": snapshot_time.isoformat(),
            "scenario": scenario,
            "description": description,
            "elapsed_seconds": elapsed,
            **counts,
            "checkpoint_id": run.checkpoint.checkpoint_id,
            "run_metadata_path": str(run.output_dir / "run_metadata.json"),
        }
        self.summaries.append(summary)
        self.manifest.append(
            {
                "SNAPSHOT_ID": snapshot_id,
                "SNAPSHOT_TIME": snapshot_time.isoformat(),
                "MAINTENANCE_CSV": str(snapshot_path.relative_to(self.workspace)),
                "SHIFT_ROSTER_CSV": str(roster_path.relative_to(self.workspace)),
                "SCENARIO": scenario,
                "DESCRIPTION": description,
            }
        )
        latest = json.loads(
            (self.runtime / "latest.json").read_text(encoding="utf-8")
        )
        if latest["snapshot_id"] != snapshot_id:
            raise AssertionError("latest.json không trỏ tới snapshot vừa chạy")
        self.log(
            "SCENARIO_SUCCESS",
            snapshot_id=snapshot_id,
            scenario=scenario,
            elapsed_seconds=elapsed,
            active_jobs=counts["active_jobs"],
            assignments=counts["assignments"],
            unassigned=counts["unassigned"],
            conflicts=counts["conflicts"],
        )
        return run

    @staticmethod
    def assignment_map(run) -> dict[str, str]:
        return {
            item.job_id: item.technician_id
            for item in run.result.optimization.assignments
        }

    def set_jobs(
        self,
        checklist_ids: list[str],
        *,
        status: str,
        technicians: dict[str, str] | None = None,
    ) -> None:
        index = self.current["CHECKLIST_ID"].isin(checklist_ids)
        self.current.loc[index, "CHECKLIST_STATUS"] = status
        if technicians is None:
            self.current.loc[index, "EMP_ACCOUNT"] = pd.NA
            return
        self.current.loc[index, "EMP_ACCOUNT"] = self.current.loc[
            index, "CHECKLIST_ID"
        ].map(technicians)

    def _write_progress(self, *, complete: bool = False) -> None:
        pd.DataFrame(self.summaries).to_csv(
            self.reports / "scenario_summary.csv",
            index=False,
            encoding="utf-8-sig",
        )
        pd.DataFrame(self.manifest).to_csv(
            self.workspace / "manifest.csv",
            index=False,
            encoding="utf-8-sig",
        )
        if not complete:
            return
        validation = {
            "status": "PASS",
            "scenarios": len(self.summaries),
            "branch": self.metadata["branch"],
            "base_jobs": self.base_jobs,
            "added_jobs": self.add_jobs,
            "technicians": len(self.roster),
            "final_snapshot": self.summaries[-1]["snapshot_id"],
            "latest_checkpoint": self.summaries[-1]["checkpoint_id"],
            "total_elapsed_seconds": round(
                sum(item["elapsed_seconds"] for item in self.summaries), 3
            ),
            "assertions": [
                "full snapshot count",
                "ADDED diff",
                "COMPLETED lifecycle event",
                "system assignment conflict",
                "BUSY technician state",
                "multi-job queue output",
                "roster shortage conflict",
                "atomic latest checkpoint",
            ],
        }
        _json_write(self.reports / "validation.json", validation)
        self.log("E2E_PASS", **validation)

    def run_next(self):
        """Run exactly one scenario; intended for both CLI and web clicks."""

        sequence = len(self.summaries) + 1
        if sequence > len(self.SCENARIOS):
            raise ValueError("Đã chạy hết bảy snapshot")

        if sequence == 1:
            run = self.run_snapshot(
                1,
                "BOOTSTRAP_LARGE_BACKLOG",
                "Nạp batch checklist thật; optimizer tạo multi-job queue "
                "theo capacity.",
            )
            if run.metadata["counts"]["active_jobs"] != self.base_jobs:
                raise AssertionError("S001 không giữ đúng số job cấu hình")
        elif sequence == 2:
            additions = self.pool.iloc[
                self.base_jobs : self.base_jobs + self.add_jobs
            ].copy()
            additions["CHECKLIST_STATUS"] = "Chưa phân công"
            additions["EMP_ACCOUNT"] = pd.NA
            self.current = pd.concat(
                [self.current, additions], ignore_index=True
            )
            run = self.run_snapshot(
                2,
                "ADD_JOB_BATCH",
                f"Thêm {len(additions):,} checklist thật vào full snapshot.",
            )
            added_changes = sum(
                item.change_type == "ADDED" for item in run.result.changes
            )
            if added_changes != len(additions):
                raise AssertionError("S002 diff ADDED không khớp batch mới")
        elif sequence == 3:
            assignments = self.assignment_map(self.last_run)
            transition_count = min(
                self.args.transition_count, len(assignments)
            )
            self.completed_ids = sorted(assignments)[:transition_count]
            self.set_jobs(
                self.completed_ids,
                status="Đóng checklist",
                technicians=assignments,
            )
            run = self.run_snapshot(
                3,
                "COMPLETE_AND_REFILL",
                f"Đóng {len(self.completed_ids)} job; KTV rảnh xét lại backlog.",
            )
            completed_changes = sum(
                item.change_type == "COMPLETED"
                for item in run.result.changes
            )
            if completed_changes != len(self.completed_ids):
                raise AssertionError(
                    "S003 COMPLETED không khớp job hoàn thành"
                )
        elif sequence == 4:
            assignments = self.assignment_map(self.last_run)
            transition_count = min(
                self.args.transition_count, len(assignments)
            )
            self.override_ids = sorted(assignments)[:transition_count]
            roster_ids = sorted(self.roster["EMP_ACCOUNT"].astype(str))
            self.override_technicians = {
                checklist_id: roster_ids[index % min(3, len(roster_ids))]
                for index, checklist_id in enumerate(self.override_ids)
            }
            self.set_jobs(
                self.override_ids,
                status="Đã phân công",
                technicians=self.override_technicians,
            )
            run = self.run_snapshot(
                4,
                "SYSTEM_ASSIGNMENT_OVERRIDE",
                f"Nguồn hệ thống ghi đè assignment của {len(self.override_ids)} job.",
            )
            if not run.result.conflicts:
                raise AssertionError("S004 phải phát hiện assignment conflict")
        elif sequence == 5:
            self.started_ids = self.override_ids[
                : max(1, len(self.override_ids) // 2)
            ]
            self.set_jobs(
                self.started_ids,
                status="Đang xử lý",
                technicians=self.override_technicians,
            )
            run = self.run_snapshot(
                5,
                "START_JOBS_AND_MARK_BUSY",
                f"Chuyển {len(self.started_ids)} job sang Đang xử lý.",
            )
            busy_count = run.metadata["counts"]["technician_statuses"]["BUSY"]
            if busy_count < 1:
                raise AssertionError("S005 phải có ít nhất một KTV BUSY")
        elif sequence == 6:
            absent_ids = sorted(
                {
                    self.override_technicians[job_id]
                    for job_id in self.started_ids
                }
            )
            shortage_roster = self.roster[
                ~self.roster["EMP_ACCOUNT"].astype(str).isin(absent_ids)
            ].copy()
            run = self.run_snapshot(
                6,
                "ROSTER_SHORTAGE",
                f"Mô phỏng {len(absent_ids)} KTV không còn trong roster snapshot.",
                roster=shortage_roster,
            )
            if not run.result.conflicts:
                raise AssertionError("S006 phải ghi nhận roster conflict")
        else:
            self.set_jobs(
                self.started_ids,
                status="Đóng checklist",
                technicians=self.override_technicians,
            )
            run = self.run_snapshot(
                7,
                "ROSTER_RECOVERY_AND_COMPLETE",
                "Khôi phục roster đầy đủ, đóng job đang xử lý và replan backlog.",
            )
            if run.metadata["status"] != "SUCCESS":
                raise AssertionError("S007 không hoàn tất")

        self.last_run = run
        self._write_progress(complete=sequence == len(self.SCENARIOS))
        return run

    def execute(self) -> int:
        while len(self.summaries) < len(self.SCENARIOS):
            self.run_next()
        print(pd.DataFrame(self.summaries).to_string(index=False))
        print(f"[E2E] Reports: {self.reports}")
        return 0


def inspect_workspace(args: argparse.Namespace) -> int:
    workspace = args.workspace.resolve()
    metadata = json.loads(
        (workspace / "prepare_metadata.json").read_text(encoding="utf-8")
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    summary_path = workspace / "reports" / "scenario_summary.csv"
    if summary_path.exists():
        summary = pd.read_csv(summary_path, encoding="utf-8-sig")
        columns = [
            "snapshot_id",
            "scenario",
            "active_jobs",
            "assignments",
            "unassigned",
            "changes",
            "conflicts",
            "elapsed_seconds",
        ]
        print("\nScenario summary:")
        print(summary[columns].to_string(index=False))
        validation = json.loads(
            (workspace / "reports" / "validation.json").read_text(
                encoding="utf-8"
            )
        )
        print("\nValidation:")
        print(json.dumps(validation, ensure_ascii=False, indent=2))
    else:
        print("\nWorkspace mới prepare, chưa chạy replay.")
    return 0


def list_branches(args: argparse.Namespace) -> int:
    frame = pd.read_csv(
        args.maintenance,
        encoding="utf-8-sig",
        dtype="string",
        usecols=["BRANCH_NAME", "CHECKLIST_ID", "EMP_ACCOUNT"],
    )
    summary = (
        frame.groupby("BRANCH_NAME", dropna=False)
        .agg(
            rows=("CHECKLIST_ID", "size"),
            checklists=("CHECKLIST_ID", "nunique"),
            historical_technicians=("EMP_ACCOUNT", "nunique"),
        )
        .sort_values("rows", ascending=False)
        .head(args.top)
    )
    print(summary.to_string())
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    branches_parser = commands.add_parser(
        "branches", help="Liệt kê branch lớn để chọn stress-test."
    )
    branches_parser.add_argument(
        "--maintenance",
        type=Path,
        default=Path("data/QOS_MAINTENANCE_utf8.csv"),
    )
    branches_parser.add_argument("--top", type=int, default=20)

    prepare_parser = commands.add_parser(
        "prepare", help="Lọc branch, tạo source pool và roster proxy."
    )
    prepare_parser.add_argument(
        "--maintenance",
        type=Path,
        default=Path("data/QOS_MAINTENANCE_utf8.csv"),
    )
    prepare_parser.add_argument(
        "--boundary",
        type=Path,
        default=Path("data/boundary_2026-07-31.geojson"),
    )
    prepare_parser.add_argument("--branch", default="HNI_04")
    prepare_parser.add_argument("--jobs", type=int, default=3_000)
    prepare_parser.add_argument("--add-jobs", type=int, default=600)
    prepare_parser.add_argument(
        "--snapshot-start", default="2026-06-30T08:00:00"
    )
    prepare_parser.add_argument("--output-dir", type=Path, required=True)

    run_parser = commands.add_parser(
        "run", help="Chạy bảy snapshot và validate end-to-end."
    )
    run_parser.add_argument("--workspace", type=Path, required=True)
    run_parser.add_argument(
        "--mode",
        choices=[mode.value for mode in CompatibilityMode],
        default=CompatibilityMode.TASK_LOCATION.value,
    )
    run_parser.add_argument("--max-distance-km", type=float, default=30.0)
    run_parser.add_argument("--average-speed-kmh", type=float, default=30.0)
    run_parser.add_argument("--transition-count", type=int, default=30)

    inspect_parser = commands.add_parser(
        "inspect", help="In metadata, scenario summary và validation."
    )
    inspect_parser.add_argument("--workspace", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "branches":
        return list_branches(args)
    if args.command == "prepare":
        return prepare(args)
    if args.command == "run":
        return ScenarioRunner(args).execute()
    return inspect_workspace(args)


if __name__ == "__main__":
    raise SystemExit(main())
