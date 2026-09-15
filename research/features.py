"""Feature cho học offline: feature của job và lịch sử KTV (không rò dữ liệu tương lai)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd

FeatureBuilder = Callable[[pd.DataFrame], pd.DataFrame]


@dataclass
class FeatureRegistry:
    # Tên nhóm feature → hàm tạo DataFrame tương ứng.
    builders: dict[str, FeatureBuilder] = field(default_factory=dict)

    def register(self, name: str, builder: FeatureBuilder) -> None:
        if name in self.builders:
            raise ValueError(f"Feature builder already registered: {name}")
        self.builders[name] = builder

    def build(self, name: str, frame: pd.DataFrame) -> pd.DataFrame:
        try:
            builder = self.builders[name]
        except KeyError as error:
            available = ", ".join(sorted(self.builders))
            raise KeyError(
                f"Unknown feature builder {name!r}. Available: {available}"
            ) from error
        return builder(frame)


def default_feature_registry() -> FeatureRegistry:
    registry = FeatureRegistry()
    registry.register("job", build_job_features)
    return registry


def _service_count(series: pd.Series) -> pd.Series:
    return (
        series.fillna("")
        .str.split("|")
        .map(lambda values: sum(bool(value.strip()) for value in values))
        .astype("Int64")
    )


def build_job_features(jobs: pd.DataFrame) -> pd.DataFrame:
    """Feature không chứa target, giữ category dễ đọc."""

    columns = [
        "CHECKLIST_ID",
        "CREATE_DATE",
        "BRANCH_NAME",
        "CASE_TYPE",
        "SERVICES_LIST",
        "OBJ_TYPE_LV1",
        "OBJ_TYPE_VIP",
        "EMP_ACCOUNT",
        "EMP_LEVEL",
        "NUM_DISCUSSION",
        "NUM_SOS_DISCUSSION",
        "NUM_APPOINTMENT",
        "APPOINTTIMES_ASSIGNED",
    ]
    missing = sorted({*columns, "OBJ_LOCATION"} - set(jobs.columns))
    if missing:
        raise KeyError(f"Missing job feature columns: {', '.join(missing)}")

    features = jobs[columns].copy()
    features["CREATE_HOUR"] = jobs["CREATE_DATE"].dt.hour.astype("Int64")
    features["CREATE_DAY_OF_WEEK"] = jobs["CREATE_DATE"].dt.dayofweek.astype("Int64")
    features["CREATE_MONTH"] = jobs["CREATE_DATE"].dt.month.astype("Int64")
    features["CREATE_IS_WEEKEND"] = features["CREATE_DAY_OF_WEEK"].ge(5).astype("Int64")
    features["SERVICE_COUNT"] = _service_count(jobs["SERVICES_LIST"])
    features["HAS_LOCATION_TEXT"] = jobs["OBJ_LOCATION"].notna().astype("Int64")
    features["HAS_ASSIGNED_TECHNICIAN"] = jobs["EMP_ACCOUNT"].notna().astype("Int64")
    features["IS_VIP"] = jobs["OBJ_TYPE_VIP"].notna().astype("Int64")
    if "SOURCE_RECORD_COUNT" in jobs:
        features["SOURCE_RECORD_COUNT"] = jobs["SOURCE_RECORD_COUNT"].astype("Int64")
    return features


def _past_mean(series: pd.Series) -> pd.Series:
    return series.shift(1).expanding(min_periods=1).mean()


def add_technician_history_features(
    dataset: pd.DataFrame,
    *,
    technician_column: str = "EMP_ACCOUNT",
    time_column: str = "CREATE_DATE",
) -> pd.DataFrame:
    """Lịch sử KTV chỉ dùng các dòng đứng trước theo thời gian."""

    required = {
        technician_column,
        time_column,
        "SERVICE_DURATION_MINUTES",
        "IS_LATE",
    }
    missing = sorted(required - set(dataset.columns))
    if missing:
        raise KeyError(f"Missing historical feature columns: {', '.join(missing)}")

    original_index_name = dataset.index.name
    ordered = dataset.sort_values([time_column, "CHECKLIST_ID"], kind="stable").copy()
    grouped = ordered.groupby(technician_column, dropna=False, sort=False)

    ordered["TECH_PAST_JOB_COUNT"] = grouped.cumcount().astype("Int64")
    ordered["TECH_PAST_AVG_DURATION_MINUTES"] = grouped[
        "SERVICE_DURATION_MINUTES"
    ].transform(_past_mean)
    ordered["TECH_PAST_LATE_RATE"] = grouped["IS_LATE"].transform(_past_mean)

    history_columns = [
        "TECH_PAST_JOB_COUNT",
        "TECH_PAST_AVG_DURATION_MINUTES",
        "TECH_PAST_LATE_RATE",
    ]
    ordered.loc[ordered[technician_column].isna(), history_columns] = pd.NA
    ordered.index.name = original_index_name
    return ordered.sort_index()
