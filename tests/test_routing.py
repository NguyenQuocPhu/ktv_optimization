"""Test lõi routing: xếp tuyến, hợp đồng JSON, luồng service có điều kiện lọc."""

from __future__ import annotations

import copy
import json
import random
import os
import subprocess
import sys
import threading
import time
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import permutations
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ktv_routing import (  # noqa: E402
    BusinessRules,
    ContractError,
    GeoPoint,
    JobFilter,
    JobInput,
    JobState,
    LocationFix,
    OsrmTravel,
    PreviousRoute,
    RouteRequest,
    RoutingConfig,
    RoutingService,
    SequenceSource,
    StartSource,
    TechnicianInput,
    WorkloadQuery,
    distance_km,
    load_time_model,
    plan_routes,
    query_from_dict,
    request_from_dict,
    rules_from_dict,
    rules_to_dict,
    time_model_config,
    to_json_dict,
)

HOME = GeoPoint(10.0, 106.0)


def at(clock: str) -> datetime:
    return datetime.fromisoformat(f"2026-08-03T{clock}")


def north(km: float) -> GeoPoint:
    """Điểm cách HOME khoảng ``km`` về phía bắc."""

    return GeoPoint(HOME.lat + km / 111.195, HOME.lng)


def job(
    job_id: str,
    *,
    km: float = 1.0,
    due: str | None = None,
    priority: int | None = None,
    state: JobState = JobState.PENDING,
    started: str | None = None,
    case_type: str = "MAINTENANCE",
    located: bool = True,
    opens: str | None = None,
    complete_by: str | None = None,
    area: str | None = None,
) -> JobInput:
    return JobInput(
        job_id=job_id,
        state=state,
        location=north(km) if located else None,
        case_type=case_type,
        due_at=at(due) if due else None,
        priority=priority,
        started_at=at(started) if started else None,
        appointment_start=at(opens) if opens else None,
        complete_by=at(complete_by) if complete_by else None,
        area=area,
    )


def technician(
    *jobs: JobInput,
    account: str = "ktv.a",
    fix_at: str | None = "08:00",
    shift: tuple[str, str] | None = None,
    previous: tuple[str, ...] = (),
) -> TechnicianInput:
    return TechnicianInput(
        emp_account=account,
        jobs=jobs,
        last_location=LocationFix(HOME, at(fix_at)) if fix_at else None,
        shift_start=at(shift[0]) if shift else None,
        shift_end=at(shift[1]) if shift else None,
        previous_sequence=previous,
    )


def plan(*technicians: TechnicianInput, planned: str = "08:00", config: RoutingConfig | None = None):
    return plan_routes(RouteRequest(planned_at=at(planned), technicians=technicians), config)


def stop_ids(route) -> list[str]:
    return [stop.job_id for stop in route.stops]


def codes(response) -> list[str]:
    return [issue.code for issue in response.issues]


class RoutingTestCase(unittest.TestCase):
    def assertTime(self, actual: datetime | None, expected: datetime) -> None:
        self.assertIsNotNone(actual)
        self.assertLess(abs((actual - expected).total_seconds()), 0.01)


