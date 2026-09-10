"""Persist the latest active optimizer state in one small CSV file."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from ktv_optimizer.domain import GeoPoint
from ktv_optimizer.optimization.models import OptimizationJob

from .snapshot import (
    OptimizerCheckpoint,
    StateRecord,
    TechnicianCheckpoint,
    TechnicianStateRecord,
    TechnicianWorkStatus,
)


STATE_COLUMNS = (
    "SNAPSHOT_ID",
    "SNAPSHOT_TIME",
    "CHECKLIST_ID",
    "CHECKLIST_STATUS",
    "BRANCH_NAME",
    "CASE_TYPE",
    "OBJ_LOCATION",
    "LATITUDE",
    "LONGITUDE",
    "WARD_CODE",
    "WARD_NAME",
    "PROVINCE_NAME",
    "LOCATION_CONFIDENCE",
    "LOCATION_METHOD",
    "EMP_ACCOUNT",
    "PLANNED_EMP_ACCOUNT",
    "ASSIGNMENT_SOURCE",
    "ROUTE_ORDER",
    "PRIORITY",
    "SERVICE_MINUTES",
    "CREATED_AT",
    "DUE_AT",
    "FINISHED_AT",
    "FLAG_ON_TIME",
    "UPDATED_AT",
)

TECHNICIAN_STATE_COLUMNS = (
    "SNAPSHOT_ID",
    "SNAPSHOT_TIME",
    "EMP_ACCOUNT",
    "BRANCH_NAME",
    "WORK_STATUS",
    "CURRENT_JOB_ID",
    "AVAILABLE_AT",
    "PLANNED_JOB_COUNT",
    "QUEUED_JOB_COUNT",
    "LATITUDE",
    "LONGITUDE",
    "UPDATED_AT",
)


def _text(value: object | None) -> str:
    return "" if value is None else str(value)


def _datetime_text(value: datetime | None) -> str:
    return value.isoformat() if value is not None else ""


def _optional(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _optional_float(value: str | None) -> float | None:
    value = _optional(value)
    return float(value) if value is not None else None


def _optional_int(value: str | None) -> int | None:
    value = _optional(value)
    return int(value) if value is not None else None


def _optional_datetime(value: str | None) -> datetime | None:
    value = _optional(value)
    return datetime.fromisoformat(value) if value is not None else None


class CsvStateStore:
    """A replaceable checkpoint adapter; no database or service required."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> OptimizerCheckpoint:
        if not self.path.is_file() or self.path.stat().st_size == 0:
            return OptimizerCheckpoint()

        records: list[StateRecord] = []
        with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            # Outcome fields were added after the first checkpoint contract.
            # Active checkpoints written by older versions remain readable.
            optional_outcome_columns = {"FINISHED_AT", "FLAG_ON_TIME"}
            missing = sorted(
                (set(STATE_COLUMNS) - optional_outcome_columns)
                - set(reader.fieldnames or ())
            )
            if missing:
                raise ValueError(
                    "State CSV thiếu cột: " + ", ".join(missing)
                )
            for row in reader:
                latitude = _optional_float(row["LATITUDE"])
                longitude = _optional_float(row["LONGITUDE"])
                location = (
                    GeoPoint(latitude, longitude)
                    if latitude is not None and longitude is not None
                    else None
                )
                snapshot_time = _optional_datetime(row["SNAPSHOT_TIME"])
                updated_at = _optional_datetime(row["UPDATED_AT"])
                if snapshot_time is None or updated_at is None:
                    raise ValueError("State CSV có timestamp rỗng/không hợp lệ")
                job = OptimizationJob(
                    checklist_id=row["CHECKLIST_ID"],
                    status=row["CHECKLIST_STATUS"],
                    branch_name=row["BRANCH_NAME"],
                    task_type=row["CASE_TYPE"],
                    address=_optional(row["OBJ_LOCATION"]),
                    location=location,
                    ward_code=_optional(row["WARD_CODE"]),
                    ward_name=_optional(row["WARD_NAME"]),
                    province_name=_optional(row["PROVINCE_NAME"]),
                    location_confidence=_optional_float(
                        row["LOCATION_CONFIDENCE"]
                    ),
                    location_method=_optional(row["LOCATION_METHOD"]),
                    assigned_technician=_optional(row["EMP_ACCOUNT"]),
                    priority=int(row["PRIORITY"]),
                    service_minutes=float(row["SERVICE_MINUTES"]),
                    created_at=_optional_datetime(row["CREATED_AT"]),
                    due_at=_optional_datetime(row["DUE_AT"]),
                    finished_at=_optional_datetime(row.get("FINISHED_AT")),
                    on_time_flag=_optional(row.get("FLAG_ON_TIME")),
                )
                records.append(
                    StateRecord(
                        snapshot_id=row["SNAPSHOT_ID"],
                        snapshot_time=snapshot_time,
                        job=job,
                        planned_technician=_optional(
                            row["PLANNED_EMP_ACCOUNT"]
                        ),
                        assignment_source=_optional(row["ASSIGNMENT_SOURCE"]),
                        route_order=_optional_int(row["ROUTE_ORDER"]),
                        updated_at=updated_at,
                    )
                )
        return OptimizerCheckpoint(tuple(records))

    def save(self, checkpoint: OptimizerCheckpoint) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=STATE_COLUMNS)
            writer.writeheader()
            for record in sorted(
                checkpoint.records, key=lambda item: item.job.checklist_id
            ):
                job = record.job
                writer.writerow(
                    {
                        "SNAPSHOT_ID": record.snapshot_id,
                        "SNAPSHOT_TIME": _datetime_text(
                            record.snapshot_time
                        ),
                        "CHECKLIST_ID": job.checklist_id,
                        "CHECKLIST_STATUS": job.status,
                        "BRANCH_NAME": job.branch_name,
                        "CASE_TYPE": job.task_type,
                        "OBJ_LOCATION": _text(job.address),
                        "LATITUDE": _text(
                            job.location.latitude if job.location else None
                        ),
                        "LONGITUDE": _text(
                            job.location.longitude if job.location else None
                        ),
                        "WARD_CODE": _text(job.ward_code),
                        "WARD_NAME": _text(job.ward_name),
                        "PROVINCE_NAME": _text(job.province_name),
                        "LOCATION_CONFIDENCE": _text(
                            job.location_confidence
                        ),
                        "LOCATION_METHOD": _text(job.location_method),
                        "EMP_ACCOUNT": _text(job.assigned_technician),
                        "PLANNED_EMP_ACCOUNT": _text(
                            record.planned_technician
                        ),
                        "ASSIGNMENT_SOURCE": _text(
                            record.assignment_source
                        ),
                        "ROUTE_ORDER": _text(record.route_order),
                        "PRIORITY": job.priority,
                        "SERVICE_MINUTES": job.service_minutes,
                        "CREATED_AT": _datetime_text(job.created_at),
                        "DUE_AT": _datetime_text(job.due_at),
                        "FINISHED_AT": _datetime_text(job.finished_at),
                        "FLAG_ON_TIME": _text(job.on_time_flag),
                        "UPDATED_AT": _datetime_text(record.updated_at),
                    }
                )
        temporary.replace(self.path)

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()


