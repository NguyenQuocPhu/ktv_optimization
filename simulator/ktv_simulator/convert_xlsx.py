#!/usr/bin/env python3
"""Stream the two large QOS workbooks to Excel-friendly UTF-8 CSV files."""

from __future__ import annotations

import csv
import re
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from xml.etree.ElementTree import iterparse
from zipfile import ZipFile


CELL_REF_RE = re.compile(r"([A-Z]+)")
DATE_TIME_STYLES = {"2"}
DATE_ONLY_STYLES = {"3"}


def column_index(cell_ref: str) -> int:
    letters = CELL_REF_RE.match(cell_ref).group(1)
    result = 0
    for char in letters:
        result = result * 26 + ord(char) - ord("A") + 1
    return result - 1


def excel_number(value: str) -> str:
    number = Decimal(value)
    if number == number.to_integral():
        return str(number.quantize(Decimal(1)))
    return format(number.normalize(), "f")


def excel_date(value: str, date_only: bool) -> str:
    serial = Decimal(value)
    # Negative values in these files are source-system sentinels, not valid dates.
    if serial < 0:
        return excel_number(value)

    moment = datetime(1899, 12, 30) + timedelta(days=float(serial))
    if date_only:
        return f"{moment.month}/{moment.day}/{moment.year}"

    hour = moment.hour % 12 or 12
    suffix = "AM" if moment.hour < 12 else "PM"
    return (
        f"{moment.month}/{moment.day}/{moment.year} "
        f"{hour}:{moment.minute:02d} {suffix}"
    )


def _text_runs(element) -> str:
    """Join direct <t> and rich-text <r><t> runs; skip phonetic <rPh> hints."""

    parts: list[str] = []
    for child in element:
        if child.tag.endswith("}t"):
            parts.append(child.text or "")
        elif child.tag.endswith("}r"):
            parts.extend(node.text or "" for node in child if node.tag.endswith("}t"))
    return "".join(parts)


def read_shared_strings(workbook: ZipFile) -> list[str]:
    """Cells with t="s" store only an index into this workbook-wide table."""

    if "xl/sharedStrings.xml" not in workbook.namelist():
        return []
    strings: list[str] = []
    with workbook.open("xl/sharedStrings.xml") as source:
        for _, element in iterparse(source, events=("end",)):
            if element.tag.endswith("}si"):
                strings.append(_text_runs(element))
                element.clear()
    return strings


def cell_value(cell, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    style = cell.attrib.get("s")

    if cell_type == "inlineStr":
        # Join all rich-text runs, if present.
        return "".join(
            node.text or "" for node in cell.iter() if node.tag.endswith("}t")
        )

    value_node = next(
        (node for node in cell if node.tag.endswith("}v")), None
    )
    value = "" if value_node is None else value_node.text or ""
    if not value:
        return ""
    if cell_type == "s":
        return shared_strings[int(value)]
    if style in DATE_TIME_STYLES:
        return excel_date(value, date_only=False)
    if style in DATE_ONLY_STYLES:
        return excel_date(value, date_only=True)
    if cell_type == "n":
        return excel_number(value)
    return value


def convert(source: Path, destination: Path) -> tuple[int, int]:
    row_count = 0
    column_count = 0
    with (
        ZipFile(source) as workbook,
        workbook.open("xl/worksheets/sheet1.xml") as sheet,
        destination.open("w", encoding="utf-8-sig", newline="") as output,
    ):
        writer = csv.writer(output, lineterminator="\r\n")
        shared_strings = read_shared_strings(workbook)
        for _, element in iterparse(sheet, events=("end",)):
            if not element.tag.endswith("}row"):
                continue

            values: dict[int, str] = {}
            for cell in element:
                if cell.tag.endswith("}c"):
                    index = column_index(cell.attrib["r"])
                    values[index] = cell_value(cell, shared_strings)

            if row_count == 0:
                column_count = max(values, default=-1) + 1
            writer.writerow([values.get(i, "") for i in range(column_count)])
            row_count += 1
            element.clear()

    return row_count, column_count


def main() -> int:
    data_dir = Path("data")
    workbooks = [
        data_dir / "QOS_MAINTENANCE.xlsx",
        data_dir / "QOS_MAINT_CHECKIN_INFO.xlsx",
    ]
    for source in workbooks:
        destination = source.with_name(f"{source.stem}_utf8.csv")
        rows, columns = convert(source, destination)
        print(
            f"{source.name} -> {destination.name}: "
            f"{rows:,} rows, {columns} columns"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
