"""Build a transparent Job–KTV compatibility graph."""

from __future__ import annotations

from dataclasses import dataclass

from .clustering import job_cluster_key
from .models import (
    CandidateEdge,
    CompatibilityMode,
    OptimizationJob,
    TechnicianShift,
)


@dataclass(frozen=True, slots=True)
class CompatibilityGraphBuilder:
    mode: CompatibilityMode  # task_location, task hoặc location.
    max_distance_km: float = 30.0  # Bán kính candidate V0.

    def _task_ok(
        self, job: OptimizationJob, technician: TechnicianShift
    ) -> bool:
        supported = technician.supported_task_types
        return not supported or job.task_type.upper() in supported

    def build(
        self,
        jobs: list[OptimizationJob],
        technicians: list[TechnicianShift],
    ) -> list[CandidateEdge]:
        edges: list[CandidateEdge] = []
        for job in jobs:
            cluster_key = job_cluster_key(job, self.mode)
            for technician in technicians:
                branch_ok = job.branch_name == technician.branch_name
                task_ok = self._task_ok(job, technician)
                distance = (
                    technician.current_location.distance_km_to(job.location)
                    if technician.current_location and job.location
                    else None
                )
                location_ok = (
                    distance is not None and distance <= self.max_distance_km
                )

                if self.mode is CompatibilityMode.TASK_LOCATION:
                    compatible = branch_ok and task_ok and location_ok
                elif self.mode is CompatibilityMode.TASK:
                    compatible = branch_ok and task_ok
                else:
                    compatible = branch_ok and location_ok

                reject_reason = None
                if not branch_ok:
                    reject_reason = "BRANCH_MISMATCH"
                elif (
                    self.mode
                    in (CompatibilityMode.TASK, CompatibilityMode.TASK_LOCATION)
                    and not task_ok
                ):
                    reject_reason = "TASK_NOT_SUPPORTED"
                elif (
                    self.mode
                    in (
                        CompatibilityMode.LOCATION,
                        CompatibilityMode.TASK_LOCATION,
                    )
                    and not location_ok
                ):
                    reject_reason = (
                        "MISSING_LOCATION"
                        if distance is None
                        else "OUTSIDE_DISTANCE_RADIUS"
                    )

                edges.append(
                    CandidateEdge(
                        job_id=job.checklist_id,
                        technician_id=technician.technician_id,
                        cluster_key=cluster_key,
                        branch_ok=branch_ok,
                        task_ok=task_ok,
                        location_ok=location_ok,
                        compatible=compatible,
                        distance_km=distance,
                        reject_reason=reject_reason,
                    )
                )
        return edges

