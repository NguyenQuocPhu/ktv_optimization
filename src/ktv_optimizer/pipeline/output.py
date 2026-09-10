"""Stable CSV/JSON output contract for a processed snapshot."""

from __future__ import annotations

import json
from dataclasses import asdict, fields
from pathlib import Path

import pandas as pd

from ktv_optimizer.optimization import AssignmentDecision, UnassignedDecision
from ktv_optimizer.state.snapshot import (
    SnapshotChange,
    SnapshotConflict,
    SnapshotRunResult,
    TechnicianJobQueueItem,
    TechnicianStateRecord,
)


JOB_OUTPUT_COLUMNS = [
    "CHECKLIST_ID",
    "CHECKLIST_STATUS",
    "BRANCH_NAME",
    "CASE_TYPE",
    "OBJ_LOCATION",
    "LATITUDE",
    "LONGITUDE",
    "EMP_ACCOUNT",
]
ROUTE_OUTPUT_COLUMNS = [
    "TECHNICIAN_ID",
    "SEQUENCE",
    "CHECKLIST_ID",
    "LATITUDE",
    "LONGITUDE",
    "LEG_DISTANCE_KM",
    "ESTIMATED_ARRIVAL",
    "ESTIMATED_FINISH",
    "ROUTE_TOTAL_DISTANCE_KM",
    "ROUTE_TOTAL_TRAVEL_MINUTES",
    "ROUTE_TOTAL_SERVICE_MINUTES",
]
COMPLETED_JOB_OUTPUT_COLUMNS = [
    "CHECKLIST_ID",
    "COMPLETION_STATUS",
    "COMPLETION_EVENT_TIME",
    "BRANCH_NAME",
    "CASE_TYPE",
    "OBJ_LOCATION",
    "LATITUDE",
    "LONGITUDE",
    "EMP_ACCOUNT",
    "CREATED_AT",
    "DUE_AT",
    "FINISH_DATE",
    "FLAG_ON_TIME",
    "WARD_CODE",
    "WARD_NAME",
    "PROVINCE_NAME",
]


def _write_dataclasses(path: Path, values, data_class) -> None:
    columns = [field.name for field in fields(data_class)]
    pd.DataFrame([asdict(value) for value in values], columns=columns).to_csv(
        path, index=False, encoding="utf-8-sig"
    )


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator * 100, 2)