class CsvTechnicianStateStore:
    """Persist one runtime availability row for every KTV in the roster."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> TechnicianCheckpoint:
        if not self.path.is_file() or self.path.stat().st_size == 0:
            return TechnicianCheckpoint()

        records: list[TechnicianStateRecord] = []
        with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            # QUEUED_JOB_COUNT was added after the first CSV checkpoint
            # contract. Older checkpoints remain readable and are migrated in
            # memory from PLANNED_JOB_COUNT + WORK_STATUS.
            required_columns = set(TECHNICIAN_STATE_COLUMNS) - {
                "QUEUED_JOB_COUNT"
            }
            missing = sorted(
                required_columns - set(reader.fieldnames or ())
            )
            if missing:
                raise ValueError(
                    "Technician state CSV thiếu cột: " + ", ".join(missing)
                )
            for row in reader:
                snapshot_time = _optional_datetime(row["SNAPSHOT_TIME"])
                updated_at = _optional_datetime(row["UPDATED_AT"])
                if snapshot_time is None or updated_at is None:
                    raise ValueError(
                        "Technician state CSV có timestamp không hợp lệ"
                    )
                work_status = TechnicianWorkStatus(row["WORK_STATUS"])
                current_job_id = _optional(row["CURRENT_JOB_ID"])
                planned_job_count = int(row["PLANNED_JOB_COUNT"])
                queued_job_count = _optional_int(
                    row.get("QUEUED_JOB_COUNT")
                )
                if queued_job_count is None:
                    queued_job_count = max(
                        0,
                        planned_job_count
                        - (
                            1
                            if work_status is TechnicianWorkStatus.BUSY
                            and current_job_id
                            else 0
                        ),
                    )
                records.append(
                    TechnicianStateRecord(
                        snapshot_id=row["SNAPSHOT_ID"],
                        snapshot_time=snapshot_time,
                        technician_id=row["EMP_ACCOUNT"],
                        branch_name=row["BRANCH_NAME"],
                        work_status=work_status,
                        current_job_id=current_job_id,
                        available_at=_optional_datetime(row["AVAILABLE_AT"]),
                        planned_job_count=planned_job_count,
                        latitude=_optional_float(row["LATITUDE"]),
                        longitude=_optional_float(row["LONGITUDE"]),
                        updated_at=updated_at,
                        queued_job_count=queued_job_count,
                    )
                )
        return TechnicianCheckpoint(tuple(records))

    def save(self, checkpoint: TechnicianCheckpoint) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=TECHNICIAN_STATE_COLUMNS
            )
            writer.writeheader()
            for record in sorted(
                checkpoint.records, key=lambda item: item.technician_id
            ):
                writer.writerow(
                    {
                        "SNAPSHOT_ID": record.snapshot_id,
                        "SNAPSHOT_TIME": _datetime_text(
                            record.snapshot_time
                        ),
                        "EMP_ACCOUNT": record.technician_id,
                        "BRANCH_NAME": record.branch_name,
                        "WORK_STATUS": record.work_status.value,
                        "CURRENT_JOB_ID": _text(record.current_job_id),
                        "AVAILABLE_AT": _datetime_text(record.available_at),
                        "PLANNED_JOB_COUNT": record.planned_job_count,
                        "QUEUED_JOB_COUNT": record.queued_job_count,
                        "LATITUDE": _text(record.latitude),
                        "LONGITUDE": _text(record.longitude),
                        "UPDATED_AT": _datetime_text(record.updated_at),
                    }
                )
        temporary.replace(self.path)

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()
