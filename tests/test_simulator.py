"""Test simulator: export QOS giả lập → luồng sự kiện → RouteRequest đúng hợp đồng → routing.

Fixture sinh trong thư mục tạm nên test không phụ thuộc file dữ liệu thật.
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import threading
import unittest
from datetime import datetime, time, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "simulator")]

from ktv_routing import (  # noqa: E402
    GeoPoint,
    JobFilter,
    JobState,
    RoutingConfig,
    RoutingService,
    StartSource,
    WorkloadQuery,
    distance_km,
    plan_routes,
    query_from_dict,
    request_from_dict,
    to_json_dict,
)
from ktv_simulator import EventWorkloadProvider  # noqa: E402
from ktv_simulator.events import build_events, write_events  # noqa: E402
from ktv_simulator.fake_boundary import build as build_fake_boundary  # noqa: E402
from ktv_simulator.fake_boundary import ward_and_province  # noqa: E402
from ktv_simulator.geocoding import WardBoundaryIndex  # noqa: E402
from ktv_simulator.provider import filter_changes  # noqa: E402
from ktv_simulator.web import make_server  # noqa: E402

DAY = "2026-08-03"
NEXT_DAY = "2026-08-04"
SHIFT = (time(8), time(17, 30))
# Phường hình vuông: centroid là tâm hình vuông.
WARDS = {
    "Alpha": (10.00, 106.00),
    "Bravo": (10.00, 106.02),
    "Charlie": (10.03, 106.00),
    "Delta": (10.10, 106.10),
}
SIDE = 0.01


def at(clock: str, day: str = DAY) -> datetime:
    return datetime.fromisoformat(f"{day}T{clock}")


def centroid(ward: str) -> GeoPoint:
    lat, lng = WARDS[ward]
    return GeoPoint(lat + SIDE / 2, lng + SIDE / 2)


def row(
    job_id: str,
    status: str,
    emp: str,
    ward: str | None,
    created: str,
    finish: str = "",
    *,
    branch: str = "B1",
    case_type: str = "MAINTENANCE",
    day: str = DAY,
) -> list[str]:
    return [
        job_id,
        status,
        branch,
        case_type,
        f"Số 1 Đường Test, Phường {ward}, Tỉnh Demo" if ward else "Không rõ địa chỉ",
        emp,
        f"{day} {created}",
        f"{day} {finish}" if ":" in finish else finish,
    ]


MAINTENANCE = [
    row("C1", "Đã xử lý", "ktv.a", "Alpha", "08:00", "11:00"),
    row("C2", "Đã phân công", "ktv.a", "Bravo", "08:30"),
    row("C2", "Đã phân công", "ktv.a", "Bravo", "08:30"),  # Dòng lặp của export.
    row("C3", "Đã xử lý", "ktv.a", "Charlie", "07:00", "09:00"),
    row("C4", "Đóng checklist", "ktv.b", "Alpha", "08:00"),  # Đóng không rõ giờ.
    row("C5", "Đã xử lý hoàn tất qua phone", "ktv.b", "Alpha", "08:00", "12:00"),
    row("C6", "Chưa phân công", "ktv.c", "Delta", "09:00", "-1", branch="B2"),
    row("C7", "Đã phân công", "ktv.b", "Bravo", "09:00", case_type="THU HỒI THIẾT BỊ"),
    row("C8", "Đã phân công", "ktv.b", None, "09:30"),
    row("C9", "Đã phân công", "", "Alpha", "09:00"),
    row("C10", "Đã phân công", "ktv.b", "Alpha", "11:00"),
    row("C11", "Tạm dừng chờ xử lý", "ktv.a", "Alpha", "08:00"),
    row("C12", "Đã phân công", "ktv.a", "Bravo", "08:00", day=NEXT_DAY),  # Sang ngày sau: có snapshot.
]
GPS = [
    ["ktv.a", "10.2,106.2", f"{DAY}T06:00:00.000000000"],
    ["ktv.b", "10.105,106.105", f"{DAY}T09:50:00"],
    ["ktv.b", "10.3,106.3", f"{DAY}T10:30:00"],
    ["ktv.x", "không phải tọa độ", f"{DAY}T09:00:00"],
]
CHECKINS = [
    ["C1", f"{DAY} 09:30", f"{DAY} 11:00", "(10.004, 106.004)", "(10.005, 106.005)"],
    ["C3", f"{DAY} 08:00", f"{DAY} 09:00", "(10.030, 106.000)", "(10.031, 106.001)"],
    ["C7", f"{DAY} 09:40", "", "", ""],  # Thiếu checkout: kết thúc khi KTV check-in job khác.
    ["C5", f"{DAY} 10:10", "", "(10.006, 106.006)", ""],
]


def _write_csv(path: Path, columns: list[str], rows: list[list[str]]) -> Path:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerows(rows)
    return path


def write_fixtures(root: Path) -> dict[str, Path]:
    features = []
    for index, (name, (lat, lng)) in enumerate(WARDS.items(), 1):
        ring = [[lng, lat], [lng + SIDE, lat], [lng + SIDE, lat + SIDE], [lng, lat + SIDE], [lng, lat]]
        features.append(
            {
                "type": "Feature",
                "properties": {"ma_xa": f"W{index:02d}", "ten_xa": name, "tinh_tp": "Demo"},
                "geometry": {"type": "Polygon", "coordinates": [ring]},
            }
        )
    boundary = root / "boundary.geojson"
    boundary.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
    return {
        "boundary": boundary,
        "maintenance": _write_csv(
            root / "maintenance.csv",
            ["CHECKLIST_ID", "CHECKLIST_STATUS", "BRANCH_NAME", "CASE_TYPE", "OBJ_LOCATION", "EMP_ACCOUNT", "CREATE_DATE", "FINISH_DATE"],
            MAINTENANCE,
        ),
        "gps": _write_csv(root / "gps.csv", ["ACCOUNTEMP", "COORDINATE", "CREATEDATE"], GPS),
        "checkins": _write_csv(
            root / "checkins.csv",
            ["CHECKLIST_ID", "CHECKIN_DATE", "CHECKOUT_DATE", "LAT_LNG_IN", "LAT_LNG_OUT"],
            CHECKINS,
        ),
    }


def write_event_stream(root: Path) -> Path:
    paths = write_fixtures(root)
    header, events = build_events(
        paths["maintenance"], paths["boundary"], checkins_csv=paths["checkins"], gps_csv=paths["gps"], workers=1
    )
    path = root / "events.jsonl"
    write_events(path, header, events)
    return path


class EventStreamTest(unittest.TestCase):
    def test_export_becomes_ordered_event_stream(self):
        with TemporaryDirectory() as tmp:
            path = write_event_stream(Path(tmp))
            header, *events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(header["format"], "ktv-events/1")
        self.assertEqual(
            header["events"], {"JOB_CREATED": 12, "CHECKIN": 4, "CHECKOUT": 2, "JOB_CLOSED": 5, "GPS": 3}
        )
        self.assertEqual((header["jobs"], header["geocoded_jobs"], header["inferred_closes"]), (12, 11, 2))
        order = ["JOB_CREATED", "CHECKIN", "CHECKOUT", "JOB_CLOSED", "GPS"]
        keys = [(event["at"], order.index(event["type"])) for event in events]
        self.assertEqual(keys, sorted(keys))

        by_job: dict[str | None, list[dict]] = {}
        for event in events:
            by_job.setdefault(event.get("job_id"), []).append(event)
        self.assertEqual([event["type"] for event in by_job["C2"]], ["JOB_CREATED"])  # Dòng lặp chỉ sinh một sự kiện.
        self.assertEqual([event["type"] for event in by_job["C6"]], ["JOB_CREATED"])  # Còn mở cuối export.
        self.assertIsNone(by_job["C8"][0]["location"])
        self.assertEqual(by_job["C1"][1], {
            "at": f"{DAY}T09:30:00", "type": "CHECKIN", "job_id": "C1", "emp_account": "ktv.a",
            "location": {"lat": 10.004, "lng": 106.004},
        })
        # Không có FINISH_DATE, không có lượt check-in: suy ra đóng lúc tạo.
        self.assertEqual(by_job["C4"][-1], {
            "at": f"{DAY}T08:00:00", "type": "JOB_CLOSED", "job_id": "C4", "emp_account": "ktv.b",
            "status": "Đóng checklist", "at_inferred": True,
        })


class ProviderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = TemporaryDirectory()
        cls.events = write_event_stream(Path(cls._tmp.name))
        cls.provider = EventWorkloadProvider(cls.events, shift=SHIFT)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.provider.close()
        cls._tmp.cleanup()

    def build(self, clock: str, *, day: str = DAY, provider: EventWorkloadProvider | None = None, **conditions):
        return (provider or self.provider).build(WorkloadQuery(at(clock, day), JobFilter(**conditions)))

    def assertNear(self, point: GeoPoint | None, expected: GeoPoint) -> None:
        self.assertIsNotNone(point)
        self.assertAlmostEqual(point.lat, expected.lat, places=6)
        self.assertAlmostEqual(point.lng, expected.lng, places=6)

    @staticmethod
    def jobs_by_technician(request):
        return {
            item.emp_account: {job.job_id: job for job in item.jobs}
            for item in request.technicians
        }

    def test_filter_and_open_jobs_at_time(self):
        request, stats = self.build("10:00", case_types=("maintenance",), branch_names=("B1",))
        # Mở tại 10:00: C1 C2 C5 C6 C7 C8 C9. C5 cuối cùng xử lý qua phone lúc 12:00 nhưng
        # lúc 10:00 chưa ai biết nên vẫn xếp. Lọc B1 + MAINTENANCE: C1 C2 C5 C8 C9.
        self.assertEqual(
            (
                stats.open_onsite_jobs,
                stats.matched_jobs,
                stats.missing_technician,
                stats.without_location,
                stats.in_progress_jobs,
            ),
            (7, 5, 1, 1, 1),
        )
        jobs = self.jobs_by_technician(request)
        self.assertEqual({key: sorted(value) for key, value in jobs.items()}, {"ktv.a": ["C1", "C2"], "ktv.b": ["C5", "C8"]})

        running, waiting = jobs["ktv.a"]["C1"], jobs["ktv.a"]["C2"]
        self.assertEqual((running.state, running.started_at), (JobState.IN_PROGRESS, at("09:30")))
        self.assertNear(running.location, centroid("Alpha"))
        # MAINTENANCE: hạn check-in = giờ tạo + 24 giờ, ưu tiên 2; khu vực = phường geocode được.
        self.assertEqual(
            (waiting.state, waiting.due_at, waiting.priority, waiting.area),
            (JobState.PENDING, at("08:30", NEXT_DAY), 2, "Bravo"),
        )
        self.assertIsNone(jobs["ktv.b"]["C8"].location)
        self.assertEqual(request.filter.case_types, ("maintenance",))

        tech_a, tech_b = request.technicians
        self.assertEqual((tech_a.shift_start, tech_a.shift_end), (at("08:00"), at("17:30")))
        # Check-in C1 lúc 09:30 là tọa độ mới nhất của ktv.a.
        self.assertEqual((tech_a.last_location.source, tech_a.last_location.recorded_at), ("CHECKIN", at("09:30")))
        self.assertNear(tech_a.last_location.location, GeoPoint(10.004, 106.004))
        # GPS 10:30 nằm sau thời điểm lập tuyến nên chưa đọc tới.
        self.assertEqual((tech_b.last_location.source, tech_b.last_location.recorded_at), ("GPS", at("09:50")))

    def test_without_filter_and_filter_by_technician(self):
        request, stats = self.build("10:00")
        jobs = self.jobs_by_technician(request)
        self.assertEqual(stats.matched_jobs, 7)
        self.assertEqual(sorted(jobs), ["ktv.a", "ktv.b", "ktv.c"])
        pickup = jobs["ktv.b"]["C7"]
        # Thu hồi: không hạn check-in, hoàn tất trong tháng, ưu tiên 4.
        self.assertEqual(
            (pickup.case_type, pickup.due_at, pickup.complete_by, pickup.priority),
            ("THU HỒI THIẾT BỊ", None, datetime(2026, 8, 31, 23, 59, 59), 4),
        )
        self.assertIn("C6", jobs["ktv.c"])  # FINISH_DATE = -1 và status còn mở: chưa đóng.

        only_b, _ = self.build("10:00", emp_accounts=("ktv.b",))
        self.assertEqual({key: sorted(value) for key, value in self.jobs_by_technician(only_b).items()}, {"ktv.b": ["C5", "C7", "C8"]})

    def test_earlier_time_replays_history(self):
        request, stats = self.build("08:45", case_types=("MAINTENANCE",), branch_names=("B1",))
        jobs = self.jobs_by_technician(request)
        self.assertEqual(
            {job_id: job.state for job_id, job in jobs["ktv.a"].items()},
            {"C1": JobState.PENDING, "C2": JobState.PENDING, "C3": JobState.IN_PROGRESS},
        )
        self.assertEqual(jobs["ktv.a"]["C3"].started_at, at("08:00"))
        # C4 (đóng) và C11 (tạm dừng) không rõ giờ đóng: suy ra đóng lúc tạo nên không xếp.
        self.assertEqual(sorted(jobs["ktv.b"]), ["C5"])
        self.assertEqual(request.technicians[0].last_location.source, "CHECKIN")
        self.assertEqual(stats.technicians, 2)

    def test_checkin_without_checkout_ends_at_next_checkin(self):
        before = self.jobs_by_technician(self.build("10:00", emp_accounts=("ktv.b",))[0])["ktv.b"]
        after = self.jobs_by_technician(self.build("10:15", emp_accounts=("ktv.b",))[0])["ktv.b"]
        self.assertEqual(
            (before["C7"].state, before["C7"].started_at, before["C5"].state),
            (JobState.IN_PROGRESS, at("09:40"), JobState.PENDING),
        )
        self.assertEqual(
            (after["C7"].state, after["C5"].state, after["C5"].started_at),
            (JobState.PENDING, JobState.IN_PROGRESS, at("10:10")),
        )

    def test_changes_report_visit_ended_by_checkin_elsewhere(self):
        with EventWorkloadProvider(self.events) as provider:
            provider.advance_to(at("10:00"))
            changes = provider.advance_to(at("10:15"), collect=True)
        self.assertEqual(
            [(change.type, change.job_id, change.emp_account) for change in changes],
            [("VISIT_ENDED", "C7", "ktv.b"), ("CHECKIN", "C5", "ktv.b")],
        )
        # Lọc THU HỒI: check-in C5 (MAINTENANCE) bị lọc, nhưng C7 vừa đổi trạng thái
        # nên ktv.b vẫn phải được xếp lại.
        pickup_only = filter_changes(changes, JobFilter(case_types=("THU HỒI THIẾT BỊ",)))
        self.assertEqual([(change.type, change.job_id) for change in pickup_only], [("VISIT_ENDED", "C7")])

    def test_rewind_matches_fresh_provider(self):
        later, _ = self.build("09:00", day=NEXT_DAY, emp_accounts=("ktv.a",))
        self.assertIn("C12", self.jobs_by_technician(later)["ktv.a"])
        # Lùi trong ngày sau: khôi phục snapshot nửa đêm. Lùi về ngày đầu: đọc lại từ đầu.
        for clock, day in (("08:30", NEXT_DAY), ("10:00", DAY)):
            with self.subTest(day=day), EventWorkloadProvider(self.events, shift=SHIFT) as fresh:
                self.assertEqual(
                    to_json_dict(self.build(clock, day=day, case_types=("MAINTENANCE",))),
                    to_json_dict(self.build(clock, day=day, provider=fresh, case_types=("MAINTENANCE",))),
                )

    def test_routing_service_end_to_end(self):
        query = WorkloadQuery(at("10:00"), JobFilter(case_types=("MAINTENANCE",), branch_names=("B1",)))
        response = RoutingService(self.provider).plan(query)

        self.assertEqual([route.emp_account for route in response.routes], ["ktv.a", "ktv.b"])
        route = response.routes[0]
        self.assertEqual((route.start_source, route.in_progress_job_id, route.start_at), (StartSource.IN_PROGRESS_JOB, "C1", at("10:30")))
        self.assertEqual([stop.job_id for stop in route.stops], ["C2"])
        self.assertAlmostEqual(route.stops[0].leg_km, distance_km(centroid("Alpha"), centroid("Bravo")), places=2)
        self.assertEqual([stop.job_id for stop in response.routes[1].stops], ["C5"])
        self.assertEqual([(issue.code, issue.job_id) for issue in response.issues], [("JOB_LOCATION_UNKNOWN", "C8")])

    def test_timezone_aware_query_is_rejected(self):
        with self.assertRaises(ValueError):
            self.provider.build(WorkloadQuery(at("10:00").replace(tzinfo=timezone.utc)))

    def test_rejects_file_that_is_not_an_event_stream(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "maintenance.csv"
            path.write_text("CHECKLIST_ID,CREATE_DATE\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                EventWorkloadProvider(path)


class FakeBoundaryTest(unittest.TestCase):
    def test_ward_and_province_from_address(self):
        cases = {
            "92 Pho Nha Chung (Yen Mi 1), Phuong Xuan Hoa, Phu Tho": ("Xuan Hoa", "Phu Tho"),
            "So nha 31 ngo 1 Tan Khai, Phuong Vinh Tuy, Ha Noi": ("Vinh Tuy", "Ha Noi"),
            "Thôn A, Xã Yên Mỹ, Hưng Yên": ("Yên Mỹ", "Hưng Yên"),
            "Thi tran Dong Anh, Ha Noi": ("Dong Anh", "Ha Noi"),
            "Không rõ địa chỉ": None,
        }
        for address, expected in cases.items():
            with self.subTest(address):
                self.assertEqual(ward_and_province(address), expected)

    def test_checkin_median_becomes_geocodable_ward(self):
        with TemporaryDirectory() as tmp:
            paths = write_fixtures(Path(tmp))
            checkins = _write_csv(
                Path(tmp) / "checkins_in.csv",
                ["CHECKLIST_ID", "LAT_LNG_IN"],
                [
                    ["C1", "(21.001, 105.801)"],
                    ["C4", "(21.003, 105.803)"],
                    ["C5", "(21.002, 105.802)"],
                    ["C3", "(21.100, 105.900)"],
                    ["C8", "(21.500, 105.500)"],  # Địa chỉ không có phường: bỏ qua.
                ],
            )
            collection, stats = build_fake_boundary(
                paths["maintenance"], checkins, min_points=2, encoding="utf-8-sig"
            )
            # Alpha có 3 check-in → giữ; Charlie chỉ 1 → bỏ.
            self.assertEqual((stats["wards_found"], stats["wards_kept"]), (2, 1))
            boundary = Path(tmp) / "fake.geojson"
            boundary.write_text(json.dumps(collection), encoding="utf-8")
            match = WardBoundaryIndex.from_geojson(boundary).match(
                "Số 9 Đường Khác, Phường Alpha, Tỉnh Demo"
            )
            self.assertEqual(match.ward_name, "Alpha")
            # Centroid polygon lệch ~1e-6 độ do sai số float.
            self.assertAlmostEqual(match.point.lat, 21.002, places=5)
            self.assertAlmostEqual(match.point.lng, 105.802, places=5)


class WebDemoTest(unittest.TestCase):
    def test_page_options_plan_and_replay_api(self):
        import socket
        import uvicorn
        from ktv_simulator.web import make_app

        with TemporaryDirectory() as tmp, EventWorkloadProvider(write_event_stream(Path(tmp))) as provider:
            app = make_app(provider, RoutingConfig())
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
            sock.close()
            uvi_config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
            server = uvicorn.Server(uvi_config)
            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()
            import time as _time
            for _ in range(50):
                if server.started:
                    break
                _time.sleep(0.1)
            base = f"http://127.0.0.1:{port}"
            opener = build_opener(ProxyHandler({}))

            def post(path: str, body: object) -> dict:
                req = Request(base + path, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
                return json.load(opener.open(req))

            try:
                page = opener.open(base + "/").read().decode("utf-8")
                self.assertIn("Lập tuyến", page)
                self.assertIn("/api/replay", page)

                options = json.load(opener.open(base + "/api/options"))
                self.assertEqual([branch["name"] for branch in options["branches"]], ["B1", "B2"])
                self.assertIn("MAINTENANCE", options["case_types"])
                self.assertEqual(options["events_to"], f"{NEXT_DAY}T08:00:00")

                query = {"planned_at": f"{DAY}T10:00", "filter": {"case_types": ["MAINTENANCE"], "branch_names": ["B1"]}}
                result = post("/api/plan", query)
                self.assertEqual(result["stats"]["matched_jobs"], 5)
                self.assertEqual(result["query"]["filter"]["branch_names"], ["B1"])
                self.assertEqual([stop["job_id"] for stop in result["response"]["routes"][0]["stops"]], ["C2"])

                # 09:00 → 10:00: C8 tạo (ktv.b), C1 check-in (ktv.a). C7 check-in bị lọc (THU HỒI).
                step = post("/api/replay", {"query": {**query, "planned_at": f"{DAY}T09:00"}, "to": f"{DAY}T10:00"})
                self.assertEqual([(change["type"], change["job_id"]) for change in step["changes"]], [("JOB_CREATED", "C8"), ("CHECKIN", "C1")])
                self.assertEqual(step["affected"], ["ktv.a", "ktv.b"])
                self.assertEqual(step["query"]["filter"]["emp_accounts"], ["ktv.a", "ktv.b"])
                self.assertEqual([route["emp_account"] for route in step["response"]["routes"]], ["ktv.a", "ktv.b"])
                self.assertEqual(step["stats"]["matched_jobs"], 5)

                quiet = post("/api/replay", {"query": {**query, "planned_at": f"{DAY}T10:15"}, "to": f"{DAY}T10:20"})
                self.assertEqual((quiet["events"], quiet["affected"], quiet["response"]), (0, [], None))

                for path, body in (
                    ("/api/plan", {"planned_at": "x"}),
                    ("/api/replay", {"query": query, "to": f"{DAY}T09:00"}),  # to trước planned_at.
                ):
                    with self.subTest(path), self.assertRaises(HTTPError) as caught:
                        post(path, body)
                    self.assertIn(caught.exception.code, (400, 422))
                    caught.exception.close()
            finally:
                server.should_exit = True
                thread.join(timeout=5)


class SimulatorCliTest(unittest.TestCase):
    def run_module(self, *args: str) -> str:
        env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT / "simulator")])}
        done = subprocess.run([sys.executable, "-m", *args], env=env, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def test_cli_builds_events_then_plans_and_replays(self):
        with TemporaryDirectory() as tmp:
            paths = write_fixtures(Path(tmp))
            events = Path(tmp) / "events.jsonl"
            self.run_module(
                "ktv_simulator.events",
                "--maintenance", str(paths["maintenance"]),
                "--boundary", str(paths["boundary"]),
                "--checkins", str(paths["checkins"]),
                "--gps", str(paths["gps"]),
                "--out", str(events),
            )

            out_dir = Path(tmp) / "simulation"
            self.run_module(
                "ktv_simulator",
                "--events", str(events),
                "--shift", "08:00-17:30",
                "--at", f"{DAY}T10:00", "--at", f"{DAY}T08:45",
                "--case-type", "MAINTENANCE", "--branch", "B1",
                "--travel", "haversine",
                "--out-dir", str(out_dir),
            )
            self.assertEqual(sorted(path.name for path in out_dir.iterdir()), ["20260803T084500", "20260803T100000"])
            run_dir = out_dir / "20260803T100000"
            query = query_from_dict(json.loads((run_dir / "query.json").read_text(encoding="utf-8")))
            self.assertEqual(query.filter.branch_names, ("B1",))
            request = request_from_dict(json.loads((run_dir / "request.json").read_text(encoding="utf-8")))
            response = json.loads((run_dir / "response.json").read_text(encoding="utf-8"))
            # request.json đủ để team routing tái tạo đúng tuyến đã trả.
            self.assertEqual(to_json_dict(plan_routes(request))["routes"], response["routes"])

            replay = self.run_module(
                "ktv_simulator",
                "--events", str(events),
                "--branch", "B1",
                "--replay", f"{DAY}T09:00", f"{DAY}T10:30",
                "--step-minutes", "30",
                "--travel", "haversine",
            )
            # 09:30: C8 tạo + C1 check-in (2 KTV); 10:00: C7 check-in;
            # 10:30: C5 check-in + C7 hết lượt (cùng ktv.b).
            self.assertIn("Tổng 3 nhịp, 5 sự kiện job, 4 lượt xếp lại KTV", replay)


if __name__ == "__main__":
    unittest.main()