def _evaluation_metrics(run: SnapshotRunResult) -> dict:
    """Return basic plan/outcome metrics available from the current contract."""

    job_by_id = {job.checklist_id: job for job in run.snapshot.jobs}
    cluster_by_job = {
        item.job_id: item.cluster_key
        for item in run.optimization.assignments
    }
    # Every active job with a due time belongs to the denominator. A job with
    # no route cannot be claimed as on time by the current plan.
    planned_sla_jobs = sum(
        job.due_at is not None for job in run.snapshot.jobs
    )
    planned_on_time_jobs = 0
    unique_clusters: set[str] = set()
    technician_cluster_visits = 0
    same_area_revisits = 0
    total_wait_minutes = 0.0
    wait_transition_count = 0

    for route in run.optimization.routes:
        cluster_sequence = [
            cluster_by_job[stop.job_id]
            for stop in route.stops
            if stop.job_id in cluster_by_job
        ]
        unique_clusters.update(cluster_sequence)
        technician_cluster_visits += len(set(cluster_sequence))
        compressed_clusters: list[str] = []
        for cluster in cluster_sequence:
            if not compressed_clusters or compressed_clusters[-1] != cluster:
                compressed_clusters.append(cluster)
        seen_clusters: set[str] = set()
        for cluster in compressed_clusters:
            if cluster in seen_clusters:
                same_area_revisits += 1
            seen_clusters.add(cluster)

        travel_minutes_per_km = (
            route.total_travel_minutes / route.total_distance_km
            if route.total_distance_km > 0
            else 0.0
        )
        for index, stop in enumerate(route.stops):
            job = job_by_id[stop.job_id]
            if job.due_at is not None and stop.estimated_finish is not None:
                planned_on_time_jobs += stop.estimated_finish <= job.due_at
            if index == 0:
                continue
            previous = route.stops[index - 1]
            if (
                previous.estimated_finish is None
                or stop.estimated_arrival is None
            ):
                continue
            gap_minutes = (
                stop.estimated_arrival - previous.estimated_finish
            ).total_seconds() / 60
            expected_travel = (
                (stop.leg_distance_km or 0.0) * travel_minutes_per_km
            )
            total_wait_minutes += max(0.0, gap_minutes - expected_travel)
            wait_transition_count += 1

    completed_sla_jobs = 0
    completed_on_time_jobs = 0
    for job in run.completed_jobs:
        flag = (job.on_time_flag or "").strip().upper()
        if flag in {"YES", "NO"}:
            completed_sla_jobs += 1
            completed_on_time_jobs += flag == "YES"
        elif job.due_at is not None:
            completed_sla_jobs += 1
            completion_time = job.finished_at or run.snapshot.captured_at
            completed_on_time_jobs += completion_time <= job.due_at
    technician_by_id = {
        item.technician_id: item for item in run.snapshot.technicians
    }
    completed_in_shift = 0
    completion_shift_attribution_missing = 0
    for job in run.completed_jobs:
        technician = technician_by_id.get(job.assigned_technician or "")
        if (
            technician is None
            or technician.shift_start is None
            or technician.shift_end is None
        ):
            completion_shift_attribution_missing += 1
            continue
        completed_in_shift += (
            technician.shift_start
            <= run.snapshot.captured_at
            <= technician.shift_end
        )

    planning_seconds = run.planning_duration_seconds
    return {
        "planned_sla_on_time_rate_percent": _rate(
            planned_on_time_jobs, planned_sla_jobs
        ),
        "planned_sla_on_time_jobs": planned_on_time_jobs,
        "planned_sla_evaluable_jobs": planned_sla_jobs,
        "completed_sla_on_time_rate_percent": _rate(
            completed_on_time_jobs, completed_sla_jobs
        ),
        "completed_sla_on_time_jobs": completed_on_time_jobs,
        "completed_sla_evaluable_jobs": completed_sla_jobs,
        "total_distance_km": round(
            sum(
                route.total_distance_km
                for route in run.optimization.routes
            ),
            3,
        ),
        "total_travel_minutes": round(
            sum(
                route.total_travel_minutes
                for route in run.optimization.routes
            ),
            2,
        ),
        "unique_clusters": len(unique_clusters),
        "technician_cluster_visits": technician_cluster_visits,
        "same_area_revisit_count": same_area_revisits,
        "completed_jobs_in_shift": completed_in_shift,
        "completion_shift_attribution_missing": (
            completion_shift_attribution_missing
        ),
        "total_wait_between_jobs_minutes": round(total_wait_minutes, 2),
        "average_wait_between_jobs_minutes": (
            round(total_wait_minutes / wait_transition_count, 2)
            if wait_transition_count
            else 0.0
        ),
        "ai_planning_seconds": planning_seconds,
        "ai_planning_target_seconds": 5.0,
        "ai_planning_target_met": (
            planning_seconds <= 5.0
            if planning_seconds is not None
            else None
        ),
    }


def snapshot_summary(run: SnapshotRunResult) -> dict:
    change_counts = {
        change_type: sum(
            item.change_type == change_type for item in run.changes
        )
        for change_type in ("ADDED", "UPDATED", "COMPLETED")
    }
    return {
        "snapshot_id": run.snapshot.snapshot_id,
        "snapshot_time": run.snapshot.captured_at.isoformat(),
        "active_jobs": len(run.snapshot.jobs),
        "incoming_job_events": len(run.incoming_jobs),
        "added_jobs": change_counts["ADDED"],
        "updated_jobs": change_counts["UPDATED"],
        "completed_jobs": change_counts["COMPLETED"],
        "technicians": len(run.snapshot.technicians),
        "changes": len(run.changes),
        "conflicts": len(run.conflicts),
        "candidate_edges": len(run.optimization.candidate_edges),
        "assignments": len(run.optimization.assignments),
        "unassigned": len(run.optimization.unassigned),
        "routes": len(run.optimization.routes),
        "job_queue_items": len(run.job_queue),
        "queued_jobs": sum(
            item.queue_status != "IN_PROGRESS" for item in run.job_queue
        ),
        "technician_statuses": {
            status: sum(
                record.work_status.value == status
                for record in run.technician_checkpoint.records
            )
            for status in (
                "IDLE",
                "RESERVED",
                "BUSY",
                "UNAVAILABLE",
                "OFF_SHIFT",
            )
        },
        "evaluation_metrics": _evaluation_metrics(run),
    }


