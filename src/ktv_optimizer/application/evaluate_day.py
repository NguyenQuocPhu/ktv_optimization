"""Build one immutable end-of-day outcome report from operational runs."""

from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

from ktv_optimizer.evaluation import DailyOutcomeEvaluator
from ktv_optimizer.evaluation.outcome import write_daily_outcome


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date", required=True, type=date.fromisoformat, help="YYYY-MM-DD"
    )
    parser.add_argument(
        "--runs-root",
        type=Path,
        default=Path("artifacts/operational_runs"),
        help="Root chứa các run có run_metadata.json/completed_jobs.csv.",
    )
    parser.add_argument(
        "--maintenance",
        type=Path,
        help="Export maintenance cuối ngày để bổ sung FLAG_ON_TIME/FINISH_DATE.",
    )
    parser.add_argument(
        "--checkin", type=Path, help="QOS_MAINT_CHECKIN_INFO CSV (tùy chọn)."
    )
    parser.add_argument(
        "--gps", type=Path, help="Lịch sử GPS KTV CSV (tùy chọn)."
    )
    parser.add_argument(
        "--roster",
        type=Path,
        help="Roster có SHIFT_START/SHIFT_END để tính hoàn thành trong ca.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("artifacts/daily_outcomes"),
    )
    return parser.parse_args()


def _write_latest(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def main() -> int:
    args = parse_args()
    evaluator = DailyOutcomeEvaluator(
        runs_root=args.runs_root,
        maintenance_path=args.maintenance,
        checkin_path=args.checkin,
        gps_path=args.gps,
        roster_path=args.roster,
    )
    result = evaluator.evaluate(args.date)
    generated = datetime.now(timezone.utc)
    safe_date = re.sub(r"[^0-9-]", "", args.date.isoformat())
    report_id = f"{safe_date}__{generated.strftime('%Y%m%dT%H%M%S%fZ')}"
    output_dir = args.output_root / report_id
    write_daily_outcome(output_dir, result)
    latest_path = args.output_root / f"latest_{safe_date}.json"
    _write_latest(
        latest_path,
        {
            "report_id": report_id,
            "report_date": args.date.isoformat(),
            "generated_at": generated.isoformat(),
            "output_dir": str(output_dir.resolve()),
        },
    )

    outcome = result.summary["checklist_outcomes"]
    travel = result.summary["travel_actual_gps_estimate"]
    planning = result.summary["ai_planning"]
    print(
        f"OUTCOME {args.date}: completed={outcome['completed_jobs']}, "
        f"cancelled={outcome['cancelled_jobs']}, "
        f"SLA={outcome['sla_on_time_rate_percent']}%"
    )
    print(
        f"GPS travel: legs={travel['gps_evaluable_legs']}/"
        f"{travel['eligible_inter_job_legs']}, "
        f"distance={travel['total_distance_km']} km, "
        f"moving={travel['total_travel_minutes']} min"
    )
    print(
        f"AI planning <=5s: {planning['target_met_snapshots']}/"
        f"{planning['evaluated_snapshots']} snapshots"
    )
    print(f"Report: {output_dir.resolve()}")
    print(f"Latest: {latest_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
