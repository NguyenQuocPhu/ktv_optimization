"""Cấu hình routing: thời gian làm, bảng chuyển job, tải mô hình thời gian."""

from __future__ import annotations

import json
from bisect import bisect_left
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from ktv_routing.contract import JobInput
from ktv_routing.rules import BusinessRules

DEFAULT_SERVICE_MINUTES: dict[str, float] = {
    "TRIỂN KHAI MỚI (NET, COMBO..)": 120,
    "BOX, CAM ONLY": 120,
    "SWAP": 60,
    "MAINTENANCE": 60,
    "VẬT LÝ": 60,
    "LOGIC": 60,
    "THU HỒI THIẾT BỊ": 15,
}
TIME_MODEL_FORMAT = "ktv-time-model/1"


@dataclass(frozen=True, slots=True)
class TransitionTable:
    """Phút từ lúc xong job trước tới lúc check-in job sau, học từ lịch sử KTV.

    Gồm cả di chuyển lẫn chờ khách, nghỉ trưa... Tra theo km chim bay giữa hai điểm
    và giờ rời điểm trước. Cột ``i`` ứng với ``km_edges[i-1] < km ≤ km_edges[i]``.
    """

    km_edges: tuple[float, ...]
    minutes: tuple[float, ...]
    minutes_by_hour: dict[int, tuple[float, ...]] = field(default_factory=dict)

    def leg_minutes(self, km: float, depart_at: datetime) -> float:
        row = self.minutes_by_hour.get(depart_at.hour, self.minutes)
        return row[bisect_left(self.km_edges, km)]


@dataclass(frozen=True, slots=True)
class RoutingConfig:
    average_speed_kmh: float = 30.0
    location_max_age_minutes: float = 240.0
    default_service_minutes: float = 60.0
    service_minutes_by_case_type: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_SERVICE_MINUTES)
    )
    service_minutes_by_emp: dict[str, dict[str, float]] = field(default_factory=dict)
    transition: TransitionTable | None = None
    rules: BusinessRules = field(default_factory=BusinessRules)

    def __post_init__(self) -> None:
        if self.average_speed_kmh <= 0:
            raise ValueError("average_speed_kmh phải > 0")

    def service_minutes(self, job: JobInput, emp_account: str | None = None) -> float:
        key = (job.case_type or "").strip().upper()
        personal = self.service_minutes_by_emp.get(emp_account or "")
        if personal and key in personal:
            return personal[key]
        return self.service_minutes_by_case_type.get(
            key, self.default_service_minutes
        )


def _minutes(value: object, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError(f"{path}: cần số phút ≥ 0")
    return float(value)


def _minutes_row(value: object, path: str, size: int) -> tuple[float, ...]:
    if not isinstance(value, list) or len(value) != size:
        raise ValueError(f"{path}: cần danh sách {size} số phút")
    return tuple(_minutes(item, f"{path}[{index}]") for index, item in enumerate(value))


def time_model_config(model: object, config: RoutingConfig | None = None) -> RoutingConfig:
    """Áp mô hình thời gian (dict JSON do ``research/time_model.py`` sinh) lên config.

    Bảng theo CASE_TYPE của mô hình ghi đè bảng mặc định; CASE_TYPE mô hình chưa
    học vẫn giữ số mặc định.
    """

    config = config or RoutingConfig()
    if not isinstance(model, dict) or model.get("format") != TIME_MODEL_FORMAT:
        raise ValueError(f"cần object JSON có format = {TIME_MODEL_FORMAT}")
    service = model.get("service_minutes")
    transition = model.get("transition_minutes")
    if not isinstance(service, dict) or not isinstance(transition, dict):
        raise ValueError("cần service_minutes và transition_minutes")

    by_case_type = {
        str(case_type).strip().upper(): _minutes(value, f"service_minutes.by_case_type.{case_type}")
        for case_type, value in (service.get("by_case_type") or {}).items()
    }
    by_emp: dict[str, dict[str, float]] = {}
    for account, values in (service.get("by_emp") or {}).items():
        if not isinstance(values, dict):
            raise ValueError(f"service_minutes.by_emp.{account}: cần object CASE_TYPE → phút")
        by_emp[str(account)] = {
            str(case_type).strip().upper(): _minutes(value, f"service_minutes.by_emp.{account}.{case_type}")
            for case_type, value in values.items()
        }

    edges_value = transition.get("km_edges")
    if not isinstance(edges_value, list) or not edges_value:
        raise ValueError("transition_minutes.km_edges: cần danh sách km tăng dần")
    edges = tuple(_minutes(value, "transition_minutes.km_edges") for value in edges_value)
    if any(later <= earlier for earlier, later in zip(edges, edges[1:])):
        raise ValueError("transition_minutes.km_edges: cần tăng dần")
    size = len(edges) + 1
    by_hour: dict[int, tuple[float, ...]] = {}
    for hour, row in (transition.get("by_hour") or {}).items():
        if not str(hour).isdigit() or not 0 <= int(hour) <= 23:
            raise ValueError(f"transition_minutes.by_hour.{hour}: giờ phải là 0..23")
        by_hour[int(hour)] = _minutes_row(row, f"transition_minutes.by_hour.{hour}", size)

    return replace(
        config,
        default_service_minutes=_minutes(service.get("default"), "service_minutes.default"),
        service_minutes_by_case_type={**config.service_minutes_by_case_type, **by_case_type},
        service_minutes_by_emp=by_emp,
        transition=TransitionTable(
            km_edges=edges,
            minutes=_minutes_row(transition.get("minutes"), "transition_minutes.minutes", size),
            minutes_by_hour=by_hour,
        ),
    )


def load_time_model(path: str | Path, config: RoutingConfig | None = None) -> RoutingConfig:
    try:
        with Path(path).open(encoding="utf-8") as handle:
            model = json.load(handle)
        return time_model_config(model, config)
    except ValueError as error:
        raise ValueError(f"{path}: {error}") from None
