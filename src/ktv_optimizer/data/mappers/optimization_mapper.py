"""Map maintenance and future shift-roster frames to optimization inputs."""

from __future__ import annotations

import re
from datetime import timedelta

import pandas as pd

from ktv_optimizer.data.mappers.qos_mapper import normalize_maintenance_frame
from ktv_optimizer.data.sources.administrative_boundaries import (
    WardBoundaryIndex,
)
from ktv_optimizer.domain import (
    ACTIVE_CHECKLIST_STATUSES,
    CHECKLIST_EVENT_STATUSES,
    GeoPoint,
)
from ktv_optimizer.optimization.models import OptimizationJob, TechnicianShift
from ktv_optimizer.optimization.task_policy import get_task_policy
from ktv_optimizer.state.snapshot import (
    TechnicianStatusUpdate,
    TechnicianWorkStatus,
)


OPTIMIZATION_STATUSES = {"Chưa phân công", "Đã phân công"}
# Backward-compatible alias for callers that only need active state.
STATE_TRACKING_STATUSES = ACTIVE_CHECKLIST_STATUSES
EVENT_TRACKING_STATUSES = CHECKLIST_EVENT_STATUSES


def maintenance_to_optimization_jobs(
    frame: pd.DataFrame,
    boundary_index: WardBoundaryIndex,
    *,
    deduplicate: bool = True,
    statuses: set[str] | frozenset[str] | None = None,
) -> list[OptimizationJob]:
    """Build geocoded checklist events; V0 defaults to two active statuses."""

    selected = frame[
        frame["CHECKLIST_STATUS"].isin(statuses or OPTIMIZATION_STATUSES)
    ]
    if deduplicate:
        selected = selected.drop_duplicates("CHECKLIST_ID", keep="first")
    selected = normalize_maintenance_frame(selected)

    jobs: list[OptimizationJob] = []
    for _, row in selected.iterrows():
        raw_task_type = row.get("CASE_TYPE")
        task_type = (
            "UNKNOWN"
            if pd.isna(raw_task_type)
            else str(raw_task_type).strip().upper()
        )
        policy = get_task_policy(task_type)
        address = None if pd.isna(row.get("OBJ_LOCATION")) else str(
            row["OBJ_LOCATION"]
        )
        location_match = boundary_index.match(address)
        created_at = (
            None if pd.isna(row.get("CREATE_DATE")) else row["CREATE_DATE"]
        )
        finished_at = (
            None if pd.isna(row.get("FINISH_DATE")) else row["FINISH_DATE"]
        )
        due_at = (
            created_at + timedelta(minutes=policy.sla_minutes)
            if created_at is not None and policy.sla_minutes is not None
            else None
        )
        assigned = (
            None
            if pd.isna(row.get("EMP_ACCOUNT"))
            else str(row["EMP_ACCOUNT"]).strip()
        )
        on_time_flag = (
            None
            if pd.isna(row.get("FLAG_ON_TIME"))
            else str(row["FLAG_ON_TIME"]).strip().upper()
        )
        jobs.append(
            OptimizationJob(
                checklist_id=str(row["CHECKLIST_ID"]),
                status=str(row["CHECKLIST_STATUS"]),
                branch_name=str(row["BRANCH_NAME"]),
                task_type=task_type,
                address=address,
                location=location_match.point if location_match else None,
                ward_code=location_match.ward_code if location_match else None,
                ward_name=location_match.ward_name if location_match else None,
                province_name=(
                    location_match.province_name if location_match else None
                ),
                location_confidence=(
                    location_match.confidence if location_match else None
                ),
                location_method=(
                    location_match.method if location_match else None
                ),
                assigned_technician=assigned,
                priority=policy.priority,
                service_minutes=policy.service_minutes,
                created_at=created_at,
                due_at=due_at,
                finished_at=finished_at,
                on_time_flag=on_time_flag,
            )
        )
    return jobs


def _parse_supported_tasks(value: object) -> frozenset[str]:
    if pd.isna(value):
        return frozenset()
    return frozenset(
        item.strip().upper()
        for item in re.split(r"[|,;]", str(value))
        if item.strip()
    )


def shift_roster_to_technicians(frame: pd.DataFrame) -> list[TechnicianShift]:
    """Map the future daily-roster data contract to TechnicianShift.

    Required columns: ``EMP_ACCOUNT`` and ``BRANCH_NAME``.
    Optional columns: ``LATITUDE``, ``LONGITUDE``, ``SHIFT_START``,
    ``SHIFT_END``, ``SUPPORTED_CASE_TYPES``, ``EXISTING_WORKLOAD_MINUTES``.
    """

    required = {"EMP_ACCOUNT", "BRANCH_NAME"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Shift roster thiếu cột: {', '.join(missing)}")

    roster = frame.copy()
    for column in ("SHIFT_START", "SHIFT_END"):
        if column in roster:
            roster[column] = pd.to_datetime(roster[column], errors="coerce")

    technicians: list[TechnicianShift] = []
    for _, row in roster.iterrows():
        latitude = pd.to_numeric(row.get("LATITUDE"), errors="coerce")
        longitude = pd.to_numeric(row.get("LONGITUDE"), errors="coerce")
        location = (
            GeoPoint(float(latitude), float(longitude))
            if pd.notna(latitude) and pd.notna(longitude)
            else None
        )
        workload = pd.to_numeric(
            row.get("EXISTING_WORKLOAD_MINUTES", 0), errors="coerce"
        )
        technicians.append(
            TechnicianShift(
                technician_id=str(row["EMP_ACCOUNT"]).strip(),
                branch_name=str(row["BRANCH_NAME"]).strip(),
                current_location=location,
                shift_start=(
                    None
                    if pd.isna(row.get("SHIFT_START"))
                    else row.get("SHIFT_START")
                ),
                shift_end=(
                    None
                    if pd.isna(row.get("SHIFT_END"))
                    else row.get("SHIFT_END")
                ),
                supported_task_types=_parse_supported_tasks(
                    row.get("SUPPORTED_CASE_TYPES")
                ),
                existing_workload_minutes=(
                    0.0 if pd.isna(workload) else float(workload)
                ),
            )
        )
    return technicians


def shift_roster_to_status_updates(
    frame: pd.DataFrame,
) -> list[TechnicianStatusUpdate]:
    """Read optional runtime columns carried beside the daily roster."""

    if "EMP_ACCOUNT" not in frame:
        raise ValueError("Shift roster thiếu cột: EMP_ACCOUNT")
    statuses = {status.value for status in TechnicianWorkStatus}
    updates: list[TechnicianStatusUpdate] = []
    for _, row in frame.iterrows():
        raw_status = row.get("WORK_STATUS")
        work_status = None
        if pd.notna(raw_status) and str(raw_status).strip():
            normalized = str(raw_status).strip().upper()
            if normalized not in statuses:
                raise ValueError(
                    f"WORK_STATUS không hợp lệ: {raw_status}; "
                    f"chọn một trong {sorted(statuses)}"
                )
            work_status = TechnicianWorkStatus(normalized)
        raw_job = row.get("CURRENT_JOB_ID")
        current_job_id = (
            str(raw_job).strip()
            if pd.notna(raw_job) and str(raw_job).strip()
            else None
        )
        available_at = pd.to_datetime(
            row.get("AVAILABLE_AT"), errors="coerce"
        )
        updates.append(
            TechnicianStatusUpdate(
                technician_id=str(row["EMP_ACCOUNT"]).strip(),
                work_status=work_status,
                current_job_id=current_job_id,
                available_at=(
                    None if pd.isna(available_at) else available_at
                ),
            )
        )
    return updates
