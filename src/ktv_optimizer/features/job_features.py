"""Features available from a canonical maintenance checklist."""

from __future__ import annotations

import pandas as pd


def _service_count(series: pd.Series) -> pd.Series:
    return (
        series.fillna("")
        .str.split("|")
        .map(lambda values: sum(bool(value.strip()) for value in values))
        .astype("Int64")
    )


def build_job_features(jobs: pd.DataFrame) -> pd.DataFrame:
    """Create non-target job features while keeping readable categories."""

    required = {
        "CHECKLIST_ID",
        "CREATE_DATE",
        "BRANCH_NAME",
        "CASE_TYPE",
        "SERVICES_LIST",
        "OBJ_LOCATION",
        "OBJ_TYPE_LV1",
        "OBJ_TYPE_VIP",
        "NUM_DISCUSSION",
        "NUM_SOS_DISCUSSION",
        "NUM_APPOINTMENT",
        "EMP_ACCOUNT",
        "EMP_LEVEL",
        "APPOINTTIMES_ASSIGNED",
    }
    missing = sorted(required - set(jobs.columns))
    if missing:
        raise KeyError(f"Missing job feature columns: {', '.join(missing)}")

    features = jobs[
        [
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
    ].copy()

    features["CREATE_HOUR"] = jobs["CREATE_DATE"].dt.hour.astype("Int64")
    features["CREATE_DAY_OF_WEEK"] = (
        jobs["CREATE_DATE"].dt.dayofweek.astype("Int64")
    )
    features["CREATE_MONTH"] = jobs["CREATE_DATE"].dt.month.astype("Int64")
    features["CREATE_IS_WEEKEND"] = (
        features["CREATE_DAY_OF_WEEK"].ge(5).astype("Int64")
    )
    features["SERVICE_COUNT"] = _service_count(jobs["SERVICES_LIST"])
    features["HAS_LOCATION_TEXT"] = jobs["OBJ_LOCATION"].notna().astype("Int64")
    features["HAS_ASSIGNED_TECHNICIAN"] = (
        jobs["EMP_ACCOUNT"].notna().astype("Int64")
    )
    features["IS_VIP"] = jobs["OBJ_TYPE_VIP"].notna().astype("Int64")
    if "SOURCE_RECORD_COUNT" in jobs:
        features["SOURCE_RECORD_COUNT"] = jobs[
            "SOURCE_RECORD_COUNT"
        ].astype("Int64")
    return features

