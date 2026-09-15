"""Test research: học mô hình thời gian và backtest routing trên dữ liệu nhỏ tự tạo."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "simulator"), str(ROOT / "research"), str(ROOT / "tests")]

from backtest_routing import (  # noqa: E402
    ACTUAL,
    NEAREST,
    PLANNER,
    day_replays,
    first_checkouts,
    job_created_at,
    job_points,
    next_job_decisions,
    summarize_days,
    summarize_decisions,
)
from ktv_routing import HaversineTravel, JobInput, JobState, RoutingConfig, time_model_config  # noqa: E402
from ktv_simulator import EventWorkloadProvider  # noqa: E402
from test_simulator import DAY, write_event_stream  # noqa: E402
from time_model import (  # noqa: E402
    CURRENT,
    LEARNED,
    evaluate_time_model,
    fit_time_model,
    load_transitions,
    load_visits,
    prepare_visits,
)

TRAIN_DAY = "2026-06-02"
KM_PER_DEGREE = 111.195


def visit(job_id: str, emp: str, checkin: str, checkout: str, north_km: float, *, lat0: float = 10.0) -> dict:
    lat = lat0 + north_km / KM_PER_DEGREE
    return {
        "job_id": job_id,
        "emp_account": emp,
        "branch_name": "B1",
        "case_type": "MAINTENANCE",
        "created_at": pd.Timestamp(f"{TRAIN_DAY} 07:00"),
        "checkin_at": pd.Timestamp(f"{TRAIN_DAY} {checkin}"),
        "checkout_at": pd.Timestamp(f"{TRAIN_DAY} {checkout}"),
        "lat_in": lat,
        "lng_in": 106.0,
        "lat_out": lat,
        "lng_out": 106.0,
    }


def one_day() -> tuple[pd.Timestamp, pd.Timestamp]:
    return pd.Timestamp(TRAIN_DAY), pd.Timestamp(TRAIN_DAY) + pd.Timedelta(days=1)


class TimeModelTest(unittest.TestCase):
    def test_fit_medians_and_serve_through_routing_config(self):
        visits = prepare_visits(
            pd.DataFrame(
                [
                    visit("A1", "ktv.a", "08:00", "08:10", 0),  # làm 10 phút
                    visit("A2", "ktv.a", "08:40", "08:52", 0.3),  # chờ 30 phút, 0,3 km; làm 12 phút
                    visit("A3", "ktv.a", "09:52", "10:06", 3.3),  # chờ 60 phút, 3 km; làm 14 phút
                    visit("B1", "ktv.b", "08:00", "08:30", 0, lat0=10.5),  # làm 30 phút
                    visit("B2", "ktv.b", "09:00", "09:40", 0.3, lat0=10.5),  # chờ 30 phút, 0,3 km; làm 40 phút
                ]
            )
        )
        transitions = load_transitions(visits)
        start, end = one_day()
        model = fit_time_model(visits, transitions, start=start, end=end, min_samples=3, min_cell=1)

        service = model["service_minutes"]
        # ktv.b chỉ có 2 lượt, dưới min_samples nên dùng median chung.
        self.assertEqual((service["default"], service["by_case_type"], service["by_emp"]), (14.0, {"MAINTENANCE": 14.0}, {"ktv.a": {"MAINTENANCE": 12.0}}))
        minutes = model["transition_minutes"]["minutes"]
        self.assertEqual((minutes[1], minutes[4], minutes[0]), (30.0, 60.0, 30.0))  # (0,05; 0,5] · (2; 4] · ô trống lấy median chung.
        self.assertEqual(model["transition_minutes"]["by_hour"], {})  # Mọi cặp đều rời lúc 8h, trùng hàng chung.

        config = time_model_config(model)
        job = JobInput(job_id="X", state=JobState.PENDING, location=None, case_type="MAINTENANCE")
        self.assertEqual((config.service_minutes(job, "ktv.a"), config.service_minutes(job, "ktv.b")), (12.0, 14.0))

        evaluation = evaluate_time_model(model, visits, transitions, start=start, end=end)
        self.assertEqual(evaluation[LEARNED]["service_minutes"]["n"], 5)
        self.assertEqual(evaluation[LEARNED]["transition_minutes"]["n"], 3)
        self.assertLess(evaluation[LEARNED]["service_minutes"]["mae"], evaluation[CURRENT]["service_minutes"]["mae"])


class BacktestTest(unittest.TestCase):
    def test_next_job_decision_on_simulator_fixture(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = write_event_stream(root)
            visits = load_visits(root / "maintenance.csv", root / "checkins.csv")
            transitions = load_transitions(visits)
            with EventWorkloadProvider(events) as provider:
                frame, skipped = next_job_decisions(
                    provider,
                    transitions,
                    points=job_points(visits),
                    done_at=first_checkouts(visits),
                    created_at=job_created_at(visits),
                    config=RoutingConfig(),
                    travel=HaversineTravel(),
                    start=pd.Timestamp(DAY),
                    end=pd.Timestamp(DAY) + pd.Timedelta(days=1),
                )

        # ktv.a checkout C3 lúc 09:00 rồi check-in C1 lúc 09:30. Lúc đó còn C1 và C2; C1 hạn
        # sớm hơn và gần hơn. Lượt C7 của ktv.b thiếu checkout nên không thành cặp.
        self.assertEqual((transitions["job_id"] + ">" + transitions["next_job_id"]).tolist(), ["C3>C1"])
        self.assertEqual(frame[["candidates", "planner_rank", "nearest_rank", "real_points"]].values.tolist(), [[2, 1, 1, 1]])
        summary = summarize_decisions(frame, skipped)
        self.assertEqual((summary["planner_top1_percent"], summary["nearest_top1_percent"], summary["random_top1_percent"]), (100.0, 100.0, 50.0))

    def test_day_replay_compares_actual_planner_and_nearest_orders(self):
        visits = prepare_visits(
            pd.DataFrame(
                [
                    visit("START", "ktv.z", "08:00", "08:10", 0),
                    visit("FAR", "ktv.z", "08:40", "08:55", 3),  # KTV đi xa trước
                    visit("NEAR1", "ktv.z", "09:30", "09:40", 0.5),
                    visit("NEAR2", "ktv.z", "10:00", "10:10", 1),
                ]
            )
        )
        start, end = one_day()
        days, etas = day_replays(visits, configs={CURRENT: RoutingConfig()}, travel=HaversineTravel(), start=start, end=end)
        summary = summarize_days(days, [CURRENT])

        # Thật: 0→3→0,5→1 = 6 km. Planner và gần nhất (cùng hạn): 0→0,5→1→3 = 3 km.
        self.assertAlmostEqual(summary["km"][CURRENT][ACTUAL], 6.0, delta=0.05)
        self.assertAlmostEqual(summary["km"][CURRENT][PLANNER], 3.0, delta=0.05)
        self.assertAlmostEqual(summary["km"][CURRENT][NEAREST], 3.0, delta=0.05)
        self.assertEqual(summary["days_shorter_than_actual_percent"][CURRENT], {PLANNER: 100.0, NEAREST: 100.0})
        self.assertEqual(summary["late"][CURRENT], {ACTUAL: 0, PLANNER: 0, NEAREST: 0})
        self.assertEqual(sorted(etas["step"]), [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
