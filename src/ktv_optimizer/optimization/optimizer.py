"""Orchestrate the two requested V0 flows: assign+route or route-only."""

from __future__ import annotations

from datetime import datetime

from .assignment import GreedyAssignmentSolver
from .clustering import job_cluster_key
from .compatibility import CompatibilityGraphBuilder
from .models import (
    AssignmentDecision,
    CompatibilityMode,
    OptimizationJob,
    OptimizationResult,
    TechnicianShift,
    UnassignedDecision,
)
from .routing import NearestNeighbourRouter
from .scoring import AssignmentScorer, AssignmentWeights


class SimpleKtvOptimizer:
    """Replaceable V0: graph → weighted greedy assignment → routing."""

    def __init__(
        self,
        *,
        mode: CompatibilityMode = CompatibilityMode.TASK_LOCATION,
        max_distance_km: float = 30.0,
        average_speed_kmh: float = 30.0,
        weights: AssignmentWeights | None = None,
    ) -> None:
        self.mode = CompatibilityMode(mode)
        self.graph_builder = CompatibilityGraphBuilder(
            mode=self.mode,
            max_distance_km=max_distance_km,
        )
        self.assignment_solver = GreedyAssignmentSolver(
            AssignmentScorer(weights)
        )
        self.router = NearestNeighbourRouter(average_speed_kmh)

    def optimize(
        self,
        jobs: list[OptimizationJob],
        technicians: list[TechnicianShift],
        *,
        planning_time: datetime | None = None,
        incumbent_assignments: dict[str, str] | None = None,
        assignable_technician_ids: set[str] | None = None,
        locked_assignments: dict[str, str] | None = None,
        max_new_jobs_per_technician: int | None = None,
        technician_available_at: dict[str, datetime] | None = None,
    ) -> OptimizationResult:
        planning_time = planning_time or datetime.now()
        technician_by_id = {
            technician.technician_id: technician
            for technician in technicians
        }
        if len(technician_by_id) != len(technicians):
            raise ValueError("technician_id must be unique in a shift roster")

        assignable_all = [
            job for job in jobs if job.status == "Chưa phân công"
        ]
        fixed = [job for job in jobs if job.status == "Đã phân công"]

        locked_decisions: list[AssignmentDecision] = []
        assignable: list[OptimizationJob] = []
        for job in assignable_all:
            technician_id = (locked_assignments or {}).get(job.checklist_id)
            technician = technician_by_id.get(technician_id or "")
            if technician_id is None or technician is None:
                assignable.append(job)
                continue
            cluster_key = job_cluster_key(job, self.mode)
            distance = (
                technician.current_location.distance_km_to(job.location)
                if technician.current_location and job.location
                else None
            )
            locked_decisions.append(
                AssignmentDecision(
                    job_id=job.checklist_id,
                    technician_id=technician_id,
                    source="OPTIMIZER_V0",
                    cluster_key=cluster_key,
                    distance_cost=0.0,
                    load_cost=0.0,
                    cluster_cost=0.0,
                    stability_cost=(
                        -self.assignment_solver.scorer.weights.incumbent_bonus
                    ),
                    assignment_cost=(
                        -self.assignment_solver.scorer.weights.incumbent_bonus
                    ),
                    distance_km=distance,
                )
            )

        fixed_decisions: list[AssignmentDecision] = []
        unassigned: list[UnassignedDecision] = []
        initial_workloads = {
            technician.technician_id: technician.existing_workload_minutes
            for technician in technicians
        }
        initial_clusters = {
            technician.technician_id: set()
            for technician in technicians
        }

        # Active work not handled by the assignment solver still consumes
        # capacity. It remains outside the route because the KTV is already at
        # that job (BUSY) or the job is explicitly paused.
        active_commitment_statuses = {
            "Đang xử lý",
            "Đã xử lý và đang theo dõi",
            "Tạm dừng chờ xử lý",
        }
        for job in jobs:
            technician_id = job.assigned_technician
            if (
                job.status not in active_commitment_statuses
                or not technician_id
                or technician_id not in initial_workloads
            ):
                continue
            initial_workloads[technician_id] += job.service_minutes
            initial_clusters[technician_id].add(
                job_cluster_key(job, self.mode)
            )

        # Previous assignments supply only soft stability/cluster context.
        # Workload is not seeded because active jobs are optimized again.
        job_by_id = {job.checklist_id: job for job in assignable}
        for job_id, technician_id in (incumbent_assignments or {}).items():
            job = job_by_id.get(job_id)
            if job is not None and technician_id in initial_clusters:
                initial_clusters[technician_id].add(
                    job_cluster_key(job, self.mode)
                )

        for decision in locked_decisions:
            job = next(
                item
                for item in assignable_all
                if item.checklist_id == decision.job_id
            )
            initial_workloads[decision.technician_id] += job.service_minutes
            initial_clusters[decision.technician_id].add(decision.cluster_key)

        for job in fixed:
            technician_id = job.assigned_technician
            if not technician_id:
                unassigned.append(
                    UnassignedDecision(
                        job_id=job.checklist_id,
                        reason="FIXED_JOB_MISSING_EMP_ACCOUNT",
                    )
                )
                continue
            technician = technician_by_id.get(technician_id)
            if technician is None:
                unassigned.append(
                    UnassignedDecision(
                        job_id=job.checklist_id,
                        reason="FIXED_TECHNICIAN_NOT_IN_SHIFT_ROSTER",
                    )
                )
                continue
            cluster_key = job_cluster_key(job, self.mode)
            distance = (
                technician.current_location.distance_km_to(job.location)
                if technician.current_location and job.location
                else None
            )
            fixed_decisions.append(
                AssignmentDecision(
                    job_id=job.checklist_id,
                    technician_id=technician_id,
                    source="SYSTEM_FIXED",
                    cluster_key=cluster_key,
                    distance_cost=0.0,
                    load_cost=0.0,
                    cluster_cost=0.0,
                    stability_cost=0.0,
                    assignment_cost=0.0,
                    distance_km=distance,
                )
            )
            initial_workloads[technician_id] += job.service_minutes
            initial_clusters[technician_id].add(cluster_key)

        assignment_technicians = (
            [
                technician
                for technician in technicians
                if technician.technician_id in assignable_technician_ids
            ]
            if assignable_technician_ids is not None
            else technicians
        )
        candidate_edges = self.graph_builder.build(
            assignable, assignment_technicians
        )
        optimized, assignment_failures = self.assignment_solver.solve(
            assignable,
            assignment_technicians,
            candidate_edges,
            initial_workloads=initial_workloads,
            initial_clusters=initial_clusters,
            incumbent_assignments=incumbent_assignments,
            max_new_jobs_per_technician=max_new_jobs_per_technician,
        )
        decisions = [*fixed_decisions, *locked_decisions, *optimized]
        routes = self.router.build_routes(
            jobs,
            technicians,
            decisions,
            planning_time=planning_time,
            available_at_by_technician=technician_available_at,
        )
        return OptimizationResult(
            mode=self.mode,
            candidate_edges=tuple(candidate_edges),
            assignments=tuple(decisions),
            unassigned=tuple([*unassigned, *assignment_failures]),
            routes=tuple(routes),
        )
