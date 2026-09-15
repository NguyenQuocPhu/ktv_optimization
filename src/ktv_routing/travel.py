"""Khoảng cách và thời gian di chuyển giữa các điểm: chim bay hoặc đường bộ OSRM.

Planner hỏi một lần cho tất cả KTV. Mỗi KTV là một nhóm điểm (điểm xuất phát +
các job chờ); kết quả là ma trận km và phút cho từng nhóm. ``OsrmTravel`` gọi
``/table`` riêng cho từng KTV và song song (OSRM tự host), hoặc gom nhiều KTV vào
một lần gọi khi server giới hạn tốc độ (OSRM public).
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .contract import GeoPoint

PUBLIC_OSRM_URL = "https://router.project-osrm.org"


def distance_km(first: GeoPoint, second: GeoPoint) -> float:
    """Khoảng cách Haversine (chim bay) giữa hai tọa độ."""

    lat1, lat2 = radians(first.lat), radians(second.lat)
    value = (
        sin((lat2 - lat1) / 2) ** 2
        + cos(lat1) * cos(lat2) * sin(radians(second.lng - first.lng) / 2) ** 2
    )
    return 6371.0088 * 2 * asin(sqrt(value))


@dataclass(frozen=True, slots=True)
class TravelMatrix:
    km: tuple[tuple[float, ...], ...]  # km[i][j]: từ điểm i tới điểm j.
    minutes: tuple[tuple[float, ...], ...]  # Phút di chuyển tương ứng.
    source: str  # OSRM (đường bộ) hoặc HAVERSINE (chim bay).
    note: str | None = None  # Lý do phải dùng chim bay cho toàn bộ hoặc một phần.


class TravelModel(Protocol):
    def matrices(self, groups: Sequence[Sequence[GeoPoint]]) -> list[TravelMatrix]: ...


@dataclass(frozen=True, slots=True)
class HaversineTravel:
    """Chim bay với tốc độ cố định; không cần mạng."""

    average_speed_kmh: float = 30.0

    def minutes(self, km: float) -> float:
        return km / self.average_speed_kmh * 60

    def matrix(self, points: Sequence[GeoPoint], note: str | None = None) -> TravelMatrix:
        km = tuple(tuple(distance_km(first, second) for second in points) for first in points)
        minutes = tuple(tuple(self.minutes(value) for value in row) for row in km)
        return TravelMatrix(km, minutes, "HAVERSINE", note)

    def matrices(self, groups: Sequence[Sequence[GeoPoint]]) -> list[TravelMatrix]:
        return [self.matrix(group) for group in groups]


class OsrmError(RuntimeError):
    """OSRM không trả được ma trận: lỗi mạng, quá tải, quá nhiều điểm..."""


class OsrmTravel:
    """Km và phút đường bộ từ OSRM ``/table``; lỗi thì dùng chim bay kèm ``note``.

    - Tọa độ trùng nhau chỉ gửi một lần.
    - OSRM tự host (mặc định ``parallel_requests=16``): mỗi KTV một request nhỏ,
      chạy song song. Bảng ``/table`` tốn gần theo bình phương số điểm, nên
      nhiều bảng 5 điểm nhanh hơn hẳn một bảng 100 điểm; KTV nào lỗi thì chỉ KTV
      đó dùng chim bay.
    - OSRM public (khoảng 1 request/giây, tối đa 100 điểm): ``parallel_requests=1``,
      gom nhiều KTV chung một request miễn tổng số điểm khác nhau không vượt
      ``max_locations``, và chờ giữa các lần gọi.
    - Thời gian là của profile ô tô trên đường trống, chưa tính kẹt xe.
    """

    def __init__(
        self,
        base_url: str = PUBLIC_OSRM_URL,
        *,
        profile: str = "driving",
        max_locations: int = 100,
        timeout_seconds: float = 30.0,
        min_interval_seconds: float | None = None,
        parallel_requests: int | None = None,
        fallback: HaversineTravel | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.profile = profile
        self.max_locations = max_locations
        self.timeout_seconds = timeout_seconds
        if min_interval_seconds is None:
            min_interval_seconds = 1.0 if self.base_url == PUBLIC_OSRM_URL else 0.0
        self.min_interval_seconds = min_interval_seconds
        if parallel_requests is None:
            parallel_requests = 1 if min_interval_seconds > 0 else 16
        if parallel_requests < 1:
            raise ValueError("parallel_requests phải ≥ 1")
        self.parallel_requests = parallel_requests
        self.fallback = fallback or HaversineTravel()
        self.calls = 0  # Số request đã gửi, để theo dõi.
        self._count_lock = threading.Lock()
        self._throttle = threading.Lock()  # Chỉ dùng khi phải chờ giữa các lần gọi.
        self._last_call = float("-inf")

    def matrices(self, groups: Sequence[Sequence[GeoPoint]]) -> list[TravelMatrix]:
        results: list[TravelMatrix | None] = [None] * len(groups)
        batches: list[list[int]] = []
        batch: list[int] = []
        batch_points: set[GeoPoint] = set()
        for index, group in enumerate(groups):
            unique = set(group)
            if len(unique) < 2:
                # Không có đoạn đường nào phải đi: khỏi gọi OSRM.
                zeros = tuple(tuple(0.0 for _ in group) for _ in group)
                results[index] = TravelMatrix(zeros, zeros, "OSRM")
            elif len(unique) > self.max_locations:
                results[index] = self.fallback.matrix(
                    group,
                    note=f"{len(unique)} điểm, vượt giới hạn {self.max_locations} điểm mỗi lần gọi OSRM",
                )
            elif self.parallel_requests > 1:
                batches.append([index])  # Mỗi KTV một request.
            else:
                if batch and len(batch_points | unique) > self.max_locations:
                    batches.append(batch)
                    batch, batch_points = [], set()
                batch.append(index)
                batch_points |= unique
        if batch:
            batches.append(batch)

        workers = min(self.parallel_requests, len(batches))
        if workers <= 1:
            solved = [self._solve(groups, batch) for batch in batches]
        else:
            with ThreadPoolExecutor(workers, thread_name_prefix="osrm") as pool:
                solved = list(pool.map(lambda batch: self._solve(groups, batch), batches))
        for batch, matrices in zip(batches, solved):
            for index, matrix in zip(batch, matrices):
                results[index] = matrix
        return results

    def _solve(self, groups: Sequence[Sequence[GeoPoint]], batch: list[int]) -> list[TravelMatrix]:
        """Một request ``/table`` cho các nhóm trong ``batch``, cắt ra ma trận từng nhóm."""

        points = list(dict.fromkeys(point for index in batch for point in groups[index]))
        try:
            distances, durations = self._table(points)
        except OsrmError as error:
            return [self.fallback.matrix(groups[index], note=str(error)) for index in batch]
        position = {point: order for order, point in enumerate(points)}
        return [self._slice(groups[index], position, distances, durations) for index in batch]

    def _slice(
        self,
        group: Sequence[GeoPoint],
        position: dict[GeoPoint, int],
        distances: list[list[float | None]],
        durations: list[list[float | None]],
    ) -> TravelMatrix:
        km_rows: list[tuple[float, ...]] = []
        minute_rows: list[tuple[float, ...]] = []
        missing = 0
        for first in group:
            km_row: list[float] = []
            minute_row: list[float] = []
            for second in group:
                meters = distances[position[first]][position[second]]
                seconds = durations[position[first]][position[second]]
                if meters is None or seconds is None:  # OSRM không tìm được đường.
                    missing += 1
                    km = distance_km(first, second)
                    km_row.append(km)
                    minute_row.append(self.fallback.minutes(km))
                else:
                    km_row.append(meters / 1000)
                    minute_row.append(seconds / 60)
            km_rows.append(tuple(km_row))
            minute_rows.append(tuple(minute_row))
        note = f"{missing} đoạn không có đường bộ trên OSRM, dùng chim bay" if missing else None
        return TravelMatrix(tuple(km_rows), tuple(minute_rows), "OSRM", note)

    def _table(self, points: list[GeoPoint]) -> tuple[list[list[float | None]], list[list[float | None]]]:
        coordinates = ";".join(f"{point.lng:.6f},{point.lat:.6f}" for point in points)
        url = f"{self.base_url}/table/v1/{self.profile}/{coordinates}?annotations=distance,duration"
        request = Request(url, headers={"User-Agent": "ktv-routing/0.2"})
        with self._count_lock:
            self.calls += 1
        # Server giới hạn tốc độ: xếp hàng từng request. Tự host: gọi song song tự do.
        with self._throttle if self.min_interval_seconds > 0 else nullcontext():
            wait = self._last_call + self.min_interval_seconds - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    body = json.load(response)
            except HTTPError as error:
                detail = error.read(200).decode("utf-8", "replace")
                raise OsrmError(f"OSRM HTTP {error.code}: {detail}") from None
            except (URLError, OSError, ValueError) as error:
                raise OsrmError(f"Không gọi được OSRM: {error}") from None
            finally:
                self._last_call = time.monotonic()
        distances, durations = body.get("distances"), body.get("durations")
        if body.get("code") != "Ok" or not distances or not durations or len(distances) != len(points):
            raise OsrmError(f"OSRM trả kết quả không hợp lệ: code={body.get('code')} {body.get('message', '')}".strip())
        return distances, durations


def travel_model(
    kind: str,
    *,
    osrm_url: str = PUBLIC_OSRM_URL,
    average_speed_kmh: float = 30.0,
    osrm_max_locations: int = 100,
    osrm_parallel: int | None = None,
) -> TravelModel:
    """Tạo TravelModel theo tham số CLI: ``osrm`` hoặc ``haversine``.

    ``osrm_max_locations`` phải ≤ ``--max-table-size`` của server OSRM (public: 100).
    ``osrm_parallel``: số request song song; bỏ trống thì public 1 (gom KTV), tự host 16.
    """

    fallback = HaversineTravel(average_speed_kmh)
    if kind == "haversine":
        return fallback
    if kind == "osrm":
        return OsrmTravel(
            osrm_url,
            max_locations=osrm_max_locations,
            parallel_requests=osrm_parallel,
            fallback=fallback,
        )
    raise ValueError(f"Kiểu tính km không hợp lệ: {kind}")
