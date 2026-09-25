"""Bộ giải xếp thứ tự: QHĐ, tham lam, 2-opt."""

from __future__ import annotations

from datetime import datetime, timedelta

from ktv_routing.contract import GeoPoint, JobInput, SequenceSource, TechnicianInput
from ktv_routing.rules import SOFT_RULE_CODES
from ktv_routing.travel import TravelMatrix, distance_km
from ktv_routing.planner.config import RoutingConfig

_MAX_IMPROVE_JOBS = 40

_RULE = {code: index for index, code in enumerate(SOFT_RULE_CODES)}
_LATE_CHECKIN = _RULE["LATE_CHECKIN"]
_LATE_MINUTES = _RULE["LATE_MINUTES"]
_LATE_COMPLETION = _RULE["LATE_COMPLETION"]
_AFTER_SHIFT = _RULE["AFTER_SHIFT"]
_KM = _RULE["KM"]
_TRAVEL_MINUTES = _RULE["TRAVEL_MINUTES"]
_AREA_REENTRY = _RULE["AREA_REENTRY"]
_PRIORITY_DELAY = _RULE["PRIORITY_DELAY"]
_FINISH = _RULE["FINISH"]


class _Label:
    """Một tuyến dở dang: giờ xong job cuối, tổng chi phí từng tầng, nhãn trước và job vừa thêm."""

    __slots__ = ("finish", "costs", "parent", "job")

    def __init__(self, finish: float, costs: tuple[float, ...], parent: _Label | None, job: int | None) -> None:
        self.finish = finish
        self.costs = costs
        self.parent = parent
        self.job = job


