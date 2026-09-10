"""Dependency-light grouped-median duration baseline."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class DurationBaseline:
    # Các cột dùng để chia nhóm khi tính median thời lượng.
    group_columns: tuple[str, ...] = ("SERVICES_LIST", "EMP_LEVEL")
    # Median toàn tập train, dùng khi gặp nhóm chưa từng xuất hiện.
    global_median_: float | None = None
    # Bảng tra group -> thời lượng dự đoán, được tạo sau fit().
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
            frame.groupby(
                list(self.group_columns), dropna=False, as_index=False
            )["SERVICE_DURATION_MINUTES"]
            .median()
            .rename(
                columns={
                    "SERVICE_DURATION_MINUTES": "PREDICTED_DURATION_MINUTES"
                }
            )
        )
        return self

    def predict(self, frame: pd.DataFrame) -> pd.Series:
        if self.lookup_ is None or self.global_median_ is None:
            raise RuntimeError("Call fit() before predict()")
        row_id = "__row_id"
        left = frame[list(self.group_columns)].copy()
        left[row_id] = range(len(left))
        predicted = left.merge(
            self.lookup_,
            on=list(self.group_columns),
            how="left",
            sort=False,
            validate="many_to_one",
        ).sort_values(row_id)["PREDICTED_DURATION_MINUTES"]
        predicted = predicted.fillna(self.global_median_)
        predicted.index = frame.index
        return predicted.astype(float)
