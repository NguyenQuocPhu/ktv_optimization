"""Stress test routing: đo số request/giây và độ trễ.

Hai chế độ:

  core  gọi thẳng plan_routes với request tổng hợp (không cần dữ liệu thật), chạy
        nhiều tiến trình song song để thấy khả năng mở rộng theo số CPU.
  http  bắn POST liên tục vào một endpoint (VD web demo /api/plan) bằng nhiều luồng.

    .venv/bin/python tests/stress_routing.py core
    .venv/bin/python tests/stress_routing.py core --scenario chi-nhanh \\
        --processes 1,8,24 --travel fake-osrm --osrm-latency-ms 5
    .venv/bin/python tests/stress_routing.py http \\
        --url http://127.0.0.1:8767/api/plan --concurrency 1,4,16

Không stress test OSRM public (router.project-osrm.org): server đó chỉ cho khoảng
1 request/giây. Muốn đo có OSRM thì dùng --travel fake-osrm hoặc OSRM tự host.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import platform
import random
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ktv_routing import (  # noqa: E402
    PUBLIC_OSRM_URL,
    GeoPoint,
    HaversineTravel,
    JobInput,
    JobState,
    LocationFix,
    OsrmTravel,
    RouteRequest,
    TechnicianInput,
    plan_routes,
)

# Tên → (số KTV, số job mỗi KTV). "chi-nhanh" gần bằng HNI_04 (105 KTV, 345 job).
SCENARIOS: dict[str, tuple[int, int]] = {
    "1-ktv": (1, 5),
    "chi-nhanh": (100, 4),
    "toan-quoc": (3400, 3),
}
PLANNED_AT = datetime(2026, 6, 15, 9, 0)
DEFAULT_QUERY = {
    "planned_at": "2026-06-15T09:00",
    "filter": {"case_types": ["MAINTENANCE"], "branch_names": ["HNI_04"]},
}


def synthetic_request(technicians: int, jobs_per_technician: int, seed: int = 7) -> RouteRequest:
    """Request quanh Hà Nội: ~20% KTV đang làm dở một job, một phần job đã quá hạn."""

    rng = random.Random(seed)

    def near(lat: float, lng: float, spread: float) -> GeoPoint:
        return GeoPoint(
            round(lat + rng.uniform(-spread, spread), 6),
            round(lng + rng.uniform(-spread, spread), 6),
        )

    items = []
    for technician in range(technicians):
        home = near(21.03, 105.82, 0.08)
        jobs = []
        for order in range(jobs_per_technician):
            running = order == 0 and rng.random() < 0.2
            jobs.append(
                JobInput(
                    job_id=f"T{technician:04d}J{order:02d}",
                    state=JobState.IN_PROGRESS if running else JobState.PENDING,
                    location=near(home.lat, home.lng, 0.03),
                    case_type="MAINTENANCE",
                    due_at=PLANNED_AT + timedelta(minutes=rng.uniform(-600, 600)),
                    priority=2,
                    started_at=(
                        PLANNED_AT - timedelta(minutes=rng.uniform(5, 50)) if running else None
                    ),
                )
            )
        items.append(
            TechnicianInput(
                emp_account=f"KTV{technician:04d}",
                jobs=tuple(jobs),
                last_location=LocationFix(
                    home, PLANNED_AT - timedelta(minutes=rng.uniform(0, 300))
                ),
                shift_start=PLANNED_AT.replace(hour=8),
                shift_end=PLANNED_AT.replace(hour=17, minute=30),
            )
        )
    return RouteRequest(planned_at=PLANNED_AT, technicians=tuple(items))


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 256  # Nhiều tiến trình kết nối cùng lúc.


def start_fake_osrm(latency_ms: float) -> ThreadingHTTPServer:
    """OSRM giả: chờ ``latency_ms`` rồi trả ma trận cố định 1 km / 2 phút mỗi đoạn."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_GET(self) -> None:
            count = urlsplit(self.path).path.rsplit("/", 1)[-1].count(";") + 1
            time.sleep(latency_ms / 1000)
            body = json.dumps(
                {
                    "code": "Ok",
                    "distances": [[1000.0] * count] * count,
                    "durations": [[120.0] * count] * count,
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = _QuietServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _core_worker(job: tuple[str, str | None, int, int | None, float]) -> list[float]:
    scenario, osrm_url, max_locations, parallel, duration = job
    request = synthetic_request(*SCENARIOS[scenario])
    travel = (
        HaversineTravel()
        if osrm_url is None
        else OsrmTravel(
            osrm_url,
            max_locations=max_locations,
            min_interval_seconds=0,
            parallel_requests=parallel,
        )
    )
    plan_routes(request, travel=travel)  # Làm nóng.
    latencies: list[float] = []
    deadline = time.perf_counter() + duration
    while time.perf_counter() < deadline:
        started = time.perf_counter()
        plan_routes(request, travel=travel)
        latencies.append(time.perf_counter() - started)
    return latencies


# Cột kết quả: req/s = số request xong mỗi giây (tất cả tiến trình/luồng cộng lại);
# tb, p50, p95, p99 = thời gian xử lý MỘT request (trung bình, 50%, 95%, 99% nhanh hơn).
SUMMARY_HEADER = f"{'req/s':>10} {'tb ms':>8} {'p50 ms':>9} {'p95 ms':>9} {'p99 ms':>9}"


def _summary(latencies: list[float], seconds: float) -> str:
    if not latencies:
        return f"{0:>10.1f} {'—':>8} {'—':>9} {'—':>9} {'—':>9}"
    ordered = sorted(latencies)

    def percentile(q: float) -> float:
        return ordered[min(len(ordered) - 1, int(q * len(ordered)))] * 1000

    return (
        f"{len(ordered) / seconds:>10.1f} {sum(ordered) / len(ordered) * 1000:>8.2f} "
        f"{percentile(0.5):>9.2f} {percentile(0.95):>9.2f} {percentile(0.99):>9.2f}"
    )


def run_core(args: argparse.Namespace) -> None:
    fake = None
    osrm_url = None
    travel_label = "chim bay (Haversine)"
    if args.travel == "fake-osrm":
        fake = start_fake_osrm(args.osrm_latency_ms)
        osrm_url = f"http://127.0.0.1:{fake.server_address[1]}"
        travel_label = f"OSRM giả, trễ {args.osrm_latency_ms:g} ms mỗi request"
    elif args.travel == "osrm":
        osrm_url = args.osrm_url
        travel_label = (
            f"OSRM {osrm_url}, gom KTV ≤{args.osrm_max_locations} điểm mỗi lần gọi, tuần tự"
            if args.osrm_parallel == 1
            else f"OSRM {osrm_url}, mỗi KTV một lần gọi, {args.osrm_parallel or 16} luồng song song"
        )

    print(f"CPU: {os.cpu_count()} · Python {platform.python_version()} · km: {travel_label} · đo {args.duration:g}s mỗi dòng")
    print(f"{'Kịch bản':<10} {'KTV×job':>9} {'điểm/req':>9} {'tiến trình':>10} {SUMMARY_HEADER}")
    context = multiprocessing.get_context("spawn")  # Tránh fork khi đang có luồng server.
    try:
        for scenario in args.scenario:
            technicians, jobs = SCENARIOS[scenario]
            stops = plan_routes(synthetic_request(technicians, jobs)).summary.routed_stops
            for processes in args.processes:
                with ProcessPoolExecutor(processes, mp_context=context) as pool:
                    job = (scenario, osrm_url, args.osrm_max_locations, args.osrm_parallel, args.duration)
                    results = list(pool.map(_core_worker, [job] * processes))
                latencies = [value for result in results for value in result]
                print(
                    f"{scenario:<10} {f'{technicians}×{jobs}':>9} {stops:>9} {processes:>10} "
                    f"{_summary(latencies, args.duration)}",
                    flush=True,
                )
    finally:
        if fake is not None:
            fake.shutdown()
            fake.server_close()


def run_http(args: argparse.Namespace) -> None:
    body = Path(args.body[1:]).read_bytes() if args.body.startswith("@") else args.body.encode("utf-8")
    opener = build_opener(ProxyHandler({}))  # Endpoint local: không đi qua proxy.
    print(f"URL: {args.url} · body {len(body)} byte · đo {args.duration:g}s mỗi dòng")
    print(f"{'luồng':>6} {SUMMARY_HEADER}  lỗi")
    for concurrency in args.concurrency:
        latencies: list[float] = []
        errors: Counter[str] = Counter()
        lock = threading.Lock()
        deadline = time.perf_counter() + args.duration

        def loop() -> None:
            local: list[float] = []
            failed: Counter[str] = Counter()
            while time.perf_counter() < deadline:
                started = time.perf_counter()
                request = Request(args.url, data=body, headers={"Content-Type": "application/json"})
                try:
                    with opener.open(request, timeout=args.timeout) as response:
                        response.read()
                    local.append(time.perf_counter() - started)
                except HTTPError as error:
                    failed[f"HTTP {error.code}"] += 1
                    error.close()
                except Exception as error:  # noqa: BLE001 - đếm mọi loại lỗi mạng.
                    failed[type(error).__name__] += 1
            with lock:
                latencies.extend(local)
                errors.update(failed)

        threads = [threading.Thread(target=loop) for _ in range(concurrency)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        print(f"{concurrency:>6} {_summary(latencies, args.duration)}  {dict(errors) or '0'}", flush=True)


def _counts(value: str) -> list[int]:
    return [int(item) for item in value.split(",") if item.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    modes = parser.add_subparsers(dest="mode", required=True)

    core = modes.add_parser("core", help="Đo plan_routes trực tiếp với request tổng hợp")
    core.add_argument("--scenario", action="append", choices=list(SCENARIOS), help="Mặc định chạy cả 3 kịch bản")
    core.add_argument("--processes", type=_counts, default=[1, os.cpu_count() or 1], help="VD 1,4,16")
    core.add_argument("--duration", type=float, default=5.0, help="Số giây đo cho mỗi dòng")
    core.add_argument("--travel", choices=("haversine", "fake-osrm", "osrm"), default="haversine")
    core.add_argument("--osrm-url", help="OSRM tự host, dùng với --travel osrm")
    core.add_argument("--osrm-latency-ms", type=float, default=5.0, help="Độ trễ mỗi request của OSRM giả")
    core.add_argument(
        "--osrm-max-locations", type=int, default=100, help="Số điểm tối đa mỗi lần gọi /table"
    )
    core.add_argument(
        "--osrm-parallel",
        type=int,
        help="Số request /table song song trong một lần xếp tuyến (mặc định 16; 1 = gom KTV, tuần tự)",
    )

    http = modes.add_parser("http", help="Bắn POST vào một endpoint đang chạy")
    http.add_argument("--url", required=True)
    http.add_argument("--body", default=json.dumps(DEFAULT_QUERY), help="JSON gửi đi, hoặc @đường/dẫn.json")
    http.add_argument("--concurrency", type=_counts, default=[1, 4, 16], help="Số luồng đồng thời, VD 1,4,16")
    http.add_argument("--duration", type=float, default=10.0)
    http.add_argument("--timeout", type=float, default=60.0)

    args = parser.parse_args()
    if args.mode == "core":
        if args.travel == "osrm":
            if not args.osrm_url:
                parser.error("--travel osrm cần --osrm-url của OSRM tự host")
            if args.osrm_url.rstrip("/") == PUBLIC_OSRM_URL:
                parser.error("Không stress test OSRM public; dùng --travel fake-osrm hoặc OSRM tự host")
        args.scenario = args.scenario or list(SCENARIOS)
        run_core(args)
    else:
        run_http(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