class PlannerTest(RoutingTestCase):
    def test_dynamic_programming_finds_shorter_order_than_nearest_first(self):
        # Gần nhất trước: A (1 km) → B (phía nam, 2,2 km) → C (4,2 km) = 7,4 km.
        # Tối ưu: B (1,2 km) → A (2,2 km) → C (2 km) = 5,4 km.
        route = plan(technician(job("A", km=1), job("B", km=-1.2), job("C", km=3))).routes[0]
        self.assertEqual(stop_ids(route), ["B", "A", "C"])
        self.assertAlmostEqual(route.total_km, 5.4, delta=0.01)
        self.assertAlmostEqual(route.score["KM"], 5.4, delta=0.01)
        self.assertEqual((route.sequence_source, route.previous_route), (SequenceSource.OPTIMAL, PreviousRoute.NONE))

    def test_late_is_judged_on_checkin_not_on_finish(self):
        response = plan(technician(job("A", km=5, due="08:30", complete_by="09:00")))
        stop = response.routes[0].stops[0]
        self.assertTime(stop.eta, at("08:10"))  # 5 km chim bay, 30 km/h.
        self.assertTime(stop.finish_at, at("09:10"))  # Làm 60 phút: xong sau hạn check-in...
        self.assertFalse(stop.late)  # ...nhưng check-in 08:10 trước 08:30 nên đúng hẹn.
        self.assertTrue(stop.completion_late)  # Xong 09:10 sau hạn hoàn tất 09:00.
        summary = response.summary
        self.assertEqual((summary.late_stops, summary.on_time_stops, summary.completion_late_stops), (0, 1, 1))

    def test_on_time_tier_beats_distance(self):
        # NEAR không hạn. FAR 10 km phải check-in trước 08:30; làm NEAR trước thì FAR trễ.
        route = plan(technician(job("NEAR", km=1), job("FAR", km=10, due="08:30"))).routes[0]
        self.assertEqual(stop_ids(route), ["FAR", "NEAR"])
        self.assertEqual(route.stops[0].late, False)
        self.assertEqual(route.score["LATE_CHECKIN"], 0)

    def test_priority_decides_which_job_to_save(self):
        # Hai job cùng hạn 08:15, chỉ kịp một: cứu job ưu tiên 1 (trọng số 4), để trễ job ưu tiên 4.
        route = plan(technician(job("P4", km=2, due="08:15", priority=4), job("P1", km=2.2, due="08:15", priority=1))).routes[0]
        self.assertEqual(stop_ids(route), ["P1", "P4"])
        self.assertEqual([stop.late for stop in route.stops], [False, True])
        self.assertEqual(route.score["LATE_CHECKIN"], 1.0)

    def test_appointment_start_makes_technician_wait(self):
        stop = plan(technician(job("A", km=5, opens="09:00", due="11:00"))).routes[0].stops[0]
        self.assertTime(stop.eta, at("08:10"))
        self.assertAlmostEqual(stop.wait_minutes, 50, delta=0.01)
        self.assertTime(stop.finish_at, at("10:00"))
        self.assertFalse(stop.late)

    def test_area_rule_avoids_coming_back_to_a_left_area(self):
        jobs = (job("A", km=1, area="X"), job("B", km=1.5, area="Y"), job("C", km=2, area="X"))
        # A→B→C ngắn nhất (2 km) nhưng quay lại khu X (≈ +2 km); A→C→B 2,5 km, không quay lại.
        grouped = plan(technician(*jobs)).routes[0]
        self.assertEqual(stop_ids(grouped), ["A", "C", "B"])
        self.assertEqual(grouped.score["AREA_REENTRY"], 0)
        no_area = plan(technician(*(replace(item, area=None) for item in jobs))).routes[0]
        self.assertEqual(stop_ids(no_area), ["A", "B", "C"])

    def test_previous_sequence_is_kept_unless_new_order_is_clearly_better(self):
        # Tuyến cũ FAR → NEAR (5 km). Xếp tự do NEAR → FAR (3 km): tốt hơn ≈ 2,3 ở tầng cuối.
        previous = technician(job("NEAR", km=1), job("FAR", km=3), previous=("FAR", "NEAR"))
        changed = plan(previous).routes[0]
        self.assertEqual((stop_ids(changed), changed.previous_route), (["NEAR", "FAR"], PreviousRoute.CHANGED))

        strict = RoutingConfig(rules=BusinessRules(reroute_min_gain=5))
        kept = plan(previous, config=strict).routes[0]
        self.assertEqual((stop_ids(kept), kept.previous_route), (["FAR", "NEAR"], PreviousRoute.KEPT))

        # Giữ tuyến cũ: job mới chèn vào chỗ tốt nhất, FAR vẫn trước NEAR.
        keep = RoutingConfig(rules=BusinessRules(previous_route_policy="KEEP"))
        inserted = plan(
            technician(job("NEAR", km=1), job("FAR", km=3), job("NEW", km=0.5), previous=("FAR", "NEAR")), config=keep
        ).routes[0]
        self.assertEqual(stop_ids(inserted), ["NEW", "FAR", "NEAR"])

        ignore = RoutingConfig(rules=BusinessRules(previous_route_policy="IGNORE"))
        self.assertEqual(plan(previous, config=ignore).routes[0].previous_route, PreviousRoute.NONE)

    def test_matches_brute_force_on_random_instances(self):
        rng = random.Random(11)
        rules = BusinessRules()
        keep = RoutingConfig(rules=replace(rules, previous_route_policy="KEEP"))
        for size in (5, 6):
            jobs = tuple(
                job(
                    f"J{index}",
                    km=rng.uniform(-8, 8),
                    due=rng.choice([None, "08:30", "09:30", "11:00", "13:00"]),
                    priority=rng.choice([None, 1, 2, 3, 4]),
                    area=rng.choice([None, "X", "Y"]),
                    opens=rng.choice([None, None, "09:00"]),
                )
                for index in range(size)
            )
            best = plan(technician(*jobs, shift=("08:00", "12:00"))).routes[0]
            self.assertEqual(best.sequence_source, SequenceSource.OPTIMAL)
            brute = min(
                rules.objective_key(
                    plan(technician(*jobs, shift=("08:00", "12:00"), previous=tuple(f"J{i}" for i in order)), config=keep)
                    .routes[0]
                    .score
                )
                for order in permutations(range(size))
            )
            for got, expected in zip(rules.objective_key(best.score), brute):
                self.assertAlmostEqual(got, expected, places=2)

    def test_large_route_falls_back_to_heuristic_and_keeps_forced_order(self):
        small = RoutingConfig(rules=BusinessRules(max_exact_jobs=3, previous_route_policy="KEEP"))
        jobs = tuple(job(f"J{km}", km=km) for km in (5, 1, 4, 2, 3))
        response = plan(technician(*jobs), config=small)
        self.assertEqual(response.routes[0].sequence_source, SequenceSource.HEURISTIC)
        self.assertEqual(stop_ids(response.routes[0]), ["J1", "J2", "J3", "J4", "J5"])
        self.assertIn("SEQUENCE_NOT_OPTIMAL", codes(response))
        forced = plan(technician(*jobs, previous=("J5", "J1", "J4", "J2", "J3")), config=small).routes[0]
        self.assertEqual(stop_ids(forced), ["J5", "J1", "J4", "J2", "J3"])

    def test_eta_finish_lateness_and_summary(self):
        # A (15 km) check-in 08:30 sau hạn 08:20 nên trễ; B (30 km) check-in 10:00 trước 12:00.
        response = plan(technician(job("A", km=15, due="08:20"), job("B", km=30, due="12:00")))
        route = response.routes[0]
        first, second = route.stops
        leg = distance_km(HOME, north(15))

        self.assertEqual(route.travel_source, "HAVERSINE")
        self.assertAlmostEqual(first.leg_km, leg, places=3)
        self.assertAlmostEqual(first.leg_minutes, leg * 2, delta=0.01)  # 30 km/h.
        self.assertTime(first.eta, at("08:00") + timedelta(minutes=leg * 2))
        self.assertTime(first.finish_at, first.eta + timedelta(minutes=60))
        self.assertTrue(first.late)
        self.assertFalse(second.late)
        self.assertTime(route.finish_at, at("11:00"))
        self.assertEqual(route.start_source, StartSource.LAST_LOCATION)
        self.assertAlmostEqual(route.total_km, 30, delta=0.01)
        self.assertEqual(route.total_service_minutes, 120)

        summary = response.summary
        self.assertEqual(
            (summary.routed_stops, summary.late_stops, summary.on_time_stops),
            (2, 1, 1),
        )
        self.assertEqual(summary.on_time_rate_percent, 50.0)
        self.assertEqual(response.issues, ())

    def test_in_progress_job_is_the_start_point(self):
        response = plan(
            technician(
                job("RUN", km=5, state=JobState.IN_PROGRESS, started="07:40"),
                job("NEXT", km=8),
            )
        )
        route = response.routes[0]
        self.assertEqual(route.start_source, StartSource.IN_PROGRESS_JOB)
        self.assertEqual(route.in_progress_job_id, "RUN")
        self.assertEqual(route.start_at, at("08:40"))
        self.assertEqual([stop.job_id for stop in route.stops], ["NEXT"])
        self.assertAlmostEqual(route.stops[0].leg_km, 3, delta=0.01)
        self.assertEqual(
            (response.summary.in_progress_jobs, response.summary.pending_jobs), (1, 1)
        )

    def test_in_progress_edge_cases(self):
        unknown_start = plan(technician(job("RUN", state=JobState.IN_PROGRESS)))
        self.assertEqual(unknown_start.routes[0].start_at, at("09:00"))
        self.assertEqual(unknown_start.routes[0].finish_at, at("09:00"))
        self.assertIn("IN_PROGRESS_START_UNKNOWN", codes(unknown_start))

        two_running = plan(
            technician(
                job("OLD", state=JobState.IN_PROGRESS, started="07:00"),
                job("NEW", km=2, state=JobState.IN_PROGRESS, started="07:50"),
            )
        )
        self.assertEqual(two_running.routes[0].in_progress_job_id, "NEW")
        self.assertEqual(two_running.routes[0].start_at, at("08:50"))
        self.assertIn("MULTIPLE_IN_PROGRESS", codes(two_running))

    def test_start_location_sources(self):
        fresh = plan(technician(job("A"), fix_at="07:00"))
        self.assertEqual(fresh.routes[0].start_source, StartSource.LAST_LOCATION)
        self.assertEqual(fresh.issues, ())

        stale = plan(technician(job("A"), fix_at="03:00"))
        self.assertEqual(stale.routes[0].start_source, StartSource.STALE_LOCATION)
        self.assertEqual(codes(stale), ["STALE_TECHNICIAN_LOCATION"])
        self.assertIsNotNone(stale.routes[0].stops[0].eta)

        unknown = plan(technician(job("A"), job("B", km=2), fix_at=None))
        route = unknown.routes[0]
        self.assertEqual(route.start_source, StartSource.UNKNOWN)
        self.assertEqual(codes(unknown), ["TECHNICIAN_LOCATION_UNKNOWN"])
        self.assertEqual([stop.eta for stop in route.stops], [None, None])
        self.assertIsNone(route.stops[0].leg_km)
        self.assertAlmostEqual(route.stops[1].leg_km, 1, delta=0.01)
        self.assertEqual(unknown.summary.stops_without_eta, 2)

    def test_shift_window(self):
        later_shift = plan(technician(job("A", km=15), shift=("09:00", "10:00")))
        stop = later_shift.routes[0].stops[0]
        self.assertEqual(later_shift.routes[0].start_at, at("09:00"))
        self.assertTrue(stop.after_shift_end)
        self.assertEqual(later_shift.summary.stops_after_shift_end, 1)

        finished_shift = plan(technician(job("A"), shift=("06:00", "07:00")))
        self.assertIn("TECHNICIAN_OFF_SHIFT", codes(finished_shift))

    def test_data_problems_become_issues(self):
        response = plan(
            technician(job("NO_GPS", located=False, due="12:00"), job("SHARED")),
            technician(job("SHARED"), job("B"), account="ktv.b"),
            technician(job("C"), account="ktv.b"),
        )
        self.assertEqual(
            codes(response),
            ["JOB_LOCATION_UNKNOWN", "DUPLICATE_JOB", "DUPLICATE_TECHNICIAN"],
        )
        self.assertEqual(
            [[stop.job_id for stop in route.stops] for route in response.routes],
            [["SHARED"], ["B"]],
        )
        # Job không xếp được vẫn nằm trong mẫu số SLA.
        self.assertEqual(response.summary.sla_evaluable_jobs, 1)
        self.assertEqual(response.summary.on_time_rate_percent, 0.0)

    def test_service_minutes_by_case_type(self):
        config = RoutingConfig()
        self.assertEqual(config.service_minutes(job("x", case_type="THU HỒI THIẾT BỊ")), 15)
        self.assertEqual(config.service_minutes(job("x", case_type="maintenance")), 60)
        self.assertEqual(config.service_minutes(job("x", case_type="LOẠI MỚI")), 60)
        custom = RoutingConfig(default_service_minutes=45, service_minutes_by_case_type={})
        self.assertEqual(custom.service_minutes(job("x")), 45)
        with self.assertRaises(ValueError):
            RoutingConfig(average_speed_kmh=0)