class _Problem:
    """Bài xếp thứ tự của một KTV. Giờ tính bằng phút kể từ ``start_at``."""

    def __init__(
        self,
        technician: TechnicianInput,
        jobs: list[JobInput],
        start_at: datetime,
        start_location: GeoPoint | None,
        config: RoutingConfig,
        matrix: TravelMatrix,
    ) -> None:
        self.jobs = jobs
        self.config = config
        self.rules = config.rules
        self.matrix = matrix
        self.known_start = start_location is not None
        self.offset = 1 if self.known_start else 0
        self._base_datetime = start_at

        def minutes(value: datetime | None) -> float | None:
            return None if value is None else (value - start_at).total_seconds() / 60

        self.service = [config.service_minutes(job, technician.emp_account) for job in jobs]
        self.due = [minutes(job.due_at) for job in jobs]
        self.opens = [minutes(job.appointment_start) for job in jobs]
        self.complete_by = [minutes(job.complete_by) for job in jobs]
        self.shift_end = minutes(technician.shift_end)
        self.weight = [self.rules.priority_weight(job.priority) for job in jobs]
        self.area = [job.area for job in jobs]
        self.area_mask: dict[str, int] = {}
        for index, area in enumerate(self.area):
            if area is not None:
                self.area_mask[area] = self.area_mask.get(area, 0) | 1 << index
        points: list[GeoPoint] = (
            ([start_location] if self.known_start and start_location is not None else [])
            + [job.location for job in jobs if job.location is not None]
        )
        self.straight = (
            [[distance_km(first, second) for second in points] for first in points]
            if config.transition is not None and self.known_start
            else None
        )
        self.tier_terms = [
            [(_RULE[code], weight) for code, weight in tier.items() if weight and code != "FINISH"]
            for tier in self.rules.tiers
        ]
        self.finish_tier, self.finish_weight = next(
            ((index, tier["FINISH"]) for index, tier in enumerate(self.rules.tiers) if tier.get("FINISH")),
            (None, 0.0),
        )
        self.root = _Label(0.0, (0.0,) * len(self.tier_terms), None, None)

    def leg(self, here: int | None, job: int, clock: float) -> tuple[float, float]:
        """(km, phút) từ ``here`` (None = điểm xuất phát) tới ``job``, rời đi lúc ``clock``."""

        if here is None and not self.known_start:
            return 0.0, 0.0
        origin = 0 if here is None else self.offset + here
        target = self.offset + job
        km = self.matrix.km[origin][target]
        if self.straight is not None and self.config.transition is not None:
            depart = self._base_datetime + timedelta(minutes=clock)
            return km, self.config.transition.leg_minutes(self.straight[origin][target], depart)
        return km, self.matrix.minutes[origin][target]

    def step(self, mask: int, here: int | None, job: int, clock: float) -> tuple[float, float, float, float, float, list[float]]:
        """Đi từ ``here`` tới ``job``: (km, phút đi, giờ tới, giờ check-in, giờ xong, chi phí từng rule)."""

        km, travel = self.leg(here, job, clock)
        arrive = clock + travel
        opens = self.opens[job]
        checkin = opens if opens is not None and arrive < opens else arrive
        done = checkin + self.service[job]
        cost = [0.0] * len(SOFT_RULE_CODES)
        due = self.due[job]
        if due is not None and checkin > due:
            cost[_LATE_CHECKIN] = self.weight[job]
            cost[_LATE_MINUTES] = checkin - due
        limit = self.complete_by[job]
        if limit is not None and done > limit:
            cost[_LATE_COMPLETION] = 1.0
        if self.shift_end is not None and done > self.shift_end:
            cost[_AFTER_SHIFT] = 1.0
        cost[_KM] = km
        cost[_TRAVEL_MINUTES] = travel
        area = self.area[job]
        if area is not None and here is not None and self.area[here] != area and mask & self.area_mask[area]:
            cost[_AREA_REENTRY] = 1.0
        cost[_PRIORITY_DELAY] = self.weight[job] * checkin / 60
        return km, travel, arrive, checkin, done, cost

    def extend(self, label: _Label, mask: int, here: int | None, job: int) -> _Label:
        *_, done, cost = self.step(mask, here, job, label.finish)
        costs = tuple(
            total + sum(weight * cost[rule] for rule, weight in terms)
            for total, terms in zip(label.costs, self.tier_terms)
        )
        return _Label(done, costs, label, job)

    def key(self, label: _Label) -> tuple[float, ...]:
        if self.finish_tier is None:
            return label.costs
        return tuple(
            total + self.finish_weight * label.finish if index == self.finish_tier else total
            for index, total in enumerate(label.costs)
        )

    def keep(self, bucket: list[_Label], label: _Label) -> bool:
        """Thêm nhãn nếu không nhãn nào cùng trạng thái hơn nó ở mọi mặt; True nếu phải cắt bớt."""

        finish, costs = label.finish, label.costs
        for old in bucket:
            if old.finish <= finish and all(a <= b for a, b in zip(old.costs, costs)):
                return False
        bucket[:] = [
            old for old in bucket if not (finish <= old.finish and all(a <= b for a, b in zip(costs, old.costs)))
        ]
        bucket.append(label)
        if len(bucket) > self.rules.max_labels_per_state:
            bucket.sort(key=self.key)
            del bucket[self.rules.max_labels_per_state :]
            return True
        return False

    @staticmethod
    def allowed(before: list[int] | None, mask: int, job: int) -> bool:
        return before is None or before[job] & mask == before[job]

    def solve(self, before: list[int] | None) -> tuple[list[int], SequenceSource]:
        """QHĐ; ``before[k]`` là tập job (bitmask) phải làm trước job k."""

        n = len(self.jobs)
        if n == 0:
            return [], SequenceSource.OPTIMAL
        if n > self.rules.max_exact_jobs:
            return self.heuristic(before), SequenceSource.HEURISTIC
        table: list[list[list[_Label]] | None] = [None] * (1 << n)
        capped = False

        def bucket(mask: int, job: int) -> list[_Label]:
            row = table[mask]
            if row is None:
                row = table[mask] = [[] for _ in range(n)]
            return row[job]

        for job in range(n):
            if self.allowed(before, 0, job):
                capped |= self.keep(bucket(1 << job, job), self.extend(self.root, 0, None, job))
        full = (1 << n) - 1
        for mask in range(1, full):
            row = table[mask]
            if row is None:
                continue
            for here, labels in enumerate(row):
                for label in labels:
                    for job in range(n):
                        bit = 1 << job
                        if mask & bit or not self.allowed(before, mask, job):
                            continue
                        capped |= self.keep(bucket(mask | bit, job), self.extend(label, mask, here, job))
        finals = [label for labels in (table[full] or ()) for label in labels]
        if not finals:
            return self.heuristic(before), SequenceSource.HEURISTIC
        best: _Label | None = min(finals, key=self.key)
        order: list[int] = []
        while best is not None and best.job is not None:
            order.append(best.job)
            best = best.parent
        order.reverse()
        return order, SequenceSource.APPROXIMATE if capped else SequenceSource.OPTIMAL

    def heuristic(self, before: list[int] | None) -> list[int]:
        """Tham lam: mỗi bước chọn job làm khóa tăng ít nhất; sau đó cải thiện bằng 2-opt."""

        n = len(self.jobs)
        label, mask, here, order = self.root, 0, None, []
        while len(order) < n:
            best: tuple[tuple, _Label, int] | None = None
            for job in range(n):
                if mask >> job & 1 or not self.allowed(before, mask, job):
                    continue
                candidate = self.extend(label, mask, here, job)
                rank = (self.key(candidate), self.jobs[job].job_id)
                if best is None or rank < best[0]:
                    best = (rank, candidate, job)
            assert best is not None
            _, label, job = best
            order.append(job)
            mask |= 1 << job
            here = job
        return self.improve(order, before)

    def evaluate(self, order: list[int]) -> tuple[float, ...]:
        label, mask, here = self.root, 0, None
        for job in order:
            label = self.extend(label, mask, here, job)
            mask |= 1 << job
            here = job
        return self.key(label)

    def valid(self, order: list[int], before: list[int] | None) -> bool:
        mask = 0
        for job in order:
            if not self.allowed(before, mask, job):
                return False
            mask |= 1 << job
        return True

    def improve(self, order: list[int], before: list[int] | None) -> list[int]:
        """2-opt: đảo một đoạn nếu khóa nhỏ hơn và vẫn giữ thứ tự bắt buộc; tối đa 2 vòng."""

        n = len(order)
        if n < 3 or n > _MAX_IMPROVE_JOBS:
            return order
        best = self.evaluate(order)
        for _ in range(2):
            improved = False
            for i in range(n - 1):
                for j in range(i + 1, n):
                    candidate = order[:i] + order[i : j + 1][::-1] + order[j + 1 :]
                    if not self.valid(candidate, before):
                        continue
                    candidate_key = self.evaluate(candidate)
                    if candidate_key < best:
                        order, best, improved = candidate, candidate_key, True
            if not improved:
                break
        return order

    def walk(self, order: list[int]) -> tuple[list[tuple[int, float, float, float, float, float]], dict[str, float]]:
        """Tính lại từng điểm dừng và tổng chi phí từng rule của thứ tự đã chọn."""

        steps = []
        totals = [0.0] * len(SOFT_RULE_CODES)
        mask, here, clock = 0, None, 0.0
        for job in order:
            km, travel, arrive, checkin, done, cost = self.step(mask, here, job, clock)
            steps.append((job, km, travel, arrive, checkin, done))
            totals = [total + value for total, value in zip(totals, cost)]
            mask |= 1 << job
            here = job
            clock = done
        totals[_FINISH] = clock
        return steps, dict(zip(SOFT_RULE_CODES, totals))
