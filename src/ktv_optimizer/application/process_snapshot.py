"""Process one checklist-event snapshot with an atomic checkpoint."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from ktv_optimizer.optimization import CompatibilityMode
from ktv_optimizer.pipeline import (
    OperationalSnapshotProcessor,
    PipelineConfig,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument(
        "--snapshot-time",
        required=True,
        help="ISO datetime, ví dụ 2026-06-30T08:00:00",
    )
    parser.add_argument("--maintenance", type=Path, required=True)
    parser.add_argument("--roster", type=Path, required=True)
    parser.add_argument(
        "--boundary",
        type=Path,
        default=Path("data/boundary_2026-07-31.geojson"),
    )
    parser.add_argument(
        "--runtime-dir", type=Path, default=Path("data/runtime_v1")
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("artifacts/operational_runs"),
    )
    parser.add_argument(
        "--mode",
        choices=[mode.value for mode in CompatibilityMode],
        default=CompatibilityMode.TASK_LOCATION.value,
    )
    parser.add_argument("--max-distance-km", type=float, default=30.0)
    parser.add_argument("--average-speed-kmh", type=float, default=30.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    processor = OperationalSnapshotProcessor(
        boundary_path=args.boundary,
        runtime_dir=args.runtime_dir,
        output_root=args.output_root,
        config=PipelineConfig(
            mode=CompatibilityMode(args.mode),
            max_distance_km=args.max_distance_km,
            average_speed_kmh=args.average_speed_kmh,
        ),
    )
    run = processor.process(
        snapshot_id=args.snapshot_id,
        snapshot_time=datetime.fromisoformat(args.snapshot_time),
        maintenance_path=args.maintenance,
        roster_path=args.roster,
    )
    counts = run.metadata["counts"]
    print(
        f"SUCCESS {run.run_id}: active={counts['active_jobs']}, "
        f"assigned={counts['assignments']}, "
        f"unassigned={counts['unassigned']}"
    )
    print(f"Checkpoint: {run.checkpoint.checkpoint_id}")
    print(f"Output: {run.output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
