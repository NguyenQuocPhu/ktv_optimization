"""CLI: build the offline visit-level dataset as CSV."""

from __future__ import annotations

import argparse
from pathlib import Path

from ktv_optimizer.data.sources.qos_maintenance import QosMaintenanceCsvSource
from ktv_optimizer.learning.offline.datasets import OfflineDatasetBuilder


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/datasets/qos_offline_dataset.csv"),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Read at most this many rows from each source (smoke tests).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = QosMaintenanceCsvSource(args.data_dir)
    result = OfflineDatasetBuilder(source).build(nrows=args.limit)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    result.dataset.to_csv(args.output, index=False, encoding="utf-8-sig")

    print(f"Dataset: {len(result.dataset):,} rows")
    print(f"Output: {args.output.resolve()}")
    for report in (
        result.maintenance_validation,
        result.checkin_validation,
    ):
        print(
            f"{report.dataset}: rows={report.row_count:,}, "
            f"valid={report.is_valid}, issues={len(report.issues)}"
        )
        if report.issues:
            print(report.to_frame().to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

