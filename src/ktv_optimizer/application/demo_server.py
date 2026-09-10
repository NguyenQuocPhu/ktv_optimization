"""End-to-end multi-snapshot demo backed by the operational CSV pipeline."""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import asdict
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pandas as pd

from ktv_optimizer.optimization import CompatibilityMode
from ktv_optimizer.optimization.clustering import cluster_jobs
from ktv_optimizer.pipeline import (
    OperationalRun,
    OperationalSnapshotProcessor,
    PipelineConfig,
)


BASE_TIME = datetime(2026, 8, 1, 8, 0)
MAINTENANCE_COLUMNS = [
    "CHECKLIST_ID",
    "CHECKLIST_STATUS",
    "BRANCH_NAME",
    "CASE_TYPE",
    "OBJ_LOCATION",
    "EMP_ACCOUNT",
    "CREATE_DATE",
]
WARD_OPTIONS = {
    "Bắc": "Phường Demo Bắc, Tỉnh Demo",
    "Đông": "Phường Demo Đông, Tỉnh Demo",
    "Nam": "Phường Demo Nam, Tỉnh Demo",
    "Tây": "Phường Demo Tây, Tỉnh Demo",
}
TASK_CATALOG = {
    "maintenance": ("VẬT LÝ", "LOGIC"),
    "deployment": (
        "TRIỂN KHAI MỚI (NET, COMBO..)",
        "BOX, CAM ONLY",
        "SWAP",
    ),
    "recovery": ("THU HỒI THIẾT BỊ",),
}
WARD_CENTERS = {
    "Bắc": (106.018, 20.846),
    "Đông": (106.048, 20.828),
    "Nam": (106.027, 20.800),
    "Tây": (105.997, 20.821),
}
ROSTER_COLUMNS = [
    "EMP_ACCOUNT",
    "BRANCH_NAME",
    "LATITUDE",
    "LONGITUDE",
    "SHIFT_START",
    "SHIFT_END",
    "SUPPORTED_CASE_TYPES",
    "EXISTING_WORKLOAD_MINUTES",
]


def _polygon(longitude: float, latitude: float, size: float = 0.008) -> list:
    return [[
        [longitude - size, latitude - size],
        [longitude + size, latitude - size],
        [longitude + size, latitude + size],
        [longitude - size, latitude + size],
        [longitude - size, latitude - size],
    ]]


def _demo_boundary() -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "ma_xa": f"DEMO-{index:02d}",
                    "ten_xa": f"Demo {ward}",
                    "tinh_tp": "Demo",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": _polygon(longitude, latitude),
                },
            }
            for index, (ward, (longitude, latitude)) in enumerate(
                WARD_CENTERS.items(), start=1
            )
        ],
    }


def _initial_rows() -> list[dict[str, Any]]:
    return [
        {
            "CHECKLIST_ID": "DEMO-J001",
            "CHECKLIST_STATUS": "Chưa phân công",
            "BRANCH_NAME": "DEMO",
            "CASE_TYPE": "VẬT LÝ",
            "OBJ_LOCATION": WARD_OPTIONS["Bắc"],
            "EMP_ACCOUNT": None,
            "CREATE_DATE": "2026-08-01 07:10:00",
        },
        {
            "CHECKLIST_ID": "DEMO-J002",
            "CHECKLIST_STATUS": "Chưa phân công",
            "BRANCH_NAME": "DEMO",
            "CASE_TYPE": "TRIỂN KHAI MỚI (NET, COMBO..)",
            "OBJ_LOCATION": WARD_OPTIONS["Đông"],
            "EMP_ACCOUNT": None,
            "CREATE_DATE": "2026-08-01 07:20:00",
        },
        {
            "CHECKLIST_ID": "DEMO-J003",
            "CHECKLIST_STATUS": "Chưa phân công",
            "BRANCH_NAME": "DEMO",
            "CASE_TYPE": "THU HỒI THIẾT BỊ",
            "OBJ_LOCATION": WARD_OPTIONS["Nam"],
            "EMP_ACCOUNT": None,
            "CREATE_DATE": "2026-08-01 07:30:00",
        },
    ]


