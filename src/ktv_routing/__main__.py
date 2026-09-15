"""CLI: đọc RouteRequest JSON, in RouteResponse JSON.

    python -m ktv_routing request.json --out response.json
    python -m ktv_routing --print-rules > rules.json   # xem, sửa rồi dùng lại bằng --rules rules.json

Mặc định km và phút di chuyển lấy từ OSRM public; ``--travel haversine`` để
dùng chim bay (không cần mạng).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from .contract import ContractError, request_from_dict, to_json_dict
from .planner import RoutingConfig, load_time_model, plan_routes
from .rules import load_rules, rules_to_dict
from .travel import PUBLIC_OSRM_URL, travel_model


def main(argv: list[str] | None = None) -> int:
    defaults = RoutingConfig()
    parser = argparse.ArgumentParser(
        prog="python -m ktv_routing",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("request", nargs="?", help="File RouteRequest JSON, '-' để đọc stdin")
    parser.add_argument("--out", type=Path, help="Ghi response ra file thay vì stdout")
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
        help="JSON rule nghiệp vụ (tầng, trọng số); bỏ trống = mặc định trong rules.py",
    )
    parser.add_argument("--print-rules", action="store_true", help="In rule nghiệp vụ đang dùng dạng JSON rồi thoát")
    parser.add_argument(
        "--average-speed-kmh", type=float, default=defaults.average_speed_kmh
    )
    parser.add_argument(
        "--location-max-age-minutes",
        type=float,
        default=defaults.location_max_age_minutes,
    )
    args = parser.parse_args(argv)

    config = RoutingConfig(
        average_speed_kmh=args.average_speed_kmh,
        location_max_age_minutes=args.location_max_age_minutes,
    )
    if args.rules is not None:
        try:
            config = replace(config, rules=load_rules(args.rules))
        except (OSError, ValueError) as error:
            print(f"Rule nghiệp vụ không dùng được: {error}", file=sys.stderr)
            return 2
    if args.time_model is not None:
        try:
            config = load_time_model(args.time_model, config)
        except (OSError, ValueError) as error:
            print(f"Mô hình thời gian không dùng được: {error}", file=sys.stderr)
            return 2
    if args.print_rules:
        print(json.dumps(rules_to_dict(config.rules), ensure_ascii=False, indent=2))
        return 0
    if args.request is None:
        parser.error("cần file request (hoặc --print-rules)")

    text = (
        sys.stdin.read()
        if args.request == "-"
        else Path(args.request).read_text(encoding="utf-8")
    )
    try:
        request = request_from_dict(json.loads(text))
    except (json.JSONDecodeError, ContractError) as error:
        print(f"Request không hợp lệ: {error}", file=sys.stderr)
        return 2

    travel = travel_model(
        args.travel,
        osrm_url=args.osrm_url,
        average_speed_kmh=args.average_speed_kmh,
        osrm_max_locations=args.osrm_max_locations,
        osrm_parallel=args.osrm_parallel,
    )
    response = plan_routes(request, config, travel)
    output = json.dumps(to_json_dict(response), ensure_ascii=False, indent=2)
    if args.out is None:
        print(output)
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
