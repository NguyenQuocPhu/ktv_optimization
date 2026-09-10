#!/usr/bin/env python3
"""Smoke: snapshot events -> completion artifacts -> end-of-day outcomes."""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ktv_optimizer.evaluation import DailyOutcomeEvaluator  # noqa: E402
from ktv_optimizer.evaluation.outcome import write_daily_outcome  # noqa: E402
from ktv_optimizer.pipeline import OperationalSnapshotProcessor  # noqa: E402


EVENT_COLUMNS = [
    "CHECKLIST_ID",
    "CHECKLIST_STATUS",
    "BRANCH_NAME",
    "CASE_TYPE",
    "OBJ_LOCATION",
    "EMP_ACCOUNT",
    "CREATE_DATE",
    "FINISH_DATE",
    "FLAG_ON_TIME",
]


def main() -> int:
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        roster_path = root / "roster.csv"
        pd.DataFrame(
            [
                {
                    "EMP_ACCOUNT": "T1",
                    "BRANCH_NAME": "A",
                    "LATITUDE": 10.005,
                    "LONGITUDE": 106.005,
                    "SHIFT_START": "2026-08-03 06:00:00",
                    "SHIFT_END": "2026-08-03 18:00:00",
                    "SUPPORTED_CASE_TYPES": "MAINTENANCE",
                }
            ]
        ).to_csv(roster_path, index=False, encoding="utf-8-sig")

        snapshot_1 = root / "snapshot_001.csv"
        pd.DataFrame(
            [
                [
                    "J01",
                    "Chưa phân công",
                    "A",
                    "MAINTENANCE",
                    "Phường Demo, Tỉnh Demo",
                    "",
                    "2026-08-03 07:00:00",
                    "",
                    "INPROCESS",
                ],
                [
                    "J02",
                    "Chưa phân công",
                    "A",
                    "MAINTENANCE",
                    "Phường Demo, Tỉnh Demo",
                    "",
                    "2026-08-03 07:10:00",
                    "",
                    "INPROCESS",
                ],
            ],
            columns=EVENT_COLUMNS,
        ).to_csv(snapshot_1, index=False, encoding="utf-8-sig")

        snapshot_2 = root / "snapshot_002.csv"
        pd.DataFrame(
            [
                [
                    "J01",
                    "Đã xử lý",
                    "A",
                    "MAINTENANCE",
                    "Phường Demo, Tỉnh Demo",
                    "T1",
                    "2026-08-03 07:00:00",
                    "2026-08-03 08:20:00",
                    "YES",
                ],
                [
                    "J02",
                    "Đã xử lý",
                    "A",
                    "MAINTENANCE",
                    "Phường Demo, Tỉnh Demo",
                    "T1",
                    "2026-08-03 07:10:00",
                    "2026-08-03 09:20:00",
                    "NO",
                ],
            ],
            columns=EVENT_COLUMNS,
        ).to_csv(snapshot_2, index=False, encoding="utf-8-sig")

        checkin_path = root / "checkin.csv"
        pd.DataFrame(
            [
                ["J01", "(10.005,106.005)", "(10.005,106.005)", "2026-08-03 08:00:00", "2026-08-03 08:20:00", "0001"],
                ["J02", "(10.040,106.005)", "(10.040,106.005)", "2026-08-03 09:00:00", "2026-08-03 09:20:00", "0001"],
            ],
            columns=[
                "CHECKLIST_ID",
                "LAT_LNG_IN",
                "LAT_LNG_OUT",
                "CHECKIN_DATE",
                "CHECKOUT_DATE",
                "EMP_CODE",
            ],
        ).to_csv(checkin_path, index=False, encoding="utf-8-sig")

        gps_path = root / "gps.csv"
        pd.DataFrame(
            [
                ["0001", "T1", "10.005,106.005", "2026-08-03 08:25:00"],
                ["0001", "T1", "10.020,106.005", "2026-08-03 08:35:00"],
                ["0001", "T1", "10.030,106.005", "2026-08-03 08:45:00"],
                ["0001", "T1", "10.040,106.005", "2026-08-03 08:55:00"],
            ],
            columns=["EMPLOYEECODE", "ACCOUNTEMP", "COORDINATE", "CREATEDATE"],
        ).to_csv(gps_path, index=False, encoding="utf-8-sig")

        runs_root = root / "runs"
        processor = OperationalSnapshotProcessor(
            boundary_path=(
                PROJECT_ROOT
                / "utils"
                / "fixtures"
                / "snapshots_v1"
                / "boundary.geojson"
            ),
            runtime_dir=root / "runtime",
            output_root=runs_root,
        )
        processor.process(
            snapshot_id="DAY-S001",
            snapshot_time=datetime(2026, 8, 3, 7, 30),
            maintenance_path=snapshot_1,
            roster_path=roster_path,
        )
        processor.process(
            snapshot_id="DAY-S002",
            snapshot_time=datetime(2026, 8, 3, 10),
            maintenance_path=snapshot_2,
            roster_path=roster_path,
        )

        result = DailyOutcomeEvaluator(
            runs_root=runs_root,
            maintenance_path=snapshot_2,
            checkin_path=checkin_path,
            gps_path=gps_path,
            roster_path=roster_path,
        ).evaluate(date(2026, 8, 3))
        output_dir = root / "daily_outcome"
        write_daily_outcome(output_dir, result)

        outcome = result.summary["checklist_outcomes"]
        travel = result.summary["travel_actual_gps_estimate"]
        shift = result.summary["shift_actual"]
        planning = result.summary["ai_planning"]
        assert outcome["terminal_events"] == 2
        assert outcome["completed_jobs"] == 2
        assert outcome["sla_evaluable_jobs"] == 2
        assert outcome["sla_on_time_rate_percent"] == 50.0
        assert outcome["total_service_minutes"] == 40.0
        assert travel["eligible_inter_job_legs"] == 1
        assert travel["gps_evaluable_legs"] == 1
        assert travel["total_distance_km"] > 3.0
        assert travel["total_travel_minutes"] == 30.0
        assert travel["total_wait_between_jobs_minutes"] == 10.0
        assert shift["completed_jobs_in_shift"] == 2
        assert planning["evaluated_snapshots"] == 2
        assert planning["target_met_snapshots"] == 2
        assert set(result.checklist_outcomes["SLA_SOURCE"]) == {
            "FLAG_ON_TIME"
        }
        assert len(result.technician_outcomes) == 1
        assert (output_dir / "daily_summary.json").is_file()
        persisted = json.loads(
            (output_dir / "daily_summary.json").read_text(encoding="utf-8")
        )
        assert persisted["checklist_outcomes"]["sla_on_time_rate_percent"] == 50.0
        print(json.dumps(result.summary, ensure_ascii=False, indent=2))
        print("PASS: daily outcome evaluator")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