def _roster_rows() -> list[dict[str, Any]]:
    return [
        {
            "EMP_ACCOUNT": technician_id,
            "BRANCH_NAME": "DEMO",
            "LATITUDE": latitude,
            "LONGITUDE": longitude,
            "SHIFT_START": "2026-08-01 06:00:00",
            "SHIFT_END": "2026-08-01 18:00:00",
            # Empty means every task. This also avoids treating the comma in
            # "TRIỂN KHAI MỚI (NET, COMBO..)" as a list separator.
            "SUPPORTED_CASE_TYPES": None,
            "EXISTING_WORKLOAD_MINUTES": 0,
        }
        for technician_id, latitude, longitude in (
            ("DEMO.KTV01", 20.844, 106.016),
            ("DEMO.KTV02", 20.829, 106.045),
            ("DEMO.KTV03", 20.803, 106.029),
            ("DEMO.KTV04", 20.820, 106.001),
        )
    ]


class DemoSession:
    """A real sequence of synthetic CSV snapshots and committed plans."""

    def __init__(self, session_root: str | Path) -> None:
        created_at = datetime.now().astimezone()
        self.session_id = created_at.strftime("demo_%Y%m%dT%H%M%S%f")
        self.session_dir = Path(session_root) / self.session_id
        self.input_dir = self.session_dir / "inputs"
        self.runtime_dir = self.session_dir / "runtime"
        self.output_root = self.session_dir / "runs"
        self.boundary_path = self.input_dir / "boundary.geojson"
        self.roster_path = self.input_dir / "roster.csv"
        self.rows = _initial_rows()
        self.roster_rows = _roster_rows()
        self.snapshot_sequence = 0
        self.user_job_sequence = 0
        self.technician_sequence = len(self.roster_rows)
        self.random = random.Random(20260801)
        self.current_run: OperationalRun | None = None
        self.last_payload: dict[str, Any] = {}
        self.history: list[dict[str, Any]] = []
        self._write_static_inputs()
        self.last_payload = self._process(
            mode=CompatibilityMode.TASK_LOCATION,
            event={"type": "BOOTSTRAP", "description": "Khởi tạo S001"},
        )

    def _write_static_inputs(self) -> None:
        self.input_dir.mkdir(parents=True, exist_ok=True)
        self.boundary_path.write_text(
            json.dumps(_demo_boundary(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._write_roster()

    def _write_roster(self) -> None:
        pd.DataFrame(self.roster_rows, columns=ROSTER_COLUMNS).to_csv(
            self.roster_path, index=False, encoding="utf-8-sig"
        )

    def _next_snapshot_time(self) -> datetime:
        return BASE_TIME + timedelta(minutes=self.snapshot_sequence * 15)

    def _write_snapshot(self, snapshot_id: str) -> Path:
        path = self.input_dir / f"{snapshot_id}.csv"
        pd.DataFrame(self.rows, columns=MAINTENANCE_COLUMNS).to_csv(
            path, index=False, encoding="utf-8-sig"
        )
        return path

    def _process(
        self,
        *,
        mode: CompatibilityMode,
        event: dict[str, Any],
    ) -> dict[str, Any]:
        previous_run = self.current_run
        self.snapshot_sequence += 1
        snapshot_id = f"DEMO-S{self.snapshot_sequence:03d}"
        captured_at = BASE_TIME + timedelta(
            minutes=(self.snapshot_sequence - 1) * 15
        )
        snapshot_path = self._write_snapshot(snapshot_id)
        processor = OperationalSnapshotProcessor(
            boundary_path=self.boundary_path,
            runtime_dir=self.runtime_dir,
            output_root=self.output_root,
            config=PipelineConfig(
                mode=mode,
                max_distance_km=30.0,
                average_speed_kmh=30.0,
            ),
        )
        run = processor.process(
            snapshot_id=snapshot_id,
            snapshot_time=captured_at,
            maintenance_path=snapshot_path,
            roster_path=self.roster_path,
        )
        self.current_run = run
        payload = self._run_payload(run, previous_run, event, snapshot_path)
        self.history.append(
            {
                "snapshot_id": snapshot_id,
                "event": event["type"],
                "active_jobs": payload["current"]["summary"]["active_jobs"],
                "assignments": payload["current"]["summary"]["assignments"],
                "unassigned": payload["current"]["summary"]["unassigned"],
                "checkpoint_id": run.checkpoint.checkpoint_id,
            }
        )
        payload["history"] = list(self.history)
        self.last_payload = payload
        return payload

    @staticmethod
    def _plan(run: OperationalRun | None) -> dict[str, Any] | None:
        if run is None:
            return None
        result = run.result
        checkpoint_by_job = result.checkpoint.by_job_id
        clusters = cluster_jobs(
            list(result.snapshot.jobs), result.optimization.mode
        )
        jobs = []
        for job in result.snapshot.jobs:
            checkpoint = checkpoint_by_job[job.checklist_id]
            jobs.append(
                {
                    "checklist_id": job.checklist_id,
                    "status": job.status,
                    "task_type": job.task_type,
                    "address": job.address,
                    "ward_code": job.ward_code,
                    "ward_name": job.ward_name,
                    "latitude": job.location.latitude if job.location else None,
                    "longitude": job.location.longitude if job.location else None,
                    "planned_technician": checkpoint.planned_technician,
                    "assignment_source": checkpoint.assignment_source,
                    "route_order": checkpoint.route_order,
                }
            )
        technician_by_id = {
            technician.technician_id: technician
            for technician in result.snapshot.technicians
        }
        routes = []
        for route in result.optimization.routes:
            technician = technician_by_id[route.technician_id]
            start = technician.current_location
            routes.append(
                {
                    "technician_id": route.technician_id,
                    "start": {
                        "latitude": start.latitude,
                        "longitude": start.longitude,
                    } if start else None,
                    "total_distance_km": round(route.total_distance_km, 3),
                    "total_travel_minutes": round(
                        route.total_travel_minutes, 1
                    ),
                    "total_service_minutes": round(
                        route.total_service_minutes, 1
                    ),
                    "stops": [
                        {
                            "sequence": stop.sequence,
                            "checklist_id": stop.job_id,
                            "latitude": (
                                stop.location.latitude if stop.location else None
                            ),
                            "longitude": (
                                stop.location.longitude if stop.location else None
                            ),
                            "leg_distance_km": (
                                round(stop.leg_distance_km, 3)
                                if stop.leg_distance_km is not None else None
                            ),
                            "estimated_arrival": (
                                stop.estimated_arrival.strftime("%H:%M")
                                if stop.estimated_arrival else None
                            ),
                            "estimated_finish": (
                                stop.estimated_finish.strftime("%H:%M")
                                if stop.estimated_finish else None
                            ),
                        }
                        for stop in route.stops
                    ],
                }
            )
        return {
            "snapshot_id": result.snapshot.snapshot_id,
            "snapshot_time": result.snapshot.captured_at.isoformat(),
            "mode": result.optimization.mode.value,
            "summary": {
                "active_jobs": len(result.snapshot.jobs),
                "incoming_job_events": len(result.incoming_jobs),
                "completed_jobs": len(result.completed_jobs),
                "assignments": len(result.optimization.assignments),
                "unassigned": len(result.optimization.unassigned),
                "routes": len(routes),
                "queued_jobs": sum(
                    item.queue_status != "IN_PROGRESS"
                    for item in result.job_queue
                ),
                "clusters": len(clusters),
                "distance_km": round(
                    sum(
                        route.total_distance_km
                        for route in result.optimization.routes
                    ),
                    3,
                ),
            },
            "jobs": jobs,
            "routes": routes,
            "clusters": [
                {
                    "key": key,
                    "jobs": [job.checklist_id for job in values],
                }
                for key, values in sorted(clusters.items())
            ],
            "unassigned": [
                {"checklist_id": item.job_id, "reason": item.reason}
                for item in result.optimization.unassigned
            ],
            "technicians": [
                {
                    "technician_id": item.technician_id,
                    "work_status": item.work_status.value,
                    "current_job_id": item.current_job_id,
                    "planned_job_count": item.planned_job_count,
                    "queued_job_count": item.queued_job_count,
                }
                for item in result.technician_checkpoint.records
            ],
            "job_queue": [
                {
                    **asdict(item),
                    "snapshot_time": item.snapshot_time.isoformat(),
                    "estimated_arrival": (
                        item.estimated_arrival.isoformat()
                        if item.estimated_arrival else None
                    ),
                    "estimated_finish": (
                        item.estimated_finish.isoformat()
                        if item.estimated_finish else None
                    ),
                }
                for item in result.job_queue
            ],
        }

    def _run_payload(
        self,
        run: OperationalRun,
        previous_run: OperationalRun | None,
        event: dict[str, Any],
        snapshot_path: Path,
    ) -> dict[str, Any]:
        previous = self._plan(previous_run)
        current = self._plan(run)
        assert current is not None
        previous_assignments = {
            job["checklist_id"]: job["planned_technician"]
            for job in (previous or {}).get("jobs", [])
        }
        current_assignments = {
            job["checklist_id"]: job["planned_technician"]
            for job in current["jobs"]
        }
        all_job_ids = sorted(previous_assignments.keys() | current_assignments.keys())
        assignment_delta = [
            {
                "checklist_id": job_id,
                "previous": previous_assignments.get(job_id),
                "current": current_assignments.get(job_id),
            }
            for job_id in all_job_ids
            if previous_assignments.get(job_id) != current_assignments.get(job_id)
        ]
        return {
            "session_id": self.session_id,
            "event": event,
            "previous": previous,
            "current": current,
            "checklist_options": [
                {
                    "checklist_id": str(row["CHECKLIST_ID"]),
                    "status": str(row["CHECKLIST_STATUS"]),
                    "technician_id": row.get("EMP_ACCOUNT"),
                }
                for row in self.rows
            ],
            "changes": [asdict(item) for item in run.result.changes],
            "conflicts": [asdict(item) for item in run.result.conflicts],
            "assignment_delta": assignment_delta,
            "distance_delta_km": round(
                current["summary"]["distance_km"]
                - ((previous or {}).get("summary") or {}).get("distance_km", 0.0),
                3,
            ),
            "operational": {
                "run_id": run.run_id,
                "status": run.metadata["status"],
                "stage": run.metadata["stage"],
                "checkpoint_id": run.checkpoint.checkpoint_id,
                "previous_checkpoint_id": run.checkpoint.previous_checkpoint_id,
                "run_metadata_path": str(
                    run.output_dir / "run_metadata.json"
                ),
                "snapshot_csv_path": str(snapshot_path),
                "latest_pointer_path": str(self.runtime_dir / "latest.json"),
            },
            "history": [],
        }

    def add_checklist(self, payload: dict[str, Any]) -> dict[str, Any]:
        group = str(payload.get("task_group", "")).strip().lower()
        if group == "random":
            group = self.random.choice(tuple(TASK_CATALOG))
        if group not in TASK_CATALOG:
            raise ValueError("Nhóm tác vụ không hợp lệ")
        task_type = self.random.choice(TASK_CATALOG[group])
        ward = str(payload.get("ward", "random")).strip()
        if ward == "random":
            ward = self.random.choice(tuple(WARD_OPTIONS))
        if ward not in WARD_OPTIONS:
            raise ValueError("Phường demo không hợp lệ")
        mode = CompatibilityMode(
            str(payload.get("mode", CompatibilityMode.TASK_LOCATION.value))
        )
        self.user_job_sequence += 1
        checklist_id = str(payload.get("checklist_id", "")).strip()
        if not checklist_id:
            checklist_id = f"USER-J{self.user_job_sequence:03d}"
        if any(row["CHECKLIST_ID"] == checklist_id for row in self.rows):
            raise ValueError(f"CHECKLIST_ID đã tồn tại: {checklist_id}")
        created_at = self._next_snapshot_time()
        self.rows.append(
            {
                "CHECKLIST_ID": checklist_id,
                "CHECKLIST_STATUS": "Chưa phân công",
                "BRANCH_NAME": "DEMO",
                "CASE_TYPE": task_type,
                "OBJ_LOCATION": WARD_OPTIONS[ward],
                "EMP_ACCOUNT": None,
                "CREATE_DATE": created_at.isoformat(sep=" "),
            }
        )
        return self._process(
            mode=mode,
            event={
                "type": "CHECKLIST_ADDED",
                "description": f"Thêm {checklist_id}: {task_type} tại Demo {ward}",
                "checklist_id": checklist_id,
                "task_group": group,
                "task_type": task_type,
                "ward": ward,
            },
        )

    def complete_checklist(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.update_checklist(payload, event_type="complete")

    def update_checklist(
        self,
        payload: dict[str, Any],
        *,
        event_type: str | None = None,
    ) -> dict[str, Any]:
        event_type = str(
            event_type or payload.get("event_type", "")
        ).strip().lower()
        if event_type == "add":
            return self.add_checklist(payload)
        supported_events = {
            "complete": ("Đóng checklist", "CHECKLIST_COMPLETED", "Hoàn thành"),
            "start": ("Đang xử lý", "CHECKLIST_STARTED", "Bắt đầu xử lý"),
            "follow_up": (
                "Đã xử lý và đang theo dõi",
                "CHECKLIST_FOLLOW_UP",
                "Chuyển sang theo dõi",
            ),
            "pause": (
                "Tạm dừng chờ xử lý",
                "CHECKLIST_PAUSED",
                "Tạm dừng",
            ),
            "reopen": ("Chưa phân công", "CHECKLIST_REOPENED", "Mở lại"),
            "assign": ("Đã phân công", "CHECKLIST_ASSIGNED", "Phân công"),
        }
        if event_type not in supported_events:
            raise ValueError("Loại cập nhật checklist không hợp lệ")
        checklist_id = str(payload.get("checklist_id", "")).strip()
        mode = CompatibilityMode(
            str(payload.get("mode", CompatibilityMode.TASK_LOCATION.value))
        )
        current_checkpoint = (
            self.current_run.result.checkpoint.by_job_id
            if self.current_run else {}
        )
        matching = [
            row for row in self.rows if row["CHECKLIST_ID"] == checklist_id
        ]
        if not matching:
            raise ValueError(f"Không tìm thấy checklist: {checklist_id}")
        row = matching[0]
        if (
            row["CHECKLIST_STATUS"] == "Đóng checklist"
            and event_type != "reopen"
        ):
            raise ValueError(f"Checklist đã đóng: {checklist_id}")
        state = current_checkpoint.get(checklist_id)
        current_technician = (
            state.planned_technician if state else row.get("EMP_ACCOUNT")
        )
        status, audit_type, action_label = supported_events[event_type]
        if event_type == "assign":
            technician_id = str(payload.get("technician_id", "")).strip()
            roster_ids = {item["EMP_ACCOUNT"] for item in self.roster_rows}
            if technician_id not in roster_ids:
                raise ValueError(f"KTV không có trong roster: {technician_id}")
            current_technician = technician_id
        elif event_type == "reopen":
            current_technician = None
        elif event_type in {"start", "follow_up", "pause"} and not current_technician:
            raise ValueError(
                f"{checklist_id} chưa có KTV để chuyển trạng thái"
            )
        row["CHECKLIST_STATUS"] = status
        row["EMP_ACCOUNT"] = current_technician
        technician_note = (
            f" cho {current_technician}" if current_technician else ""
        )
        return self._process(
            mode=mode,
            event={
                "type": audit_type,
                "description": (
                    f"{action_label} {checklist_id}{technician_note}"
                ),
                "checklist_id": checklist_id,
                "status": status,
                "technician_id": current_technician,
            },
        )

    def add_technician(self, payload: dict[str, Any]) -> dict[str, Any]:
        mode = CompatibilityMode(
            str(payload.get("mode", CompatibilityMode.TASK_LOCATION.value))
        )
        ward = str(payload.get("ward", "random")).strip()
        if ward == "random":
            ward = self.random.choice(tuple(WARD_OPTIONS))
        if ward not in WARD_OPTIONS:
            raise ValueError("Phường demo không hợp lệ")
        technician_id = str(payload.get("technician_id", "")).strip().upper()
        if not technician_id:
            self.technician_sequence += 1
            technician_id = f"DEMO.KTV{self.technician_sequence:02d}"
        if any(
            row["EMP_ACCOUNT"] == technician_id for row in self.roster_rows
        ):
            raise ValueError(f"KTV đã tồn tại trong roster: {technician_id}")
        longitude, latitude = WARD_CENTERS[ward]
        self.roster_rows.append(
            {
                "EMP_ACCOUNT": technician_id,
                "BRANCH_NAME": "DEMO",
                "LATITUDE": latitude,
                "LONGITUDE": longitude,
                "SHIFT_START": "2026-08-01 06:00:00",
                "SHIFT_END": "2026-08-01 18:00:00",
                "SUPPORTED_CASE_TYPES": None,
                "EXISTING_WORKLOAD_MINUTES": 0,
            }
        )
        self._write_roster()
        return self._process(
            mode=mode,
            event={
                "type": "TECHNICIAN_ADDED",
                "description": f"Thêm {technician_id} vào ca tại Demo {ward}",
                "technician_id": technician_id,
                "ward": ward,
            },
        )

    def replan(self, payload: dict[str, Any]) -> dict[str, Any]:
        mode = CompatibilityMode(
            str(payload.get("mode", CompatibilityMode.TASK_LOCATION.value))
        )
        return self._process(
            mode=mode,
            event={
                "type": "MANUAL_REPLAN",
                "description": f"User tối ưu lại bằng mode {mode.value}",
            },
        )


class DemoRequestHandler(BaseHTTPRequestHandler):
    session: DemoSession | None = None
    session_root = Path("artifacts/demo_e2e")
    html_path = Path(__file__).with_name("demo.html")

    @classmethod
    def current_session(cls) -> DemoSession:
        if cls.session is None:
            cls.session = DemoSession(cls.session_root)
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
            self._json(200, self.current_session().last_payload)
            return
        self._json(404, {"error": "Not found"})

    def do_POST(self) -> None:  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 64_000:
                raise ValueError("Request quá lớn")
            payload = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/api/jobs":
                result = self.current_session().add_checklist(payload)
            elif self.path == "/api/snapshot":
                result = self.current_session().update_checklist(payload)
            elif self.path == "/api/complete":
                result = self.current_session().complete_checklist(payload)
            elif self.path == "/api/technicians":
                result = self.current_session().add_technician(payload)
            elif self.path == "/api/replan":
                result = self.current_session().replan(payload)
            elif self.path == "/api/reset":
                type(self).session = DemoSession(type(self).session_root)
                result = type(self).session.last_payload
            else:
                self._json(404, {"error": "Not found"})
                return
            self._json(200, result)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})

    def log_message(self, format: str, *args: object) -> None:
        print(f"[demo] {self.address_string()} - {format % args}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--session-root", type=Path, default=Path("artifacts/demo_e2e")
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    DemoRequestHandler.session_root = args.session_root
    DemoRequestHandler.session = DemoSession(args.session_root)
    server = HTTPServer((args.host, args.port), DemoRequestHandler)
    print(f"Demo: http://{args.host}:{args.port}")
    print(f"Session: {DemoRequestHandler.session.session_dir.resolve()}")
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
