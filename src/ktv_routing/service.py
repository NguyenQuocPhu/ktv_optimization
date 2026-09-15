"""Ghép luồng giữa các team: UI (điều kiện lọc) → team data → routing."""

from __future__ import annotations

from .contract import ContractError, RouteResponse, WorkloadProvider, WorkloadQuery
from .planner import RoutingConfig, plan_routes
from .travel import TravelModel


class RoutingService:
    """Nhận query từ UI, lấy job đã gán từ team data rồi xếp tuyến."""

    def __init__(
        self,
        provider: WorkloadProvider,
        config: RoutingConfig | None = None,
        travel: TravelModel | None = None,
    ) -> None:
        self.provider = provider
        self.config = config or RoutingConfig()
        self.travel = travel

    def plan(self, query: WorkloadQuery) -> RouteResponse:
        request = self.provider.fetch_workload(query)
        if request.planned_at != query.planned_at:
            raise ContractError(
                f"team data trả planned_at={request.planned_at.isoformat()}, "
                f"khác query={query.planned_at.isoformat()}"
            )
        if request.filter != query.filter:
            raise ContractError("team data trả filter khác điều kiện lọc đã gửi")
        return plan_routes(request, self.config, self.travel)
