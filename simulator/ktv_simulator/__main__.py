"""Simulator: đóng vai team data realtime (đọc luồng sự kiện) rồi xếp tuyến.

Tạo luồng sự kiện một lần từ export QOS: ``python -m ktv_simulator.events --help``.

Chạy batch, ghi JSON cho từng mốc giờ:

    python -m ktv_simulator \\
        --events data/events_2026-06.jsonl --shift 08:00-17:30 \\
        --at 2026-06-15T09:00 --at 2026-06-15T13:00 \\
        --case-type MAINTENANCE --branch HNI_04

Mỗi mốc --at ghi query.json, request.json, response.json vào một thư mục con.

Tua realtime: xếp đầy đủ lúc FROM, sau đó mỗi nhịp chỉ xếp lại KTV có sự kiện job:

    python -m ktv_simulator --events ... --branch HNI_04 \\
        --replay 2026-06-15T08:00 2026-06-15T18:00 --step-minutes 5

Thay bằng --serve 8766 để mở web demo (đóng vai frontend).
Km/phút mặc định lấy từ OSRM public; --travel haversine để dùng chim bay.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime, time, timedelta
from pathlib import Path
from time import perf_counter

from ktv_routing import (
    PUBLIC_OSRM_URL,
    JobFilter,
    RoutingConfig,
    TravelModel,
    WorkloadQuery,
    load_rules,
    load_time_model,
    plan_routes,
    to_json_dict,
    travel_model,
)

from .provider import EventWorkloadProvider
from .web import replay_step, serve


def _shift(value: str) -> tuple[time, time]:
    try:
        start, end = (time.fromisoformat(part.strip()) for part in value.split("-"))
    except ValueError:
        raise argparse.ArgumentTypeError("cần dạng HH:MM-HH:MM") from None
    if end <= start:
        raise argparse.ArgumentTypeError("giờ kết thúc ca phải sau giờ bắt đầu")
    return start, end


def _write(path: Path, payload: object) -> None:
    text = json.dumps(to_json_dict(payload), ensure_ascii=False, indent=2)
    path.write_text(text + "\n", encoding="utf-8")


def _replay(
    provider: EventWorkloadProvider,
    config: RoutingConfig,
    travel: TravelModel,
    job_filter: JobFilter,
    start: datetime,
    end: datetime,
    step: timedelta,
) -> None:
    clock = perf_counter()
    request, stats = provider.build(WorkloadQuery(start, job_filter))
    summary = plan_routes(request, config, travel).summary
    print(
        f"{start.isoformat(timespec='minutes')} | xếp đầy đủ: KTV={summary.technicians} "
        f"điểm={summary.routed_stops} | đọc {stats.events_applied:,} sự kiện + xếp tuyến "
        f"{perf_counter() - clock:.1f}s"
    )
    step_ms: list[float] = []
    events = replans = 0
    at = start
    while at < end:
        to = min(at + step, end)
        result = replay_step(provider, config, travel, WorkloadQuery(at, job_filter), to)
        routing_ms = result["response"]["summary"]["planning_ms"] if result["response"] else 0.0
        step_ms.append(result["data_ms"] + routing_ms)
        events += result["events"]
        replans += len(result["affected"])
        if result["events"]:
            print(
                f"{to.isoformat(timespec='minutes')} | sự kiện job {result['events']:>4} | "
                f"xếp lại {len(result['affected']):>4} KTV | data {result['data_ms']:7.1f} ms | "
                f"routing {routing_ms:7.1f} ms"
            )
        at = to
    ordered = sorted(step_ms)
    print(
        f"Tổng {len(ordered)} nhịp, {events} sự kiện job, {replans} lượt xếp lại KTV | mỗi nhịp "
        f"p50 {ordered[len(ordered) // 2]:.1f} ms, p95 {ordered[int(len(ordered) * 0.95)]:.1f} ms, "
        f"max {ordered[-1]:.1f} ms"
    )


def main(argv: list[str] | None = None) -> int:
    defaults = RoutingConfig()
    parser = argparse.ArgumentParser(
        prog="python -m ktv_simulator",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--events", type=Path, required=True, help="Luồng sự kiện JSONL (python -m ktv_simulator.events)"
    )
    parser.add_argument("--shift", type=_shift, help="Ca làm cho mọi KTV, VD 08:00-17:30")
    modes = parser.add_argument_group("chế độ chạy (chọn một)")
    modes.add_argument(
        "--at",
        type=datetime.fromisoformat,
        action="append",
        default=[],
        help="Thời điểm lập tuyến; lặp lại để chạy nhiều mốc",
    )
    modes.add_argument(
        "--replay",
        nargs=2,
        type=datetime.fromisoformat,
        metavar=("FROM", "TO"),
        help="Tua realtime từ FROM tới TO",
    )
    modes.add_argument("--serve", type=int, metavar="PORT", help="Mở web demo")
    parser.add_argument("--step-minutes", type=float, default=5.0, help="Độ dài mỗi nhịp khi --replay")
    conditions = parser.add_argument_group("điều kiện lọc (UI gửi cho team data)")
    conditions.add_argument("--case-type", action="append", default=[], dest="case_types")
    conditions.add_argument("--branch", action="append", default=[], dest="branch_names")
    conditions.add_argument("--emp", action="append", default=[], dest="emp_accounts")
    parser.add_argument("--out-dir", type=Path, default=Path("artifacts/simulation"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--travel", choices=("osrm", "haversine"), default="osrm")
    parser.add_argument("--osrm-url", default=PUBLIC_OSRM_URL)
    parser.add_argument(
        "--osrm-max-locations",
        type=int,
        default=100,
        help="Số điểm tối đa mỗi lần gọi /table (public: 100; tự host: ≤ --max-table-size)",
    )
    parser.add_argument(
        "--osrm-parallel",
        type=int,
        help="Số request /table song song, mỗi KTV một request (mặc định: public 1, tự host 16)",
    )
    parser.add_argument(
        "--time-model",
        type=Path,
        help="JSON thời gian học từ lịch sử (research/time_model.py); bỏ trống = bảng cố định",
    )
    parser.add_argument(
        "--rules",
        type=Path,
        help="JSON rule nghiệp vụ (python -m ktv_routing --print-rules); bỏ trống = mặc định",
    )
    parser.add_argument("--average-speed-kmh", type=float, default=defaults.average_speed_kmh)
    parser.add_argument(
        "--location-max-age-minutes",
        type=float,
        default=defaults.location_max_age_minutes,
    )
    args = parser.parse_args(argv)
    if sum((bool(args.at), args.replay is not None, args.serve is not None)) != 1:
        parser.error("chọn đúng một chế độ: --at, --replay FROM TO hoặc --serve PORT")
    if args.replay is not None and args.replay[1] <= args.replay[0]:
        parser.error("--replay: TO phải sau FROM")
    if args.step_minutes <= 0:
        parser.error("--step-minutes phải > 0")

    provider = EventWorkloadProvider(args.events, shift=args.shift)
    config = RoutingConfig(
        average_speed_kmh=args.average_speed_kmh,
        location_max_age_minutes=args.location_max_age_minutes,
    )
    if args.rules is not None:
        try:
            config = replace(config, rules=load_rules(args.rules))
        except (OSError, ValueError) as error:
            parser.error(f"--rules: {error}")
    if args.time_model is not None:
        try:
            config = load_time_model(args.time_model, config)
        except (OSError, ValueError) as error:
            parser.error(f"--time-model: {error}")
    travel = travel_model(
        args.travel,
        osrm_url=args.osrm_url,
        average_speed_kmh=args.average_speed_kmh,
        osrm_max_locations=args.osrm_max_locations,
        osrm_parallel=args.osrm_parallel,
    )
    if args.serve is not None:
        serve(provider, config, travel, host=args.host, port=args.serve)
        return 0

    job_filter = JobFilter(
        case_types=tuple(args.case_types),
        branch_names=tuple(args.branch_names),
        emp_accounts=tuple(args.emp_accounts),
    )
    print("Đang đọc luồng sự kiện…", flush=True)
    if args.replay is not None:
        _replay(provider, config, travel, job_filter, *args.replay, timedelta(minutes=args.step_minutes))
        return 0

    for planned_at in sorted(args.at):
        query = WorkloadQuery(planned_at=planned_at, filter=job_filter)
        request, stats = provider.build(query)
        response = plan_routes(request, config, travel)

        run_dir = args.out_dir / planned_at.strftime("%Y%m%dT%H%M%S")
        run_dir.mkdir(parents=True, exist_ok=True)
        _write(run_dir / "query.json", query)
        _write(run_dir / "request.json", request)
        _write(run_dir / "response.json", response)

        summary = response.summary
        print(
            f"{planned_at.isoformat(timespec='minutes')} | sự kiện={stats.events_applied:,} "
            f"job mở={stats.open_onsite_jobs} khớp lọc={stats.matched_jobs} "
            f"không geocode={stats.without_location} | "
            f"KTV={summary.technicians} điểm={summary.routed_stops} "
            f"trễ={summary.late_stops} km={summary.total_km} "
            f"(tra km {summary.travel_ms / 1000:.1f}s) issue={len(response.issues)} | {run_dir}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
