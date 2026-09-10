"""Maintenance checklist domain model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Job:
    """One canonical maintenance checklist.

    The source CSV can contain more than one row for a checklist when
    ``SERVICES_LIST`` differs. The data mapper collapses those rows before
    constructing this object.
    """

    checklist_id: str  # ID duy nhất của checklist bảo trì.
    object_id: str | None  # Mã hợp đồng/đối tượng cần bảo trì.
    branch_name: str | None  # Chi nhánh đang quản lý checklist.
    status: str | None  # Trạng thái xử lý hiện tại của checklist.
    created_at: datetime | None  # Thời điểm checklist được tạo.
    finished_at: datetime | None  # Thời điểm hoàn tất; None nếu chưa xong.
    services: tuple[str, ...]  # Các dịch vụ cần kiểm tra/bảo trì.
    object_location: str | None  # Địa chỉ lắp đặt của khách hàng.

    case_type: str | None = None  # Loại ca vụ, thường là MAINTENANCE.
    customer_type: str | None = None  # Phân loại khách hàng cấp 1 (cá nhân, tổ chức).
    vip_type: str | None = None  # Mã phân loại khách hàng VIP.
    on_time_flag: str | None = None  # YES/NO/NA/INPROCESS theo SLA.
    discussion_count: int = 0  # Số lần checklist bị giục.
    sos_discussion_count: int = 0  # Số lần bị giục ở mức SOS.
    appointment_count: int = 0  # Số lần hẹn xử lý với khách hàng.
    technician_account: str | None = None  # Account KTV được gán.
    technician_level: str | None = None  # Cấp độ của KTV được gán.
    reassignment_count: int = 0  # Số lần thay đổi KTV xử lý.

    @property
    def is_finished(self) -> bool:
        return self.finished_at is not None #Cần sửa điều kiện is_finished

    @property
    def has_assignment(self) -> bool:
        return bool(self.technician_account)

    @property
    def elapsed_minutes(self) -> float | None:
        """Calendar elapsed time, not technician service duration."""

        if self.created_at is None or self.finished_at is None:
            return None
        return (self.finished_at - self.created_at).total_seconds() / 60
