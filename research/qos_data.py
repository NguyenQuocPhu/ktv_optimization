"""Đọc, chuẩn hóa và kiểm tra chất lượng hai file CSV QOS (dùng cho research).

Tên file mặc định là bản UTF-8 do ``simulator/ktv_simulator/convert_xlsx.py``
sinh ra từ workbook gốc.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

MAINTENANCE_FILENAME = "QOS_MAINTENANCE_utf8.csv"
CHECKIN_FILENAME = "QOS_MAINT_CHECKIN_INFO_utf8.csv"

MAINTENANCE_COLUMNS = (
    "OBJ_ID",
    "BRANCH_NAME",
    "CHECKLIST_ID",
    "CHECKLIST_STATUS",
    "CREATE_DATE",
    "FINISH_DATE",
    "CASE_TYPE",
    "SERVICES_LIST",
    "OBJ_LOCATION",
    "OBJ_TYPE_LV1",
    "OBJ_TYPE_VIP",
    "FLAG_ON_TIME",
    "NUM_DISCUSSION",
    "NUM_SOS_DISCUSSION",
    "NUM_APPOINTMENT",
    "EMP_ACCOUNT",
    "EMP_LEVEL",
    "APPOINTTIMES_ASSIGNED",
    "PROCESS_NOTE",
)
CHECKIN_COLUMNS = (
    "CHECKLIST_ID",
    "LAT_LNG_IN",
    "LAT_LNG_OUT",
    "CHECKIN_DATE",
    "CHECKOUT_DATE",
    "EMP_CODE",
)
DATE_COLUMNS = ("CREATE_DATE", "FINISH_DATE", "CHECKIN_DATE", "CHECKOUT_DATE")
COUNT_COLUMNS = (
    "NUM_DISCUSSION",
    "NUM_SOS_DISCUSSION",
    "NUM_APPOINTMENT",
    "APPOINTTIMES_ASSIGNED",
)
DATE_SENTINELS = {"-1", "1000-01-01", "1000-01-01 00:00:00"}
COORDINATE_PATTERN = r"\(\s*([-\d.]+)\s*,\s*([-\d.]+)\s*\)"


# ---------------------------------------------------------------- đọc file


class QosCsvSource:
    """Đọc hai file CSV QOS; cột text giữ dạng string, ngày để chuẩn hóa sau."""

    def __init__(self, data_dir: str | Path = "data") -> None:
        root = Path(data_dir)
        self.maintenance_path = root / MAINTENANCE_FILENAME
        self.checkins_path = root / CHECKIN_FILENAME

    def _read(
        self, path: Path, columns: tuple[str, ...], nrows: int | None
    ) -> pd.DataFrame:
        if not path.is_file():
            raise FileNotFoundError(f"Thiếu file CSV: {path}")
        dtypes = {
            column: "string"
            for column in columns
            if column not in DATE_COLUMNS and column not in COUNT_COLUMNS
        }
        return pd.read_csv(path, encoding="utf-8-sig", dtype=dtypes, nrows=nrows)

    def read_maintenance(self, *, nrows: int | None = None) -> pd.DataFrame:
        return self._read(self.maintenance_path, MAINTENANCE_COLUMNS, nrows)

    def read_checkins(self, *, nrows: int | None = None) -> pd.DataFrame:
        return self._read(self.checkins_path, CHECKIN_COLUMNS, nrows)


# ---------------------------------------------------------------- chuẩn hóa


def _clean_text(series: pd.Series) -> pd.Series:
    result = series.astype("string").str.strip()
    return result.mask(result.eq(""))


def parse_datetime_series(series: pd.Series) -> pd.Series:
    cleaned = _clean_text(series).mask(lambda values: values.isin(DATE_SENTINELS))
    return pd.to_datetime(cleaned, format="mixed", errors="coerce")


def normalize_maintenance_frame(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for column in MAINTENANCE_COLUMNS:
        if column not in result:
            continue
        if column in DATE_COLUMNS:
            result[column] = parse_datetime_series(result[column])
        elif column in COUNT_COLUMNS:
            result[column] = (
                pd.to_numeric(result[column], errors="coerce")
                .fillna(0)
                .astype("Int64")
            )
        else:
            result[column] = _clean_text(result[column])
    return result


def normalize_checkin_frame(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for column in CHECKIN_COLUMNS:
        if column not in result:
            continue
        if column in DATE_COLUMNS:
            result[column] = parse_datetime_series(result[column])
        else:
            result[column] = _clean_text(result[column])

    for source, latitude, longitude in (
        ("LAT_LNG_IN", "LATITUDE_IN", "LONGITUDE_IN"),
        ("LAT_LNG_OUT", "LATITUDE_OUT", "LONGITUDE_OUT"),
    ):
        if source not in result:
            continue
        coordinates = result[source].str.extract(COORDINATE_PATTERN)
        result[latitude] = pd.to_numeric(coordinates[0], errors="coerce")
        result[longitude] = pd.to_numeric(coordinates[1], errors="coerce")
    return result


def _merge_services(values: Iterable[Any]) -> str | pd.NA:
    services: set[str] = set()
    for value in values:
        if pd.isna(value):
            continue
        services.update(
            item.strip() for item in str(value).split("|") if item.strip()
        )
    return " | ".join(sorted(services)) if services else pd.NA


def collapse_maintenance_checklists(frame: pd.DataFrame) -> pd.DataFrame:
    """Một dòng mỗi checklist, không join nhiều-nhiều tùy tiện.

    Trong CSV, các dòng checklist lặp giống nhau ở mọi field đã mô tả và chỉ
    khác ``SERVICES_LIST``. Services được hợp lại, số dòng gốc lưu ở
    ``SOURCE_RECORD_COUNT``.
    """

    if "CHECKLIST_ID" not in frame:
        raise KeyError("CHECKLIST_ID is required")

    result = frame.drop_duplicates("CHECKLIST_ID", keep="first").copy()
    grouped = frame.groupby("CHECKLIST_ID", dropna=False, sort=False)
    result["SOURCE_RECORD_COUNT"] = (
        result["CHECKLIST_ID"].map(grouped.size()).astype("Int64")
    )
    if "SERVICES_LIST" in frame:
        services = grouped["SERVICES_LIST"].agg(_merge_services)
        result["SERVICES_LIST"] = result["CHECKLIST_ID"].map(services)
    return result.reset_index(drop=True)


# ---------------------------------------------------------------- kiểm tra


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    severity: str  # warning hoặc error.
    code: str  # Mã ngắn, ổn định để máy xử lý.
    count: int  # Số record vi phạm.
    message: str  # Diễn giải cho người đọc.


@dataclass(frozen=True, slots=True)
class ValidationReport:
    dataset: str
    row_count: int
    issues: tuple[ValidationIssue, ...]

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


def _report(
    dataset: str,
    frame: pd.DataFrame,
    checks: Iterable[tuple[str, str, int, str]],
) -> ValidationReport:
    issues = tuple(ValidationIssue(*check) for check in checks if check[2] > 0)
    return ValidationReport(dataset, len(frame), issues)


def validate_maintenance(frame: pd.DataFrame) -> ValidationReport:
    issues = _schema_issues(frame, MAINTENANCE_COLUMNS)
    if issues:
        return ValidationReport("maintenance", len(frame), tuple(issues))

    finish_before_create = (
        frame["FINISH_DATE"].notna()
        & frame["CREATE_DATE"].notna()
        & (frame["FINISH_DATE"] < frame["CREATE_DATE"])
    )
    return _report(
        "maintenance",
        frame,
        (
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
                int(finish_before_create.sum()),
                "FINISH_DATE occurs before CREATE_DATE.",
            ),
        ),
    )


def validate_checkins(frame: pd.DataFrame) -> ValidationReport:
    issues = _schema_issues(frame, CHECKIN_COLUMNS)
    if issues:
        return ValidationReport("checkins", len(frame), tuple(issues))

    def outside_vietnam(latitude: str, longitude: str) -> int:
        parsed = frame[[latitude, longitude]].notna().all(axis=1)
        valid = frame[latitude].between(8, 24) & frame[longitude].between(102, 110)
        return int((parsed & ~valid).sum())

    checkout_before_checkin = (
        frame["CHECKOUT_DATE"].notna()
        & frame["CHECKIN_DATE"].notna()
        & (frame["CHECKOUT_DATE"] < frame["CHECKIN_DATE"])
    )
    return _report(
        "checkins",
        frame,
        (
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
                int(checkout_before_checkin.sum()),
                "CHECKOUT_DATE occurs before CHECKIN_DATE.",
            ),
            (
                "warning",
                "invalid_checkin_coordinate",
                outside_vietnam("LATITUDE_IN", "LONGITUDE_IN"),
                "Parsed check-in coordinate is outside Vietnam bounds.",
            ),
            (
                "warning",
                "invalid_checkout_coordinate",
                outside_vietnam("LATITUDE_OUT", "LONGITUDE_OUT"),
                "Parsed check-out coordinate is outside Vietnam bounds.",
            ),
        ),
    )
