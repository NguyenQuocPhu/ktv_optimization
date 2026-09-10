"""Weighted V0 cost with explicit components."""

from __future__ import annotations

from dataclasses import dataclass

from .models import CandidateEdge


@dataclass(frozen=True, slots=True)
class AssignmentWeights:
    distance_per_km: float = 1.0  # Phạt mỗi km chim bay.
    load_per_hour: float = 5.0  # Phạt mỗi giờ workload đã nhận.
    same_cluster_bonus: float = 8.0  # Giảm cost nếu KTV đã có cùng cluster.
    missing_distance_penalty: float = 25.0  # Dùng trong task-only mode.
    incumbent_bonus: float = 10.0  # Giảm cost nếu giữ assignment snapshot trước.


@dataclass(frozen=True, slots=True)
class ScoreBreakdown:
    distance_cost: float  # Thành phần khoảng cách.
    load_cost: float  # Thành phần cân bằng tải.
    cluster_cost: float  # Giá trị âm là bonus gom cụm.
    stability_cost: float  # Giá trị âm nếu giữ KTV incumbent.
    total_cost: float  # Tổng các thành phần.


class AssignmentScorer:
    def __init__(self, weights: AssignmentWeights | None = None) -> None:
        self.weights = weights or AssignmentWeights()

    def score(
        self,
        edge: CandidateEdge,
        *,
        workload_minutes: float,
        technician_has_cluster: bool,
        is_incumbent: bool = False, 
    ) -> ScoreBreakdown:
        distance_cost = (
            edge.distance_km * self.weights.distance_per_km
            if edge.distance_km is not None
            else self.weights.missing_distance_penalty
        )
        load_cost = (
            workload_minutes / 60 * self.weights.load_per_hour
        )
        cluster_cost = (
            -self.weights.same_cluster_bonus
            if technician_has_cluster
            else 0.0
        )
        stability_cost = (
            -self.weights.incumbent_bonus if is_incumbent else 0.0
        )
        return ScoreBreakdown(
            distance_cost=distance_cost,
            load_cost=load_cost,
            cluster_cost=cluster_cost,
            stability_cost=stability_cost,
            total_cost=(
                distance_cost + load_cost + cluster_cost + stability_cost
            ),
        )
