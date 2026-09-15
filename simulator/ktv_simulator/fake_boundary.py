"""TẠM: dựng GeoJSON phường/xã giả từ tọa độ check-in thật khi chưa có boundary.

Mỗi phường/xã là một hình vuông nhỏ quanh trung vị tọa độ check-in của các
checklist có địa chỉ ghi phường/xã đó. Chỉ đủ để simulator có tọa độ gần đúng;
thay bằng file boundary thật khi có. Phường/xã chưa từng có check-in sẽ không
geocode được.

    PYTHONPATH=src:simulator .venv/bin/python -m ktv_simulator.fake_boundary \\
        --maintenance data/QOS_MAINTENANCE_utf8.csv \\
        --checkins data/QOS_MAINT_CHECKIN_INFO_utf8.csv \\
        --out data/boundary_fake_from_checkins.geojson
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .geocoding import normalize_vietnamese_text
from .events import _coordinates, _read_csv, _text

WARD_PREFIXES = ("thi tran ", "phuong ", "xa ")
HALF_SIDE_DEGREES = 0.0005  # Nửa cạnh hình vuông, khoảng 55 m.


def ward_and_province(address: str) -> tuple[str, str] | None:
    """"..., Phuong Xuan Hoa, Phu Tho" → ("Xuan Hoa", "Phu Tho")."""

    parts = [part.strip() for part in address.split(",") if part.strip()]
    if len(parts) < 2:
        return None
    for part in reversed(parts[:-1]):
        normalized = normalize_vietnamese_text(part)
        for prefix in WARD_PREFIXES:
            if normalized.startswith(prefix) and len(normalized) > len(prefix):
                ward = " ".join(part.split()[len(prefix.split()) :])
                return (ward, parts[-1]) if ward else None
    return None


def build(
    maintenance_csv: Path, checkins_csv: Path, *, min_points: int, encoding: str
) -> tuple[dict, dict]:
    jobs = _read_csv(maintenance_csv, ("CHECKLIST_ID", "OBJ_LOCATION"), encoding)
    visits = _read_csv(checkins_csv, ("CHECKLIST_ID", "LAT_LNG_IN"), encoding)

    lat, lng = _coordinates(visits["LAT_LNG_IN"])
    points = pd.DataFrame(
        {"job_id": _text(visits["CHECKLIST_ID"]), "lat": lat, "lng": lng}
    ).dropna()
    addresses = (
        pd.DataFrame(
            {"job_id": _text(jobs["CHECKLIST_ID"]), "address": _text(jobs["OBJ_LOCATION"])}
        )
        .dropna()
        .drop_duplicates("job_id")
    )
    parsed = {
        address: ward_and_province(address)
        for address in addresses["address"].unique()
    }
    addresses["parsed"] = addresses["address"].map(parsed)
    addresses = addresses[addresses["parsed"].notna()]
    addresses["ward"] = addresses["parsed"].str[0]
    addresses["province"] = addresses["parsed"].str[1]
    addresses["ward_key"] = addresses["ward"].map(normalize_vietnamese_text)
    addresses["province_key"] = addresses["province"].map(normalize_vietnamese_text)

    merged = points.merge(addresses, on="job_id")
    groups = merged.groupby(["province_key", "ward_key"], sort=True).agg(
        ward=("ward", "first"),
        province=("province", "first"),
        lat=("lat", "median"),
        lng=("lng", "median"),
        checkins=("job_id", "size"),
    )
    kept = groups[groups["checkins"] >= min_points]

    features = []
    for index, row in enumerate(kept.itertuples(index=False), 1):
        west, east = row.lng - HALF_SIDE_DEGREES, row.lng + HALF_SIDE_DEGREES
        south, north = row.lat - HALF_SIDE_DEGREES, row.lat + HALF_SIDE_DEGREES
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "ma_xa": f"FAKE{index:05d}",
                    "ten_xa": row.ward,
                    "tinh_tp": row.province,
                    "checkin_count": int(row.checkins),
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[west, south], [east, south], [east, north], [west, north], [west, south]]
                    ],
                },
            }
        )
    stats = {
        "checklists_with_address": len(addresses),
        "checkins_with_coordinate": len(points),
        "checkins_matched_to_ward": len(merged),
        "wards_found": len(groups),
        "wards_kept": len(kept),
        "min_points": min_points,
    }
    return {"type": "FeatureCollection", "features": features}, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ktv_simulator.fake_boundary",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--maintenance", type=Path, required=True)
    parser.add_argument("--checkins", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("data/boundary_fake_from_checkins.geojson"))
    parser.add_argument("--min-points", type=int, default=3, help="Số check-in tối thiểu mỗi phường/xã")
    parser.add_argument("--encoding", default="utf-8-sig")
    args = parser.parse_args(argv)

    collection, stats = build(
        args.maintenance, args.checkins, min_points=args.min_points, encoding=args.encoding
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(collection, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False))
    print(f"Đã ghi {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
