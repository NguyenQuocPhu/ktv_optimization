#!/usr/bin/env python3
"""Assertions for atomic state, observability and end-to-end demo events."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ktv_optimizer.application.demo_server import DemoSession  # noqa: E402
from ktv_optimizer.domain import GeoPoint  # noqa: E402
from ktv_optimizer.optimization import OptimizationJob  # noqa: E402
from ktv_optimizer.pipeline import (  # noqa: E402
    OperationalSnapshotProcessor,
    VersionedCheckpointManager,
)
from ktv_optimizer.state import (  # noqa: E402
    OptimizerCheckpoint,
    StateRecord,
    TechnicianCheckpoint,
)


def _checkpoint(snapshot_id: str) -> OptimizerCheckpoint:
    now = datetime(2026, 8, 1, 8)
    job = OptimizationJob(
        checklist_id=f"J-{snapshot_id}",
        status="Chưa phân công",
        branch_name="HYN",
        task_type="BẢO TRÌ",
        address="Demo",
        location=GeoPoint(20.82, 106.01),
    )
    return OptimizerCheckpoint(
        (
            StateRecord(
                snapshot_id=snapshot_id,
                snapshot_time=now,
                job=job,
                planned_technician=None,
                assignment_source=None,
                route_order=None,
                updated_at=now,
            ),
        )
    )


def main() -> int:
    with TemporaryDirectory() as temporary:
        temporary_path = Path(temporary)
        manager = VersionedCheckpointManager(temporary_path / "runtime")
        first = manager.commit(
            snapshot_id="S001",
            snapshot_time=datetime(2026, 8, 1, 8),
            run_id="S001__RUN1",
            optimizer=_checkpoint("S001"),
            technicians=TechnicianCheckpoint(),
        )
        latest_before = json.loads(manager.latest_path.read_text())

        # Simulate a crashed/incomplete next version. Because latest.json is
        # not switched, readers must still see the complete first checkpoint.
        broken = manager.staging_dir / "S002__BROKEN"
        broken.mkdir(parents=True)
        (broken / manager.OPTIMIZER_FILE).write_text("partial", encoding="utf-8")
        latest_after = json.loads(manager.latest_path.read_text())
        assert latest_before == latest_after
        optimizer, technicians, latest = manager.load()
        assert latest == first
        assert len(optimizer.records) == 1
        assert not technicians.records

        fixture = PROJECT_ROOT / "utils" / "fixtures" / "snapshots_v1"
        processor = OperationalSnapshotProcessor(
            boundary_path=fixture / "boundary.geojson",
            runtime_dir=temporary_path / "service_runtime",
            output_root=temporary_path / "runs",
        )
        run1 = processor.process(
            snapshot_id="S001",
            snapshot_time=datetime(2026, 8, 1, 8),
            maintenance_path=fixture / "snapshot_001.csv",
            roster_path=fixture / "roster.csv",
        )
        run2 = processor.process(
            snapshot_id="S002",
            snapshot_time=datetime(2026, 8, 1, 9),
            maintenance_path=fixture / "snapshot_002.csv",
            roster_path=fixture / "roster.csv",
        )
        assert run1.metadata["status"] == "SUCCESS"
        assert run2.metadata["stage"] == "COMPLETED"
        assert run2.metadata["previous_checkpoint_id"] == (
            run1.checkpoint.checkpoint_id
        )
        assert run2.metadata["inputs"]["maintenance"]["sha256"]
        assert run2.metadata["counts"]["active_jobs"] == 2
        assert (run2.output_dir / "run_metadata.json").is_file()
        assert (run2.output_dir / "technician_job_queue.csv").is_file()

        failed_processor = OperationalSnapshotProcessor(
            boundary_path=fixture / "boundary.geojson",
            runtime_dir=temporary_path / "failed_runtime",
            output_root=temporary_path / "failed_runs",
        )
        try:
            failed_processor.process(
                snapshot_id="FAIL",
                snapshot_time=datetime(2026, 8, 1, 10),
                maintenance_path=temporary_path / "missing.csv",
                roster_path=fixture / "roster.csv",
            )
        except FileNotFoundError:
            failed_metadata_paths = list(
                (temporary_path / "failed_runs").glob("*/run_metadata.json")
            )
            assert len(failed_metadata_paths) == 1
            failed_metadata = json.loads(
                failed_metadata_paths[0].read_text(encoding="utf-8")
            )
            assert failed_metadata["status"] == "FAILED"
            assert failed_metadata["stage"] == "VALIDATE_INPUT"
            assert failed_metadata["error"]["type"] == "FileNotFoundError"
        else:
            raise AssertionError("Missing input must fail with run metadata")

        session = DemoSession(temporary_path / "demo_sessions")
        bootstrap = session.last_payload
        assert bootstrap["current"]["snapshot_id"] == "DEMO-S001"
        assert bootstrap["current"]["summary"]["active_jobs"] == 3
        assert bootstrap["current"]["summary"]["assignments"] == 3
        assert sum(
            item["work_status"] == "IDLE"
            for item in bootstrap["current"]["technicians"]
        ) == 1

        added = session.add_checklist(
            {
                "task_group": "maintenance",
                "ward": "Tây",
                "mode": "task_location",
            }
        )
        assert added["current"]["snapshot_id"] == "DEMO-S002"
        assert added["event"]["type"] == "CHECKLIST_ADDED"
        assert added["current"]["summary"]["active_jobs"] == 4
        assert added["current"]["summary"]["assignments"] == 4
        assert added["changes"][0]["change_type"] == "ADDED"
        assert added["assignment_delta"][0]["current"] == "DEMO.KTV04"
        assert added["operational"]["previous_checkpoint_id"] == (
            bootstrap["operational"]["checkpoint_id"]
        )

        queued = session.add_checklist(
            {
                "task_group": "random",
                "ward": "random",
                "mode": "task_location",
            }
        )
        assert queued["current"]["snapshot_id"] == "DEMO-S003"
        assert queued["current"]["summary"]["unassigned"] == 0
        assert queued["current"]["summary"]["queued_jobs"] == 5
        assert max(
            item["planned_job_count"]
            for item in queued["current"]["technicians"]
        ) == 2

        completed_id = queued["current"]["routes"][0]["stops"][0][
            "checklist_id"
        ]
        completed = session.complete_checklist(
            {"checklist_id": completed_id, "mode": "task_location"}
        )
        assert completed["current"]["snapshot_id"] == "DEMO-S004"
        assert completed["event"]["type"] == "CHECKLIST_COMPLETED"
        assert completed["current"]["summary"]["completed_jobs"] == 1
        assert completed["current"]["summary"]["unassigned"] == 0
        completion_metrics = session.current_run.metadata["counts"][
            "evaluation_metrics"
        ]
        assert completion_metrics["completed_jobs_in_shift"] == 1
        assert (
            completion_metrics["completed_sla_on_time_rate_percent"]
            == 100.0
        )
        assert any(
            item["checklist_id"] == completed_id
            and item["change_type"] == "COMPLETED"
            for item in completed["changes"]
        )
        completed_output = session.current_run.output_dir
        assert (completed_output / "completed_jobs.csv").is_file()
        completed_csv = (completed_output / "completed_jobs.csv").read_text(
            encoding="utf-8-sig"
        )
        assert completed_id in completed_csv
        latest = json.loads(
            Path(completed["operational"]["latest_pointer_path"]).read_text(
                encoding="utf-8"
            )
        )
        assert latest["snapshot_id"] == "DEMO-S004"
        assert len(completed["history"]) == 4

        reopened = session.update_checklist(
            {
                "event_type": "reopen",
                "checklist_id": completed_id,
                "mode": "task_location",
            }
        )
        assert reopened["event"]["type"] == "CHECKLIST_REOPENED"
        assert reopened["current"]["summary"]["active_jobs"] == 5
        assert reopened["current"]["summary"]["unassigned"] == 0

        technician_added = session.add_technician(
            {
                "technician_id": "DEMO.KTV05",
                "ward": "Bắc",
                "mode": "task_location",
            }
        )
        assert technician_added["event"]["type"] == "TECHNICIAN_ADDED"
        assert len(technician_added["current"]["technicians"]) == 5
        roster = Path(session.roster_path).read_text(encoding="utf-8-sig")
        assert "DEMO.KTV05" in roster

        assigned = session.update_checklist(
            {
                "event_type": "assign",
                "checklist_id": completed_id,
                "technician_id": "DEMO.KTV05",
                "mode": "task_location",
            }
        )
        assigned_job = next(
            item
            for item in assigned["current"]["jobs"]
            if item["checklist_id"] == completed_id
        )
        assert assigned_job["status"] == "Đã phân công"
        assert assigned_job["planned_technician"] == "DEMO.KTV05"

        started = session.update_checklist(
            {
                "event_type": "start",
                "checklist_id": completed_id,
                "mode": "task_location",
            }
        )
        started_technician = next(
            item
            for item in started["current"]["technicians"]
            if item["technician_id"] == "DEMO.KTV05"
        )
        assert started_technician["work_status"] == "BUSY"
        assert started_technician["current_job_id"] == completed_id

        paused = session.update_checklist(
            {
                "event_type": "pause",
                "checklist_id": completed_id,
                "mode": "task_location",
            }
        )
        paused_technician = next(
            item
            for item in paused["current"]["technicians"]
            if item["technician_id"] == "DEMO.KTV05"
        )
        assert paused_technician["work_status"] == "RESERVED"

        followed = session.update_checklist(
            {
                "event_type": "follow_up",
                "checklist_id": completed_id,
                "mode": "task_location",
            }
        )
        assert followed["event"]["type"] == "CHECKLIST_FOLLOW_UP"

        finished_again = session.update_checklist(
            {
                "event_type": "complete",
                "checklist_id": completed_id,
                "mode": "task_location",
            }
        )
        assert finished_again["current"]["snapshot_id"] == "DEMO-S011"
        assert finished_again["event"]["type"] == "CHECKLIST_COMPLETED"
        assert len(finished_again["history"]) == 11

    print("Operational checkpoint + interactive demo assertions: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
