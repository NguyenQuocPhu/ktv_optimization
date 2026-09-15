"""CLI: chronological evaluation of dependency-light offline baselines."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from baselines import DurationBaseline, LatenessBaseline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("artifacts/datasets/qos_offline_dataset.csv"),
    )
    parser.add_argument("--test-fraction", type=float, default=0.2)
    return parser.parse_args()


def _chronological_split(
    frame: pd.DataFrame, test_fraction: float
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not 0 < test_fraction < 1:
        raise ValueError("test_fraction must be between 0 and 1")
    ordered = frame.sort_values("CHECKIN_DATE", kind="stable")
    split_at = max(1, int(len(ordered) * (1 - test_fraction)))
    return ordered.iloc[:split_at].copy(), ordered.iloc[split_at:].copy()


def main() -> int:
    args = parse_args()
    dataset = pd.read_csv(
        args.dataset,
        encoding="utf-8-sig",
        dtype={
            "CHECKLIST_ID": "string",
            "EMP_CODE": "string",
            "EMP_ACCOUNT": "string",
        },
    )
    dataset["CHECKIN_DATE"] = pd.to_datetime(
        dataset["CHECKIN_DATE"], errors="coerce"
    )
    train, test = _chronological_split(dataset, args.test_fraction)

    duration_model = DurationBaseline().fit(train)
    duration_prediction = duration_model.predict(test)
    duration_actual = test["SERVICE_DURATION_MINUTES"].astype(float)

    late_train = train.dropna(subset=["IS_LATE"])
    late_test = test.dropna(subset=["IS_LATE"])
    lateness_model = LatenessBaseline().fit(late_train)
    late_probability = lateness_model.predict_proba(late_test)
    late_actual = late_test["IS_LATE"].astype(float)

    metrics = {
        "train_rows": len(train),
        "test_rows": len(test),
        "duration_mae_minutes": float(
            np.mean(np.abs(duration_actual - duration_prediction))
        ),
        "lateness_brier_score": float(
            np.mean((late_actual - late_probability) ** 2)
        ),
        "lateness_accuracy_at_0_5": float(
            np.mean(late_actual == late_probability.ge(0.5).astype(float))
        ),
    }
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

