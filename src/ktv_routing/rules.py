"""Rule nghiệp vụ dùng để chọn thứ tự làm việc cho KTV. Đọc file này là biết routing ưu tiên gì.

Nguồn: file nghiệp vụ "Mô tả loại tác vụ - ưu tiên để Gợi ý công việc" (sheet "#Bảng KPI, SLA
Tác vụ", "1. Mục tiêu & Data đầu vào", "2. Rule nghiệp vụ", "6. Tiêu chí đánh giá AI"). Mô tả
đầy đủ, ví dụ và các điểm chờ xác nhận nằm ở ``docs/BUSINESS_RULES.md``.

Cách so hai thứ tự làm việc:
- Rule cứng: thứ tự vi phạm không được xét.
- Rule mềm: mỗi rule cho một con số chi phí, càng nhỏ càng tốt. Rule xếp vào các tầng: tầng
  trên quyết định trước, tầng dưới chỉ phân xử khi các tầng trên bằng nhau. Trong một tầng,
  chi phí được cộng có trọng số.

Muốn đổi thứ tự tầng hoặc trọng số mà không sửa code: ``python -m ktv_routing --print-rules``
ra JSON, sửa file đó, rồi chạy với ``--rules file.json``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

RULES_FORMAT = "ktv-rules/1"


@dataclass(frozen=True, slots=True)
class RuleInfo:
    code: str
    name: str
    unit: str
    source: str  # Chỗ tương ứng trong file nghiệp vụ.
    description: str


# Rule mềm: với mỗi thứ tự, routing cộng các chi phí này theo từng điểm dừng.
SOFT_RULES: tuple[RuleInfo, ...] = (
    RuleInfo(
        "LATE_CHECKIN",
        "Check-in trễ hẹn",
        "điểm",
        "Rule 1; Bảng KPI/SLA: check in trước mốc hẹn cuối (B)",
        "Mỗi job check-in sau due_at cộng trọng số theo ưu tiên trong ngày của job (priority_weights).",
    ),
    RuleInfo(
        "LATE_COMPLETION",
        "Hoàn tất quá hạn",
        "job",
        "Bảng KPI/SLA: hoàn tất trong ngày hẹn / ngày tạo phiếu / trong tháng",
        "Mỗi job làm xong sau complete_by cộng 1.",
    ),
    RuleInfo(
        "AFTER_SHIFT",
        "Xong sau giờ hết ca",
        "job",
        "Mục tiêu 5: tăng số công việc hoàn thành trong ngày",
        "Mỗi job làm xong sau shift_end cộng 1.",
    ),
    RuleInfo(
        "LATE_MINUTES",
        "Số phút check-in trễ",
        "phút",
        "Rule 1: không để trễ KPI, SLA",
        "Tổng số phút check-in sau due_at.",
    ),
    RuleInfo(
        "KM",
        "Quãng đường",
        "km",
        "Mục tiêu 2; tiêu chí: tổng quãng đường thấp nhất",
        "Tổng km giữa các điểm theo nguồn km (OSRM đường bộ hoặc chim bay).",
    ),
    RuleInfo(
        "TRAVEL_MINUTES",
        "Thời gian di chuyển",
        "phút",
        "Mục tiêu 4; tiêu chí: tổng thời gian di chuyển thấp nhất",
        "Tổng số phút từ lúc xong điểm trước tới lúc tới điểm sau.",
    ),
    RuleInfo(
        "AREA_REENTRY",
        "Quay lại khu vực đã rời",
        "lần",
        "Rule 2: hạn chế quay đầu, hạn chế vào một khu vực nhiều lần",
        "Cộng 1 khi tới một job thuộc khu vực đã làm trước đó nhưng vừa rời sang khu vực khác (cần area).",
    ),
    RuleInfo(
        "PRIORITY_DELAY",
        "Job ưu tiên cao bị để muộn",
        "điểm × giờ",
        "Bảng KPI/SLA: ưu tiên trong ngày",
        "Cộng trọng số ưu tiên × số giờ từ lúc xuất phát tới lúc check-in job đó.",
    ),
    RuleInfo(
        "FINISH",
        "Giờ xong job cuối",
        "phút",
        "Tiêu chí: thời gian chờ giữa các công việc thấp nhất; số việc hoàn thành trong ca cao nhất",
        "Số phút từ lúc xuất phát tới lúc xong job cuối, gồm cả thời gian chờ.",
    ),
)
SOFT_RULE_CODES = tuple(rule.code for rule in SOFT_RULES)

# Rule cứng và rule tính giờ: không cộng chi phí, quyết định thứ tự nào hợp lệ và giờ tính ra sao.
HARD_RULES: tuple[RuleInfo, ...] = (
    RuleInfo(
        "KEEP_PREVIOUS_ORDER",
        "Giữ thứ tự tuyến cũ",
        "",
        "Rule 4; 1. Mục tiêu dòng 7: chỉ xây routing thêm, không phá",
        "Khi request có previous_sequence: job cũ giữ nguyên thứ tự, job mới chèn vào chỗ tốt nhất. "
        "Có đổi sang thứ tự tự do hay không tùy previous_route_policy.",
    ),
    RuleInfo(
        "WAIT_FOR_APPOINTMENT",
        "Không check-in trước mốc hẹn đầu",
        "",
        "Bảng KPI/SLA: SLA mốc hẹn (A→B)",
        "Tới trước appointment_start thì chờ tới mốc đó mới check-in; thứ tự vẫn hợp lệ.",
    ),
)

PREVIOUS_ROUTE_POLICIES = {
    "IF_BETTER": "Giữ thứ tự tuyến cũ; chỉ đổi khi tuyến mới tốt hơn ở tầng trên, hoặc giảm tầng cuối ít nhất reroute_min_gain (Rule 4)",
    "KEEP": "Luôn giữ thứ tự tuyến cũ, chỉ chèn job mới",
    "IGNORE": "Bỏ qua tuyến cũ, luôn xếp lại từ đầu",
}

# Trọng số theo "Ưu tiên trong ngày" (1 = gấp nhất). [GIẢ ĐỊNH] chờ nghiệp vụ xác nhận.
DEFAULT_PRIORITY_WEIGHTS: dict[int, float] = {1: 4.0, 2: 3.0, 3: 2.0, 4: 1.0}

# Thứ tự tầng và trọng số. [GIẢ ĐỊNH] chờ nghiệp vụ xác nhận.
DEFAULT_TIERS: tuple[dict[str, float], ...] = (
    # Tầng 1: đúng hẹn khách hàng (Rule 1, "Highest Priority").
    {"LATE_CHECKIN": 1.0},
    # Tầng 2: hoàn tất đúng hạn và trong ca.
    {"LATE_COMPLETION": 1.0, "AFTER_SHIFT": 1.0},
    # Tầng 3: các đánh đổi còn lại, quy về "km tương đương".
    {
        "KM": 1.0,
        "LATE_MINUTES": 0.1,  # 10 phút trễ ≈ 1 km
        "TRAVEL_MINUTES": 0.05,  # 20 phút đi ≈ 1 km
        "AREA_REENTRY": 2.0,  # quay lại khu vực 1 lần ≈ 2 km
        "PRIORITY_DELAY": 0.5,  # job ưu tiên 1 (trọng số 4) làm muộn 1 giờ ≈ 2 km
        "FINISH": 0.01,  # xong muộn 100 phút ≈ 1 km
    },
)


def _is_number(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float))


@dataclass(frozen=True, slots=True)
class BusinessRules:
    tiers: tuple[dict[str, float], ...] = DEFAULT_TIERS
    priority_weights: dict[int, float] = field(default_factory=lambda: dict(DEFAULT_PRIORITY_WEIGHTS))
    default_priority_weight: float = 1.0  # Job không có priority hoặc priority ngoài bảng.
    max_exact_jobs: int = 9  # Nhiều job hơn thì dùng heuristic thay cho QHĐ.
    max_labels_per_state: int = 32  # Vượt thì cắt bớt nhãn: kết quả gần đúng.
    previous_route_policy: str = "IF_BETTER"
    reroute_min_gain: float = 1.0  # Tầng cuối phải giảm ít nhất ngần này mới đổi tuyến cũ. [GIẢ ĐỊNH]

    def __post_init__(self) -> None:
        if not isinstance(self.tiers, tuple) or not self.tiers:
            raise ValueError("tiers: cần ít nhất một tầng")
        seen: set[str] = set()
        for index, tier in enumerate(self.tiers):
            if not isinstance(tier, dict) or not tier:
                raise ValueError(f"tiers[{index}]: cần object rule → trọng số, không rỗng")
            for code, weight in tier.items():
                if code not in SOFT_RULE_CODES:
                    raise ValueError(f"tiers[{index}]: không có rule {code!r}; rule hợp lệ: {', '.join(SOFT_RULE_CODES)}")
                if code in seen:
                    raise ValueError(f"tiers[{index}]: rule {code} đã nằm ở tầng khác")
                if not _is_number(weight) or weight < 0:
                    raise ValueError(f"tiers[{index}].{code}: trọng số phải là số ≥ 0")
                seen.add(code)
        for priority, weight in self.priority_weights.items():
            if isinstance(priority, bool) or not isinstance(priority, int) or not _is_number(weight) or weight < 0:
                raise ValueError(f"priority_weights.{priority}: cần ưu tiên số nguyên và trọng số ≥ 0")
        if not _is_number(self.default_priority_weight) or self.default_priority_weight < 0:
            raise ValueError("default_priority_weight: cần số ≥ 0")
        if isinstance(self.max_exact_jobs, bool) or not isinstance(self.max_exact_jobs, int) or not 1 <= self.max_exact_jobs <= 16:
            raise ValueError("max_exact_jobs: cần số nguyên 1..16")
        if isinstance(self.max_labels_per_state, bool) or not isinstance(self.max_labels_per_state, int) or self.max_labels_per_state < 1:
            raise ValueError("max_labels_per_state: cần số nguyên ≥ 1")
        if self.previous_route_policy not in PREVIOUS_ROUTE_POLICIES:
            raise ValueError(f"previous_route_policy: cần một trong {', '.join(PREVIOUS_ROUTE_POLICIES)}")
        if not _is_number(self.reroute_min_gain) or self.reroute_min_gain < 0:
            raise ValueError("reroute_min_gain: cần số ≥ 0")

    def priority_weight(self, priority: int | None) -> float:
        if priority is None:
            return self.default_priority_weight
        return self.priority_weights.get(priority, self.default_priority_weight)

    def objective_key(self, score: dict[str, float]) -> tuple[float, ...]:
        """Khóa so sánh từ chi phí từng rule: so từ tầng đầu, nhỏ hơn là tốt hơn."""

        return tuple(sum(weight * score.get(code, 0.0) for code, weight in tier.items()) for tier in self.tiers)

    def catalog(self) -> dict:
        """Mô tả rule đang dùng, để hiển thị trên giao diện và tài liệu."""

        info = {rule.code: rule for rule in SOFT_RULES}
        used = {code for tier in self.tiers for code in tier}

        def describe(rule: RuleInfo, weight: float | None = None) -> dict:
            item = {"code": rule.code, "name": rule.name, "unit": rule.unit, "source": rule.source, "description": rule.description}
            if weight is not None:
                item["weight"] = weight
            return item

        return {
            "tiers": [[describe(info[code], weight) for code, weight in tier.items()] for tier in self.tiers],
            "unused": [describe(rule) for rule in SOFT_RULES if rule.code not in used],
            "hard": [describe(rule) for rule in HARD_RULES],
            "priority_weights": {str(priority): weight for priority, weight in sorted(self.priority_weights.items())},
            "default_priority_weight": self.default_priority_weight,
            "max_exact_jobs": self.max_exact_jobs,
            "max_labels_per_state": self.max_labels_per_state,
            "previous_route_policy": {
                "code": self.previous_route_policy,
                "description": PREVIOUS_ROUTE_POLICIES[self.previous_route_policy],
            },
            "reroute_min_gain": self.reroute_min_gain,
        }


def rules_to_dict(rules: BusinessRules) -> dict:
    return {
        "format": RULES_FORMAT,
        "tiers": [dict(tier) for tier in rules.tiers],
        "priority_weights": {str(priority): weight for priority, weight in sorted(rules.priority_weights.items())},
        "default_priority_weight": rules.default_priority_weight,
        "max_exact_jobs": rules.max_exact_jobs,
        "max_labels_per_state": rules.max_labels_per_state,
        "previous_route_policy": rules.previous_route_policy,
        "reroute_min_gain": rules.reroute_min_gain,
        # Chỉ để người sửa file đọc; khi nạp lại, key bắt đầu bằng "_" bị bỏ qua.
        "_rules": {rule.code: f"{rule.name} ({rule.unit}): {rule.description}" for rule in SOFT_RULES},
        "_previous_route_policies": PREVIOUS_ROUTE_POLICIES,
    }


def rules_from_dict(data: object) -> BusinessRules:
    if not isinstance(data, dict) or data.get("format") != RULES_FORMAT:
        raise ValueError(f"cần object JSON có format = {RULES_FORMAT}")
    allowed = {
        "format",
        "tiers",
        "priority_weights",
        "default_priority_weight",
        "max_exact_jobs",
        "max_labels_per_state",
        "previous_route_policy",
        "reroute_min_gain",
    }
    unknown = sorted(key for key in data if key not in allowed and not str(key).startswith("_"))
    if unknown:
        raise ValueError(f"field không có trong rule: {', '.join(unknown)}")
    defaults = BusinessRules()
    tiers = data.get("tiers", [dict(tier) for tier in defaults.tiers])
    if not isinstance(tiers, list):
        raise ValueError("tiers: cần mảng các tầng")
    weights = data.get("priority_weights", {str(key): value for key, value in defaults.priority_weights.items()})
    if not isinstance(weights, dict) or not all(str(key).isdigit() for key in weights):
        raise ValueError("priority_weights: cần object ưu tiên (số nguyên) → trọng số")
    return BusinessRules(
        tiers=tuple(dict(tier) if isinstance(tier, dict) else tier for tier in tiers),
        priority_weights={int(key): value for key, value in weights.items()},
        default_priority_weight=data.get("default_priority_weight", defaults.default_priority_weight),
        max_exact_jobs=data.get("max_exact_jobs", defaults.max_exact_jobs),
        max_labels_per_state=data.get("max_labels_per_state", defaults.max_labels_per_state),
        previous_route_policy=data.get("previous_route_policy", defaults.previous_route_policy),
        reroute_min_gain=data.get("reroute_min_gain", defaults.reroute_min_gain),
    )


def load_rules(path: str | Path) -> BusinessRules:
    try:
        with Path(path).open(encoding="utf-8") as handle:
            return rules_from_dict(json.load(handle))
    except ValueError as error:  # Gồm cả JSON hỏng.
        raise ValueError(f"{path}: {error}") from None
