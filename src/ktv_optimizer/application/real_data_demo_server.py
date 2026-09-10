"""Interactive web lab for the real-data multi-snapshot replay."""

from __future__ import annotations

import argparse
import json
import re
import sys
import traceback
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.real_data_e2e import ScenarioRunner, prepare  # noqa: E402


PIPELINE_STAGES = (
    "VALIDATE_INPUT",
    "LOAD_INPUT",
    "MAP_INPUT",
    "LOAD_PREVIOUS_CHECKPOINT",
    "OPTIMIZE",
    "WRITE_OUTPUT",
    "COMMIT_CHECKPOINT",
)


def _route_payload(run) -> list[dict[str, Any]]:
    technician_by_id = {
        item.technician_id: item for item in run.result.snapshot.technicians
    }
    routes = sorted(
        run.result.optimization.routes,
        key=lambda item: (-len(item.stops), item.technician_id),
    )
    payload = []
    for route in routes[:30]:
        technician = technician_by_id[route.technician_id]
        location = technician.current_location
        payload.append(
            {
                "technician_id": route.technician_id,
                "start": (
                    {
                        "latitude": location.latitude,
                        "longitude": location.longitude,
                    }
                    if location
                    else None
                ),
                "distance_km": round(route.total_distance_km, 3),
                "travel_minutes": round(route.total_travel_minutes, 1),
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
                        "arrival": (
                            stop.estimated_arrival.strftime("%H:%M")
                            if stop.estimated_arrival
                            else None
                        ),
                    }
                    for stop in route.stops
                ],
            }
        )
    return payload


