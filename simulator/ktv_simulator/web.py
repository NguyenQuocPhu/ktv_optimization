"""Web demo đóng vai frontend: chọn điều kiện lọc → team data → routing, rồi tua realtime. Không deploy.

Chạy qua CLI simulator với ``--serve PORT``. API:

    GET  /              trang web.html
    GET  /api/options   danh mục chi nhánh, loại việc, khoảng thời gian luồng sự kiện
    POST /api/plan      body = PlanRequest JSON → PlanResponse
    POST /api/replay    body = ReplayRequest JSON → ReplayResponse
"""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from time import perf_counter

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from ktv_routing import (
    HaversineTravel,
    JobFilter,
    OsrmTravel,
    RoutingConfig,
    TravelModel,
    WorkloadQuery,
    plan_routes,
    to_json_dict,
)

from .provider import EventWorkloadProvider, filter_changes

PAGE_PATH = Path(__file__).with_name("web.html")
MAX_CHANGES = 200


# ---------------------------------------------------------------- Pydantic models


class FilterModel(BaseModel):
    case_types: list[str] = []
    branch_names: list[str] = []
    emp_accounts: list[str] = []


class PlanRequest(BaseModel):
    planned_at: datetime
    filter: FilterModel = FilterModel()


class ReplayRequest(BaseModel):
    query: PlanRequest
    to: datetime


class BranchInfo(BaseModel):
    name: str
    jobs: int


class OptionsResponse(BaseModel):
    travel: str
    time_model: str
    rules: dict
    branches: list[BranchInfo]
    case_types: list[str]
    created_from: str
    created_to: str
    events_to: str
    events: dict[str, int]
    shift: list[str] | None


class PlanResponse(BaseModel):
    query: dict
    stats: dict
    request: dict
    response: dict


class ReplayResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_: str = Field(alias="from", serialization_alias="from")
    to: str
    events: int
    changes: list[dict]
    affected: list[str]
    stats: dict
    query: dict | None
    request: dict | None
    response: dict | None
    data_ms: float


# ---------------------------------------------------------------- helpers


def _to_filter(model: FilterModel) -> JobFilter:
    return JobFilter(
        case_types=tuple(model.case_types),
        branch_names=tuple(model.branch_names),
        emp_accounts=tuple(model.emp_accounts),
    )


def _to_query(model: PlanRequest) -> WorkloadQuery:
    planned_at = model.planned_at
    if planned_at.tzinfo is not None:
        raise ValueError("planned_at phải naive (không có múi giờ)")
    return WorkloadQuery(
        planned_at=planned_at,
        filter=_to_filter(model.filter),
    )


def _catalog(provider: EventWorkloadProvider, config: RoutingConfig, travel: TravelModel) -> dict:
    info = provider.info
    return {
        "travel": (
            f"đường bộ OSRM ({travel.base_url})"
            if isinstance(travel, OsrmTravel)
            else "chim bay"
        ),
        "time_model": (
            f"học từ lịch sử ({len(config.service_minutes_by_emp):,} KTV có thời gian riêng)"
            if config.transition is not None
            else "cố định theo loại việc"
        ),
        "rules": config.rules.catalog(),
        "branches": [{"name": name, "jobs": count} for name, count in info["branches"].items()],
        "case_types": info["case_types"],
        "created_from": info["created_from"],
        "created_to": info["created_to"],
        "events_to": info["to"],
        "events": info["events"],
        "shift": (
            [value.isoformat(timespec="minutes") for value in provider.shift]
            if provider.shift
            else None
        ),
    }


def _replay_step(
    provider: EventWorkloadProvider,
    config: RoutingConfig,
    travel: TravelModel,
    query: WorkloadQuery,
    to: datetime,
) -> dict:
    if to <= query.planned_at:
        raise ValueError("to phải sau planned_at")
    clock = perf_counter()
    provider.advance_to(query.planned_at)
    changes = filter_changes(provider.advance_to(to, collect=True), query.filter)
    affected = tuple(sorted({change.emp_account for change in changes if change.emp_account}))
    _, stats = provider.build(WorkloadQuery(to, query.filter))
    sub_query = request = response = None
    if affected:
        sub_query = WorkloadQuery(
            to,
            JobFilter(
                case_types=query.filter.case_types,
                branch_names=query.filter.branch_names,
                emp_accounts=affected,
            ),
        )
        request, _ = provider.build(sub_query)
    data_ms = (perf_counter() - clock) * 1000
    if request is not None:
        response = plan_routes(request, config, travel)
    return {
        "from": query.planned_at.isoformat(),
        "to": to.isoformat(),
        "events": len(changes),
        "changes": [to_json_dict(change) for change in changes[-MAX_CHANGES:]],
        "affected": list(affected),
        "stats": to_json_dict(stats),
        "query": to_json_dict(sub_query) if sub_query is not None else None,
        "request": to_json_dict(request) if request is not None else None,
        "response": to_json_dict(response) if response is not None else None,
        "data_ms": round(data_ms, 3),
    }


# Keep for CLI replay (no FastAPI involved).
replay_step = _replay_step


# ---------------------------------------------------------------- app factory


def make_app(
    provider: EventWorkloadProvider,
    config: RoutingConfig,
    travel: TravelModel | None = None,
) -> FastAPI:
    travel = travel or HaversineTravel(config.average_speed_kmh)
    page = PAGE_PATH.read_bytes()
    options = _catalog(provider, config, travel)
    lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield

    app = FastAPI(lifespan=lifespan)

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return HTMLResponse(page, media_type="text/html; charset=utf-8")

    @app.get("/api/options", response_model=OptionsResponse)
    async def get_options():
        return options

    @app.post("/api/plan", response_model=PlanResponse)
    async def post_plan(body: PlanRequest):
        try:
            query = _to_query(body)
            with lock:
                request, stats = provider.build(query)
                response = plan_routes(request, config, travel)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))
        return {
            "query": to_json_dict(query),
            "stats": to_json_dict(stats),
            "request": to_json_dict(request),
            "response": to_json_dict(response),
        }

    @app.post("/api/replay", response_model=ReplayResponse, response_model_by_alias=True)
    async def post_replay(body: ReplayRequest):
        try:
            query = _to_query(body.query)
            to = body.to
            with lock:
                payload = _replay_step(provider, config, travel, query, to)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))
        return payload

    return app


# ---------------------------------------------------------------- convenience for stdlib ThreadingHTTPServer (test compat)


def make_server(
    provider: EventWorkloadProvider,
    config: RoutingConfig,
    travel: TravelModel | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
):
    """Return a ``uvicorn.Server`` bound to *host*:*port* (use port=0 for random)."""
    import uvicorn

    app = make_app(provider, config, travel)
    uvi_config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    return uvicorn.Server(uvi_config)


# ---------------------------------------------------------------- CLI entry


def serve(
    provider: EventWorkloadProvider,
    config: RoutingConfig,
    travel: TravelModel,
    *,
    host: str,
    port: int,
) -> None:
    import uvicorn

    app = make_app(provider, config, travel)
    print(f"Web demo: http://{host}:{port}  (Ctrl+C để dừng)", flush=True)
    uvicorn.run(app, host=host, port=port, log_level="info")
