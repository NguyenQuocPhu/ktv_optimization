"""Deterministic cluster keys for the three requested V0 options."""

from __future__ import annotations

from collections import defaultdict

from .models import CompatibilityMode, OptimizationJob


def location_key(job: OptimizationJob) -> str:
    if job.ward_code:
        return f"WARD:{job.ward_code}"
    if job.location:
        return (
            f"GRID:{round(job.location.latitude, 2)}:"
            f"{round(job.location.longitude, 2)}"
        )
    return "LOCATION:UNKNOWN"


def job_cluster_key(
    job: OptimizationJob, mode: CompatibilityMode
) -> str:
    task = f"TASK:{job.task_type.strip().upper()}"
    location = location_key(job)
    if mode is CompatibilityMode.TASK_LOCATION:
        return f"{task}|{location}"
    if mode is CompatibilityMode.TASK:
        return task
    return location


def cluster_jobs(
    jobs: list[OptimizationJob], mode: CompatibilityMode
) -> dict[str, list[OptimizationJob]]:
    clusters: dict[str, list[OptimizationJob]] = defaultdict(list)
    for job in jobs:
        clusters[job_cluster_key(job, mode)].append(job)
    return dict(clusters)