class FakeOsrm:
    """Server /table giả: km đường bộ = 1,5 × chim bay, lái xe 40 km/h."""

    def __init__(
        self,
        *,
        status: int = 200,
        missing_first_to_last: bool = False,
        delay_seconds: float = 0.0,
    ) -> None:
        self.calls: list[int] = []  # Số điểm trong từng request.
        self.max_in_flight = 0  # Số request cùng lúc nhiều nhất.
        in_flight = 0
        lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass

            def do_GET(self) -> None:
                nonlocal in_flight
                # urlsplit giữ nguyên dấu ";" trong path (urlparse cắt thành params).
                coordinates = urlsplit(self.path).path.rsplit("/", 1)[-1]
                points = [
                    GeoPoint(float(lat), float(lng))
                    for lng, lat in (pair.split(",") for pair in coordinates.split(";"))
                ]
                with lock:
                    owner.calls.append(len(points))
                    in_flight += 1
                    owner.max_in_flight = max(owner.max_in_flight, in_flight)
                time.sleep(delay_seconds)
                with lock:
                    in_flight -= 1
                if status == 200:
                    distances = [[distance_km(a, b) * 1500 for b in points] for a in points]
                    durations = [[meters / 1000 / 40 * 3600 for meters in row] for row in distances]
                    if missing_first_to_last:
                        distances[0][-1] = durations[0][-1] = None
                    body = {"code": "Ok", "distances": distances, "durations": durations}
                else:
                    body = {"code": "Error", "message": "quá tải"}
                data = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class TravelTest(RoutingTestCase):
    def setUp(self) -> None:
        self.fakes: list[FakeOsrm] = []

    def tearDown(self) -> None:
        for fake in self.fakes:
            fake.close()

    def fake(self, **options) -> FakeOsrm:
        fake = FakeOsrm(**options)
        self.fakes.append(fake)
        return fake

    def test_road_km_and_minutes_drive_eta(self):
        fake = self.fake()
        request = RouteRequest(at("08:00"), (technician(job("A", km=15, due="09:00"), job("B", km=30)),))
        response = plan_routes(request, travel=OsrmTravel(fake.url, min_interval_seconds=0))
        route = response.routes[0]
        first = route.stops[0]
        road_km = distance_km(HOME, north(15)) * 1.5
        road_minutes = road_km / 40 * 60

        self.assertEqual(route.travel_source, "OSRM")
        self.assertAlmostEqual(first.leg_km, road_km, places=2)
        self.assertAlmostEqual(first.leg_minutes, road_minutes, delta=0.01)
        self.assertTime(first.eta, at("08:00") + timedelta(minutes=road_minutes))
        self.assertAlmostEqual(route.total_km, 45, delta=0.05)
        self.assertEqual(fake.calls, [3])  # Vị trí KTV + 2 job trong một request.
        self.assertEqual(response.issues, ())

    @staticmethod
    def three_technicians() -> RouteRequest:
        return RouteRequest(
            at("08:00"),
            tuple(
                technician(job(f"{name}1", km=offset + 1), job(f"{name}2", km=offset + 2), account=f"ktv.{name}")
                for name, offset in (("a", 0), ("b", 10), ("c", 20))
            ),
        )

    def test_self_hosted_sends_one_request_per_technician_in_parallel(self):
        request = self.three_technicians()
        fake = self.fake(delay_seconds=0.3)
        travel = OsrmTravel(fake.url)  # Không phải server public: không chờ, song song.
        started = time.monotonic()
        response = plan_routes(request, travel=travel)

        self.assertEqual(travel.parallel_requests, 16)
        self.assertEqual(fake.calls, [3, 3, 3])  # Vị trí KTV + 2 job, mỗi KTV một request.
        self.assertGreaterEqual(fake.max_in_flight, 2)
        self.assertLess(time.monotonic() - started, 0.8)  # Tuần tự sẽ mất ≥ 0,9 s.
        sequential = plan_routes(request, travel=OsrmTravel(self.fake().url, parallel_requests=1))
        self.assertEqual(to_json_dict(response.routes), to_json_dict(sequential.routes))

    def test_technicians_share_requests_up_to_location_limit(self):
        request = self.three_technicians()
        shared_fake = self.fake()
        shared = plan_routes(
            request,
            travel=OsrmTravel(shared_fake.url, min_interval_seconds=0, parallel_requests=1),
        )
        self.assertEqual(shared_fake.calls, [7])  # Vị trí KTV dùng chung + 6 job.

        limited_fake = self.fake()
        started = time.monotonic()
        limited = plan_routes(
            request,
            travel=OsrmTravel(limited_fake.url, max_locations=4, min_interval_seconds=0.1),
        )
        self.assertEqual(limited_fake.calls, [3, 3, 3])
        self.assertGreaterEqual(time.monotonic() - started, 0.2)  # Chờ giữa các request.
        self.assertEqual(to_json_dict(limited.routes), to_json_dict(shared.routes))

    def test_falls_back_to_straight_line_with_issue(self):
        request = RouteRequest(at("08:00"), (technician(job("A", km=15)),))
        for travel in (
            OsrmTravel(self.fake(status=503).url, min_interval_seconds=0),
            OsrmTravel("http://127.0.0.1:9", timeout_seconds=2, min_interval_seconds=0),
        ):
            with self.subTest(travel.base_url):
                response = plan_routes(request, travel=travel)
                route = response.routes[0]
                self.assertEqual(route.travel_source, "HAVERSINE")
                self.assertAlmostEqual(route.stops[0].leg_km, distance_km(HOME, north(15)), places=3)
                self.assertEqual(codes(response), ["ROAD_DISTANCE_FALLBACK"])

    def test_missing_road_segment_uses_straight_line(self):
        fake = self.fake(missing_first_to_last=True)
        request = RouteRequest(at("08:00"), (technician(job("A", km=5)),))
        response = plan_routes(request, travel=OsrmTravel(fake.url, min_interval_seconds=0))
        route = response.routes[0]
        self.assertEqual(route.travel_source, "OSRM")
        self.assertAlmostEqual(route.stops[0].leg_km, distance_km(HOME, north(5)), places=3)
        self.assertEqual(codes(response), ["ROAD_DISTANCE_FALLBACK"])

    def test_same_point_needs_no_request(self):
        fake = self.fake()
        request = RouteRequest(at("08:00"), (technician(job("HERE", km=0), job("THERE", km=0)),))
        response = plan_routes(request, travel=OsrmTravel(fake.url, min_interval_seconds=0))
        self.assertEqual(fake.calls, [])
        self.assertEqual([stop.leg_km for stop in response.routes[0].stops], [0.0, 0.0])


