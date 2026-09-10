"""Commit both optimizer CSV states as one versioned checkpoint."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from ktv_optimizer.state import (
    CsvStateStore,
    CsvTechnicianStateStore,
    OptimizerCheckpoint,
    TechnicianCheckpoint,
)


@dataclass(frozen=True, slots=True)
class CheckpointRef:
    checkpoint_id: str  # Version state được latest.json trỏ tới.
    snapshot_id: str  # Snapshot đã sinh ra version này.
    snapshot_time: str  # ISO timestamp của snapshot.
    run_id: str  # Run vận hành tạo checkpoint.
    created_at: str  # Thời điểm commit.
    previous_checkpoint_id: str | None  # Version cha để audit chuỗi state.


class VersionedCheckpointManager:
    """A tiny CSV transaction: stage directory, rename, switch pointer."""

    OPTIMIZER_FILE = "optimizer_state.csv"
    TECHNICIAN_FILE = "technician_state.csv"
    METADATA_FILE = "checkpoint_metadata.json"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.checkpoints_dir = self.root / "checkpoints"
        self.staging_dir = self.root / "staging"
        self.latest_path = self.root / "latest.json"

    @staticmethod
    def _safe(value: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
        if not safe:
            raise ValueError("Không tạo được checkpoint ID hợp lệ")
        return safe

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)

    def latest(self) -> CheckpointRef | None:
        if not self.latest_path.is_file():
            return None
        payload = json.loads(self.latest_path.read_text(encoding="utf-8"))
        ref = CheckpointRef(**payload)
        checkpoint_dir = self.checkpoints_dir / ref.checkpoint_id
        expected = (
            checkpoint_dir / self.OPTIMIZER_FILE,
            checkpoint_dir / self.TECHNICIAN_FILE,
            checkpoint_dir / self.METADATA_FILE,
        )
        missing = [str(path) for path in expected if not path.is_file()]
        if missing:
            raise ValueError(
                "latest.json trỏ tới checkpoint không hoàn chỉnh: "
                + ", ".join(missing)
            )
        return ref

    def load(
        self,
    ) -> tuple[OptimizerCheckpoint, TechnicianCheckpoint, CheckpointRef | None]:
        ref = self.latest()
        if ref is None:
            return OptimizerCheckpoint(), TechnicianCheckpoint(), None
        checkpoint_dir = self.checkpoints_dir / ref.checkpoint_id
        optimizer = CsvStateStore(
            checkpoint_dir / self.OPTIMIZER_FILE
        ).load()
        technicians = CsvTechnicianStateStore(
            checkpoint_dir / self.TECHNICIAN_FILE
        ).load()
        return optimizer, technicians, ref

    def commit(
        self,
        *,
        snapshot_id: str,
        snapshot_time: datetime,
        run_id: str,
        optimizer: OptimizerCheckpoint,
        technicians: TechnicianCheckpoint,
    ) -> CheckpointRef:
        previous = self.latest()
        # run_id already starts with snapshot_id and is unique per invocation.
        checkpoint_id = self._safe(run_id)
        staging = self.staging_dir / checkpoint_id
        final = self.checkpoints_dir / checkpoint_id
        if staging.exists() or final.exists():
            raise FileExistsError(f"Checkpoint đã tồn tại: {checkpoint_id}")

        staging.mkdir(parents=True)
        CsvStateStore(staging / self.OPTIMIZER_FILE).save(optimizer)
        CsvTechnicianStateStore(staging / self.TECHNICIAN_FILE).save(
            technicians
        )
        ref = CheckpointRef(
            checkpoint_id=checkpoint_id,
            snapshot_id=snapshot_id,
            snapshot_time=snapshot_time.isoformat(),
            run_id=run_id,
            created_at=datetime.now().astimezone().isoformat(),
            previous_checkpoint_id=(
                previous.checkpoint_id if previous is not None else None
            ),
        )
        self._write_json_atomic(staging / self.METADATA_FILE, asdict(ref))

        # The directory rename exposes both CSVs together. The pointer switch
        # is the only operation that makes this version current.
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)
        staging.replace(final)
        self._write_json_atomic(self.latest_path, asdict(ref))
        return ref
