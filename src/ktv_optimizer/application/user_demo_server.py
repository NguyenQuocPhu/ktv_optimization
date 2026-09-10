"""User-facing two-snapshot route demo backed by the V0 optimizer."""

from __future__ import annotations

import argparse
import csv
import json
import random
from collections import Counter
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from ktv_optimizer.domain import GeoPoint
from ktv_optimizer.optimization import (
    CompatibilityMode,
    OptimizationJob,
    SimpleKtvOptimizer,
    TechnicianShift,
)


PLANNING_TIME = datetime(2026, 8, 1, 8, 30)
TECHNICIAN = {
    "technician_id": "PNC09.F.TUANNA41",
    "name": "Nguyễn Anh Tuấn",
    "branch": "Phương Nam - 09",
    "latitude": 10.72945,
    "longitude": 106.72172,
    "shift": "08:00–17:30",
}
TASK_GROUPS = {
    "deployment": {
        "label": "Triển khai",
        "short": "Lắp mới dịch vụ",
        "icon": "wrench",
        "types": (
            "TRIỂN KHAI MỚI (NET, COMBO..)",
            "BOX, CAM ONLY",
            "SWAP",
        ),
    },
    "maintenance": {
        "label": "Bảo trì",
        "short": "Xử lý sự cố",
        "icon": "gear",
        "types": ("VẬT LÝ", "LOGIC"),
    },
    "recovery": {
        "label": "Thu hồi",
        "short": "Thu hồi thiết bị",
        "icon": "box",
        "types": ("THU HỒI THIẾT BỊ",),
    },
    "random": {
        "label": "Ngẫu nhiên",
        "short": "Trộn nhiều tác vụ",
        "icon": "spark",
        "types": (
            "VẬT LÝ",
            "TRIỂN KHAI MỚI (NET, COMBO..)",
            "THU HỒI THIẾT BỊ",
            "BOX, CAM ONLY",
            "LOGIC",
        ),
    },
}
CLUSTERS = {
    "q7": {"label": "Cụm 1 · Quận 7", "color": "#159447"},
    "q4": {"label": "Cụm 2 · Quận 4", "color": "#f06a24"},
    "nhabe": {"label": "Cụm 3 · Nhà Bè", "color": "#713bc4"},
}
INITIAL_LOCATIONS = (
    {
        "cluster_id": "q7",
        "address": "Nguyễn Thị Thập, Tân Phú, Quận 7",
        "latitude": 10.73230,
        "longitude": 106.71314,
    },
    {
        "cluster_id": "q7",
        "address": "Nguyễn Lương Bằng, Tân Phú, Quận 7",
        "latitude": 10.72769,
        "longitude": 106.71818,
    },
    {
        "cluster_id": "q4",
        "address": "Khánh Hội, Phường 3, Quận 4",
        "latitude": 10.75917,
        "longitude": 106.70087,
    },
    {
        "cluster_id": "q4",
        "address": "Tôn Đản, Phường 13, Quận 4",
        "latitude": 10.75613,
        "longitude": 106.70860,
    },
    {
        "cluster_id": "nhabe",
        "address": "Nguyễn Hữu Thọ, Phước Kiển, Nhà Bè",
        "latitude": 10.70258,
        "longitude": 106.72053,
    },
)
ADD_LOCATIONS = {
    "q7": {
        "address": "Lâm Văn Bền, Tân Quy, Quận 7",
        "latitude": 10.74231,
        "longitude": 106.71170,
    },
    "q4": {
        "address": "Hoàng Diệu, Phường 6, Quận 4",
        "latitude": 10.76318,
        "longitude": 106.70521,
    },
    "nhabe": {
        "address": "Lê Văn Lương, Nhơn Đức, Nhà Bè",
        "latitude": 10.68791,
        "longitude": 106.70452,
    },
}
MODE_LABELS = {
    "task_location": "Đúng việc + ở gần",
    "task": "Ưu tiên loại việc",
    "location": "Ưu tiên khoảng cách",
}


