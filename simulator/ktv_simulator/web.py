"""Web demo đóng vai frontend: chọn điều kiện lọc → team data → routing, rồi tua realtime. Không deploy.

Chạy qua CLI simulator với ``--serve PORT``. API:

    GET  /              trang web.html
    GET  /api/options   danh mục chi nhánh, loại việc, khoảng thời gian luồng sự kiện
    POST /api/plan      body = WorkloadQuery JSON → {query, stats, request, response}
    POST /api/replay    body = {query: WorkloadQuery tại giờ đang xem, to: giờ mới}
                        → sự kiện trong khoảng, KTV bị ảnh hưởng và tuyến mới của riêng họ
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import perf_counter

from ktv_routing import (
    HaversineTravel,
    JobFilter,
    OsrmTravel,
    RoutingConfig,
    TravelModel,
    WorkloadQuery,
    plan_routes,
    query_from_dict,
    to_json_dict,
)

from .provider import EventWorkloadProvider, filter_changes

PAGE_PATH = Path(__file__).with_name("web.html")
MAX_CHANGES = 200  # Số sự kiện tối đa trả về mỗi nhịp để hiển thị.


def catalog(provider: EventWorkloadProvider, config: RoutingConfig, travel: TravelModel) -> dict:
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


def replay_step(
    provider: EventWorkloadProvider,
    config: RoutingConfig,
    travel: TravelModel,
    query: WorkloadQuery,
    to: datetime,
) -> dict:
    """Một nhịp realtime trong khoảng ``(query.planned_at, to]``.

    Team data áp sự kiện mới; frontend lấy các KTV có sự kiện job khớp bộ lọc và chỉ
    gửi routing request của những KTV đó. GPS chỉ cập nhật vị trí, không kích hoạt xếp lại.
    """

    if to <= query.planned_at:
        raise ValueError("to phải sau planned_at")
    clock = perf_counter()
    provider.advance_to(query.planned_at)
    changes = filter_changes(provider.advance_to(to, collect=True), query.filter)
    affected = tuple(sorted({change.emp_account for change in changes if change.emp_account}))
    _, stats = provider.build(WorkloadQuery(to, query.filter))  # Số liệu tổng cho thẻ tổng quan.
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


def make_server(
    provider: EventWorkloadProvider,
    config: RoutingConfig,
    travel: TravelModel | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> ThreadingHTTPServer:
    travel = travel or HaversineTravel(config.average_speed_kmh)
    page = PAGE_PATH.read_bytes()
    options = catalog(provider, config, travel)
    lock = threading.Lock()  # Provider có một con trỏ đọc luồng sự kiện dùng chung.

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass  # Giữ terminal gọn.

        def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: HTTPStatus, payload: object) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

        def do_GET(self) -> None:
            if self.path in ("/", "/index.html"):
                self._send(HTTPStatus.OK, page, "text/html; charset=utf-8")
            elif self.path == "/api/options":
                self._json(HTTPStatus.OK, options)
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "không có đường dẫn này"})

        def do_POST(self) -> None:
            if self.path not in ("/api/plan", "/api/replay"):
                self._json(HTTPStatus.NOT_FOUND, {"error": "không có API này"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"null")
                if self.path == "/api/plan":
                    query = query_from_dict(body)
                    with lock:
                        request, stats = provider.build(query)
                        response = plan_routes(request, config, travel)
                    payload = {
                        "query": to_json_dict(query),
                        "stats": to_json_dict(stats),
                        "request": to_json_dict(request),
                        "response": to_json_dict(response),
                    }
                else:
                    if not isinstance(body, dict):
                        raise ValueError("body phải là {query, to}")
                    query = query_from_dict(body.get("query"))
                    to = datetime.fromisoformat(str(body.get("to")))
                    with lock:
                        payload = replay_step(provider, config, travel, query, to)
            except ValueError as error:  # JSON hỏng, sai hợp đồng, giờ có múi giờ, to không hợp lệ.
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                return
            self._json(HTTPStatus.OK, payload)

    return ThreadingHTTPServer((host, port), Handler)


def serve(
    provider: EventWorkloadProvider,
    config: RoutingConfig,
    travel: TravelModel,
    *,
    host: str,
    port: int,
) -> None:
    try:
        server = make_server(provider, config, travel, host, port)
    except OSError as error:
        raise SystemExit(f"Không mở được {host}:{port} ({error.strerror}). Thử cổng khác với --serve.") from None
    print(f"Web demo: http://{host}:{server.server_address[1]}  (Ctrl+C để dừng)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