TIME_MODEL = {
    "format": "ktv-time-model/1",
    "service_minutes": {"default": 12, "by_case_type": {"MAINTENANCE": 20}, "by_emp": {"ktv.a": {"MAINTENANCE": 5}}},
    "transition_minutes": {"km_edges": [1, 5], "minutes": [10, 30, 60], "by_hour": {"11": [100, 120, 150]}},
}


class TimeModelTest(RoutingTestCase):
    def test_service_minutes_prefer_technician_then_case_type(self):
        config = time_model_config(TIME_MODEL)
        self.assertEqual(config.service_minutes(job("A"), "ktv.a"), 5)
        self.assertEqual(config.service_minutes(job("A"), "ktv.b"), 20)
        # Loại việc mô hình chưa học giữ bảng mặc định; loại lạ dùng default của mô hình.
        self.assertEqual(config.service_minutes(job("P", case_type="THU HỒI THIẾT BỊ"), "ktv.a"), 15)
        self.assertEqual(config.service_minutes(job("X", case_type="LOẠI MỚI"), "ktv.a"), 12)

    def test_transition_table_drives_eta_by_distance_and_departure_hour(self):
        config = time_model_config(TIME_MODEL)
        request = RouteRequest(at("08:00"), (technician(job("A", km=0.5, due="09:00"), job("B", km=3, due="10:00")),))
        first, second = plan_routes(request, config).routes[0].stops

        self.assertEqual((first.leg_minutes, second.leg_minutes), (10, 30))  # 0,5 km ≤ 1; 2,5 km thuộc (1, 5].
        self.assertTime(first.eta, at("08:10"))
        self.assertTime(first.finish_at, at("08:15"))  # ktv.a làm 5 phút.
        self.assertTime(second.eta, at("08:45"))
        self.assertAlmostEqual(first.leg_km, 0.5, places=2)  # km vẫn lấy từ TravelModel.

        lunch = plan_routes(RouteRequest(at("11:30"), request.technicians), config).routes[0]
        self.assertEqual(lunch.stops[0].leg_minutes, 100)

    def test_load_time_model_rejects_invalid_files(self):
        transition = TIME_MODEL["transition_minutes"]
        cases = {
            "sai format": {**TIME_MODEL, "format": "khác"},
            "thiếu một cột phút": {**TIME_MODEL, "transition_minutes": {**transition, "minutes": [10, 30]}},
            "km không tăng dần": {**TIME_MODEL, "transition_minutes": {**transition, "km_edges": [5, 1]}},
            "phút âm": {**TIME_MODEL, "service_minutes": {**TIME_MODEL["service_minutes"], "default": -1}},
            "giờ ngoài 0..23": {**TIME_MODEL, "transition_minutes": {**transition, "by_hour": {"25": [1, 2, 3]}}},
        }
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "model.json"
            for name, model in cases.items():
                with self.subTest(name):
                    path.write_text(json.dumps(model), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_time_model(path)
            path.write_text("{hỏng", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_time_model(path)

    def test_cli_uses_time_model(self):
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
        with TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.json"
            model.write_text(json.dumps(TIME_MODEL), encoding="utf-8")
            request = Path(tmp) / "request.json"
            request.write_text(json.dumps(REQUEST_JSON), encoding="utf-8")
            done = subprocess.run(
                [sys.executable, "-m", "ktv_routing", str(request), "--travel", "haversine", "--time-model", str(model)],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(done.returncode, 0, done.stderr)
            route = json.loads(done.stdout)["routes"][0]
            km = distance_km(GeoPoint(10.01, 106.0), GeoPoint(10.05, 106.02))  # CL1 đang làm → CL2.
            self.assertEqual(route["stops"][0]["leg_minutes"], 30 if 1 < km <= 5 else 60)
            self.assertEqual(route["start_at"], "2026-08-03T08:00:00+07:00")  # CL1 bắt đầu 07:30, ktv.a làm 5 phút.

            model.write_text("{}", encoding="utf-8")
            failed = subprocess.run(
                [sys.executable, "-m", "ktv_routing", str(request), "--travel", "haversine", "--time-model", str(model)],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(failed.returncode, 2)
            self.assertIn("Mô hình thời gian", failed.stderr)


REQUEST_JSON = {
    "planned_at": "2026-08-03T08:00:00+07:00",
    "filter": {"case_types": ["MAINTENANCE"], "branch_names": ["HNI_04"]},
    "technicians": [
        {
            "emp_account": "ktv.a",
            "shift_start": "2026-08-03T08:00:00+07:00",
            "shift_end": "2026-08-03T17:30:00+07:00",
            "last_location": {
                "location": {"lat": 10.0, "lng": 106.0},
                "recorded_at": "2026-08-03T07:55:00+07:00",
                "source": "GPS",
            },
            "jobs": [
                {
                    "job_id": "CL1",
                    "state": "IN_PROGRESS",
                    "location": {"lat": 10.01, "lng": 106.0},
                    "case_type": "MAINTENANCE",
                    "started_at": "2026-08-03T07:30:00+07:00",
                },
                {
                    "job_id": "CL2",
                    "state": "PENDING",
                    "location": {"lat": 10.05, "lng": 106.02},
                    "case_type": "MAINTENANCE",
                    "address": "Phường X",
                    "due_at": "2026-08-03T18:00:00+07:00",
                    "priority": 2,
                    "appointment_start": "2026-08-03T08:00:00+07:00",
                    "complete_by": "2026-08-03T23:59:59+07:00",
                    "area": "Phường X",
                },
                {"job_id": "CL3", "state": "PENDING", "location": None},
            ],
            "previous_sequence": ["CL2"],
        }
    ],
}


class ContractTest(unittest.TestCase):
    def test_json_round_trip_and_planning(self):
        request = request_from_dict(REQUEST_JSON)
        self.assertEqual(request.filter, JobFilter(("MAINTENANCE",), ("HNI_04",), ()))
        self.assertEqual(
            request_from_dict(json.loads(json.dumps(to_json_dict(request)))), request
        )

        response = plan_routes(request)
        body = json.loads(json.dumps(to_json_dict(response)))
        self.assertEqual(body["routes"][0]["start_source"], "IN_PROGRESS_JOB")
        self.assertEqual(body["routes"][0]["start_at"], "2026-08-03T08:30:00+07:00")
        self.assertEqual([stop["job_id"] for stop in body["routes"][0]["stops"]], ["CL2"])
        self.assertEqual(body["issues"][0]["code"], "JOB_LOCATION_UNKNOWN")

    def test_query_from_dict(self):
        query = query_from_dict(
            {"planned_at": "2026-08-03T08:00:00", "filter": {"case_types": ["MAINTENANCE"]}}
        )
        self.assertEqual(query, WorkloadQuery(at("08:00"), JobFilter(("MAINTENANCE",))))
        self.assertEqual(
            query_from_dict({"planned_at": "2026-08-03T08:00:00"}).filter, JobFilter()
        )

    def test_errors_name_the_broken_field(self):
        def jobs(data):
            return data["technicians"][0]["jobs"]

        cases = {
            "request.technicians[0].jobs[0].state": lambda d: jobs(d)[0].update(state="DONE"),
            "request.technicians[0].jobs[1]: field không có": lambda d: jobs(d)[1].update(dueAt="x"),
            "request.technicians[0].jobs[1].due_at": lambda d: jobs(d)[1].update(
                due_at="2026-08-03T18:00:00"
            ),
            "request.technicians[0].jobs[1].location.lat": lambda d: jobs(d)[1].update(
                location={"lat": 91, "lng": 106}
            ),
            "request.technicians[0].jobs[1].priority": lambda d: jobs(d)[1].update(priority=True),
            "request.technicians[0].emp_account": lambda d: d["technicians"][0].update(
                emp_account=" "
            ),
            "request.technicians[0]: shift_end": lambda d: d["technicians"][0].update(
                shift_end="2026-08-03T07:00:00+07:00"
            ),
            "request.technicians[0].jobs[1]: due_at trước appointment_start": lambda d: jobs(d)[1].update(
                appointment_start="2026-08-03T19:00:00+07:00"
            ),
            "request.technicians[0].previous_sequence[0]": lambda d: d["technicians"][0].update(
                previous_sequence=[" "]
            ),
            "request.filter.case_types": lambda d: d["filter"].update(case_types="MAINTENANCE"),
            "request.technicians: cần mảng": lambda d: d.pop("technicians"),
        }
        for expected, mutate in cases.items():
            with self.subTest(expected):
                data = copy.deepcopy(REQUEST_JSON)
                mutate(data)
                with self.assertRaises(ContractError) as caught:
                    request_from_dict(data)
                self.assertIn(expected, str(caught.exception))


class RulesTest(unittest.TestCase):
    def test_rules_round_trip_catalog_and_validation(self):
        rules = BusinessRules()
        data = json.loads(json.dumps(rules_to_dict(rules)))
        self.assertEqual(rules_from_dict(data), rules)
        catalog = rules.catalog()
        self.assertEqual(
            [[item["code"] for item in tier] for tier in catalog["tiers"][:2]],
            [["LATE_CHECKIN"], ["LATE_COMPLETION", "AFTER_SHIFT"]],
        )
        self.assertEqual(catalog["unused"], [])
        cases = {
            "sai format": {**data, "format": "khác"},
            "rule không tồn tại": {**data, "tiers": [{"KM_SAI": 1}]},
            "rule nằm ở hai tầng": {**data, "tiers": [{"KM": 1}, {"KM": 2}]},
            "trọng số âm": {**data, "tiers": [{"KM": -1}]},
            "không có tầng": {**data, "tiers": []},
            "policy lạ": {**data, "previous_route_policy": "ALWAYS"},
            "field lạ": {**data, "weights": {}},
            "max_exact_jobs quá lớn": {**data, "max_exact_jobs": 30},
        }
        for name, value in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                rules_from_dict(value)

    def test_cli_prints_rules_and_plans_with_edited_rules(self):
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
        printed = subprocess.run(
            [sys.executable, "-m", "ktv_routing", "--print-rules"], env=env, capture_output=True, text=True
        )
        self.assertEqual(printed.returncode, 0, printed.stderr)
        rules = json.loads(printed.stdout)
        rules["tiers"] = [{"KM": 1.0}]
        with TemporaryDirectory() as tmp:
            rules_path = Path(tmp) / "rules.json"
            rules_path.write_text(json.dumps(rules), encoding="utf-8")
            request_path = Path(tmp) / "request.json"
            request_path.write_text(json.dumps(REQUEST_JSON), encoding="utf-8")
            command = [sys.executable, "-m", "ktv_routing", str(request_path), "--travel", "haversine", "--rules", str(rules_path)]
            done = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
            route = json.loads(done.stdout)["routes"][0]
            self.assertEqual((route["sequence_source"], route["previous_route"]), ("OPTIMAL", "KEPT"))
            self.assertIn("KM", route["score"])

            rules_path.write_text(json.dumps({**rules, "tiers": [{"KM_SAI": 1}]}), encoding="utf-8")
            bad = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertEqual(bad.returncode, 2)
            self.assertIn("Rule nghiệp vụ", bad.stderr)


class FakeDataTeam:
    def __init__(self, returned_filter: JobFilter | None = None) -> None:
        self.queries: list[WorkloadQuery] = []
        self.returned_filter = returned_filter

    def fetch_workload(self, query: WorkloadQuery) -> RouteRequest:
        self.queries.append(query)
        return RouteRequest(
            planned_at=query.planned_at,
            technicians=(technician(job("A")),),
            filter=query.filter if self.returned_filter is None else self.returned_filter,
        )


class ServiceTest(unittest.TestCase):
    def test_filter_goes_to_data_team_and_comes_back(self):
        query = WorkloadQuery(at("08:00"), JobFilter(case_types=("MAINTENANCE",)))
        data_team = FakeDataTeam()
        response = RoutingService(data_team).plan(query)
        self.assertEqual(data_team.queries, [query])
        self.assertEqual(response.filter, query.filter)
        self.assertEqual(response.summary.routed_stops, 1)

        wrong = FakeDataTeam(returned_filter=JobFilter(branch_names=("HNI_04",)))
        with self.assertRaises(ContractError):
            RoutingService(wrong).plan(query)


class CliTest(unittest.TestCase):
    def test_cli_reads_request_and_writes_response(self):
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
        with TemporaryDirectory() as tmp:
            request_path = Path(tmp) / "request.json"
            response_path = Path(tmp) / "out" / "response.json"
            request_path.write_text(json.dumps(REQUEST_JSON), encoding="utf-8")
            done = subprocess.run(
                [
                    sys.executable, "-m", "ktv_routing", str(request_path),
                    "--travel", "haversine", "--out", str(response_path),
                ],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(done.returncode, 0, done.stderr)
            body = json.loads(response_path.read_text(encoding="utf-8"))
            self.assertEqual(body["routes"][0]["stops"][0]["job_id"], "CL2")
            self.assertEqual(body["filter"]["case_types"], ["MAINTENANCE"])

            request_path.write_text("{}", encoding="utf-8")
            bad = subprocess.run(
                [sys.executable, "-m", "ktv_routing", str(request_path)],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(bad.returncode, 2)
            self.assertIn("request.planned_at", bad.stderr)


if __name__ == "__main__":
    unittest.main()
