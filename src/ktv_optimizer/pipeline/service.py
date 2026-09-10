"""One operational entrypoint: files -> plan -> atomic checkpoint -> metadata."""

from __future__ import annotations

import hashlib
import json
import re
import traceback
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
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
from ktv_optimizer.data.sources.administrative_boundaries import WardBoundaryIndex
from ktv_optimizer.optimization import CompatibilityMode, SimpleKtvOptimizer
from ktv_optimizer.state import Snapshot, SnapshotReducer
from ktv_optimizer.state.snapshot import SnapshotRunResult

from .checkpoint import CheckpointRef, VersionedCheckpointManager
from .output import write_snapshot_output


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
REQUIRED_MAINTENANCE_COLUMNS = set(MAINTENANCE_COLUMNS) - {
    "FINISH_DATE",
    "FLAG_ON_TIME",
}


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    mode: CompatibilityMode = CompatibilityMode.TASK_LOCATION
    max_distance_km: float = 30.0
    average_speed_kmh: float = 30.0


@dataclass(frozen=True, slots=True)
class OperationalRun:
    run_id: str
    output_dir: Path
    checkpoint: CheckpointRef
    result: SnapshotRunResult
    metadata: dict


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    if not safe:
        raise ValueError("Không tạo được run ID hợp lệ")
    return safe


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


class OperationalSnapshotProcessor:
    """Sequential V1 runner designed for cron/manual invocation."""

    def __init__(
        self,
        *,
        boundary_path: str | Path,
        runtime_dir: str | Path,
        output_root: str | Path,
        config: PipelineConfig | None = None,
    ) -> None:
        self.boundary_path = Path(boundary_path)
        self.output_root = Path(output_root)
        self.config = config or PipelineConfig()
        self.checkpoints = VersionedCheckpointManager(runtime_dir)
        self._boundary_index: WardBoundaryIndex | None = None

    def process(
        self,
        *,
        snapshot_id: str,
        snapshot_time: datetime,
        maintenance_path: str | Path,
        roster_path: str | Path,
    ) -> OperationalRun:
        started = _utc_now()
        maintenance_path = Path(maintenance_path).resolve()
        roster_path = Path(roster_path).resolve()
        boundary_path = self.boundary_path.resolve()
        input_files = {
            "maintenance": maintenance_path,
            "shift_roster": roster_path,
            "boundary": boundary_path,
        }
        run_id = _safe(
            f"{snapshot_id}__{started.strftime('%Y%m%dT%H%M%S%fZ')}"
        )
        output_dir = self.output_root / run_id
        metadata_path = output_dir / "run_metadata.json"
        metadata = {
            "run_id": run_id,
            "snapshot_id": snapshot_id,
            "snapshot_time": snapshot_time.isoformat(),
            "status": "RUNNING",
            "stage": "INITIALIZE",
            "started_at": started.isoformat(),
            "finished_at": None,
            "duration_seconds": None,
            "config": {
                **asdict(self.config),
                "mode": self.config.mode.value,
            },
            "inputs": {
                name: {
                    "path": str(path),
                    "sha256": None,
                    "size_bytes": None,
                }
                for name, path in input_files.items()
            },
            "previous_checkpoint_id": None,
            "checkpoint_id": None,
            "counts": {},
            "error": None,
        }
        _write_json_atomic(metadata_path, metadata)

        def stage(name: str) -> None:
            metadata["stage"] = name
            _write_json_atomic(metadata_path, metadata)
            print(f"[{run_id}] {name}")

        try:
            stage("VALIDATE_INPUT")
            for name, path in input_files.items():
                if not path.is_file():
                    raise FileNotFoundError(
                        f"Input {name} không tồn tại: {path}"
                    )
                metadata["inputs"][name].update(
                    {
                        "sha256": _sha256(path),
                        "size_bytes": path.stat().st_size,
                    }
                )

            stage("LOAD_INPUT")
            available_columns = set(
                pd.read_csv(
                    maintenance_path,
                    encoding="utf-8-sig",
                    nrows=0,
                ).columns
            )
            missing_columns = sorted(
                REQUIRED_MAINTENANCE_COLUMNS - available_columns
            )
            if missing_columns:
                raise ValueError(
                    "Maintenance event thiếu cột: "
                    + ", ".join(missing_columns)
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
            metadata["counts"]["maintenance_rows"] = len(maintenance)
            metadata["counts"]["roster_rows"] = len(roster)

            stage("MAP_INPUT")
            if self._boundary_index is None:
                self._boundary_index = WardBoundaryIndex.from_geojson(
                    boundary_path
                )
            boundary = self._boundary_index
            jobs = maintenance_to_optimization_jobs(
                maintenance,
                boundary,
                deduplicate=False,
                statuses=EVENT_TRACKING_STATUSES,
            )
            technicians = shift_roster_to_technicians(roster)
            updates = shift_roster_to_status_updates(roster)
            snapshot = Snapshot(
                snapshot_id=snapshot_id,
                captured_at=snapshot_time,
                jobs=tuple(jobs),
                technicians=tuple(technicians),
                technician_updates=tuple(updates),
            )

            stage("LOAD_PREVIOUS_CHECKPOINT")
            previous, previous_technicians, previous_ref = (
                self.checkpoints.load()
            )
            metadata["previous_checkpoint_id"] = (
                previous_ref.checkpoint_id if previous_ref else None
            )

            stage("OPTIMIZE")
            reducer = SnapshotReducer(
                SimpleKtvOptimizer(
                    mode=self.config.mode,
                    max_distance_km=self.config.max_distance_km,
                    average_speed_kmh=self.config.average_speed_kmh,
                )
            )
            result = reducer.reduce(snapshot, previous, previous_technicians)

            stage("WRITE_OUTPUT")
            summary = write_snapshot_output(output_dir, result)
            metadata["counts"].update(summary)

            stage("COMMIT_CHECKPOINT")
            checkpoint = self.checkpoints.commit(
                snapshot_id=snapshot_id,
                snapshot_time=snapshot_time,
                run_id=run_id,
                optimizer=result.checkpoint,
                technicians=result.technician_checkpoint,
            )
            metadata["checkpoint_id"] = checkpoint.checkpoint_id
            finished = _utc_now()
            metadata.update(
                {
                    "status": "SUCCESS",
                    "stage": "COMPLETED",
                    "finished_at": finished.isoformat(),
                    "duration_seconds": round(
                        (finished - started).total_seconds(), 3
                    ),
                }
            )
            _write_json_atomic(metadata_path, metadata)
            return OperationalRun(
                run_id=run_id,
                output_dir=output_dir,
                checkpoint=checkpoint,
                result=result,
                metadata=metadata,
            )
        except Exception as exc:
            finished = _utc_now()
            metadata.update(
                {
                    "status": "FAILED",
                    "finished_at": finished.isoformat(),
                    "duration_seconds": round(
                        (finished - started).total_seconds(), 3
                    ),
                    "error": {
                        "type": type(exc).__name__,
                        "message": str(exc),
                        "traceback": traceback.format_exc(),
                    },
                }
            )
            _write_json_atomic(metadata_path, metadata)
            raise
