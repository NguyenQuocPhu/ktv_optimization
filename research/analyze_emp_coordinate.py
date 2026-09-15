#!/usr/bin/env python3
"""Kiểm tra lịch sử GPS KTV và nối các điểm liên tiếp theo thời gian."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


REQUIRED_COLUMNS = {
    "ID",
    "EMPLOYEECODE",
    "ACCOUNTEMP",
    "COORDINATE",
    "CREATEBY",
    "CREATEDATE",
    "FLAG",
}
COORDINATE_PATTERN = r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$"


@dataclass(frozen=True, slots=True)
class CoordinateAudit:
    row_count: int  # Tổng số bản ghi GPS.
    employee_count: int  # Số account KTV khác nhau.
    createby_match_count: int  # Số dòng CREATEBY bằng ACCOUNTEMP.
    createby_mismatch_count: int  # Số dòng hai account không khớp.
    valid_coordinate_count: int  # Số GPS parse được và thuộc khoảng Việt Nam.
    invalid_coordinate_count: int  # Số GPS thiếu, lỗi hoặc ngoài khoảng.
    account_mapping_conflicts: int  # Account map sang nhiều employee code.
    employee_mapping_conflicts: int  # Employee code map sang nhiều account.


def load_coordinate_history(path: str | Path) -> pd.DataFrame:
    """Đọc CSV và chuẩn hóa account, thời gian, latitude/longitude."""

    frame = pd.read_csv(
        path,
        dtype={
            "ID": "string",
            "EMPLOYEECODE": "string",
            "ACCOUNTEMP": "string",
            "COORDINATE": "string",
            "CREATEBY": "string",
            "UPDATEBY": "string",
            "FLAG": "Int64",
        },
    )
    missing = sorted(REQUIRED_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError(f"CSV thiếu cột: {', '.join(missing)}")

    for column in ("EMPLOYEECODE", "ACCOUNTEMP", "CREATEBY"):
        frame[column] = frame[column].str.strip()

    frame["CREATEDATE"] = pd.to_datetime(
        frame["CREATEDATE"], errors="coerce"
    )
    coordinates = frame["COORDINATE"].str.extract(COORDINATE_PATTERN)
    frame["LATITUDE"] = pd.to_numeric(coordinates[0], errors="coerce")
    frame["LONGITUDE"] = pd.to_numeric(coordinates[1], errors="coerce")
    frame["COORDINATE_VALID"] = (
        frame["LATITUDE"].between(8, 24)
        & frame["LONGITUDE"].between(102, 110)
    )
    frame["CREATEBY_MATCHES_ACCOUNT"] = frame["CREATEBY"].eq(
        frame["ACCOUNTEMP"]
    )
    return frame


def audit_coordinate_history(frame: pd.DataFrame) -> CoordinateAudit:
    """Tóm tắt tính toàn vẹn account và tọa độ."""

    account_conflicts = (
        frame.groupby("ACCOUNTEMP")["EMPLOYEECODE"].nunique().gt(1).sum()
    )
    employee_conflicts = (
        frame.groupby("EMPLOYEECODE")["ACCOUNTEMP"].nunique().gt(1).sum()
    )
    matches = frame["CREATEBY_MATCHES_ACCOUNT"].fillna(False)
    valid = frame["COORDINATE_VALID"].fillna(False)
    return CoordinateAudit(
        row_count=len(frame),
        employee_count=frame["ACCOUNTEMP"].nunique(),
        createby_match_count=int(matches.sum()),
        createby_mismatch_count=int((~matches).sum()),
        valid_coordinate_count=int(valid.sum()),
        invalid_coordinate_count=int((~valid).sum()),
        account_mapping_conflicts=int(account_conflicts),
        employee_mapping_conflicts=int(employee_conflicts),
    )


def _haversine_km(
    latitude: pd.Series,
    longitude: pd.Series,
    previous_latitude: pd.Series,
    previous_longitude: pd.Series,
) -> pd.Series:
    latitude_1 = np.radians(previous_latitude)
    latitude_2 = np.radians(latitude)
    delta_latitude = latitude_2 - latitude_1
    delta_longitude = np.radians(longitude - previous_longitude)
    value = (
        np.sin(delta_latitude / 2) ** 2
        + np.cos(latitude_1)
        * np.cos(latitude_2)
        * np.sin(delta_longitude / 2) ** 2
    )
    return 6371.0088 * 2 * np.arctan2(np.sqrt(value), np.sqrt(1 - value))


def build_track_links(
    frame: pd.DataFrame,
    *,
    max_gap_minutes: float = 30,
    max_reasonable_speed_kmh: float = 120,
) -> pd.DataFrame:
    """Nối mỗi GPS với điểm trước của cùng account theo CREATEDATE.

    Một segment mới được tạo nếu đây là điểm đầu tiên hoặc khoảng thời gian
    với điểm trước lớn hơn ``max_gap_minutes``. Nhờ vậy hai ngày/lượt theo dõi
    tách biệt không bị hiểu nhầm thành một chặng di chuyển.
    """

    tracks = frame.sort_values(
        ["ACCOUNTEMP", "CREATEDATE", "ID"], kind="stable"
    ).copy()
    grouped = tracks.groupby("ACCOUNTEMP", sort=False, dropna=False)

    tracks["PREVIOUS_ID"] = grouped["ID"].shift()
    tracks["PREVIOUS_CREATEDATE"] = grouped["CREATEDATE"].shift()
    tracks["PREVIOUS_LATITUDE"] = grouped["LATITUDE"].shift()
    tracks["PREVIOUS_LONGITUDE"] = grouped["LONGITUDE"].shift()
    tracks["DELTA_SECONDS"] = (
        tracks["CREATEDATE"] - tracks["PREVIOUS_CREATEDATE"]
    ).dt.total_seconds()
    tracks["DISTANCE_KM"] = _haversine_km(
        tracks["LATITUDE"],
        tracks["LONGITUDE"],
        tracks["PREVIOUS_LATITUDE"],
        tracks["PREVIOUS_LONGITUDE"],
    )
    tracks["SPEED_KMH"] = tracks["DISTANCE_KM"] / (
        tracks["DELTA_SECONDS"] / 3600
    )

    new_segment = (
        tracks["PREVIOUS_ID"].isna()
        | tracks["DELTA_SECONDS"].isna()
        | tracks["DELTA_SECONDS"].gt(max_gap_minutes * 60)
    )
    tracks["TRACK_SEGMENT"] = (
        new_segment.groupby(tracks["ACCOUNTEMP"]).cumsum().astype("Int64")
    )
    tracks["IS_SPEED_OUTLIER"] = tracks["SPEED_KMH"].gt(
        max_reasonable_speed_kmh
    )
    return tracks


def print_report(
    frame: pd.DataFrame,
    tracks: pd.DataFrame,
    *,
    account: str | None,
    head: int,
    max_gap_minutes: float,
) -> None:
    audit = audit_coordinate_history(frame)
    links = tracks[tracks["PREVIOUS_ID"].notna()]

    print("=== DATA QUALITY ===")
    print(f"Rows: {audit.row_count:,}")
    print(f"Accounts: {audit.employee_count:,}")
    print(
        "CREATEBY == ACCOUNTEMP: "
        f"{audit.createby_match_count:,}/{audit.row_count:,}"
    )
    print(f"CREATEBY mismatches: {audit.createby_mismatch_count:,}")
    print(
        "Valid coordinates: "
        f"{audit.valid_coordinate_count:,}/{audit.row_count:,}"
    )
    print(f"Account -> employee code conflicts: {audit.account_mapping_conflicts}")
    print(f"Employee code -> account conflicts: {audit.employee_mapping_conflicts}")

    print("\n=== TRACK LINKS ===")
    print(f"Consecutive links: {len(links):,}")
    print(
        f"New segments caused by gap > {max_gap_minutes:g} minutes: "
        f"{int(links['DELTA_SECONDS'].gt(max_gap_minutes * 60).sum()):,}"
    )
    print(
        "Speed outliers > 120 km/h: "
        f"{int(links['IS_SPEED_OUTLIER'].sum()):,}"
    )
    print("Median interval (seconds):", round(links["DELTA_SECONDS"].median(), 3))
    print("Median distance (km):", round(links["DISTANCE_KM"].median(), 6))

    print("\nPoints by account:")
    print(
        frame["ACCOUNTEMP"]
        .value_counts()
        .rename_axis("ACCOUNTEMP")
        .to_frame("POINT_COUNT")
        .to_string()
    )

    selected_account = account or frame["ACCOUNTEMP"].value_counts().index[0]
    selected = tracks[tracks["ACCOUNTEMP"].eq(selected_account)]
    if selected.empty:
        raise ValueError(f"Không tìm thấy ACCOUNTEMP={selected_account!r}")

    columns = [
        "ID",
        "ACCOUNTEMP",
        "CREATEDATE",
        "LATITUDE",
        "LONGITUDE",
        "TRACK_SEGMENT",
        "PREVIOUS_ID",
        "DELTA_SECONDS",
        "DISTANCE_KM",
        "SPEED_KMH",
        "IS_SPEED_OUTLIER",
    ]
    print(f"\n=== TRACK SAMPLE: {selected_account} ===")
    print(selected[columns].head(head).to_string(index=False))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "csv_path",
        nargs="?",
        type=Path,
        default=Path("data/sample_emp_coordinate.csv"),
    )
    parser.add_argument(
        "--account",
        help="ACCOUNTEMP cần in chi tiết; mặc định chọn account nhiều điểm nhất.",
    )
    parser.add_argument("--head", type=int, default=20)
    parser.add_argument("--max-gap-minutes", type=float, default=30)
    parser.add_argument("--max-speed-kmh", type=float, default=120)
    parser.add_argument(
        "--output",
        type=Path,
        help="Nếu có, lưu toàn bộ track đã nối thành CSV.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    frame = load_coordinate_history(args.csv_path)
    tracks = build_track_links(
        frame,
        max_gap_minutes=args.max_gap_minutes,
        max_reasonable_speed_kmh=args.max_speed_kmh,
    )
    print_report(
        frame,
        tracks,
        account=args.account,
        head=args.head,
        max_gap_minutes=args.max_gap_minutes,
    )

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        tracks.to_csv(args.output, index=False, encoding="utf-8-sig")
        print(f"\nSaved: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

