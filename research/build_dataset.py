"""CLI: dựng dataset visit-level (maintenance × check-in) để học offline.

    python research/build_dataset.py --data-dir data --limit 10000
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from features import add_technician_history_features, build_job_features
from qos_data import (
    QosCsvSource,
    ValidationReport,
    collapse_maintenance_checklists,
    normalize_checkin_frame,
    normalize_maintenance_frame,
    validate_checkins,
    validate_maintenance,
)


@dataclass(frozen=True, slots=True)
class DatasetBuildResult:
    dataset: pd.DataFrame  # Dataset visit-level sẵn sàng để học offline.
    maintenance_validation: ValidationReport
    checkin_validation: ValidationReport


def _gps_displacement_km(frame: pd.DataFrame) -> pd.Series:
    valid = (
        frame["LATITUDE_IN"].between(8, 24)
        & frame["LONGITUDE_IN"].between(102, 110)
        & frame["LATITUDE_OUT"].between(8, 24)
        & frame["LONGITUDE_OUT"].between(102, 110)
    )
    result = pd.Series(np.nan, index=frame.index, dtype=float)
    if not valid.any():
        return result

    selected = frame.loc[valid]
    lat_in = np.radians(selected["LATITUDE_IN"])
    lat_out = np.radians(selected["LATITUDE_OUT"])
    delta_lat = lat_out - lat_in
    delta_lng = np.radians(selected["LONGITUDE_OUT"] - selected["LONGITUDE_IN"])
    value = (
        np.sin(delta_lat / 2) ** 2
        + np.cos(lat_in) * np.cos(lat_out) * np.sin(delta_lng / 2) ** 2
    )
    result.loc[valid] = 6371.0088 * 2 * np.arctan2(np.sqrt(value), np.sqrt(1 - value))
    return result


class OfflineDatasetBuilder:
    """Tạo dòng dữ liệu cho baseline thời lượng và trễ, tránh rò dữ liệu tương lai."""

    def __init__(
        self, source: QosCsvSource, *, max_service_minutes: float = 24 * 60
    ) -> None:
        self.source = source
        # Loại thời lượng bất thường, mặc định tối đa 24 giờ.
        self.max_service_minutes = max_service_minutes

    def build(self, *, nrows: int | None = None) -> DatasetBuildResult:
        maintenance = normalize_maintenance_frame(
            self.source.read_maintenance(nrows=nrows)
        )
        checkins = normalize_checkin_frame(self.source.read_checkins(nrows=nrows))

        maintenance_report = validate_maintenance(maintenance)
        checkin_report = validate_checkins(checkins)

        jobs = collapse_maintenance_checklists(maintenance)
        job_context = build_job_features(jobs).merge(
            jobs[["CHECKLIST_ID", "FLAG_ON_TIME"]],
            on="CHECKLIST_ID",
            how="left",
            validate="one_to_one",
        )

        visits = checkins.copy()
        visits["SERVICE_DURATION_MINUTES"] = (
            visits["CHECKOUT_DATE"] - visits["CHECKIN_DATE"]
        ).dt.total_seconds() / 60
        visits["GPS_DISPLACEMENT_KM"] = _gps_displacement_km(visits)
        visits["GPS_IN_VALID"] = (
            visits["LATITUDE_IN"].between(8, 24)
            & visits["LONGITUDE_IN"].between(102, 110)
        ).astype("Int64")
        visits["GPS_OUT_VALID"] = (
            visits["LATITUDE_OUT"].between(8, 24)
            & visits["LONGITUDE_OUT"].between(102, 110)
        ).astype("Int64")
        visits = visits[
            visits["SERVICE_DURATION_MINUTES"].between(
                0, self.max_service_minutes, inclusive="both"
            )
        ].copy()

        dataset = visits.merge(
            job_context, on="CHECKLIST_ID", how="inner", validate="many_to_one"
        )
        dataset["IS_LATE"] = (
            dataset["FLAG_ON_TIME"].map({"NO": 1, "YES": 0}).astype("Int64")
        )
        dataset = add_technician_history_features(
            dataset, technician_column="EMP_CODE", time_column="CHECKIN_DATE"
        )
        return DatasetBuildResult(
            dataset=dataset,
            maintenance_validation=maintenance_report,
            checkin_validation=checkin_report,
        )


def main() -> int:
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
        help="Chỉ đọc tối đa N dòng mỗi file (chạy thử).",
    )
    args = parser.parse_args()

    result = OfflineDatasetBuilder(QosCsvSource(args.data_dir)).build(nrows=args.limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result.dataset.to_csv(args.output, index=False, encoding="utf-8-sig")

    print(f"Dataset: {len(result.dataset):,} rows")
    print(f"Output: {args.output.resolve()}")
    for report in (result.maintenance_validation, result.checkin_validation):
        print(
            f"{report.dataset}: rows={report.row_count:,}, "
            f"valid={report.is_valid}, issues={len(report.issues)}"
        )
        if report.issues:
            print(report.to_frame().to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
