"""TẠM: cắt export QOS theo chi nhánh thành bộ dữ liệu mẫu nhỏ để commit.

Giữ nguyên cột và định dạng của CSV gốc, chỉ lọc dòng: maintenance theo
BRANCH_NAME, check-in theo CHECKLIST_ID của các checklist đã giữ, GPS theo
ACCOUNTEMP của KTV đã giữ. Cột PROCESS_NOTE bị xóa trắng. Boundary giả và luồng sự kiện mẫu sinh lại từ bộ này
bằng ``fake_boundary`` và ``events`` (xem README).

    PYTHONPATH=src:simulator .venv/bin/python -m ktv_simulator.sample_data \\
        --data-dir data --branch HNI_04 --out-dir data/sample
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

MAINTENANCE = "QOS_MAINTENANCE_utf8.csv"
CHECKINS = "QOS_MAINT_CHECKIN_INFO_utf8.csv"
GPS = "sample_emp_coordinate.csv"


def _filter_csv(
    source: Path, target: Path, column: str, keep, encoding: str, blank: tuple[str, ...] = ()
) -> tuple[int, set[str]]:
    """Chép dòng có ``keep(row)`` đúng, xóa trắng các cột ``blank``; trả (số dòng, tập giá trị ``column``)."""

    count, values = 0, set()
    with (
        source.open(encoding=encoding, newline="") as handle,
        target.open("w", encoding="utf-8-sig", newline="") as output,
    ):
        reader = csv.DictReader(handle)
        if column not in (reader.fieldnames or ()):
            raise ValueError(f"{source}: thiếu cột {column}")
        writer = csv.DictWriter(output, fieldnames=reader.fieldnames, lineterminator="\r\n")
        writer.writeheader()
        for row in reader:
            if keep(row):
                writer.writerow(row | dict.fromkeys(blank, ""))
                count += 1
                values.add(row[column].strip())
    return count, values


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ktv_simulator.sample_data",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--branch", action="append", required=True, dest="branches")
    parser.add_argument("--out-dir", type=Path, default=Path("data/sample"))
    parser.add_argument("--encoding", default="utf-8-sig")
    args = parser.parse_args(argv)

    csv.field_size_limit(sys.maxsize)  # PROCESS_NOTE có ô rất dài.
    args.out_dir.mkdir(parents=True, exist_ok=True)
    branches = set(args.branches)

    jobs, job_ids = _filter_csv(
        args.data_dir / MAINTENANCE,
        args.out_dir / MAINTENANCE,
        "CHECKLIST_ID",
        lambda row: row["BRANCH_NAME"].strip() in branches,
        args.encoding,
        # Ghi chú tự do: nặng nhất, có SĐT/tên khách, không code nào dùng.
        blank=("PROCESS_NOTE",),
    )
    with (args.out_dir / MAINTENANCE).open(encoding="utf-8-sig", newline="") as handle:
        accounts = {row["EMP_ACCOUNT"].strip() for row in csv.DictReader(handle)} - {""}
    checkins, _ = _filter_csv(
        args.data_dir / CHECKINS,
        args.out_dir / CHECKINS,
        "CHECKLIST_ID",
        lambda row: row["CHECKLIST_ID"].strip() in job_ids,
        args.encoding,
    )
    print(f"{MAINTENANCE}: {jobs:,} dòng, {len(job_ids):,} checklist, {len(accounts):,} KTV")
    print(f"{CHECKINS}: {checkins:,} dòng")

    gps_source = args.data_dir / GPS
    if gps_source.exists():
        points, _ = _filter_csv(
            gps_source,
            args.out_dir / GPS,
            "ACCOUNTEMP",
            lambda row: row["ACCOUNTEMP"].strip() in accounts,
            args.encoding,
        )
        if points:
            print(f"{GPS}: {points:,} dòng")
        else:
            (args.out_dir / GPS).unlink()
            print(f"{GPS}: không có KTV nào của chi nhánh đã chọn, bỏ qua")
    print(f"Đã ghi {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
