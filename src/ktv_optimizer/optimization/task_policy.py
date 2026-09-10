"""V0 task priorities derived from the supplied KPI/SLA workbook."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TaskPolicy:
    priority: int  # 1 là mức ưu tiên cao nhất.
    service_minutes: float  # Thời lượng onsite mặc định.
    sla_minutes: float | None  # Hạn xử lý từ lúc tạo, nếu định lượng được.
    onsite_required: bool = True  # False nếu tác vụ có thể xử lý từ xa.


# Runtime không đọc Excel. Các giá trị này là V0, có thể chuyển sang config/data
# source khi team data cung cấp CASE_TYPE chi tiết và policy chính thức.
DEFAULT_TASK_POLICIES: dict[str, TaskPolicy] = {
    "TRIỂN KHAI MỚI (NET, COMBO..)": TaskPolicy(
        priority=3,
        service_minutes=120,
        sla_minutes=20 * 60,
    ),
    "BOX, CAM ONLY": TaskPolicy(
        priority=3,
        service_minutes=120,
        sla_minutes=20 * 60,
    ),
    "SWAP": TaskPolicy(
        priority=3,
        service_minutes=60,
        sla_minutes=20 * 60,
    ),
    "MAINTENANCE": TaskPolicy(
        priority=2,
        service_minutes=60,
        sla_minutes=10 * 60,
    ),
    "VẬT LÝ": TaskPolicy(priority=1, service_minutes=60, sla_minutes=10 * 60),
    "LOGIC": TaskPolicy(priority=2, service_minutes=60, sla_minutes=10 * 60),
    "THU HỒI THIẾT BỊ": TaskPolicy(
        priority=4,
        service_minutes=15,
        sla_minutes=None,
    ),
    "NGƯNG KẾT NỐI 4H": TaskPolicy(
        priority=2,
        service_minutes=30,
        sla_minutes=30,
        onsite_required=False,
    ),
    "MẠNG CHẬP CHỜN, SUY HAO CAO": TaskPolicy(
        priority=4,
        service_minutes=30,
        sla_minutes=120,
        onsite_required=False,
    ),
}

DEFAULT_POLICY = TaskPolicy(
    priority=3,
    service_minutes=60,
    sla_minutes=None,
)


def get_task_policy(task_type: str | None) -> TaskPolicy:
    key = (task_type or "").strip().upper()
    return DEFAULT_TASK_POLICIES.get(key, DEFAULT_POLICY)
