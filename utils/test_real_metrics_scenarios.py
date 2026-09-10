#!/usr/bin/env python3
"""Five-snapshot metric audit using real HNI_04 checklist identities."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from time import perf_counter

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ktv_optimizer.pipeline import OperationalSnapshotProcessor  # noqa: E402


REAL_CHECKLIST_IDS = (
    "1480032756",
    "1480040046",
    "1479980576",
    "1480037796",
    "1479995696",
    "1480307086",
    "1480085036",
    "1480001466",
    "1480606556",
)
KTV_IDS = (
    "TIN0401.BOTX",
    "TIN0402.ANHVQ8",
    "TIN0403.HUANBN1",
)
MAINTENANCE_COLUMNS = [
    "CHECKLIST_ID",
    "CHECKLIST_STATUS",
    "BRANCH_NAME",
    "CASE_TYPE",
    "OBJ_LOCATION",
    "EMP_ACCOUNT",
    "CREATE_DATE",
]
SNAPSHOT_TIMES = (
    datetime(2026, 6, 30, 8, 0),
    datetime(2026, 6, 30, 8, 30),
    datetime(2026, 6, 30, 9, 0),
    datetime(2026, 6, 30, 9, 30),
    datetime(2026, 6, 30, 10, 0),
)
SCENARIO_CREATE_DATE = {
    "1480032756": "2026-06-30 02:00:00",  # due 12:00
    "1480040046": "2026-06-30 05:00:00",  # due 15:00
    "1479980576": "2026-06-30 03:00:00",  # due 13:00
    "1480037796": "2026-06-30 03:30:00",  # due 13:30
    "1479995696": "2026-06-30 04:00:00",  # due 14:00
    "1480307086": "2026-06-30 04:30:00",  # due 14:30
    "1480085036": "2026-06-29 22:45:00",  # urgent: due 08:45
    "1480001466": "2026-06-29 23:40:00",  # urgent: due 09:40
    "1480606556": "2026-06-30 10:00:00",  # new at S005
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "real_metrics_test",
    )
    return parser.parse_args()


def _find_extracted_inputs() -> Path | None:
    candidates = sorted(
        (
            PROJECT_ROOT / "data" / "temp_real_replay" / "web_sessions"
        ).glob("*/inputs"),
        reverse=True,
    )
    for candidate in candidates:
        required = (
            candidate / "source_jobs_canonical.csv",
            candidate / "roster_proxy_all.csv",
            candidate / "boundary_centroids.geojson",
        )
        if all(path.is_file() for path in required):
            return candidate
    return None


def _extract_selected_from_source() -> pd.DataFrame:
    source = PROJECT_ROOT / "data" / "QOS_MAINTENANCE_utf8.csv"
    selected_chunks = []
    for chunk in pd.read_csv(
        source,
        encoding="utf-8-sig",
        dtype="string",
        usecols=[
            "CHECKLIST_ID",
            "CHECKLIST_STATUS",
            "BRANCH_NAME",
            "CASE_TYPE",
            "OBJ_LOCATION",
            "EMP_ACCOUNT",
            "CREATE_DATE",
            "FINISH_DATE",
            "FLAG_ON_TIME",
        ],
        chunksize=100_000,
    ):
        matched = chunk[chunk["CHECKLIST_ID"].isin(REAL_CHECKLIST_IDS)]
        if not matched.empty:
            selected_chunks.append(matched)
    if not selected_chunks:
        raise ValueError("Không tìm thấy checklist đã chọn trong CSV gốc")
    return pd.concat(selected_chunks).drop_duplicates(
        "CHECKLIST_ID", keep="first"
    )


def _load_real_sources() -> tuple[pd.DataFrame, pd.DataFrame, Path, dict]:
    extracted = _find_extracted_inputs()
    if extracted is not None:
        source_jobs = pd.read_csv(
            extracted / "source_jobs_canonical.csv",
            dtype={"CHECKLIST_ID": "string", "WARD_CODE": "string"},
        )
        selected = source_jobs[
            source_jobs["CHECKLIST_ID"].isin(REAL_CHECKLIST_IDS)
        ].copy()
        roster = pd.read_csv(extracted / "roster_proxy_all.csv")
        roster = roster[roster["EMP_ACCOUNT"].isin(KTV_IDS)].copy()
        provenance = {
            "selection_source": str(
                (extracted / "source_jobs_canonical.csv").resolve()
            ),
            "selection_mode": "REUSE_EXISTING_CANONICAL_EXTRACT",
            "roster_source": str(
                (extracted / "roster_proxy_all.csv").resolve()
            ),
            "boundary_source": str(
                (extracted / "boundary_centroids.geojson").resolve()
            ),
        }
        return (
            selected,
            roster,
            extracted / "boundary_centroids.geojson",
            provenance,
        )

    selected = _extract_selected_from_source()
    roster = pd.DataFrame(
        [
            ("TIN0401.BOTX", 20.974580, 105.861819),
            ("TIN0402.ANHVQ8", 20.912349, 105.837039),
            ("TIN0403.HUANBN1", 20.879834, 105.887811),
        ],
        columns=["EMP_ACCOUNT", "LATITUDE", "LONGITUDE"],
    )
    roster["BRANCH_NAME"] = "HNI_04"
    provenance = {
        "selection_source": str(
            (PROJECT_ROOT / "data" / "QOS_MAINTENANCE_utf8.csv").resolve()
        ),
        "selection_mode": "CHUNK_EXTRACT_FROM_ORIGINAL",
        "roster_source": "KNOWN_HISTORICAL_WARD_CENTROID_PROXY",
        "boundary_source": str(
            (PROJECT_ROOT / "data" / "boundary_2026-07-31.geojson").resolve()
        ),
    }
    return (
        selected,
        roster,
        PROJECT_ROOT / "data" / "boundary_2026-07-31.geojson",
        provenance,
    )


def _prepare_roster(roster: pd.DataFrame) -> pd.DataFrame:
    selected = roster[roster["EMP_ACCOUNT"].isin(KTV_IDS)].copy()
    if set(selected["EMP_ACCOUNT"]) != set(KTV_IDS):
        raise ValueError("Roster proxy không đủ ba KTV đã chọn")
    selected["BRANCH_NAME"] = "HNI_04"
    selected["SHIFT_START"] = "2026-06-30 06:00:00"
    selected["SHIFT_END"] = "2026-06-30 22:00:00"
    selected["SUPPORTED_CASE_TYPES"] = "MAINTENANCE"
    selected["EXISTING_WORKLOAD_MINUTES"] = 0
    return selected[
        [
            "EMP_ACCOUNT",
            "BRANCH_NAME",
            "LATITUDE",
            "LONGITUDE",
            "SHIFT_START",
            "SHIFT_END",
            "SUPPORTED_CASE_TYPES",
            "EXISTING_WORKLOAD_MINUTES",
        ]
    ].sort_values("EMP_ACCOUNT")


def _base_rows(selected: pd.DataFrame) -> dict[str, dict]:
    if set(selected["CHECKLIST_ID"].astype(str)) != set(REAL_CHECKLIST_IDS):
        missing = sorted(
            set(REAL_CHECKLIST_IDS)
            - set(selected["CHECKLIST_ID"].astype(str))
        )
        raise ValueError(f"Thiếu checklist thật: {missing}")
    rows = {}
    for _, source in selected.iterrows():
        checklist_id = str(source["CHECKLIST_ID"])
        rows[checklist_id] = {
            "CHECKLIST_ID": checklist_id,
            "CHECKLIST_STATUS": "Chưa phân công",
            "BRANCH_NAME": str(source["BRANCH_NAME"]),
            "CASE_TYPE": str(source["CASE_TYPE"]),
            "OBJ_LOCATION": str(source["OBJ_LOCATION"]),
            "EMP_ACCOUNT": None,
            "CREATE_DATE": SCENARIO_CREATE_DATE[checklist_id],
        }
    return rows


def _event(
    base: dict[str, dict],
    checklist_id: str,
    status: str,
    technician_id: str | None = None,
) -> dict:
    row = dict(base[checklist_id])
    row["CHECKLIST_STATUS"] = status
    row["EMP_ACCOUNT"] = technician_id
    return row


def _assignment_map(run) -> dict[str, str]:
    return {
        item.job_id: item.technician_id
        for item in run.result.optimization.assignments
    }


def _independent_metrics(run, roster: pd.DataFrame) -> dict:
    output_dir = run.output_dir
    checkpoint_path = (
        output_dir.parents[1]
        / "runtime"
        / "checkpoints"
        / run.checkpoint.checkpoint_id
        / "optimizer_state.csv"
    )
    state = pd.read_csv(checkpoint_path, encoding="utf-8-sig")
    assignments = pd.read_csv(
        output_dir / "assignments.csv", encoding="utf-8-sig"
    )
    routes = pd.read_csv(output_dir / "routes.csv", encoding="utf-8-sig")
    completed = pd.read_csv(
        output_dir / "completed_jobs.csv", encoding="utf-8-sig"
    )

    due_by_job = dict(
        zip(
            state["CHECKLIST_ID"].astype(str),
            pd.to_datetime(state["DUE_AT"], errors="coerce"),
        )
    )
    sla_denominator = sum(pd.notna(value) for value in due_by_job.values())
    sla_numerator = 0
    if not routes.empty:
        finish = pd.to_datetime(routes["ESTIMATED_FINISH"], errors="coerce")
        for checklist_id, estimated_finish in zip(
            routes["CHECKLIST_ID"].astype(str), finish
        ):
            due_at = due_by_job.get(checklist_id)
            sla_numerator += (
                pd.notna(due_at)
                and pd.notna(estimated_finish)
                and estimated_finish <= due_at
            )

    completed_due = pd.to_datetime(
        completed.get("DUE_AT"), errors="coerce"
    )
    completed_time = pd.to_datetime(
        completed.get("COMPLETION_EVENT_TIME"), errors="coerce"
    )
    completed_sla_denominator = int(completed_due.notna().sum())
    completed_sla_numerator = int(
        (completed_due.notna() & (completed_time <= completed_due)).sum()
    )

    completed_in_shift = 0
    completion_shift_attribution_missing = 0
    if not completed.empty:
        shift_by_technician = roster.set_index("EMP_ACCOUNT")
        for _, row in completed.iterrows():
            technician_id = row.get("EMP_ACCOUNT")
            if (
                pd.isna(technician_id)
                or technician_id not in shift_by_technician.index
            ):
                completion_shift_attribution_missing += 1
                continue
            shift = shift_by_technician.loc[technician_id]
            event_time = pd.to_datetime(row["COMPLETION_EVENT_TIME"])
            completed_in_shift += (
                pd.to_datetime(shift["SHIFT_START"])
                <= event_time
                <= pd.to_datetime(shift["SHIFT_END"])
            )

    total_distance = 0.0
    total_travel = 0.0
    if not routes.empty:
        route_totals = routes.groupby("TECHNICIAN_ID", as_index=False).first()
        total_distance = round(
            route_totals["ROUTE_TOTAL_DISTANCE_KM"].sum(), 3
        )
        total_travel = round(
            route_totals["ROUTE_TOTAL_TRAVEL_MINUTES"].sum(), 2
        )

    cluster_by_job = dict(
        zip(assignments["job_id"].astype(str), assignments["cluster_key"])
    )
    unique_clusters: set[str] = set()
    technician_cluster_visits = 0
    revisit_count = 0
    total_wait = 0.0
    wait_count = 0
    for _, technician_route in routes.groupby("TECHNICIAN_ID", sort=False):
        ordered = technician_route.sort_values("SEQUENCE")
        sequence = [
            cluster_by_job[str(job_id)]
            for job_id in ordered["CHECKLIST_ID"]
        ]
        unique_clusters.update(sequence)
        technician_cluster_visits += len(set(sequence))
        compressed = []
        for cluster in sequence:
            if not compressed or compressed[-1] != cluster:
                compressed.append(cluster)
        seen = set()
        for cluster in compressed:
            revisit_count += cluster in seen
            seen.add(cluster)

        distance = float(ordered["ROUTE_TOTAL_DISTANCE_KM"].iloc[0])
        travel = float(ordered["ROUTE_TOTAL_TRAVEL_MINUTES"].iloc[0])
        minutes_per_km = travel / distance if distance > 0 else 0.0
        arrivals = pd.to_datetime(ordered["ESTIMATED_ARRIVAL"])
        finishes = pd.to_datetime(ordered["ESTIMATED_FINISH"])
        legs = ordered["LEG_DISTANCE_KM"].fillna(0.0).astype(float).tolist()
        for index in range(1, len(ordered)):
            gap = (arrivals.iloc[index] - finishes.iloc[index - 1]).total_seconds() / 60
            total_wait += max(0.0, gap - legs[index] * minutes_per_km)
            wait_count += 1

    def rate(numerator: int, denominator: int) -> float | None:
        return (
            round(numerator / denominator * 100, 2)
            if denominator
            else None
        )

    return {
        "planned_sla_on_time_rate_percent": rate(
            sla_numerator, sla_denominator
        ),
        "planned_sla_on_time_jobs": int(sla_numerator),
        "planned_sla_evaluable_jobs": int(sla_denominator),
        "completed_sla_on_time_rate_percent": rate(
            completed_sla_numerator, completed_sla_denominator
        ),
        "completed_sla_on_time_jobs": completed_sla_numerator,
        "completed_sla_evaluable_jobs": completed_sla_denominator,
        "total_distance_km": total_distance,
        "total_travel_minutes": total_travel,
        "unique_clusters": len(unique_clusters),
        "technician_cluster_visits": technician_cluster_visits,
        "same_area_revisit_count": revisit_count,
        "completed_jobs_in_shift": int(completed_in_shift),
        "completion_shift_attribution_missing": (
            completion_shift_attribution_missing
        ),
        "total_wait_between_jobs_minutes": round(total_wait, 2),
        "average_wait_between_jobs_minutes": (
            round(total_wait / wait_count, 2) if wait_count else 0.0
        ),
        "ai_planning_seconds": run.result.planning_duration_seconds,
        "ai_planning_target_seconds": 5.0,
        "ai_planning_target_met": (
            run.result.planning_duration_seconds <= 5.0
        ),
    }


def _metric_equal(left, right) -> bool:
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right)) <= 1e-6
    return left == right


def main() -> int:
    args = parse_args()
    session = args.output_root / datetime.now().strftime(
        "real_metrics_%Y%m%dT%H%M%S%f"
    )
    input_dir = session / "inputs"
    input_dir.mkdir(parents=True)

    selected, roster_source, boundary_path, provenance = _load_real_sources()
    roster = _prepare_roster(roster_source)
    roster_path = input_dir / "roster.csv"
    roster.to_csv(roster_path, index=False, encoding="utf-8-sig")

    selected = selected.copy()
    selected["IS_REAL_CHECKLIST_ID"] = True
    selected["SIMULATED_CREATE_DATE"] = selected["CHECKLIST_ID"].map(
        SCENARIO_CREATE_DATE
    )
    selected.to_csv(
        input_dir / "source_selection.csv",
        index=False,
        encoding="utf-8-sig",
    )
    base = _base_rows(selected)

    processor = OperationalSnapshotProcessor(
        boundary_path=boundary_path,
        runtime_dir=session / "runtime",
        output_root=session / "runs",
    )
    runs = []
    event_manifest = []
    external_durations = []

    def process(snapshot_index: int, scenario: str, events: list[dict]):
        snapshot_id = f"METRIC-S{snapshot_index:03d}"
        event_path = input_dir / f"events_{snapshot_index:03d}.csv"
        pd.DataFrame(events, columns=MAINTENANCE_COLUMNS).to_csv(
            event_path, index=False, encoding="utf-8-sig"
        )
        started = perf_counter()
        run = processor.process(
            snapshot_id=snapshot_id,
            snapshot_time=SNAPSHOT_TIMES[snapshot_index - 1],
            maintenance_path=event_path,
            roster_path=roster_path,
        )
        external_durations.append(round(perf_counter() - started, 6))
        runs.append(run)
        event_manifest.append(
            {
                "snapshot_id": snapshot_id,
                "snapshot_time": SNAPSHOT_TIMES[
                    snapshot_index - 1
                ].isoformat(),
                "scenario": scenario,
                "event_rows": len(events),
                "event_file": str(event_path.resolve()),
            }
        )
        return run

    initial = [
        _event(base, "1480032756", "Chưa phân công"),
        _event(base, "1480040046", "Đã phân công", "TIN0401.BOTX"),
        _event(base, "1479980576", "Chưa phân công"),
        _event(base, "1480037796", "Đã phân công", "TIN0402.ANHVQ8"),
        _event(base, "1479995696", "Chưa phân công"),
        _event(base, "1480307086", "Chưa phân công"),
    ]
    run1 = process(1, "INITIAL_MIX_UNASSIGNED_AND_SYSTEM_FIXED", initial)
    assignment1 = _assignment_map(run1)

    run2 = process(
        2,
        "SYSTEM_OVERRIDE_START_JOB_AND_ADD_URGENT",
        [
            _event(
                base,
                "1479980576",
                "Đã phân công",
                "TIN0401.BOTX",
            ),
            _event(
                base,
                "1479995696",
                "Đang xử lý",
                assignment1["1479995696"],
            ),
            _event(
                base,
                "1480085036",
                "Đã phân công",
                "TIN0401.BOTX",
            ),
        ],
    )

    out_of_order_candidates = [
        (route.technician_id, stop.sequence, stop.job_id)
        for route in run2.result.optimization.routes
        for stop in route.stops
        if stop.sequence > 1
        and stop.job_id not in {"1480040046", "1480085036"}
    ]
    if not out_of_order_candidates:
        raise AssertionError("Không tìm được route stop ngoài thứ tự đầu")
    out_of_order = max(out_of_order_candidates, key=lambda item: item[1])
    run3 = process(
        3,
        "COMPLETE_ROUTED_STOP_OUT_OF_ORDER",
        [
            _event(
                base,
                out_of_order[2],
                "Đã xử lý",
                out_of_order[0],
            )
        ],
    )

    run4 = process(
        4,
        "COMPLETE_SELECTED_SYSTEM_JOB_AND_ADD_URGENT",
        [
            _event(
                base,
                "1480040046",
                "Đã xử lý",
                "TIN0401.BOTX",
            ),
            _event(base, "1480001466", "Chưa phân công"),
        ],
    )
    assignment4 = _assignment_map(run4)

    run5 = process(
        5,
        "COMPLETE_URGENT_LATE_START_AND_PAUSE_OTHER_JOBS",
        [
            _event(
                base,
                "1480085036",
                "Đã xử lý",
                "TIN0401.BOTX",
            ),
            _event(
                base,
                "1480001466",
                "Đang xử lý",
                assignment4["1480001466"],
            ),
            _event(
                base,
                "1480307086",
                "Tạm dừng chờ xử lý",
                assignment4["1480307086"],
            ),
            _event(base, "1480606556", "Chưa phân công"),
        ],
    )

    assert run2 is runs[1] and run3 is runs[2] and run5 is runs[4]
    assert any(
        conflict.code == "SYSTEM_ASSIGNMENT_OVERRIDE"
        and conflict.checklist_id == "1479980576"
        for conflict in run2.result.conflicts
    )
    assert out_of_order[1] > 1
    assert any(
        item.checklist_id == out_of_order[2]
        for item in run3.result.completed_jobs
    )
    assert any(
        item.checklist_id == "1480040046"
        for item in run4.result.completed_jobs
    )
    assert any(
        item.checklist_id == "1480085036"
        for item in run5.result.completed_jobs
    )

    audit_rows = []
    summary_rows = []
    for run, external_seconds in zip(runs, external_durations):
        summary = json.loads(
            (run.output_dir / "summary.json").read_text(encoding="utf-8")
        )
        actual = summary["evaluation_metrics"]
        independent = _independent_metrics(run, roster)
        for metric, expected in independent.items():
            observed = actual.get(metric)
            audit_rows.append(
                {
                    "SNAPSHOT_ID": run.result.snapshot.snapshot_id,
                    "METRIC": metric,
                    "SUMMARY_VALUE": observed,
                    "INDEPENDENT_VALUE": expected,
                    "MATCH": _metric_equal(observed, expected),
                }
            )
        summary_rows.append(
            {
                "SNAPSHOT_ID": run.result.snapshot.snapshot_id,
                "ACTIVE_JOBS": summary["active_jobs"],
                "ASSIGNMENTS": summary["assignments"],
                "UNASSIGNED": summary["unassigned"],
                **actual,
                "EXTERNAL_PIPELINE_SECONDS": external_seconds,
            }
        )

    audit = pd.DataFrame(audit_rows)
    scenario_summary = pd.DataFrame(summary_rows)
    audit.to_csv(
        session / "metrics_audit.csv", index=False, encoding="utf-8-sig"
    )
    scenario_summary.to_csv(
        session / "scenario_summary.csv",
        index=False,
        encoding="utf-8-sig",
    )
    pd.DataFrame(event_manifest).to_csv(
        session / "events_manifest.csv",
        index=False,
        encoding="utf-8-sig",
    )
    if not audit["MATCH"].all():
        mismatches = audit[~audit["MATCH"]]
        raise AssertionError(
            "Metric mismatch:\n" + mismatches.to_string(index=False)
        )
    assert scenario_summary[
        "planned_sla_on_time_rate_percent"
    ].tolist() == [100.0, 71.43, 66.67, 50.0, 50.0]
    actual_sla = scenario_summary[
        "completed_sla_on_time_rate_percent"
    ].tolist()
    assert pd.isna(actual_sla[0]) and pd.isna(actual_sla[1])
    assert actual_sla[2:] == [100.0, 100.0, 0.0]
    assert scenario_summary["same_area_revisit_count"].tolist() == [
        0,
        1,
        1,
        0,
        0,
    ]
    assert scenario_summary["completed_jobs_in_shift"].tolist() == [
        0,
        0,
        1,
        1,
        1,
    ]
    assert (
        scenario_summary["total_wait_between_jobs_minutes"] == 0.0
    ).all()

    report = {
        "status": "PASS",
        "provenance": provenance,
        "real_checklist_ids": list(REAL_CHECKLIST_IDS),
        "simulated_fields": [
            "CHECKLIST_STATUS",
            "CREATE_DATE/deadline",
            "event time/order",
        ],
        "out_of_order_completion": {
            "checklist_id": out_of_order[2],
            "previous_route_sequence": out_of_order[1],
            "technician_id": out_of_order[0],
        },
        "checks": {
            "system_override_conflict_logged": True,
            "out_of_order_completion_applied": True,
            "selected_system_job_completed": True,
            "late_urgent_completion_applied": True,
            "all_metrics_match_independent_recalculation": True,
            "all_ai_planning_runs_under_5_seconds": bool(
                scenario_summary["ai_planning_target_met"].all()
            ),
        },
        "files": {
            "source_selection": str(
                (input_dir / "source_selection.csv").resolve()
            ),
            "events_manifest": str(
                (session / "events_manifest.csv").resolve()
            ),
            "scenario_summary": str(
                (session / "scenario_summary.csv").resolve()
            ),
            "metrics_audit": str(
                (session / "metrics_audit.csv").resolve()
            ),
        },
        "session_dir": str(session.resolve()),
    }
    (session / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(scenario_summary.to_string(index=False))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("Real checklist five-snapshot metric audit: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
