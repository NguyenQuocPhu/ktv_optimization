"""Nearest-neighbour V0 routing with SLA-first ordering."""

from __future__ import annotations

from datetime import datetime, timedelta

from .models import (
    AssignmentDecision,
    OptimizationJob,
    RouteStop,
    TechnicianRoute,
    TechnicianShift,
)


class NearestNeighbourRouter:
    """Straight-line V0 router; replace later with a road routing engine."""

    def __init__(self, average_speed_kmh: float = 30.0) -> None:
        if average_speed_kmh <= 0:
            raise ValueError("average_speed_kmh must be positive")
        self.average_speed_kmh = average_speed_kmh

    def build_route(
        self,
        technician: TechnicianShift,
        jobs: list[OptimizationJob],
        *,
        planning_time: datetime,
        available_at: datetime | None = None,
    ) -> TechnicianRoute:
        remaining = list(jobs)
        current_location = technician.current_location
        current_time: datetime | None = max(
            planning_time,
            technician.shift_start or planning_time,
            available_at or planning_time,
        )
        stops: list[RouteStop] = []
        total_distance = 0.0
        total_travel_minutes = 0.0
        total_service_minutes = 0.0

        while remaining:
            def route_rank(job: OptimizationJob) -> tuple:
                distance = (
                    current_location.distance_km_to(job.location)
                    if current_location and job.location
                    else float("inf")
                )
                return (
                    job.due_at or datetime.max,
                    job.priority,
                    distance,
                    job.checklist_id,
                )

            job = min(remaining, key=route_rank)
            remaining.remove(job)
            leg_distance = (
                current_location.distance_km_to(job.location)
                if current_location and job.location
                else None
            )
            if leg_distance is not None and current_time is not None:
                travel_minutes = (
                    leg_distance / self.average_speed_kmh * 60
                )
                estimated_arrival = current_time + timedelta(
                    minutes=travel_minutes
                )
                total_distance += leg_distance
                total_travel_minutes += travel_minutes
            else:
                estimated_arrival = None

            estimated_finish = (
                estimated_arrival
                + timedelta(minutes=job.service_minutes)
                if estimated_arrival is not None
                else None
            )
            stops.append(
                RouteStop(
                    sequence=len(stops) + 1,
                    job_id=job.checklist_id,
                    location=job.location,
                    leg_distance_km=leg_distance,
                    estimated_arrival=estimated_arrival,
                    estimated_finish=estimated_finish,
                )
            )
            total_service_minutes += job.service_minutes
            if job.location is not None:
                current_location = job.location
            current_time = estimated_finish

        return TechnicianRoute(
            technician_id=technician.technician_id,
            stops=tuple(stops),
            total_distance_km=total_distance,
            total_travel_minutes=total_travel_minutes,
            total_service_minutes=total_service_minutes,
        )

    def build_routes(
        self,
        jobs: list[OptimizationJob],
        technicians: list[TechnicianShift],
        assignments: list[AssignmentDecision],
        *,
        planning_time: datetime,
        available_at_by_technician: dict[str, datetime] | None = None,
    ) -> list[TechnicianRoute]:
        job_by_id = {job.checklist_id: job for job in jobs}
        jobs_by_technician: dict[str, list[OptimizationJob]] = {}
        for assignment in assignments:
            jobs_by_technician.setdefault(
                assignment.technician_id, []
            ).append(job_by_id[assignment.job_id])

        routes: list[TechnicianRoute] = []
        for technician in technicians:
            assigned_jobs = jobs_by_technician.get(
                technician.technician_id, []
            )
            if assigned_jobs:
                routes.append(
                    self.build_route(
                        technician,
                        assigned_jobs,
                        planning_time=planning_time,
                        available_at=(available_at_by_technician or {}).get(
                            technician.technician_id
                        ),
                    )
                )
        return routes