class UserDemoSession:
    """One KTV, five initial jobs and exactly one add-job snapshot."""

    def __init__(self, session_root: Path) -> None:
        now = datetime.now().astimezone()
        self.session_id = now.strftime("user_%Y%m%dT%H%M%S%f")
        self.session_dir = session_root / self.session_id
        self.jobs: list[dict[str, Any]] = []
        self.sequence = 0
        self.selected_group: str | None = None
        self.mode = CompatibilityMode.TASK_LOCATION
        self.current_plan: dict[str, Any] | None = None
        self.previous_plan: dict[str, Any] | None = None
        self.route_revision = 0
        self.completion_events: list[dict[str, Any]] = []
        self.technician_location = {
            "latitude": TECHNICIAN["latitude"],
            "longitude": TECHNICIAN["longitude"],
        }
        self.technician_location_job_id: str | None = None
        self.random = random.Random(20260801)

    @staticmethod
    def _validate_group(value: object) -> str:
        group = str(value or "").strip().lower()
        if group not in TASK_GROUPS:
            raise ValueError("Nhóm tác vụ không hợp lệ")
        return group

    @staticmethod
    def _validate_mode(value: object) -> CompatibilityMode:
        try:
            return CompatibilityMode(str(value or "task_location"))
        except ValueError as exc:
            raise ValueError("Chế độ tối ưu không hợp lệ") from exc

    @staticmethod
    def _task_type(group: str, index: int) -> str:
        values = TASK_GROUPS[group]["types"]
        return values[index % len(values)]

    def _job_row(
        self,
        *,
        checklist_id: str,
        group: str,
        index: int,
        location: dict[str, Any],
        cluster_id: str,
        is_new: bool,
    ) -> dict[str, Any]:
        return {
            "checklist_id": checklist_id,
            "status": "Chưa phân công",
            "task_group": group,
            "task_type": self._task_type(group, index),
            "address": location["address"],
            "latitude": float(location["latitude"]),
            "longitude": float(location["longitude"]),
            "cluster_id": cluster_id,
            "service_minutes": 35 + (index % 3) * 10,
            "priority": 1 if is_new else 2,
            "is_new": is_new,
        }

    def _initial_jobs(self, group: str) -> list[dict[str, Any]]:
        rows = []
        for index, location in enumerate(INITIAL_LOCATIONS, start=1):
            job_group = (
                tuple(key for key in TASK_GROUPS if key != "random")[
                    (index - 1) % 3
                ]
                if group == "random"
                else group
            )
            rows.append(
                self._job_row(
                    checklist_id=f"PNC09-{index:03d}",
                    group=job_group,
                    index=index - 1,
                    location=location,
                    cluster_id=location["cluster_id"],
                    is_new=False,
                )
            )
        return rows

    @staticmethod
    def _optimization_job(row: dict[str, Any]) -> OptimizationJob:
        return OptimizationJob(
            checklist_id=row["checklist_id"],
            status=row["status"],
            branch_name="PNC09",
            task_type=row["task_type"],
            address=row["address"],
            location=GeoPoint(row["latitude"], row["longitude"]),
            ward_code=row["cluster_id"].upper(),
            ward_name=CLUSTERS[row["cluster_id"]]["label"],
            province_name="Hồ Chí Minh",
            priority=row["priority"],
            service_minutes=row["service_minutes"],
            created_at=PLANNING_TIME - timedelta(minutes=40),
        )

    def _technician_payload(self) -> dict[str, Any]:
        return {
            **TECHNICIAN,
            **self.technician_location,
            "location_source": (
                "COMPLETED_JOB"
                if self.technician_location_job_id
                else "SHIFT_START"
            ),
            "location_job_id": self.technician_location_job_id,
        }

    def _technician(self) -> TechnicianShift:
        return TechnicianShift(
            technician_id=TECHNICIAN["technician_id"],
            branch_name="PNC09",
            current_location=GeoPoint(
                self.technician_location["latitude"],
                self.technician_location["longitude"],
            ),
            shift_start=PLANNING_TIME.replace(hour=8, minute=0),
            shift_end=PLANNING_TIME.replace(hour=17, minute=30),
            supported_task_types=frozenset(),
        )

    def _build_plan(
        self,
        *,
        added_ids: list[str],
        last_completed_id: str | None = None,
    ) -> dict[str, Any]:
        optimization_jobs = [self._optimization_job(row) for row in self.jobs]
        technician = self._technician()
        optimizer = SimpleKtvOptimizer(
            mode=self.mode,
            max_distance_km=30.0,
            average_speed_kmh=24.0,
        )
        result = optimizer.optimize(
            optimization_jobs,
            [technician],
            planning_time=PLANNING_TIME + timedelta(minutes=15 * self.sequence),
        )
        if len(result.assignments) != len(self.jobs):
            raise RuntimeError("Không thể tạo đủ tuyến demo cho KTV")

        row_by_id = {row["checklist_id"]: row for row in self.jobs}
        route = result.routes[0] if result.routes else None
        if self.jobs and route is None:
            raise RuntimeError("Không thể tạo tuyến demo cho các job còn lại")
        stops = []
        for stop in route.stops if route else ():
            row = row_by_id[stop.job_id]
            cluster = CLUSTERS[row["cluster_id"]]
            stops.append(
                {
                    **row,
                    "sequence": stop.sequence,
                    "cluster_label": cluster["label"],
                    "cluster_color": cluster["color"],
                    "leg_distance_km": round(stop.leg_distance_km or 0.0, 2),
                    "estimated_arrival": (
                        stop.estimated_arrival.strftime("%H:%M")
                        if stop.estimated_arrival
                        else None
                    ),
                    "estimated_finish": (
                        stop.estimated_finish.strftime("%H:%M")
                        if stop.estimated_finish
                        else None
                    ),
                }
            )

        counts = Counter(row["cluster_id"] for row in self.jobs)
        travel_minutes = route.total_travel_minutes if route else 0.0
        service_minutes = route.total_service_minutes if route else 0.0
        total_minutes = travel_minutes + service_minutes
        return {
            "snapshot_id": f"USER-S{self.sequence:03d}",
            "snapshot_time": (
                PLANNING_TIME + timedelta(minutes=15 * (self.sequence - 1))
            ).isoformat(),
            "selected_group": self.selected_group,
            "selected_group_label": TASK_GROUPS[self.selected_group]["label"],
            "mode": self.mode.value,
            "mode_label": MODE_LABELS[self.mode.value],
            "added_job_ids": added_ids,
            "route_revision": self.route_revision,
            "completed_job_ids": [
                item["checklist_id"] for item in self.completion_events
            ],
            "last_completed_id": last_completed_id,
            "summary": {
                "jobs": len(self.jobs),
                "distance_km": round(route.total_distance_km, 1) if route else 0.0,
                "travel_minutes": round(travel_minutes),
                "service_minutes": round(service_minutes),
                "total_minutes": round(total_minutes),
                "on_time_rate": 98 if total_minutes <= 510 else 91,
            },
            "technician": self._technician_payload(),
            "route": {
                "start": {
                    "latitude": self.technician_location["latitude"],
                    "longitude": self.technician_location["longitude"],
                },
                "stops": stops,
            },
            "clusters": [
                {
                    "cluster_id": cluster_id,
                    "label": values["label"],
                    "color": values["color"],
                    "jobs": counts.get(cluster_id, 0),
                }
                for cluster_id, values in CLUSTERS.items()
                if counts.get(cluster_id)
            ],
            "unassigned": [
                {"checklist_id": item.job_id, "reason": item.reason}
                for item in result.unassigned
            ],
        }

    def optimize(self, payload: dict[str, Any]) -> dict[str, Any]:
        group = self._validate_group(payload.get("task_group"))
        self.mode = self._validate_mode(payload.get("mode"))
        self.selected_group = group
        self.jobs = self._initial_jobs(group)
        self.sequence = 1
        self.previous_plan = None
        self.route_revision = 0
        self.completion_events = []
        self.technician_location = {
            "latitude": TECHNICIAN["latitude"],
            "longitude": TECHNICIAN["longitude"],
        }
        self.technician_location_job_id = None
        self.current_plan = self._build_plan(added_ids=[])
        self._persist()
        return self.state()

    def add_snapshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.current_plan is None:
            raise ValueError("Hãy tối ưu tuyến đầu tiên trước")
        if self.sequence >= 2:
            raise ValueError("Demo V1 chỉ dùng hai snapshot; hãy bấm Làm mới")
        group = self._validate_group(
            payload.get("task_group") or self.selected_group
        )
        cluster_id = str(payload.get("cluster_id", "q7")).strip().lower()
        if cluster_id not in ADD_LOCATIONS:
            raise ValueError("Khu vực thêm checklist không hợp lệ")
        location = ADD_LOCATIONS[cluster_id]
        checklist_id = "PNC09-006"
        self.jobs.append(
            self._job_row(
                checklist_id=checklist_id,
                group=group,
                index=5,
                location=location,
                cluster_id=cluster_id,
                is_new=True,
            )
        )
        self.sequence = 2
        self.route_revision = 0
        self.previous_plan = self.current_plan
        self.current_plan = self._build_plan(added_ids=[checklist_id])
        self._persist()
        return self.state()

    def complete_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Complete one active stop and re-route without adding a snapshot."""

        if self.current_plan is None:
            raise ValueError("Hãy tối ưu tuyến đầu tiên trước")
        checklist_id = str(payload.get("checklist_id", "")).strip()
        matching = [
            row for row in self.jobs if row["checklist_id"] == checklist_id
        ]
        if not matching:
            raise ValueError(f"Checklist không còn active: {checklist_id}")

        completed = matching[0]
        previous_location = dict(self.technician_location)
        self.technician_location = {
            "latitude": completed["latitude"],
            "longitude": completed["longitude"],
        }
        self.technician_location_job_id = checklist_id
        self.jobs = [
            row for row in self.jobs if row["checklist_id"] != checklist_id
        ]
        self.route_revision += 1
        event = {
            "event_id": (
                f"{self.current_plan['snapshot_id']}"
                f"-R{self.route_revision:03d}"
            ),
            "event_type": "CHECKLIST_COMPLETED",
            "occurred_at": datetime.now().astimezone().isoformat(),
            "snapshot_id": self.current_plan["snapshot_id"],
            "checklist_id": checklist_id,
            "previous_route_order": next(
                (
                    stop["sequence"]
                    for stop in self.current_plan["route"]["stops"]
                    if stop["checklist_id"] == checklist_id
                ),
                None,
            ),
            "task_type": completed["task_type"],
            "address": completed["address"],
            "technician_previous_location": previous_location,
            "technician_current_location": dict(self.technician_location),
        }
        self.completion_events.append(event)
        added_ids = [
            value
            for value in self.current_plan.get("added_job_ids", [])
            if value != checklist_id
        ]
        self.current_plan = self._build_plan(
            added_ids=added_ids,
            last_completed_id=checklist_id,
        )
        self._persist_progress(event)
        return self.state()

    def _persist(self) -> None:
        assert self.current_plan is not None
        snapshot_id = self.current_plan["snapshot_id"]
        snapshot_dir = self.session_dir / "snapshots"
        plan_dir = self.session_dir / "plans"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        plan_dir.mkdir(parents=True, exist_ok=True)
        columns = (
            "checklist_id",
            "status",
            "task_group",
            "task_type",
            "address",
            "latitude",
            "longitude",
            "cluster_id",
            "service_minutes",
            "priority",
            "is_new",
        )
        with (snapshot_dir / f"{snapshot_id}.csv").open(
            "w", encoding="utf-8-sig", newline=""
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(self.jobs)
        plan_path = plan_dir / f"{snapshot_id}.json"
        plan_path.write_text(
            json.dumps(self.current_plan, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        latest = {
            "session_id": self.session_id,
            "snapshot_id": snapshot_id,
            "snapshot_csv": str(snapshot_dir / f"{snapshot_id}.csv"),
            "plan_json": str(plan_path),
            "active_plan_json": str(plan_path),
            "route_revision": 0,
        }
        self._write_latest(latest)

    def _persist_progress(self, event: dict[str, Any]) -> None:
        assert self.current_plan is not None
        progress_dir = self.session_dir / "progress"
        progress_dir.mkdir(parents=True, exist_ok=True)
        progress_path = progress_dir / f"{event['event_id']}.json"
        progress_path.write_text(
            json.dumps(
                {"event": event, "active_plan": self.current_plan},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        snapshot_id = self.current_plan["snapshot_id"]
        latest = {
            "session_id": self.session_id,
            "snapshot_id": snapshot_id,
            "snapshot_csv": str(
                self.session_dir / "snapshots" / f"{snapshot_id}.csv"
            ),
            "plan_json": str(
                self.session_dir / "plans" / f"{snapshot_id}.json"
            ),
            "active_plan_json": str(progress_path),
            "route_revision": self.route_revision,
            "last_completed_id": event["checklist_id"],
        }
        self._write_latest(latest)

    def _write_latest(self, latest: dict[str, Any]) -> None:
        temp_path = self.session_dir / ".latest.json.tmp"
        temp_path.write_text(
            json.dumps(latest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self.session_dir / "latest.json")

    def state(self) -> dict[str, Any]:
        return {
            "phase": (
                "CHOOSE_TASK"
                if self.current_plan is None
                else "UPDATED"
                if self.sequence == 2
                else "ROUTE_READY"
            ),
            "session_id": self.session_id,
            "options": [
                {"id": key, **{k: v for k, v in value.items() if k != "types"}}
                for key, value in TASK_GROUPS.items()
            ],
            "technician": self._technician_payload(),
            "plan": self.current_plan,
            "previous_plan": self.previous_plan,
            "completion_events": list(self.completion_events),
            "can_add_snapshot": self.current_plan is not None and self.sequence == 1,
            "storage_path": str(self.session_dir),
        }


class UserDemoRequestHandler(BaseHTTPRequestHandler):
    session: UserDemoSession | None = None
    session_root = Path("artifacts/user_demo")
    html_path = Path(__file__).with_name("user_demo.html")

    @classmethod
    def current_session(cls) -> UserDemoSession:
        if cls.session is None:
            cls.session = UserDemoSession(cls.session_root)
        return cls.session

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/":
            body = self.html_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/api/state":
            self._json(200, self.current_session().state())
            return
        self._json(404, {"error": "Not found"})

    def do_POST(self) -> None:  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 64_000:
                raise ValueError("Request quá lớn")
            payload = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/api/optimize":
                result = self.current_session().optimize(payload)
            elif self.path == "/api/snapshot":
                result = self.current_session().add_snapshot(payload)
            elif self.path == "/api/complete":
                result = self.current_session().complete_job(payload)
            elif self.path == "/api/reset":
                type(self).session = UserDemoSession(type(self).session_root)
                result = type(self).session.state()
            else:
                self._json(404, {"error": "Not found"})
                return
            self._json(200, result)
        except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})

    def log_message(self, format: str, *args: object) -> None:
        print(f"[user-demo] {self.address_string()} - {format % args}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--session-root", type=Path, default=Path("artifacts/user_demo")
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    UserDemoRequestHandler.session_root = args.session_root
    UserDemoRequestHandler.session = UserDemoSession(args.session_root)
    server = HTTPServer((args.host, args.port), UserDemoRequestHandler)
    print(f"User demo: http://{args.host}:{args.port}")
    print("Ctrl+C để dừng")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
