"""Test bộ sinh message giả theo file API và bộ kiểm tra schema."""

from __future__ import annotations

import copy
import sys
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "simulator"), str(ROOT / "tests")]

from ktv_simulator.fake_worklist import GROUP_KEYS, generate, load_context, validate  # noqa: E402
from test_simulator import DAY, write_event_stream  # noqa: E402


class FakeWorklistTest(unittest.TestCase):
    def test_generated_messages_follow_api_schema(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = write_event_stream(root)
            ctx = load_context(events, root / "checkins.csv")
            messages, sources = generate(events, ctx, [date.fromisoformat(DAY)], seed=1)
            again, _ = generate(events, ctx, [date.fromisoformat(DAY)], seed=1)

        self.assertTrue(messages)
        self.assertEqual(messages, again)  # Cùng seed → cùng dữ liệu.
        self.assertEqual([error for message in messages for error in validate(message)], [])
        for message, source in zip(messages, sources):
            self.assertEqual(tuple(message["tasks"]), GROUP_KEYS)
            ids = [task["task_id"] for key in GROUP_KEYS for task in message["tasks"][key]]
            self.assertEqual(set(ids), set(source))
        self.assertEqual(messages[0]["trigger"], "DAY_START")

    def test_validate_reports_broken_fields(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = write_event_stream(root)
            ctx = load_context(events, root / "checkins.csv")
            messages, _ = generate(events, ctx, [date.fromisoformat(DAY)], seed=1)
        message = copy.deepcopy(next(m for m in messages if any(m["tasks"][key] for key in GROUP_KEYS)))
        key = next(key for key in GROUP_KEYS if message["tasks"][key])
        task = message["tasks"][key][0]
        task["latlng"] = "21.03;105.80"
        task["sla"]["priority_in_day"] = 9
        message["staff"]["staff_id"] = 324668
        del message["tasks"]["onsite"]

        paths = {path for path, _, _ in validate(message)}
        self.assertEqual(
            paths,
            {f"tasks.{key}[0].latlng", f"tasks.{key}[0].sla.priority_in_day", f"tasks.{key}[0].sla", "staff.staff_id", "tasks", "tasks.onsite"},
        )


if __name__ == "__main__":
    unittest.main()
