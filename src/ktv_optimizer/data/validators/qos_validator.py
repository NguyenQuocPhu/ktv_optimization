"""Explicit, non-destructive validation of the available CSV data."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ktv_optimizer.data.sources.qos_maintenance.schema import (
    CHECKIN_COLUMNS,
    MAINTENANCE_COLUMNS,
)


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    severity: str  # Mức độ: warning hoặc error.
    code: str  # Mã ngắn, ổn định để máy có thể xử lý.
    count: int  # Số record vi phạm kiểm tra này.
    message: str  # Diễn giải vấn đề cho người đọc.


@dataclass(frozen=True, slots=True)
class ValidationReport:
    dataset: str  # Tên tập dữ liệu được kiểm tra.
    row_count: int  # Tổng số record đã kiểm tra.
    issues: tuple[ValidationIssue, ...]  # Các lỗi/cảnh báo phát hiện được.

    @property
    def is_valid(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "dataset": self.dataset,
                    "severity": issue.severity,
                    "code": issue.code,
                    "count": issue.count,
                    "message": issue.message,
                }
                for issue in self.issues
            ]
        )


def _schema_issues(
    frame: pd.DataFrame, required: tuple[str, ...]
) -> list[ValidationIssue]:
    missing = sorted(set(required) - set(frame.columns))
    if not missing:
        return []
    return [
        ValidationIssue(
            "error",
            "missing_columns",
            len(missing),
            f"Missing columns: {', '.join(missing)}",
        )
    ]


def validate_maintenance(frame: pd.DataFrame) -> ValidationReport:
    issues = _schema_issues(frame, MAINTENANCE_COLUMNS)
    if issues:
        return ValidationReport("maintenance", len(frame), tuple(issues))

    checks = (
        (
            "error",
            "missing_checklist_id",
            int(frame["CHECKLIST_ID"].isna().sum()),
            "Rows without CHECKLIST_ID cannot be linked.",
        ),
        (
            "warning",
            "duplicate_checklist_rows",
            int(frame["CHECKLIST_ID"].duplicated().sum()),
            "Collapse repeated checklist rows before joining check-ins.",
        ),
        (
            "warning",
            "missing_object_location",
            int(frame["OBJ_LOCATION"].isna().sum()),
            "Rows have no installation address.",
        ),
        (
            "error",
            "finish_before_create",
            int(
                (
                    frame["FINISH_DATE"].notna()
                    & frame["CREATE_DATE"].notna()
                    & (frame["FINISH_DATE"] < frame["CREATE_DATE"])
                ).sum()
            ),
            "FINISH_DATE occurs before CREATE_DATE.",
        ),
    )
    issues.extend(
        ValidationIssue(*check) for check in checks if check[2] > 0
    )
    return ValidationReport("maintenance", len(frame), tuple(issues))


def validate_checkins(frame: pd.DataFrame) -> ValidationReport:
    issues = _schema_issues(frame, CHECKIN_COLUMNS)
    if issues:
        return ValidationReport("checkins", len(frame), tuple(issues))

    valid_in = frame["LATITUDE_IN"].between(8, 24) & frame[
        "LONGITUDE_IN"
    ].between(102, 110)
    parsed_in = frame[["LATITUDE_IN", "LONGITUDE_IN"]].notna().all(axis=1)
    valid_out = frame["LATITUDE_OUT"].between(8, 24) & frame[
        "LONGITUDE_OUT"
    ].between(102, 110)
    parsed_out = frame[["LATITUDE_OUT", "LONGITUDE_OUT"]].notna().all(axis=1)

    checks = (
        (
            "error",
            "missing_checklist_id",
            int(frame["CHECKLIST_ID"].isna().sum()),
            "Rows without CHECKLIST_ID cannot be linked.",
        ),
        (
            "warning",
            "missing_checkin",
            int(frame["CHECKIN_DATE"].isna().sum()),
            "Rows have no usable check-in timestamp.",
        ),
        (
            "warning",
            "missing_checkout",
            int(frame["CHECKOUT_DATE"].isna().sum()),
            "Rows have no usable check-out timestamp.",
        ),
        (
            "error",
            "checkout_before_checkin",
            int(
                (
                    frame["CHECKOUT_DATE"].notna()
                    & frame["CHECKIN_DATE"].notna()
                    & (frame["CHECKOUT_DATE"] < frame["CHECKIN_DATE"])
                ).sum()
            ),
            "CHECKOUT_DATE occurs before CHECKIN_DATE.",
        ),
        (
            "warning",
            "invalid_checkin_coordinate",
            int((parsed_in & ~valid_in).sum()),
            "Parsed check-in coordinate is outside Vietnam bounds.",
        ),
        (
            "warning",
            "invalid_checkout_coordinate",
            int((parsed_out & ~valid_out).sum()),
            "Parsed check-out coordinate is outside Vietnam bounds.",
        ),
    )
    issues.extend(
        ValidationIssue(*check) for check in checks if check[2] > 0
    )
    return ValidationReport("checkins", len(frame), tuple(issues))