def write_snapshot_output(output_dir: Path, run: SnapshotRunResult) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    jobs = [
        {
            "CHECKLIST_ID": job.checklist_id,
            "CHECKLIST_STATUS": job.status,
            "BRANCH_NAME": job.branch_name,
            "CASE_TYPE": job.task_type,
            "OBJ_LOCATION": job.address,
            "LATITUDE": job.location.latitude if job.location else None,
            "LONGITUDE": job.location.longitude if job.location else None,
            "EMP_ACCOUNT": job.assigned_technician,
        }
        for job in run.snapshot.jobs
    ]
    pd.DataFrame(jobs, columns=JOB_OUTPUT_COLUMNS).to_csv(
        output_dir / "jobs.csv", index=False, encoding="utf-8-sig"
    )
    completed_jobs = [
        {
            "CHECKLIST_ID": job.checklist_id,
            "COMPLETION_STATUS": job.status,
            "COMPLETION_EVENT_TIME": run.snapshot.captured_at,
            "BRANCH_NAME": job.branch_name,
            "CASE_TYPE": job.task_type,
            "OBJ_LOCATION": job.address,
            "LATITUDE": job.location.latitude if job.location else None,
            "LONGITUDE": job.location.longitude if job.location else None,
            "EMP_ACCOUNT": job.assigned_technician,
            "CREATED_AT": job.created_at,
            "DUE_AT": job.due_at,
            "FINISH_DATE": job.finished_at,
            "FLAG_ON_TIME": job.on_time_flag,
            "WARD_CODE": job.ward_code,
            "WARD_NAME": job.ward_name,
            "PROVINCE_NAME": job.province_name,
        }
        for job in run.completed_jobs
    ]
    pd.DataFrame(
        completed_jobs, columns=COMPLETED_JOB_OUTPUT_COLUMNS
    ).to_csv(
        output_dir / "completed_jobs.csv",
        index=False,
        encoding="utf-8-sig",
    )
    _write_dataclasses(output_dir / "changes.csv", run.changes, SnapshotChange)
    _write_dataclasses(
        output_dir / "conflicts.csv", run.conflicts, SnapshotConflict
    )
    _write_dataclasses(
        output_dir / "assignments.csv",
        run.optimization.assignments,
        AssignmentDecision,
    )
    _write_dataclasses(
        output_dir / "unassigned.csv",
        run.optimization.unassigned,
        UnassignedDecision,
    )
    _write_dataclasses(
        output_dir / "technicians.csv",
        run.technician_checkpoint.records,
        TechnicianStateRecord,
    )
    _write_dataclasses(
        output_dir / "technician_job_queue.csv",
        run.job_queue,
        TechnicianJobQueueItem,
    )
    routes = [
        {
            "TECHNICIAN_ID": route.technician_id,
            "SEQUENCE": stop.sequence,
            "CHECKLIST_ID": stop.job_id,
            "LATITUDE": stop.location.latitude if stop.location else None,
            "LONGITUDE": stop.location.longitude if stop.location else None,
            "LEG_DISTANCE_KM": stop.leg_distance_km,
            "ESTIMATED_ARRIVAL": stop.estimated_arrival,
            "ESTIMATED_FINISH": stop.estimated_finish,
            "ROUTE_TOTAL_DISTANCE_KM": route.total_distance_km,
            "ROUTE_TOTAL_TRAVEL_MINUTES": route.total_travel_minutes,
            "ROUTE_TOTAL_SERVICE_MINUTES": route.total_service_minutes,
        }
        for route in run.optimization.routes
        for stop in route.stops
    ]
    pd.DataFrame(routes, columns=ROUTE_OUTPUT_COLUMNS).to_csv(
        output_dir / "routes.csv", index=False, encoding="utf-8-sig"
    )
    summary = snapshot_summary(run)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary
