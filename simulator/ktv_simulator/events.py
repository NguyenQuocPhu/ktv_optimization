"""Giả lập hệ thống nguồn realtime: đổi export QOS tháng thành luồng sự kiện (JSONL).

Chỉ để chạy thử, không deploy. Định dạng sự kiện thật do team data/checklist quyết
định; file này cho simulator một luồng giống thật để thử xếp lại tuyến theo sự kiện.

    python -m ktv_simulator.events \\
        --maintenance data/QOS_MAINTENANCE_utf8.csv \\
        --boundary data/boundary_fake_from_checkins.geojson \\
        --checkins data/QOS_MAINT_CHECKIN_INFO_utf8.csv \\
        --gps data/sample_emp_coordinate.csv \\
        --out data/events_2026-06.jsonl

Dòng đầu là header (format, khoảng thời gian, danh mục chi nhánh). Mỗi dòng sau là
một sự kiện, sắp theo ``at`` (giờ địa phương, không múi giờ):

    JOB_CREATED  CREATE_DATE    job_id emp_account branch_name case_type address location area
    CHECKIN      CHECKIN_DATE   job_id emp_account location (LAT_LNG_IN)
    CHECKOUT     CHECKOUT_DATE  job_id emp_account location (LAT_LNG_OUT)
    JOB_CLOSED   FINISH_DATE    job_id emp_account status [at_inferred]
    GPS          CREATEDATE     emp_account location

Export chỉ lưu trạng thái cuối nên phải giả định:
- KTV được gán ngay lúc tạo job (export chỉ có EMP_ACCOUNT cuối cùng).
- ``location`` của job là tâm phường geocode từ địa chỉ (boundary tạm); ``area`` là tên
  phường đó, dùng làm khu vực cho rule hạn chế quay lại.
- Status cuối đã đóng mà FINISH_DATE trống (phần lớn "Đóng checklist"): lấy giờ
  check-in/checkout cuối, không có thì giờ tạo; đánh dấu ``at_inferred``.
- Status cuối còn mở: không có JOB_CLOSED.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

from .geocoding import WardBoundaryIndex
from .provider import EVENT_FORMAT, EVENT_TYPES

OPEN_STATUSES = frozenset({"Chưa phân công", "Đã phân công", "Đang xử lý"})

MAINTENANCE_COLUMNS = (
    "CHECKLIST_ID",
    "CHECKLIST_STATUS",
    "BRANCH_NAME",
    "CASE_TYPE",
    "OBJ_LOCATION",
    "EMP_ACCOUNT",
    "CREATE_DATE",
    "FINISH_DATE",
)
GPS_COLUMNS = ("ACCOUNTEMP", "COORDINATE", "CREATEDATE")
CHECKIN_COLUMNS = ("CHECKLIST_ID", "CHECKIN_DATE", "CHECKOUT_DATE", "LAT_LNG_IN", "LAT_LNG_OUT")
DATE_SENTINELS = ("-1", "1000-01-01", "1000-01-01 00:00:00")
COORDINATE_PATTERN = r"\(?\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)?"


def _read_csv(path: Path, columns: tuple[str, ...], encoding: str) -> pd.DataFrame:
    header = pd.read_csv(path, encoding=encoding, nrows=0).columns
    missing = sorted(set(columns) - set(header))
    if missing:
        raise ValueError(f"{path}: thiếu cột {', '.join(missing)}")
    return pd.read_csv(
        path,
        encoding=encoding,
        dtype="string",
        keep_default_na=False,
        usecols=list(columns),
    )


def _text(series: pd.Series) -> pd.Series:
    cleaned = series.str.strip()
    return cleaned.mask(cleaned.eq(""))


def _datetimes(series: pd.Series) -> pd.Series:
    cleaned = _text(series)
    cleaned = cleaned.mask(cleaned.isin(DATE_SENTINELS))
    return pd.to_datetime(cleaned, format="mixed", errors="coerce")


def _coordinates(series: pd.Series) -> tuple[pd.Series, pd.Series]:
    parts = series.str.extract(COORDINATE_PATTERN)
    lat = pd.to_numeric(parts[0], errors="coerce").astype("float64")
    lng = pd.to_numeric(parts[1], errors="coerce").astype("float64")
    inside = lat.between(8, 24) & lng.between(102, 110)  # Khung Việt Nam.
    return lat.where(inside), lng.where(inside)


def _load_jobs(path: Path, encoding: str) -> pd.DataFrame:
    frame = _read_csv(path, MAINTENANCE_COLUMNS, encoding)
    jobs = pd.DataFrame(
        {
            "job_id": _text(frame["CHECKLIST_ID"]),
            "status": _text(frame["CHECKLIST_STATUS"]),
            "branch_name": _text(frame["BRANCH_NAME"]),
            "case_type": _text(frame["CASE_TYPE"]).str.upper(),
            "address": _text(frame["OBJ_LOCATION"]),
            "emp_account": _text(frame["EMP_ACCOUNT"]),
            "created_at": _datetimes(frame["CREATE_DATE"]),
            "finished_at": _datetimes(frame["FINISH_DATE"]),
        }
    )
    jobs = jobs[jobs["job_id"].notna() & jobs["created_at"].notna()]
    # Export có CHECKLIST_ID lặp, chỉ khác SERVICES_LIST: giữ dòng đầu.
    return jobs.drop_duplicates("job_id", keep="first").reset_index(drop=True)


def _load_visits(path: Path, encoding: str) -> pd.DataFrame:
    frame = _read_csv(path, CHECKIN_COLUMNS, encoding)
    lat_in, lng_in = _coordinates(frame["LAT_LNG_IN"])
    lat_out, lng_out = _coordinates(frame["LAT_LNG_OUT"])
    visits = pd.DataFrame(
        {
            "job_id": _text(frame["CHECKLIST_ID"]),
            "checkin_at": _datetimes(frame["CHECKIN_DATE"]),
            "checkout_at": _datetimes(frame["CHECKOUT_DATE"]),
            "lat_in": lat_in,
            "lng_in": lng_in,
            "lat_out": lat_out,
            "lng_out": lng_out,
        }
    )
    return visits.dropna(subset=["job_id", "checkin_at"]).reset_index(drop=True)


def _load_gps(path: Path, encoding: str) -> pd.DataFrame:
    frame = _read_csv(path, GPS_COLUMNS, encoding)
    lat, lng = _coordinates(frame["COORDINATE"])
    gps = pd.DataFrame(
        {
            "emp_account": _text(frame["ACCOUNTEMP"]),
            "recorded_at": _datetimes(frame["CREATEDATE"]),
            "lat": lat,
            "lng": lng,
        }
    ).dropna()
    return gps.reset_index(drop=True)


_GEOCODER: WardBoundaryIndex | None = None


def _init_geocoder(boundary_geojson: str) -> None:
    global _GEOCODER
    _GEOCODER = WardBoundaryIndex.from_geojson(boundary_geojson)


def _geocode_chunk(addresses: list[str]) -> list[tuple[float, float, str] | None]:
    assert _GEOCODER is not None
    points: list[tuple[float, float, str] | None] = []
    for address in addresses:
        match = _GEOCODER.match(address)
        points.append(None if match is None else (match.point.lat, match.point.lng, match.ward_name))
    return points


def geocode(
    addresses: list[str], boundary_geojson: Path, workers: int | None = None
) -> dict[str, tuple[float, float, str] | None]:
    """Địa chỉ → tâm phường. Địa chỉ không khớp chính xác phải so gần đúng (chậm), nên chia nhiều tiến trình."""

    workers = workers or os.cpu_count() or 1
    if workers == 1 or len(addresses) < 2000:
        _init_geocoder(str(boundary_geojson))
        return dict(zip(addresses, _geocode_chunk(addresses)))
    size = max(500, len(addresses) // (workers * 8))
    chunks = [addresses[start : start + size] for start in range(0, len(addresses), size)]
    with ProcessPoolExecutor(workers, initializer=_init_geocoder, initargs=(str(boundary_geojson),)) as pool:
        points = [point for chunk in pool.map(_geocode_chunk, chunks) for point in chunk]
    return dict(zip(addresses, points))


def build_events(
    maintenance_csv: str | Path,
    boundary_geojson: str | Path,
    *,
    checkins_csv: str | Path | None = None,
    gps_csv: str | Path | None = None,
    encoding: str = "utf-8-sig",
    workers: int | None = None,
) -> tuple[dict, pd.DataFrame]:
    jobs = _load_jobs(Path(maintenance_csv), encoding)
    points = geocode(jobs["address"].dropna().unique().tolist(), Path(boundary_geojson), workers)
    located = jobs["address"].map(points)
    lat = located.map(lambda point: point[0] if isinstance(point, tuple) else None).astype("float64")
    lng = located.map(lambda point: point[1] if isinstance(point, tuple) else None).astype("float64")
    area = located.map(lambda point: point[2] if isinstance(point, tuple) else None)
    frames = [
        pd.DataFrame(
            {
                "at": jobs["created_at"],
                "type": "JOB_CREATED",
                "job_id": jobs["job_id"],
                "emp_account": jobs["emp_account"],
                "branch_name": jobs["branch_name"],
                "case_type": jobs["case_type"],
                "address": jobs["address"],
                "lat": lat,
                "lng": lng,
                "area": area,
            }
        )
    ]

    last_visit = pd.Series(dtype="datetime64[ns]")
    if checkins_csv:
        visits = _load_visits(Path(checkins_csv), encoding).merge(
            jobs[["job_id", "emp_account"]], on="job_id", how="inner"
        )
        checked_out = visits[visits["checkout_at"].notna()]
        frames += [
            pd.DataFrame(
                {
                    "at": visits["checkin_at"],
                    "type": "CHECKIN",
                    "job_id": visits["job_id"],
                    "emp_account": visits["emp_account"],
                    "lat": visits["lat_in"],
                    "lng": visits["lng_in"],
                }
            ),
            pd.DataFrame(
                {
                    "at": checked_out["checkout_at"],
                    "type": "CHECKOUT",
                    "job_id": checked_out["job_id"],
                    "emp_account": checked_out["emp_account"],
                    "lat": checked_out["lat_out"],
                    "lng": checked_out["lng_out"],
                }
            ),
        ]
        last_visit = (
            visits.assign(last=visits[["checkin_at", "checkout_at"]].max(axis=1))
            .groupby("job_id")["last"]
            .max()
        )

    finished = jobs["finished_at"]
    closed = finished.notna() | ~jobs["status"].isin(OPEN_STATUSES)
    close_at = finished.fillna(jobs["job_id"].map(last_visit)).fillna(jobs["created_at"])
    frames.append(
        pd.DataFrame(
            {
                "at": close_at[closed],
                "type": "JOB_CLOSED",
                "job_id": jobs.loc[closed, "job_id"],
                "emp_account": jobs.loc[closed, "emp_account"],
                "status": jobs.loc[closed, "status"],
                "at_inferred": finished[closed].isna(),
            }
        )
    )

    if gps_csv:
        gps = _load_gps(Path(gps_csv), encoding)
        frames.append(
            pd.DataFrame(
                {
                    "at": gps["recorded_at"].dt.floor("s"),
                    "type": "GPS",
                    "emp_account": gps["emp_account"],
                    "lat": gps["lat"],
                    "lng": gps["lng"],
                }
            )
        )

    events = pd.concat(frames, ignore_index=True)
    events["rank"] = events["type"].map({name: order for order, name in enumerate(EVENT_TYPES)})
    events = events.sort_values(["at", "rank", "job_id"], kind="stable", na_position="last").reset_index(drop=True)

    def iso(value: pd.Timestamp) -> str:
        return value.strftime("%Y-%m-%dT%H:%M:%S")

    counts = events["type"].value_counts()
    header = {
        "format": EVENT_FORMAT,
        "note": "Giả lập từ export QOS, không phải định dạng chính thức của team data",
        "from": iso(events["at"].min()),
        "to": iso(events["at"].max()),
        "created_from": iso(jobs["created_at"].min()),
        "created_to": iso(jobs["created_at"].max()),
        "events": {name: int(counts[name]) for name in EVENT_TYPES if name in counts},
        "jobs": len(jobs),
        "geocoded_jobs": int(lat.notna().sum()),
        "inferred_closes": int((closed & finished.isna()).sum()),
        "branches": {
            name: int(count) for name, count in sorted(jobs["branch_name"].value_counts().items())
        },
        "case_types": sorted(jobs["case_type"].dropna().unique().tolist()),
    }
    return header, events


def _dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _event(at, kind, job_id, emp_account, branch_name, case_type, address, lat, lng, status, at_inferred, area) -> dict:
    event = {"at": at, "type": kind}
    if kind != "GPS":
        event["job_id"] = job_id
    event["emp_account"] = emp_account
    if kind == "JOB_CREATED":
        event.update(branch_name=branch_name, case_type=case_type, address=address, area=area)
    if kind == "JOB_CLOSED":
        event["status"] = status
        if at_inferred:
            event["at_inferred"] = True
    else:
        event["location"] = None if lat is None or lng is None else {"lat": round(lat, 7), "lng": round(lng, 7)}
    return event


def write_events(path: str | Path, header: dict, events: pd.DataFrame) -> None:
    def values(name: str) -> list:
        series = events[name] if name in events else pd.Series(None, index=events.index, dtype=object)
        return series.astype(object).where(series.notna(), None).tolist()

    rows = zip(
        events["at"].dt.strftime("%Y-%m-%dT%H:%M:%S").tolist(),
        events["type"].tolist(),
        *(
            values(name)
            for name in ("job_id", "emp_account", "branch_name", "case_type", "address", "lat", "lng", "status", "at_inferred", "area")
        ),
    )
    with Path(path).open("w", encoding="utf-8") as handle:
        handle.write(_dumps(header) + "\n")
        for row in rows:
            handle.write(_dumps(_event(*row)) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ktv_simulator.events",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--maintenance", type=Path, required=True, help="CSV export QOS maintenance")
    parser.add_argument("--boundary", type=Path, required=True, help="GeoJSON ranh giới phường/xã")
    parser.add_argument("--checkins", type=Path, help="CSV check-in: CHECKLIST_ID, CHECKIN_DATE, CHECKOUT_DATE, LAT_LNG_IN, LAT_LNG_OUT")
    parser.add_argument("--gps", type=Path, help="CSV GPS: ACCOUNTEMP, COORDINATE, CREATEDATE")
    parser.add_argument("--encoding", default="utf-8-sig")
    parser.add_argument("--workers", type=int, help="Số tiến trình geocode (mặc định: số CPU)")
    parser.add_argument("--out", type=Path, required=True, help="File JSONL đầu ra")
    args = parser.parse_args(argv)

    started = time.perf_counter()
    header, events = build_events(
        args.maintenance,
        args.boundary,
        checkins_csv=args.checkins,
        gps_csv=args.gps,
        encoding=args.encoding,
        workers=args.workers,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    write_events(args.out, header, events)
    counts = " ".join(f"{name}={count:,}" for name, count in header["events"].items())
    print(f"{args.out}: {len(events):,} sự kiện, {header['from']} → {header['to']}, {time.perf_counter() - started:.0f}s")
    print(
        f"{counts} | geocode {header['geocoded_jobs']:,}/{header['jobs']:,} job | "
        f"JOB_CLOSED suy ra giờ: {header['inferred_closes']:,}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
