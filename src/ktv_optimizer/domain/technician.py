"""Technician domain model."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Technician:
    account: str  # Account định danh KTV trong hệ thống.
    level: str | None = None  # Cấp độ nghiệp vụ của KTV.
    branch_name: str | None = None  # Chi nhánh KTV đang trực thuộc.
    historical_job_count: int = 0  # Số checklist KTV từng được giao.
    historical_visit_count: int = 0  # Số lượt check-in KTV từng thực hiện.
