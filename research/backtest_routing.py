"""Backtest routing trên lịch sử: planner khác cách KTV thật làm ở đâu, ETA đoán đúng tới đâu.

    PYTHONPATH=src:simulator .venv/bin/python research/backtest_routing.py \\
        --data-dir data --events data/events_2026-06.jsonl \\
        --time-model artifacts/models/time_model.json \\
        --from 2026-06-16 --to 2026-07-01 --out artifacts/backtest/2026-06-16_30.json

Không deploy. Tọa độ job lấy từ check-in thật (tâm phường giả lệch ~1,6 km trong khi
mỗi chặng thật chỉ ~1,1 km); ``due_at`` theo SLA giả định của simulator.

A. Chọn job kế tiếp. Mỗi lần KTV checkout job A rồi check-in job B trong cùng ngày:
   dựng trạng thái lúc checkout từ luồng sự kiện (job đang mở của KTV), bỏ các job
   đã có checkout (đang chờ đóng). B có đứng đầu tuyến planner không, có phải job
   gần nhất không, so với chọn ngẫu nhiên.
B. Xếp lại cả ngày. Bắt đầu từ checkout job đầu tiên trong ngày, các job KTV làm sau
   đó được xếp theo thứ tự thật, thứ tự planner và thứ tự gần nhất. So km và số job
   trễ dưới cùng một mô hình thời gian.
C. ETA theo thứ tự thật: giờ tới dự đoán so với giờ check-in thật, theo số bước tính
   từ điểm xuất phát; cấu hình hiện tại so với mô hình học.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd

from ktv_routing import (
    GeoPoint,
    HaversineTravel,
    JobFilter,
    JobInput,
    JobState,
    LocationFix,
    RouteRequest,
    RoutingConfig,
    TechnicianInput,
    TechnicianRoute,
    TravelModel,
    WorkloadQuery,
    distance_km,
    load_time_model,
    plan_routes,
    travel_model,
)
from ktv_simulator.provider import EventWorkloadProvider, job_deadlines

from time_model import CURRENT, LEARNED, load_transitions, load_visits

ACTUAL, PLANNER, NEAREST = "thực tế", "planner", "gần nhất"


def job_points(visits: pd.DataFrame) -> dict[str, GeoPoint]:
    """Tọa độ check-in đầu tiên của từng job: chỗ KTV thật sự đã tới."""

    located = visits.dropna(subset=["lat_in", "lng_in"]).sort_values("checkin_at", kind="stable").drop_duplicates("job_id")
    return {
        job_id: GeoPoint(float(lat), float(lng))
        for job_id, lat, lng in zip(located["job_id"], located["lat_in"], located["lng_in"])
    }


def first_checkouts(visits: pd.DataFrame) -> dict[str, datetime]:
    done = visits.dropna(subset=["checkout_at"]).groupby("job_id")["checkout_at"].min()
    return {job_id: value.to_pydatetime() for job_id, value in done.items()}


def job_created_at(visits: pd.DataFrame) -> dict[str, datetime]:
    created = visits.drop_duplicates("job_id").set_index("job_id")["created_at"].dropna()
    return {job_id: value.to_pydatetime() for job_id, value in created.items()}


def _route(
    emp_account: str,
    jobs,
    origin: GeoPoint,
    at: datetime,
    config: RoutingConfig,
    travel: TravelModel,
    previous: tuple[str, ...] = (),
) -> TechnicianRoute:
    technician = TechnicianInput(
        emp_account=emp_account,
        jobs=tuple(jobs),
        last_location=LocationFix(location=origin, recorded_at=at, source="CHECKOUT"),
        shift_start=None,
        shift_end=None,
        previous_sequence=previous,
    )
    request = RouteRequest(planned_at=at, technicians=(technician,), filter=JobFilter())
    return plan_routes(request, config, travel).routes[0]


def follow_order(jobs, emp_account: str, origin: GeoPoint, at: datetime, config: RoutingConfig, travel: TravelModel) -> TechnicianRoute:
    """ETA, trễ và chi phí rule cho đúng thứ tự cho sẵn, tính bằng chính planner.

    Truyền thứ tự làm tuyến cũ và bắt planner luôn giữ tuyến cũ (rule KEEP_PREVIOUS_ORDER).
    """

    keep = replace(config, rules=replace(config.rules, previous_route_policy="KEEP"))
    return _route(emp_account, jobs, origin, at, keep, travel, previous=tuple(job.job_id for job in jobs))


def next_job_decisions(
    provider: EventWorkloadProvider,
    transitions: pd.DataFrame,
    *,
    points: dict[str, GeoPoint],
    done_at: dict[str, datetime],
    created_at: dict[str, datetime],
    config: RoutingConfig,
    travel: TravelModel,
    start,
    end,
    branch_names: tuple[str, ...] = (),
    limit: int | None = None,
    progress: bool = False,
) -> tuple[pd.DataFrame, Counter]:
    moves = transitions[
        (transitions["checkout_at"] >= start) & (transitions["checkout_at"] < end) & (transitions["raw_gap_minutes"] >= 0)
    ]
    if branch_names:
        moves = moves[moves["branch_name"].isin(branch_names)]
    moves = moves.sort_values(["checkout_at", "emp_account"], kind="stable")
    if limit is not None:
        moves = moves.head(limit)

    rows: list[dict] = []
    skipped: Counter = Counter()
    clock = perf_counter()
    columns = ("emp_account", "checkout_at", "next_job_id", "from_lat", "from_lng")
    for index, (account, checkout, chosen, lat, lng) in enumerate(zip(*(moves[name] for name in columns)), 1):
        if progress and index % 20000 == 0:
            print(f"  … {index:,}/{len(moves):,} lần chọn job, {perf_counter() - clock:.0f}s", flush=True)
        at = checkout.to_pydatetime()
        request, _ = provider.build(WorkloadQuery(at, JobFilter(emp_accounts=(account,))))
        jobs = request.technicians[0].jobs if request.technicians else ()
        if any(job.state is JobState.IN_PROGRESS for job in jobs):
            skipped["KTV còn lượt chưa checkout ở job khác"] += 1
            continue
        candidates = [
            replace(job, location=points.get(job.job_id, job.location))
            for job in jobs
            if not (job.job_id in done_at and done_at[job.job_id] <= at)
        ]
        if chosen not in {job.job_id for job in candidates}:
            if chosen in created_at and created_at[chosen] > at:
                skipped["job kế tiếp được tạo sau lúc checkout"] += 1
            elif chosen in done_at and done_at[chosen] <= at:
                skipped["job kế tiếp là quay lại job đã làm"] += 1
            else:
                skipped["job kế tiếp không thuộc KTV lúc checkout"] += 1
            continue
        candidates = [job for job in candidates if job.location is not None]
        if len(candidates) < 2:
            skipped["chỉ còn 1 job để chọn"] += 1
            continue

        origin = GeoPoint(float(lat), float(lng))
        planned = [stop.job_id for stop in _route(account, candidates, origin, at, config, travel).stops]
        distance = {job.job_id: distance_km(origin, job.location) for job in candidates}
        nearest = sorted(distance, key=lambda job_id: (distance[job_id], job_id))
        # Hạn = giờ tạo + SLA theo loại việc, nên hạn muộn nhất ≈ job mới tạo nhất.
        newest = [
            job.job_id
            for job in sorted(
                candidates,
                key=lambda item: (item.due_at is None, -(item.due_at.timestamp() if item.due_at else 0), item.job_id),
            )
        ]
        rows.append(
            {
                "emp_account": account,
                "checkout_at": at,
                "candidates": len(candidates),
                "real_points": sum(job.job_id in points for job in candidates),
                "planner_rank": planned.index(chosen) + 1,
                "nearest_rank": nearest.index(chosen) + 1,
                "newest_rank": newest.index(chosen) + 1,
                "chosen_km": distance[chosen],
                "nearest_km": distance[nearest[0]],
                "planner_first_km": distance[planned[0]],
            }
        )
    return pd.DataFrame(rows), skipped


def _percent(mask: pd.Series) -> float:
    return round(float(mask.mean() * 100), 1)


def summarize_decisions(frame: pd.DataFrame, skipped: Counter) -> dict:
    ordered_skips = dict(skipped.most_common())
    if frame.empty:
        return {"decisions": 0, "skipped": ordered_skips}

    def block(part: pd.DataFrame) -> dict:
        spread = part["candidates"] - 1
        return {
            "decisions": len(part),
            "mean_candidates": round(float(part["candidates"].mean()), 1),
            "planner_top1_percent": _percent(part["planner_rank"] == 1),
            "nearest_top1_percent": _percent(part["nearest_rank"] == 1),
            "newest_top1_percent": _percent(part["newest_rank"] == 1),
            "random_top1_percent": round(float((1 / part["candidates"]).mean() * 100), 1),
            # 0 = job KTV chọn luôn đứng đầu, 1 = luôn đứng cuối, ngẫu nhiên ≈ 0,5.
            "planner_rank_position": round(float(((part["planner_rank"] - 1) / spread).mean()), 3),
            "nearest_rank_position": round(float(((part["nearest_rank"] - 1) / spread).mean()), 3),
            "newest_rank_position": round(float(((part["newest_rank"] - 1) / spread).mean()), 3),
        }

    groups = pd.cut(frame["candidates"], [1, 2, 4, 9, np.inf], labels=["2 job", "3–4 job", "5–9 job", "≥10 job"])
    return {
        **block(frame),
        "real_point_percent": round(float(frame["real_points"].sum() / frame["candidates"].sum() * 100), 1),
        "km_median": {
            "chosen": round(float(frame["chosen_km"].median()), 2),
            "nearest": round(float(frame["nearest_km"].median()), 2),
            "planner_first": round(float(frame["planner_first_km"].median()), 2),
        },
        "by_candidates": {str(label): block(part) for label, part in frame.groupby(groups, observed=True)},
        "skipped": ordered_skips,
    }


def _nearest_order(origin: GeoPoint, jobs: list[JobInput]) -> list[JobInput]:
    remaining, here, order = list(jobs), origin, []
    while remaining:
        job = min(remaining, key=lambda item: (distance_km(here, item.location), item.job_id))
        remaining.remove(job)
        order.append(job)
        here = job.location
    return order


def day_replays(
    visits: pd.DataFrame,
    *,
    configs: dict[str, RoutingConfig],
    travel: TravelModel,
    start,
    end,
    branch_names: tuple[str, ...] = (),
    min_jobs: int = 3,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = visits[_in_period(visits["checkin_at"], start, end) & visits["lat_in"].notna()]
    if branch_names:
        frame = frame[frame["branch_name"].isin(branch_names)]
    frame = (
        frame.assign(day=frame["checkin_at"].dt.normalize())
        .sort_values(["emp_account", "checkin_at", "job_id"], kind="stable")
        .drop_duplicates(["emp_account", "day", "job_id"])
    )
    days: list[dict] = []
    etas: list[dict] = []
    for (account, day), group in frame.groupby(["emp_account", "day"], sort=False):
        head = group.iloc[0]
        if len(group) < min_jobs or pd.isna(head["checkout_at"]):
            continue
        rest = group.iloc[1:]
        at = head["checkout_at"].to_pydatetime()
        origin = GeoPoint(
            float(head["lat_out"] if pd.notna(head["lat_out"]) else head["lat_in"]),
            float(head["lng_out"] if pd.notna(head["lng_out"]) else head["lng_in"]),
        )
        jobs = []
        for row in rest.itertuples(index=False):
            case_type = None if pd.isna(row.case_type) else row.case_type
            due_at, complete_by, priority = (
                job_deadlines(case_type, row.created_at.to_pydatetime()) if pd.notna(row.created_at) else (None, None, None)
            )
            jobs.append(
                JobInput(
                    job_id=row.job_id,
                    state=JobState.PENDING,
                    location=GeoPoint(float(row.lat_in), float(row.lng_in)),
                    case_type=case_type,
                    address=None,
                    due_at=due_at,
                    priority=priority,
                    started_at=None,
                    complete_by=complete_by,
                )
            )
        due = {job.job_id: job.due_at for job in jobs}
        by_id = {job.job_id: job for job in jobs}
        checkins = dict(zip(rest["job_id"], rest["checkin_at"]))
        nearest = _nearest_order(origin, jobs)
        record = {
            "emp_account": account,
            "day": day.date().isoformat(),
            "jobs": len(jobs),
            # Trễ thật: check-in thật sau hạn check-in.
            "late_real": sum(
                due[job_id] is not None and checkin.to_pydatetime() > due[job_id] for job_id, checkin in checkins.items()
            ),
        }
        for config_name, config in configs.items():
            # Planner xếp bằng đúng cấu hình đang chấm: giờ tới khác thì thứ tự tối ưu cũng khác.
            orders = {
                ACTUAL: jobs,
                PLANNER: [by_id[stop.job_id] for stop in _route(account, jobs, origin, at, config, travel).stops],
                NEAREST: nearest,
            }
            for order_name, order in orders.items():
                route = follow_order(order, account, origin, at, config, travel)
                late_minutes = [
                    (stop.eta + timedelta(minutes=stop.wait_minutes or 0) - due[stop.job_id]).total_seconds() / 60
                    for stop in route.stops
                    if stop.late
                ]
                record[f"{order_name}|{config_name}|km"] = route.total_km
                record[f"{order_name}|{config_name}|late"] = len(late_minutes)
                record[f"{order_name}|{config_name}|late_minutes"] = sum(late_minutes)
                if order_name == ACTUAL:
                    etas.extend(
                        {
                            "config": config_name,
                            "step": step,
                            "error_minutes": (stop.eta - checkins[stop.job_id].to_pydatetime()).total_seconds() / 60,
                        }
                        for step, stop in enumerate(route.stops, 1)
                        if stop.eta is not None
                    )
        days.append(record)
    return pd.DataFrame(days), pd.DataFrame(etas)


def _in_period(series: pd.Series, start, end) -> pd.Series:
    return (series >= start) & (series < end)


def summarize_days(days: pd.DataFrame, config_names: list[str]) -> dict:
    if days.empty:
        return {"days": 0}
    orders = (ACTUAL, PLANNER, NEAREST)
    return {
        "days": len(days),
        "jobs": int(days["jobs"].sum()),
        "late_real": int(days["late_real"].sum()),
        "km": {name: {order: round(float(days[f"{order}|{name}|km"].sum()), 1) for order in orders} for name in config_names},
        "days_shorter_than_actual_percent": {
            name: {
                order: _percent(days[f"{order}|{name}|km"] < days[f"{ACTUAL}|{name}|km"] - 1e-6) for order in (PLANNER, NEAREST)
            }
            for name in config_names
        },
        "late": {name: {order: int(days[f"{order}|{name}|late"].sum()) for order in orders} for name in config_names},
        "late_minutes": {
            name: {order: round(float(days[f"{order}|{name}|late_minutes"].sum())) for order in orders} for name in config_names
        },
    }


def summarize_etas(etas: pd.DataFrame) -> dict:
    if etas.empty:
        return {}
    steps = etas["step"].clip(upper=4).map({1: "1", 2: "2", 3: "3", 4: "≥4"})
    result = {}
    for name, part in etas.groupby("config", sort=False):
        result[name] = {}
        for label, rows in [("tất cả", part), *part.groupby(steps.loc[part.index], sort=True)]:
            error = rows["error_minutes"]
            result[name][str(label)] = {
                "n": len(rows),
                "mae": round(float(error.abs().mean()), 1),
                "median_abs": round(float(error.abs().median()), 1),
                "bias": round(float(error.mean()), 1),
                "within_30_min_percent": _percent(error.abs() <= 30),
            }
    return result


def print_report(result: dict) -> None:
    decisions = result["next_job"]
    print(f"\n## A. Chọn job kế tiếp: {decisions['decisions']:,} lần chọn có ≥ 2 job")
    if decisions["decisions"]:
        print(
            f"Trung bình {decisions['mean_candidates']} job để chọn · {decisions['real_point_percent']}% job có tọa độ "
            "check-in thật (còn lại là tâm phường)"
        )
        print("  Tỷ lệ đoán đúng job KTV làm tiếp theo, và vị trí trung bình của job đó trong từng danh sách:")
        print(
            f"  {'nhóm':<9}{'lần chọn':>9}{'planner':>9}{'gần nhất':>10}{'mới nhất':>10}{'ngẫu nhiên':>12}"
            f"{'vị trí planner':>16}{'vị trí km':>11}{'vị trí mới':>12}"
        )
        for label, block in [("tất cả", decisions), *decisions["by_candidates"].items()]:
            print(
                f"  {label:<9}{block['decisions']:>9,}{block['planner_top1_percent']:>8}%{block['nearest_top1_percent']:>9}%"
                f"{block['newest_top1_percent']:>9}%{block['random_top1_percent']:>11}%{block['planner_rank_position']:>16}"
                f"{block['nearest_rank_position']:>11}{block['newest_rank_position']:>12}"
            )
        km = decisions["km_median"]
        print(f"  km tới job kế (median): KTV chọn {km['chosen']} · job gần nhất {km['nearest']} · job planner xếp đầu {km['planner_first']}")
        print("  (vị trí: 0 = job KTV chọn đứng đầu danh sách, 1 = đứng cuối, ngẫu nhiên ≈ 0,5)")
    print("  Bỏ qua: " + " · ".join(f"{reason} {count:,}" for reason, count in decisions["skipped"].items()))

    days = result["day_replay"]
    print(f"\n## B. Xếp lại cả ngày: {days['days']:,} KTV-ngày, {days.get('jobs', 0):,} job (sau job đầu tiên)")
    if days["days"]:
        orders = (ACTUAL, PLANNER, NEAREST)
        print("  " + f"{'':<28}" + "".join(f"{order:>12}" for order in orders))
        for name in days["km"]:
            print(f"  -- planner xếp và chấm bằng cấu hình: {name}")
            print("  " + f"{'tổng km (chim bay/đường bộ)':<28}" + "".join(f"{days['km'][name][order]:>12,.0f}" for order in orders))
            print(
                "  " + f"{'ngày ngắn hơn thực tế':<28}" + f"{'':>12}"
                + "".join(f"{days['days_shorter_than_actual_percent'][name][order]:>11}%" for order in (PLANNER, NEAREST))
            )
            print("  " + f"{'job check-in trễ':<28}" + "".join(f"{days['late'][name][order]:>12,}" for order in orders))
            print("  " + f"{'tổng phút check-in trễ':<28}" + "".join(f"{days['late_minutes'][name][order]:>12,}" for order in orders))
        print(f"  job trễ thật (check-in thật > hạn check-in): {days['late_real']:,}")

    print("\n## C. ETA theo thứ tự thật (dự đoán − giờ check-in thật, phút)")
    for name, blocks in result["eta"].items():
        for label, metrics in blocks.items():
            print(
                f"  {name:<12} bước {label:<7} n {metrics['n']:>8,} · MAE {metrics['mae']:>6} · median|lỗi| {metrics['median_abs']:>5}"
                f" · lệch TB {metrics['bias']:>+7} · ±30 phút {metrics['within_30_min_percent']:>5}%"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--events", type=Path, default=Path("data/events_2026-06.jsonl"))
    parser.add_argument("--encoding", default="utf-8-sig")
    parser.add_argument("--time-model", type=Path, help="JSON từ research/time_model.py; bỏ trống thì chỉ chấm cấu hình hiện tại")
    parser.add_argument("--from", dest="start", type=pd.Timestamp, default=pd.Timestamp("2026-06-16"))
    parser.add_argument("--to", dest="end", type=pd.Timestamp, default=pd.Timestamp("2026-07-01"))
    parser.add_argument("--branch", action="append", default=[], dest="branch_names")
    parser.add_argument("--travel", choices=("haversine", "osrm"), default="haversine", help="Nguồn km (mặc định chim bay)")
    parser.add_argument("--osrm-url", help="OSRM tự host khi --travel osrm")
    parser.add_argument("--min-jobs", type=int, default=3, help="Số job tối thiểu của một KTV-ngày ở phần B")
    parser.add_argument("--limit", type=int, help="Chỉ lấy N lần chọn job đầu tiên (chạy thử nhanh)")
    parser.add_argument("--out", type=Path, help="Ghi kết quả JSON")
    args = parser.parse_args(argv)
    if args.start >= args.end:
        parser.error("--from phải trước --to")
    if args.travel == "osrm" and not args.osrm_url:
        parser.error("--travel osrm cần --osrm-url")

    started = perf_counter()
    visits = load_visits(
        args.data_dir / "QOS_MAINTENANCE_utf8.csv", args.data_dir / "QOS_MAINT_CHECKIN_INFO_utf8.csv", args.encoding
    )
    transitions = load_transitions(visits)
    print(f"Nạp {len(visits):,} lượt check-in, {len(transitions):,} cặp chuyển job ({perf_counter() - started:.0f}s)", flush=True)

    configs = {CURRENT: RoutingConfig()}
    if args.time_model is not None:
        configs[LEARNED] = load_time_model(args.time_model)
    travel = HaversineTravel() if args.travel == "haversine" else travel_model("osrm", osrm_url=args.osrm_url)
    branch_names = tuple(args.branch_names)

    clock = perf_counter()
    with EventWorkloadProvider(args.events) as provider:
        decisions, skipped = next_job_decisions(
            provider,
            transitions,
            points=job_points(visits),
            done_at=first_checkouts(visits),
            created_at=job_created_at(visits),
            config=configs.get(LEARNED, configs[CURRENT]),  # Có mô hình học thì dùng: giờ tới sát thực tế hơn.
            travel=travel,
            start=args.start,
            end=args.end,
            branch_names=branch_names,
            limit=args.limit,
            progress=True,
        )
    print(f"Phần A xong ({perf_counter() - clock:.0f}s)", flush=True)
    clock = perf_counter()
    days, etas = day_replays(
        visits, configs=configs, travel=travel, start=args.start, end=args.end, branch_names=branch_names, min_jobs=args.min_jobs
    )
    print(f"Phần B, C xong ({perf_counter() - clock:.0f}s)", flush=True)

    result = {
        "period": {"from": args.start.isoformat(), "to": args.end.isoformat()},
        "branches": list(branch_names) or "tất cả",
        "travel": args.travel,
        "time_model": str(args.time_model) if args.time_model else None,
        "next_job_config": LEARNED if LEARNED in configs else CURRENT,
        "next_job": summarize_decisions(decisions, skipped),
        "day_replay": summarize_days(days, list(configs)),
        "eta": summarize_etas(etas),
    }
    print_report(result)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\nĐã ghi {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
