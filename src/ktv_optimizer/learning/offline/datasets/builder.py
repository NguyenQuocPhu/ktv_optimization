"""Build a visit-level offline dataset from the two supplied CSV files."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ktv_optimizer.data.mappers import (
    collapse_maintenance_checklists,
    normalize_checkin_frame,
    normalize_maintenance_frame,
)
from ktv_optimizer.data.sources.qos_maintenance import QosMaintenanceCsvSource
from ktv_optimizer.data.validators import (
    ValidationReport,
    validate_checkins,
    validate_maintenance,
)
from ktv_optimizer.features import (
    add_technician_history_features,
    build_job_features,
)


@dataclass(frozen=True, slots=True)
class DatasetBuildResult:
    dataset: pd.DataFrame  # Dataset visit-level sẵn sàng để học offline.
    maintenance_validation: ValidationReport  # Chất lượng CSV maintenance.
    checkin_validation: ValidationReport  # Chất lượng CSV check-in.


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
    delta_lng = np.radians(
        selected["LONGITUDE_OUT"] - selected["LONGITUDE_IN"]
    )
    value = (
        np.sin(delta_lat / 2) ** 2
        + np.cos(lat_in) * np.cos(lat_out) * np.sin(delta_lng / 2) ** 2
    )
    result.loc[valid] = 6371.0088 * 2 * np.arctan2(
        np.sqrt(value), np.sqrt(1 - value)
    )
    return result


class OfflineDatasetBuilder:
    """Create leakage-aware rows for duration and lateness baselines."""

    def __init__(
        self,
        source: QosMaintenanceCsvSource,
        *,
        max_service_minutes: float = 24 * 60,
    ) -> None:
        # Nguồn cung cấp hai CSV QOS.
        self.source = source
        # Ngưỡng loại thời lượng bất thường, mặc định tối đa 24 giờ.
        self.max_service_minutes = max_service_minutes

    def build(self, *, nrows: int | None = None) -> DatasetBuildResult:
        maintenance = normalize_maintenance_frame(
            self.source.read_maintenance(nrows=nrows)
        )
        checkins = normalize_checkin_frame(
            self.source.read_checkins(nrows=nrows)
        )

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
            job_context,
            on="CHECKLIST_ID",
            how="inner",
            validate="many_to_one",
        )
        dataset["IS_LATE"] = (
            dataset["FLAG_ON_TIME"]
            .map({"NO": 1, "YES": 0})
            .astype("Int64")
        )
        dataset = add_technician_history_features(
            dataset,
            technician_column="EMP_CODE",
            time_column="CHECKIN_DATE",
        )
        return DatasetBuildResult(
            dataset=dataset,
            maintenance_validation=maintenance_report,
            checkin_validation=checkin_report,
        )
