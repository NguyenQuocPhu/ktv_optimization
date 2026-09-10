"""Consistent offline state snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .job import Job
from .technician import Technician
from .visit import Visit


@dataclass(frozen=True, slots=True)
class SystemState:
    """Minimal snapshot shared by later optimization modules."""

    timestamp: datetime  # Thời điểm chụp snapshot trạng thái.
    jobs: tuple[Job, ...]  # Các checklist có trong snapshot.
    technicians: tuple[Technician, ...]  # Các KTV có trong snapshot.
    visits: tuple[Visit, ...] = ()  # Các lượt thực hiện đã biết tại snapshot.

    @property
    def active_jobs(self) -> tuple[Job, ...]:
        return tuple(job for job in self.jobs if not job.is_finished)

    @property
    def unassigned_jobs(self) -> tuple[Job, ...]:
        return tuple(job for job in self.active_jobs if not job.has_assignment)
