"""Map OBJ_LOCATION text to a representative ward/commune coordinate."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from ktv_optimizer.domain import GeoPoint


def normalize_vietnamese_text(value: str) -> str:
    """Lowercase, remove accents/punctuation and normalize spaces."""

    value = value.replace("Đ", "D").replace("đ", "d")
    value = "".join(
        character
        for character in unicodedata.normalize("NFD", value)
        if unicodedata.category(character) != "Mn"
    )
    value = re.sub(r"[^a-zA-Z0-9]+", " ", value.lower())
    return re.sub(r"\s+", " ", value).strip()


@dataclass(frozen=True, slots=True)
class WardBoundary:
    ward_code: str  # Mã phường/xã trong GeoJSON.
    ward_name: str  # Tên phường/xã có dấu.
    province_name: str  # Tỉnh/thành sau chuẩn hóa hành chính.
    point: GeoPoint  # Centroid đại diện của polygon.
    normalized_ward: str  # Tên phường/xã không dấu để search.
    normalized_province: str  # Tỉnh/thành không dấu để search.


@dataclass(frozen=True, slots=True)
class LocationMatch:
    address: str  # OBJ_LOCATION đầu vào.
    ward_code: str  # Mã phường/xã match được.
    ward_name: str  # Tên phường/xã match được.
    province_name: str  # Tỉnh/thành của phường/xã.
    point: GeoPoint  # Tọa độ đại diện trả về.
    confidence: float  # Điểm tin cậy heuristic từ 0 đến 1.
    method: str  # exact_substring hoặc fuzzy_segment.


def _ring_centroid(ring: list[list[float]]) -> tuple[float, float, float]:
    """Return longitude, latitude and absolute planar area of a ring."""

    if len(ring) < 3:
        longitude = sum(point[0] for point in ring) / max(1, len(ring))
        latitude = sum(point[1] for point in ring) / max(1, len(ring))
        return longitude, latitude, 0.0

    points = ring if ring[0] == ring[-1] else [*ring, ring[0]]
    area_twice = 0.0
    centroid_x = 0.0
    centroid_y = 0.0
    for first, second in zip(points, points[1:]):
        cross = first[0] * second[1] - second[0] * first[1]
        area_twice += cross
        centroid_x += (first[0] + second[0]) * cross
        centroid_y += (first[1] + second[1]) * cross

    if abs(area_twice) < 1e-12:
        longitude = sum(point[0] for point in ring) / len(ring)
        latitude = sum(point[1] for point in ring) / len(ring)
        return longitude, latitude, 0.0
    return (
        centroid_x / (3 * area_twice),
        centroid_y / (3 * area_twice),
        abs(area_twice) / 2,
    )


def _geometry_centroid(geometry: dict[str, Any]) -> GeoPoint:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates", [])
    if geometry_type == "Polygon":
        exterior_rings = [coordinates[0]] if coordinates else []
    elif geometry_type == "MultiPolygon":
        exterior_rings = [
            polygon[0] for polygon in coordinates if polygon
        ]
    else:
        raise ValueError(f"Unsupported geometry type: {geometry_type}")

    weighted_longitude = 0.0
    weighted_latitude = 0.0
    total_area = 0.0
    fallback_points: list[tuple[float, float]] = []
    for ring in exterior_rings:
        longitude, latitude, area = _ring_centroid(ring)
        fallback_points.append((longitude, latitude))
        weighted_longitude += longitude * area
        weighted_latitude += latitude * area
        total_area += area

    if total_area > 0:
        return GeoPoint(
            latitude=weighted_latitude / total_area,
            longitude=weighted_longitude / total_area,
        )
    if fallback_points:
        return GeoPoint(
            latitude=sum(point[1] for point in fallback_points)
            / len(fallback_points),
            longitude=sum(point[0] for point in fallback_points)
            / len(fallback_points),
        )
    raise ValueError("Geometry contains no polygon coordinates")


def _address_segments(address: str) -> list[str]:
    prefixes = (
        "xa ",
        "phuong ",
        "thi tran ",
        "quan ",
        "huyen ",
        "tinh ",
        "thanh pho ",
        "tp ",
    )
    segments: list[str] = []
    for segment in address.split(","):
        cleaned = normalize_vietnamese_text(segment)
        for prefix in prefixes:
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix) :]
                break
        if cleaned:
            segments.append(cleaned)
    return segments


def _ward_level_segments(address: str) -> list[str]:
    """Extract segments explicitly labelled xã/phường/thị trấn."""

    prefixes = ("xa ", "phuong ", "thi tran ")
    segments: list[str] = []
    for segment in address.split(","):
        normalized = normalize_vietnamese_text(segment)
        for prefix in prefixes:
            if normalized.startswith(prefix):
                value = normalized[len(prefix) :].strip()
                if value:
                    segments.append(value)
                break
    return segments


class WardBoundaryIndex:
    """In-memory index over ward polygons and representative centroids."""

    def __init__(self, boundaries: list[WardBoundary]) -> None:
        self.boundaries = tuple(boundaries)
        self._by_ward: dict[str, tuple[WardBoundary, ...]] = {}
        ward_groups: dict[str, list[WardBoundary]] = {}
        province_groups: dict[str, list[WardBoundary]] = {}
        for boundary in boundaries:
            ward_groups.setdefault(
                boundary.normalized_ward, []
            ).append(boundary)
            province_groups.setdefault(
                boundary.normalized_province, []
            ).append(boundary)
        self._by_ward = {
            name: tuple(values) for name, values in ward_groups.items()
        }
        self._by_province = {
            name: tuple(values) for name, values in province_groups.items()
        }
        self._match_cache: dict[
            tuple[str, float], LocationMatch | None
        ] = {}
        self._province_names = sorted(
            {item.normalized_province for item in boundaries},
            key=len,
            reverse=True,
        )

    @classmethod
    def from_geojson(cls, path: str | Path) -> "WardBoundaryIndex":
        with Path(path).open(encoding="utf-8") as source:
            collection = json.load(source)

        boundaries: list[WardBoundary] = []
        for feature in collection.get("features", []):
            properties = feature.get("properties", {})
            ward_name = str(properties.get("ten_xa", "")).strip()
            province_name = str(properties.get("tinh_tp", "")).strip()
            ward_code = str(properties.get("ma_xa", "")).strip()
            geometry = feature.get("geometry")
            if not ward_name or not province_name or not geometry:
                continue
            boundaries.append(
                WardBoundary(
                    ward_code=ward_code,
                    ward_name=ward_name,
                    province_name=province_name,
                    point=_geometry_centroid(geometry),
                    normalized_ward=normalize_vietnamese_text(ward_name),
                    normalized_province=normalize_vietnamese_text(province_name),
                )
            )
        return cls(boundaries)

    def match(
        self,
        address: str | None,
        *,
        fuzzy_threshold: float = 0.86,
    ) -> LocationMatch | None:
        cache_key = (address or "", fuzzy_threshold)
        if cache_key in self._match_cache:
            return self._match_cache[cache_key]
        result = self._match_uncached(
            address,
            fuzzy_threshold=fuzzy_threshold,
        )
        self._match_cache[cache_key] = result
        return result

    def _match_uncached(
        self,
        address: str | None,
        *,
        fuzzy_threshold: float,
    ) -> LocationMatch | None:
        if not address or not address.strip():
            return None

        normalized_address = normalize_vietnamese_text(address)
        padded_address = f" {normalized_address} "
        segments = _address_segments(address)
        ward_level_segments = _ward_level_segments(address)
        province_in_address = next(
            (
                province
                for province in self._province_names
                if f" {province} " in padded_address
            ),
            None,
        )

        preferred_exact = [
            boundary
            for segment in ward_level_segments
            for boundary in self._by_ward.get(segment, ())
        ]
        exact = preferred_exact or [
            boundary
            for segment in segments
            for boundary in self._by_ward.get(segment, ())
        ]
        if exact:
            exact.sort(
                key=lambda boundary: (
                    boundary.normalized_province == province_in_address,
                    len(boundary.normalized_ward),
                ),
                reverse=True,
            )
            selected = exact[0]
            province_bonus = (
                0.25
                if province_in_address
                and selected.normalized_province == province_in_address
                else 0.0
            )
            return LocationMatch(
                address=address,
                ward_code=selected.ward_code,
                ward_name=selected.ward_name,
                province_name=selected.province_name,
                point=selected.point,
                confidence=min(
                    1.0,
                    0.70
                    + province_bonus
                    + min(len(selected.normalized_ward), 20) / 400,
                ),
                method="exact_substring",
            )

        candidates = (
            self._by_province.get(province_in_address, ())
            if province_in_address
            else self.boundaries
        )
        best_boundary: WardBoundary | None = None
        best_ratio = 0.0
        for boundary in candidates:
            ratio = max(
                (
                    SequenceMatcher(
                        None, segment, boundary.normalized_ward
                    ).ratio()
                    for segment in segments
                ),
                default=0.0,
            )
            if ratio > best_ratio:
                best_boundary = boundary
                best_ratio = ratio

        if best_boundary is None or best_ratio < fuzzy_threshold:
            return None
        return LocationMatch(
            address=address,
            ward_code=best_boundary.ward_code,
            ward_name=best_boundary.ward_name,
            province_name=best_boundary.province_name,
            point=best_boundary.point,
            confidence=best_ratio,
            method="fuzzy_segment",
        )
