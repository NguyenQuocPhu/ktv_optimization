"""Point-in-time historical features that avoid future-data leakage."""

from __future__ import annotations

import pandas as pd


def _past_mean(series: pd.Series) -> pd.Series:
    return series.shift(1).expanding(min_periods=1).mean()


def add_technician_history_features(
    dataset: pd.DataFrame,
    *,
    technician_column: str = "EMP_ACCOUNT",
    time_column: str = "CREATE_DATE",
) -> pd.DataFrame:
    """Add technician history using rows strictly earlier in sorted order."""

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
    ordered = dataset.sort_values(
        [time_column, "CHECKLIST_ID"], kind="stable"
    ).copy()
    grouped = ordered.groupby(technician_column, dropna=False, sort=False)

    ordered["TECH_PAST_JOB_COUNT"] = grouped.cumcount().astype("Int64")
    ordered["TECH_PAST_AVG_DURATION_MINUTES"] = grouped[
        "SERVICE_DURATION_MINUTES"
    ].transform(_past_mean)
    ordered["TECH_PAST_LATE_RATE"] = grouped["IS_LATE"].transform(_past_mean)

    missing_technician = ordered[technician_column].isna()
    ordered.loc[
        missing_technician,
        [
            "TECH_PAST_JOB_COUNT",
            "TECH_PAST_AVG_DURATION_MINUTES",
            "TECH_PAST_LATE_RATE",
        ],
    ] = pd.NA
    ordered.index.name = original_index_name
    return ordered.sort_index()

