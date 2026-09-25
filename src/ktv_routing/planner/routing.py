"""Điều phối routing: ``plan_routes`` nhận request, gọi solver, trả response."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from time import perf_counter

from ktv_routing.contract import (
    GeoPoint,
    Issue,
    JobInput,
    JobState,
    PlannedStop,
    PreviousRoute,
    RouteRequest,
    RouteResponse,
    RouteSummary,
    SequenceSource,
    StartSource,
    TechnicianInput,
    TechnicianRoute,
)
from ktv_routing.travel import HaversineTravel, TravelMatrix, TravelModel
from ktv_routing.planner.config import RoutingConfig
from ktv_routing.planner.solver import _Problem


@dataclass(frozen=True, slots=True)
class _Start:
    at: datetime
    location: GeoPoint | None
    source: StartSource
    in_progress_job_id: str | None = None


def plan_routes(
    request: RouteRequest,
    config: RoutingConfig | None = None,
    travel: TravelModel | None = None,
) -> RouteResponse:
    config = config or RoutingConfig()
    travel = travel or HaversineTravel(config.average_speed_kmh)
    clock = perf_counter()
    issues: list[Issue] = []
    planned: list[tuple[TechnicianInput, list[JobInput], _Start]] = []
    owner_by_job: dict[str, str] = {}
    seen_technicians: set[str] = set()
    job_count = pending_count = in_progress_count = sla_evaluable = 0

    for technician in sorted(request.technicians, key=lambda item: item.emp_account):
        account = technician.emp_account
        if account in seen_technicians:
            issues.append(Issue("DUPLICATE_TECHNICIAN", emp_account=account))
            continue
        seen_technicians.add(account)

        pending: list[JobInput] = []
        running: list[JobInput] = []
        for job in sorted(technician.jobs, key=lambda item: item.job_id):
            job_count += 1
            if job.job_id in owner_by_job:
                issues.append(
                    Issue(
                        "DUPLICATE_JOB",
                        emp_account=account,
                        job_id=job.job_id,
                        detail=f"first_emp_account={owner_by_job[job.job_id]}",
                    )
                )
                continue
            owner_by_job[job.job_id] = account
            if job.state is JobState.IN_PROGRESS:
                in_progress_count += 1
                running.append(job)
                continue
            pending_count += 1
            sla_evaluable += job.due_at is not None
            if job.location is None:
                issues.append(
                    Issue(
                        "JOB_LOCATION_UNKNOWN",
                        emp_account=account,
                        job_id=job.job_id,
                        detail=f"address={job.address}" if job.address else None,
                    )
                )
            else:
                pending.append(job)

        if not pending and not running:
            continue
        start = _resolve_start(technician, running, request.planned_at, config, issues)
        if (
            pending
            and technician.shift_end is not None
            and request.planned_at >= technician.shift_end
        ):
            issues.append(
                Issue(
                    "TECHNICIAN_OFF_SHIFT",
                    emp_account=account,
                    detail=f"shift_end={technician.shift_end.isoformat()}",
                )
            )
        planned.append((technician, pending, start))

    groups: list[list[GeoPoint]] = [
        ([start.location] if start.location is not None else [])
        + [job.location for job in pending if job.location is not None]
        for _, pending, start in planned
    ]
    travel_clock = perf_counter()
    matrices = travel.matrices(groups)
    travel_ms = (perf_counter() - travel_clock) * 1000

    routes: list[TechnicianRoute] = []
    for (technician, pending, start), matrix in zip(planned, matrices, strict=True):
        if matrix.note:
            issues.append(
                Issue(
                    "ROAD_DISTANCE_FALLBACK",
                    emp_account=technician.emp_account,
                    detail=matrix.note,
                )
            )
        routes.append(_sequence(technician, pending, start, config, matrix, issues))

    stops = [stop for route in routes for stop in route.stops]
    on_time = sum(stop.late is False for stop in stops)
    summary = RouteSummary(
        technicians=len(routes),
        jobs=job_count,
        pending_jobs=pending_count,
        in_progress_jobs=in_progress_count,
        routed_stops=len(stops),
        stops_without_eta=sum(stop.eta is None for stop in stops),
        late_stops=sum(stop.late is True for stop in stops),
        completion_late_stops=sum(stop.completion_late is True for stop in stops),
        stops_after_shift_end=sum(stop.after_shift_end is True for stop in stops),
        sla_evaluable_jobs=sla_evaluable,
        on_time_stops=on_time,
        on_time_rate_percent=(
            round(on_time / sla_evaluable * 100, 2) if sla_evaluable else None
        ),
        total_km=round(sum(route.total_km for route in routes), 3),
        travel_ms=round(travel_ms, 3),
        planning_ms=round((perf_counter() - clock) * 1000, 3),
    )
    return RouteResponse(
        planned_at=request.planned_at,
        filter=request.filter,
        routes=tuple(routes),
        issues=tuple(issues),
        summary=summary,
    )


def _resolve_start(
    technician: TechnicianInput,
    running: list[JobInput],
    planned_at: datetime,
    config: RoutingConfig,
    issues: list[Issue],
) -> _Start:
    """Điểm và giờ xuất phát: job đang làm → vị trí mới nhất → không rõ."""

    account = technician.emp_account
    start_at = max(planned_at, technician.shift_start or planned_at)
    in_progress_job_id = None
    if running:
        running.sort(
            key=lambda job: (
                job.started_at is not None,
                job.started_at or planned_at,
                job.job_id,
            )
        )
        current = running[-1]
        in_progress_job_id = current.job_id
        if len(running) > 1:
            issues.append(
                Issue(
                    "MULTIPLE_IN_PROGRESS",
                    emp_account=account,
                    job_id=current.job_id,
                    detail="in_progress=" + "|".join(job.job_id for job in running),
                )
            )
        service = timedelta(minutes=config.service_minutes(current, account))
        if current.started_at is None:
            issues.append(
                Issue(
                    "IN_PROGRESS_START_UNKNOWN",
                    emp_account=account,
                    job_id=current.job_id,
                    detail="giả định bắt đầu tại planned_at",
                )
            )
            start_at = max(start_at, planned_at + service)
        else:
            start_at = max(start_at, current.started_at + service)
        if current.location is not None:
            return _Start(
                start_at,
                current.location,
                StartSource.IN_PROGRESS_JOB,
                in_progress_job_id,
            )
        issues.append(
            Issue(
                "JOB_LOCATION_UNKNOWN",
                emp_account=account,
                job_id=current.job_id,
                detail="job đang làm không có tọa độ",
            )
        )

    fix = technician.last_location
    if fix is None:
        issues.append(Issue("TECHNICIAN_LOCATION_UNKNOWN", emp_account=account))
        return _Start(start_at, None, StartSource.UNKNOWN, in_progress_job_id)
    if planned_at - fix.recorded_at > timedelta(
        minutes=config.location_max_age_minutes
    ):
        issues.append(
            Issue(
                "STALE_TECHNICIAN_LOCATION",
                emp_account=account,
                detail=(
                    f"source={fix.source}; "
                    f"recorded_at={fix.recorded_at.isoformat()}"
                ),
            )
        )
        return _Start(
            start_at, fix.location, StartSource.STALE_LOCATION, in_progress_job_id
        )
    return _Start(
        start_at, fix.location, StartSource.LAST_LOCATION, in_progress_job_id
    )


def _after(value: datetime | None, limit: datetime | None) -> bool | None:
    if value is None or limit is None:
        return None
    return value > limit


def _rounded(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def _clearly_better(new: tuple[float, ...], old: tuple[float, ...], min_gain: float) -> bool:
    """Tuyến mới tốt hơn ở một tầng trên, hoặc bằng ở các tầng trên và giảm tầng cuối ≥ min_gain."""

    if new[:-1] != old[:-1]:
        return new[:-1] < old[:-1]
    return old[-1] - new[-1] >= min_gain


def _sequence(
    technician: TechnicianInput,
    jobs: list[JobInput],
    start: _Start,
    config: RoutingConfig,
    matrix: TravelMatrix,
    issues: list[Issue],
) -> TechnicianRoute:
    """Chọn thứ tự bằng QHĐ theo rule nghiệp vụ rồi tính ETA từng điểm."""

    rules = config.rules
    problem = _Problem(technician, jobs, start.at, start.location, config, matrix)

    position = {job.job_id: index for index, job in enumerate(jobs)}
    previous = [job_id for job_id in dict.fromkeys(technician.previous_sequence) if job_id in position]
    previous_route = PreviousRoute.NONE
    if previous and rules.previous_route_policy != "IGNORE":
        before = [0] * len(jobs)
        done = 0
        for job_id in previous:
            before[position[job_id]] = done
            done |= 1 << position[job_id]
        order, source = problem.solve(before)
        previous_route = PreviousRoute.KEPT
        if rules.previous_route_policy == "IF_BETTER":
            free_order, free_source = problem.solve(None)
            if free_order != order and _clearly_better(
                rules.objective_key(problem.walk(free_order)[1]),
                rules.objective_key(problem.walk(order)[1]),
                rules.reroute_min_gain,
            ):
                order, source, previous_route = free_order, free_source, PreviousRoute.CHANGED
    else:
        order, source = problem.solve(None)
    if source is not SequenceSource.OPTIMAL:
        issues.append(
            Issue(
                "SEQUENCE_NOT_OPTIMAL",
                emp_account=technician.emp_account,
                detail=(
                    f"{len(jobs)} job > max_exact_jobs={rules.max_exact_jobs}: dùng heuristic"
                    if source is SequenceSource.HEURISTIC
                    else f"vượt max_labels_per_state={rules.max_labels_per_state}: kết quả gần đúng"
                ),
            )
        )

    steps, totals = problem.walk(order)
    stops: list[PlannedStop] = []
    total_km = total_travel = total_service = 0.0
    for sequence, (index, km, travel, arrive, checkin, done) in enumerate(steps, 1):
        job = jobs[index]
        assert job.location is not None
        total_service += problem.service[index]
        if problem.known_start:
            eta = start.at + timedelta(minutes=arrive)
            checked_in = start.at + timedelta(minutes=checkin)
            finish = start.at + timedelta(minutes=done)
            leg_km, leg_minutes, wait = km, travel, checkin - arrive
            total_km += km
            total_travel += travel
        else:
            eta = checked_in = finish = wait = None
            leg_km = None if sequence == 1 else km
            leg_minutes = None if sequence == 1 else travel
        stops.append(
            PlannedStop(
                sequence=sequence,
                job_id=job.job_id,
                location=job.location,
                leg_km=_rounded(leg_km, 3),
                leg_minutes=_rounded(leg_minutes, 2),
                eta=eta,
                wait_minutes=_rounded(wait, 2),
                finish_at=finish,
                due_at=job.due_at,
                late=_after(checked_in, job.due_at),
                completion_late=_after(finish, job.complete_by),
                after_shift_end=_after(finish, technician.shift_end),
            )
        )

    return TechnicianRoute(
        emp_account=technician.emp_account,
        start_at=start.at,
        start_location=start.location,
        start_source=start.source,
        in_progress_job_id=start.in_progress_job_id,
        travel_source=matrix.source,
        sequence_source=source,
        previous_route=previous_route,
        score={code: round(value, 3) for code, value in totals.items()} if problem.known_start else {},
        stops=tuple(stops),
        total_km=round(total_km, 3),
        total_travel_minutes=round(total_travel, 2),
        total_service_minutes=round(total_service, 2),
        finish_at=stops[-1].finish_at if stops else start.at,
    )
