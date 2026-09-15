"""Baseline đơn giản: thời lượng làm (median theo nhóm) và xác suất trễ (tỷ lệ làm mượt)."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


def _lookup(
    frame: pd.DataFrame,
    group_columns: tuple[str, ...],
    table: pd.DataFrame,
    value_column: str,
    fallback: float,
) -> pd.Series:
    row_id = "__row_id"
    left = frame[list(group_columns)].copy()
    left[row_id] = range(len(left))
    predicted = left.merge(
        table,
        on=list(group_columns),
        how="left",
        sort=False,
        validate="many_to_one",
    ).sort_values(row_id)[value_column]
    predicted = predicted.fillna(fallback)
    predicted.index = frame.index
    return predicted.astype(float)


@dataclass
class DurationBaseline:
    # Cột chia nhóm khi tính median thời lượng.
    group_columns: tuple[str, ...] = ("SERVICES_LIST", "EMP_LEVEL")
    # Median toàn tập train, dùng cho nhóm chưa gặp.
    global_median_: float | None = None
    # Bảng group → thời lượng dự đoán, tạo sau fit().
    lookup_: pd.DataFrame | None = None

    def fit(self, frame: pd.DataFrame) -> "DurationBaseline":
        required = {*self.group_columns, "SERVICE_DURATION_MINUTES"}
        missing = sorted(required - set(frame.columns))
        if missing:
            raise KeyError(f"Missing duration columns: {', '.join(missing)}")

        target = frame["SERVICE_DURATION_MINUTES"].dropna()
        if target.empty:
            raise ValueError("No valid duration target is available")
        self.global_median_ = float(target.median())
        self.lookup_ = (
            frame.groupby(list(self.group_columns), dropna=False, as_index=False)[
                "SERVICE_DURATION_MINUTES"
            ]
            .median()
            .rename(
                columns={"SERVICE_DURATION_MINUTES": "PREDICTED_DURATION_MINUTES"}
            )
        )
        return self

    def predict(self, frame: pd.DataFrame) -> pd.Series:
        if self.lookup_ is None or self.global_median_ is None:
            raise RuntimeError("Call fit() before predict()")
        return _lookup(
            frame,
            self.group_columns,
            self.lookup_,
            "PREDICTED_DURATION_MINUTES",
            self.global_median_,
        )


@dataclass
class LatenessBaseline:
    # Cột chia nhóm khi ước lượng tỷ lệ trễ.
    group_columns: tuple[str, ...] = ("BRANCH_NAME", "SERVICES_LIST")
    # Độ làm mượt để nhóm ít mẫu không ra xác suất cực đoan.
    smoothing: float = 20.0
    # Tỷ lệ trễ toàn tập train, dùng cho nhóm chưa gặp.
    global_rate_: float | None = None
    # Bảng group → xác suất trễ, tạo sau fit().
    lookup_: pd.DataFrame | None = None

    def fit(self, frame: pd.DataFrame) -> "LatenessBaseline":
        required = {*self.group_columns, "IS_LATE"}
        missing = sorted(required - set(frame.columns))
        if missing:
            raise KeyError(f"Missing lateness columns: {', '.join(missing)}")

        training = frame.dropna(subset=["IS_LATE"]).copy()
        if training.empty:
            raise ValueError("No valid lateness target is available")

        training["IS_LATE"] = training["IS_LATE"].astype(float)
        self.global_rate_ = float(training["IS_LATE"].mean())
        grouped = (
            training.groupby(list(self.group_columns), dropna=False, as_index=False)[
                "IS_LATE"
            ]
            .agg(["sum", "count"])
            .reset_index()
        )
        grouped["PREDICTED_LATE_PROBABILITY"] = (
            grouped["sum"] + self.smoothing * self.global_rate_
        ) / (grouped["count"] + self.smoothing)
        self.lookup_ = grouped[[*self.group_columns, "PREDICTED_LATE_PROBABILITY"]]
        return self

    def predict_proba(self, frame: pd.DataFrame) -> pd.Series:
        if self.lookup_ is None or self.global_rate_ is None:
            raise RuntimeError("Call fit() before predict_proba()")
        return _lookup(
            frame,
            self.group_columns,
            self.lookup_,
            "PREDICTED_LATE_PROBABILITY",
            self.global_rate_,
        )
