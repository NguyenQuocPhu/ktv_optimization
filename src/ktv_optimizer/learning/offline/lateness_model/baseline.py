"""Smoothed historical lateness-rate baseline."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class LatenessBaseline:
    # Các cột dùng để chia nhóm khi ước lượng tỷ lệ trễ.
    group_columns: tuple[str, ...] = ("BRANCH_NAME", "SERVICES_LIST")
    # Độ làm mượt để nhóm ít mẫu không có xác suất quá cực đoan.
    smoothing: float = 20.0
    # Tỷ lệ trễ toàn tập train, dùng cho nhóm chưa từng xuất hiện.
    global_rate_: float | None = None
    # Bảng tra group -> xác suất trễ, được tạo sau fit().
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
            training.groupby(
                list(self.group_columns), dropna=False, as_index=False
            )["IS_LATE"]
            .agg(["sum", "count"])
            .reset_index()
        )
        grouped["PREDICTED_LATE_PROBABILITY"] = (
            grouped["sum"] + self.smoothing * self.global_rate_
        ) / (grouped["count"] + self.smoothing)
        self.lookup_ = grouped[
            [*self.group_columns, "PREDICTED_LATE_PROBABILITY"]
        ]
        return self

    def predict_proba(self, frame: pd.DataFrame) -> pd.Series:
        if self.lookup_ is None or self.global_rate_ is None:
            raise RuntimeError("Call fit() before predict_proba()")
        row_id = "__row_id"
        left = frame[list(self.group_columns)].copy()
        left[row_id] = range(len(left))
        predicted = left.merge(
            self.lookup_,
            on=list(self.group_columns),
            how="left",
            sort=False,
            validate="many_to_one",
        ).sort_values(row_id)["PREDICTED_LATE_PROBABILITY"]
        predicted = predicted.fillna(self.global_rate_)
        predicted.index = frame.index
        return predicted.astype(float)