class RealDataLab:
    """One in-memory click-through session with disk-backed checkpoints."""

    def __init__(
        self,
        *,
        session_root: Path,
        maintenance_path: Path,
        boundary_path: Path,
    ) -> None:
        self.session_root = session_root
        self.maintenance_path = maintenance_path
        self.boundary_path = boundary_path
        self.workspace: Path | None = None
        self.runner: ScenarioRunner | None = None
        self.config = {
            "branch": "HNI_04",
            "jobs": 3000,
            "add_jobs": 600,
            "mode": "task_location",
            "transition_count": 30,
        }

    def prepare(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.runner is not None:
            raise ValueError("Session đã prepare; bấm Làm mới để tạo session khác")
        branch = str(payload.get("branch", "HNI_04")).strip().upper()
        if not re.fullmatch(r"[A-Z0-9_]+", branch):
            raise ValueError("Mã branch không hợp lệ")
        jobs = int(payload.get("jobs", 3000))
        add_jobs = int(payload.get("add_jobs", 600))
        transition_count = int(payload.get("transition_count", 30))
        mode = str(payload.get("mode", "task_location"))
        if not 100 <= jobs <= 10_000:
            raise ValueError("Số job đầu phải trong khoảng 100–10.000")
        if not 0 <= add_jobs <= 5_000:
            raise ValueError("Batch thêm phải trong khoảng 0–5.000")
        if not 1 <= transition_count <= 200:
            raise ValueError("Số job chuyển trạng thái phải trong khoảng 1–200")
        if mode not in {"task_location", "task", "location"}:
            raise ValueError("Mode không hợp lệ")
        self.config = {
            "branch": branch,
            "jobs": jobs,
            "add_jobs": add_jobs,
            "mode": mode,
            "transition_count": transition_count,
        }
        session_id = datetime.now().strftime("web_%Y%m%dT%H%M%S%f")
        self.workspace = self.session_root / f"{branch.lower()}_{session_id}"
        prepare(
            argparse.Namespace(
                maintenance=self.maintenance_path,
                boundary=self.boundary_path,
                branch=branch,
                jobs=jobs,
                add_jobs=add_jobs,
                snapshot_start="2026-06-30T08:00:00",
                output_dir=self.workspace,
            )
        )
        self.runner = ScenarioRunner(
            argparse.Namespace(
                workspace=self.workspace,
                mode=mode,
                max_distance_km=30.0,
                average_speed_kmh=30.0,
                transition_count=transition_count,
            )
        )
        return self.state()

    def run_next(self) -> dict[str, Any]:
        if self.runner is None:
            raise ValueError("Hãy chuẩn bị dữ liệu trước")
        self.runner.run_next()
        return self.state()

    def _current(self) -> dict[str, Any] | None:
        if self.runner is None or self.runner.last_run is None:
            return None
        run = self.runner.last_run
        summary = self.runner.summaries[-1]
        previous = (
            self.runner.summaries[-2]
            if len(self.runner.summaries) > 1
            else None
        )
        conflicts = [asdict(item) for item in run.result.conflicts]
        changes = [asdict(item) for item in run.result.changes]
        assignments = [
            {
                "checklist_id": item.job_id,
                "technician_id": item.technician_id,
                "source": item.source,
                "distance_km": (
                    round(item.distance_km, 3)
                    if item.distance_km is not None
                    else None
                ),
                "cost": round(item.assignment_cost, 3),
            }
            for item in run.result.optimization.assignments[:50]
        ]
        conflict_codes = Counter(item["code"] for item in conflicts)
        change_types = Counter(item["change_type"] for item in changes)
        unassigned_reasons = Counter(
            item.reason for item in run.result.optimization.unassigned
        )
        return {
            "summary": summary,
            "previous_summary": previous,
            "deltas": {
                key: summary.get(key, 0) - (previous or {}).get(key, 0)
                for key in (
                    "active_jobs",
                    "assignments",
                    "unassigned",
                    "conflicts",
                )
            },
            "pipeline_stages": list(PIPELINE_STAGES),
            "change_types": dict(change_types),
            "changes": changes[:40],
            "conflict_codes": dict(conflict_codes),
            "conflicts": conflicts[:40],
            "assignments": assignments,
            "unassigned_reasons": dict(unassigned_reasons),
            "technician_statuses": summary["technician_statuses"],
            "routes": _route_payload(run),
            "files": {
                "snapshot_csv": str(
                    self.runner.snapshots / f"{summary['snapshot_id']}.csv"
                ),
                "roster_csv": str(
                    self.runner.rosters / f"{summary['snapshot_id']}.csv"
                ),
                "run_metadata": summary["run_metadata_path"],
                "latest_checkpoint": str(self.runner.runtime / "latest.json"),
                "run_output": str(run.output_dir),
            },
        }

    def state(self) -> dict[str, Any]:
        completed = len(self.runner.summaries) if self.runner else 0
        prepared = self.runner is not None
        timeline = [
            {
                "snapshot_id": snapshot_id,
                "scenario": scenario,
                "description": description,
                "status": (
                    "completed"
                    if index < completed
                    else "next"
                    if index == completed
                    else "pending"
                ),
                "summary": (
                    self.runner.summaries[index]
                    if self.runner and index < completed
                    else None
                ),
            }
            for index, (snapshot_id, scenario, description) in enumerate(
                ScenarioRunner.SCENARIOS
            )
        ]
        metadata = self.runner.metadata if self.runner else None
        return {
            "phase": (
                "COMPLETE"
                if completed == len(ScenarioRunner.SCENARIOS)
                else "RUNNING"
                if completed
                else "PREPARED"
                if prepared
                else "EMPTY"
            ),
            "config": self.config,
            "workspace": str(self.workspace) if self.workspace else None,
            "prepared": metadata,
            "timeline": timeline,
            "completed_steps": completed,
            "total_steps": len(ScenarioRunner.SCENARIOS),
            "next_step": timeline[completed] if completed < len(timeline) else None,
            "current": self._current(),
            "source_files": (
                {
                    "branch_rows": str(
                        self.runner.inputs / "source_branch_all.csv"
                    ),
                    "canonical_jobs": str(
                        self.runner.inputs / "source_jobs_canonical.csv"
                    ),
                    "job_pool": str(
                        self.runner.inputs / "scenario_job_pool.csv"
                    ),
                    "roster_proxy": str(
                        self.runner.inputs / "roster_proxy_all.csv"
                    ),
                    "light_boundary": str(
                        self.runner.inputs / "boundary_centroids.geojson"
                    ),
                }
                if self.runner
                else None
            ),
        }


class RealDataRequestHandler(BaseHTTPRequestHandler):
    lab: RealDataLab
    html_path = Path(__file__).with_name("real_data_demo.html")

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
            self._json(200, type(self).lab.state())
            return
        self._json(404, {"error": "Not found"})

    def do_POST(self) -> None:  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 64_000:
                raise ValueError("Request quá lớn")
            payload = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/api/prepare":
                result = type(self).lab.prepare(payload)
            elif self.path == "/api/next":
                result = type(self).lab.run_next()
            elif self.path == "/api/reset":
                previous = type(self).lab
                type(self).lab = RealDataLab(
                    session_root=previous.session_root,
                    maintenance_path=previous.maintenance_path,
                    boundary_path=previous.boundary_path,
                )
                result = type(self).lab.state()
            else:
                self._json(404, {"error": "Not found"})
                return
            self._json(200, result)
        except (ValueError, FileNotFoundError, FileExistsError) as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:  # pragma: no cover - visible demo diagnostics
            traceback.print_exc()
            self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

    def log_message(self, format: str, *args: object) -> None:
        print(f"[real-data-web] {self.address_string()} - {format % args}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--session-root",
        type=Path,
        default=Path("data/temp_real_replay/web_sessions"),
    )
    parser.add_argument(
        "--maintenance",
        type=Path,
        default=Path("data/QOS_MAINTENANCE_utf8.csv"),
    )
    parser.add_argument(
        "--boundary",
        type=Path,
        default=Path("data/boundary_2026-07-31.geojson"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    RealDataRequestHandler.lab = RealDataLab(
        session_root=args.session_root,
        maintenance_path=args.maintenance,
        boundary_path=args.boundary,
    )
    server = HTTPServer((args.host, args.port), RealDataRequestHandler)
    print(f"Real-data lab: http://{args.host}:{args.port}")
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
