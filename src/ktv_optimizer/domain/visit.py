"""Technician check-in/check-out domain models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import asin, cos, radians, sin, sqrt


@dataclass(frozen=True, slots=True)
class GeoPoint:
    latitude: float  # Vĩ độ GPS, đơn vị độ.
    longitude: float  # Kinh độ GPS, đơn vị độ.

    def is_in_vietnam_bounds(self) -> bool:
        """Cheap data-quality check; this is not a territory polygon test."""

        return 8 <= self.latitude <= 24 and 102 <= self.longitude <= 110

    def distance_km_to(self, other: "GeoPoint") -> float:
        """Great-circle distance between two coordinates."""

        radius_km = 6371.0088
        lat1, lat2 = radians(self.latitude), radians(other.latitude)
        delta_lat = lat2 - lat1
        delta_lng = radians(other.longitude - self.longitude)
        value = (
            sin(delta_lat / 2) ** 2
            + cos(lat1) * cos(lat2) * sin(delta_lng / 2) ** 2
        )
        return radius_km * 2 * asin(sqrt(value))


@dataclass(frozen=True, slots=True)
class Visit:
    checklist_id: str  # Checklist mà lượt ghé này phục vụ.
    technician_code: str | None  # Mã nhân viên thực hiện check-in.
    checkin_at: datetime | None  # Thời điểm KTV bắt đầu lượt làm việc.
    checkout_at: datetime | None  # Thời điểm KTV kết thúc lượt làm việc.
    checkin_location: GeoPoint | None  # GPS tại lúc check-in.
    checkout_location: GeoPoint | None  # GPS tại lúc checkout.

    @property
    def service_minutes(self) -> float | None:
        if self.checkin_at is None or self.checkout_at is None:
            return None
        return (self.checkout_at - self.checkin_at).total_seconds() / 60

    @property
    def movement_km(self) -> float | None:
        if self.checkin_location is None or self.checkout_location is None:
            return None
        return self.checkin_location.distance_km_to(self.checkout_location)
