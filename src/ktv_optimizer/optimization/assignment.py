"""Greedy V0 assignment over a pre-built compatibility graph."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from .models import (
    AssignmentDecision,
    CandidateEdge,
    OptimizationJob,
    TechnicianShift,
    UnassignedDecision,
)
from .scoring import AssignmentScorer


def _capacity_minutes(technician: TechnicianShift) -> float | None:
    if technician.shift_start is None or technician.shift_end is None:
        return None
    capacity = (
        technician.shift_end - technician.shift_start
    ).total_seconds() / 60
    return capacity if capacity > 0 else None


class GreedyAssignmentSolver:
    """Fast baseline; replaceable later by GAP/CP-SAT."""

    def __init__(self, scorer: AssignmentScorer | None = None) -> None:
        self.scorer = scorer or AssignmentScorer()

    def solve(
        self,
        jobs: list[OptimizationJob],
        technicians: list[TechnicianShift],
        edges: list[CandidateEdge],
        *,
        initial_workloads: dict[str, float] | None = None,
        initial_clusters: dict[str, set[str]] | None = None,
        incumbent_assignments: dict[str, str] | None = None,
        max_new_jobs_per_technician: int | None = None,
    ) -> tuple[list[AssignmentDecision], list[UnassignedDecision]]:
        technician_by_id = {
            technician.technician_id: technician
            for technician in technicians
        }
        compatible_by_job: dict[str, list[CandidateEdge]] = defaultdict(list)
        rejected_by_job: dict[str, list[CandidateEdge]] = defaultdict(list)
        for edge in edges:
            target = compatible_by_job if edge.compatible else rejected_by_job
            target[edge.job_id].append(edge)

        workloads = {
            technician.technician_id: technician.existing_workload_minutes
            for technician in technicians
        }
        workloads.update(initial_workloads or {})
        clusters = {
            technician.technician_id: set()
            for technician in technicians
        }
        for technician_id, values in (initial_clusters or {}).items():
            clusters.setdefault(technician_id, set()).update(values)

        far_future = datetime.max
        ordered_jobs = sorted(
            jobs,
            key=lambda job: (
                job.due_at is None,
                job.due_at or far_future,
                job.priority,
                job.created_at or far_future,
                job.checklist_id,
            ),
        )

        decisions: list[AssignmentDecision] = []
        unassigned: list[UnassignedDecision] = []
        new_job_counts = {technician_id: 0 for technician_id in technician_by_id}
        for job in ordered_jobs:
            candidates = compatible_by_job.get(job.checklist_id, [])
            if not candidates:
                if not technician_by_id:
                    unassigned.append(
                        UnassignedDecision(
                            job_id=job.checklist_id,
                            reason="NO_AVAILABLE_TECHNICIAN",
                        )
                    )
                    continue
                reasons = sorted(
                    {
                        edge.reject_reason or "INCOMPATIBLE"
                        for edge in rejected_by_job.get(job.checklist_id, [])
                    }
                )
                unassigned.append(
                    UnassignedDecision(
                        job_id=job.checklist_id,
                        reason="NO_COMPATIBLE_TECHNICIAN:"
                        + ",".join(reasons),
                    )
                )
                continue

            scored: list[tuple[float, str, CandidateEdge, object]] = []
            capacity_rejected = 0
            limit_rejected = 0
            for edge in candidates:
                technician = technician_by_id[edge.technician_id]
                if (
                    max_new_jobs_per_technician is not None
                    and new_job_counts[edge.technician_id]
                    >= max_new_jobs_per_technician
                ):
                    limit_rejected += 1
                    continue
                projected = workloads[edge.technician_id] + job.service_minutes
                capacity = _capacity_minutes(technician)
                if capacity is not None and projected > capacity:
                    capacity_rejected += 1
                    continue
                score = self.scorer.score(
                    edge,
                    workload_minutes=workloads[edge.technician_id],
                    technician_has_cluster=(
                        edge.cluster_key in clusters[edge.technician_id]
                    ),
                    is_incumbent=(
                        (incumbent_assignments or {}).get(job.checklist_id)
                        == edge.technician_id
                    ),
                )
                scored.append(
                    (
                        score.total_cost,
                        edge.technician_id,
                        edge,
                        score,
                    )
                )

            if not scored:
                reason = (
                    "SHIFT_CAPACITY_EXCEEDED"
                    if capacity_rejected
                    else "TECHNICIAN_NEW_JOB_LIMIT_REACHED"
                    if limit_rejected
                    else "NO_FEASIBLE_CANDIDATE"
                )
                unassigned.append(
                    UnassignedDecision(job_id=job.checklist_id, reason=reason)
                )
                continue

            _, technician_id, edge, score = min(scored)
            decisions.append(
                AssignmentDecision(
                    job_id=job.checklist_id,
                    technician_id=technician_id,
                    source="OPTIMIZER_V0",
                    cluster_key=edge.cluster_key,
                    distance_cost=score.distance_cost,
                    load_cost=score.load_cost,
                    cluster_cost=score.cluster_cost,
                    stability_cost=score.stability_cost,
                    assignment_cost=score.total_cost,
                    distance_km=edge.distance_km,
                )
            )
            workloads[technician_id] += job.service_minutes
            clusters[technician_id].add(edge.cluster_key)
            new_job_counts[technician_id] += 1
        return decisions, unassigned
