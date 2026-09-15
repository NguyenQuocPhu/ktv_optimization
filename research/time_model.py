"""Học thời gian làm và thời gian chuyển giữa hai job từ lịch sử check-in, xuất JSON cho routing.

    PYTHONPATH=src:simulator .venv/bin/python research/time_model.py \\
        --data-dir data --train-from 2026-06-01 --test-from 2026-06-16 --test-to 2026-07-01 \\
        --out artifacts/models/time_model.json

Routing cần hai con số để tính ETA:
- Thời gian làm = CHECKOUT − CHECKIN của lượt đầu tiên tới job. Lấy median riêng
  của KTV khi có đủ ``--min-samples`` lượt; nếu không thì median theo CASE_TYPE,
  rồi median chung.
- Thời gian chuyển = CHECKIN job sau − CHECKOUT job trước, cùng KTV, cùng ngày.
  Con số này gồm cả di chuyển lẫn chờ khách, nghỉ trưa... Lấy median theo km chim
  bay giữa hai điểm × giờ rời điểm trước. Bỏ các cặp check-in job sau trước cả khi
  checkout job trước (~14%, dồn nhiều vào buổi tối): đó là nhập liệu chồng lượt,
  không phải thời gian chuyển thật; tính là 0 phút sẽ làm cả hàng giờ tối về 0.

Dùng median vì phân phối lệch rất nặng: vài lượt kéo dài hàng giờ. Khoảng test được
chấm bằng chính code routing (``time_model_config``) và so với cấu hình mặc định
hiện tại. Không deploy.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ktv_routing import TIME_MODEL_FORMAT, JobInput, JobState, RoutingConfig, time_model_config
from ktv_simulator.events import _load_jobs, _load_visits

KM_EDGES = (0.05, 0.5, 1.0, 2.0, 4.0, 8.0)  # km chim bay; ≤ 50 m coi như cùng chỗ.
MAX_SERVICE_MINUTES = 480.0
CURRENT = "hiện tại"
LEARNED = "mô hình học"


def haversine_km(lat1, lng1, lat2, lng2) -> np.ndarray:
    lat1, lng1, lat2, lng2 = (np.radians(np.asarray(value, dtype="float64")) for value in (lat1, lng1, lat2, lng2))
    value = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lng2 - lng1) / 2) ** 2
    return 6371.0088 * 2 * np.arcsin(np.sqrt(value))


def prepare_visits(visits: pd.DataFrame) -> pd.DataFrame:
    """Bỏ lượt không có KTV, sắp theo giờ check-in, thêm ``visit_no`` (0 = lượt đầu tới job)
    và ``service_minutes``."""

    visits = visits[visits["emp_account"].notna()]
    visits = visits.sort_values(["checkin_at", "job_id"], kind="stable").reset_index(drop=True)
    visits["visit_no"] = visits.groupby("job_id").cumcount()
    minutes = (visits["checkout_at"] - visits["checkin_at"]).dt.total_seconds() / 60
    visits["service_minutes"] = minutes.clip(0, MAX_SERVICE_MINUTES)
    return visits


def load_visits(maintenance_csv: str | Path, checkins_csv: str | Path, encoding: str = "utf-8-sig") -> pd.DataFrame:
    """Mỗi dòng một lượt check-in kèm KTV, chi nhánh, loại việc và giờ tạo của job."""

    jobs = _load_jobs(Path(maintenance_csv), encoding)
    visits = _load_visits(Path(checkins_csv), encoding).merge(
        jobs[["job_id", "emp_account", "branch_name", "case_type", "created_at"]], on="job_id", how="inner"
    )
    return prepare_visits(visits)


def load_transitions(visits: pd.DataFrame) -> pd.DataFrame:
    """Hai lượt liên tiếp của cùng KTV trong cùng ngày: checkout job trước → check-in job sau."""

    ordered = visits.sort_values(["emp_account", "checkin_at", "job_id"], kind="stable").reset_index(drop=True)
    after = ordered.groupby("emp_account")[["job_id", "checkin_at", "lat_in", "lng_in"]].shift(-1)
    from_lat = ordered["lat_out"].fillna(ordered["lat_in"])
    from_lng = ordered["lng_out"].fillna(ordered["lng_in"])
    frame = pd.DataFrame(
        {
            "emp_account": ordered["emp_account"],
            "branch_name": ordered["branch_name"],
            "job_id": ordered["job_id"],
            "next_job_id": after["job_id"],
            "checkout_at": ordered["checkout_at"],
            "next_checkin_at": after["checkin_at"],
            "from_lat": from_lat,
            "from_lng": from_lng,
            "km": haversine_km(from_lat, from_lng, after["lat_in"], after["lng_in"]),
            "raw_gap_minutes": (after["checkin_at"] - ordered["checkout_at"]).dt.total_seconds() / 60,
        }
    )
    keep = (
        frame["checkout_at"].notna()
        & frame["next_checkin_at"].notna()
        & (frame["next_job_id"] != frame["job_id"]).fillna(False).astype(bool)
        & (frame["next_checkin_at"].dt.normalize() == ordered["checkin_at"].dt.normalize())
        & (frame["raw_gap_minutes"] >= 0)  # Chồng lượt: nhập liệu, không phải thời gian chuyển.
        & frame["km"].notna()
    )
    frame = frame[keep].reset_index(drop=True)
    frame["gap_minutes"] = frame["raw_gap_minutes"]
    return frame


def _between(series: pd.Series, start, end) -> pd.Series:
    return (series >= start) & (series < end)


def _first_visits(visits: pd.DataFrame, start, end) -> pd.DataFrame:
    return visits[
        (visits["visit_no"] == 0) & visits["service_minutes"].notna() & _between(visits["checkin_at"], start, end)
    ]


def fit_time_model(
    visits: pd.DataFrame,
    transitions: pd.DataFrame,
    *,
    start,
    end,
    min_samples: int = 10,
    min_cell: int = 30,
) -> dict:
    first = _first_visits(visits, start, end)
    moves = transitions[_between(transitions["checkout_at"], start, end)]
    if first.empty or moves.empty:
        raise ValueError("khoảng train không có lượt nào có checkout hoặc cặp chuyển job nào")

    def rounded(value: float) -> float:
        return round(float(value), 1)

    by_case_type = {
        str(case_type): rounded(row["median"])
        for case_type, row in first.groupby("case_type")["service_minutes"].agg(["median", "size"]).iterrows()
        if row["size"] >= min_samples
    }
    by_emp: dict[str, dict[str, float]] = {}
    personal = first.groupby(["emp_account", "case_type"])["service_minutes"].agg(["median", "size"])
    for (account, case_type), row in personal[personal["size"] >= min_samples].iterrows():
        by_emp.setdefault(str(account), {})[str(case_type)] = rounded(row["median"])

    size = len(KM_EDGES) + 1
    cells = pd.DataFrame(
        {
            # searchsorted(side="left") chia bucket giống bisect_left trong routing.
            "bucket": np.searchsorted(np.asarray(KM_EDGES), moves["km"].to_numpy(), side="left"),
            "hour": moves["checkout_at"].dt.hour.to_numpy(),
            "gap": moves["gap_minutes"].to_numpy(),
        }
    )
    fallback = rounded(cells["gap"].median())
    overall = cells.groupby("bucket")["gap"].agg(["median", "size"])
    minutes = [
        rounded(overall.loc[bucket, "median"])
        if bucket in overall.index and overall.loc[bucket, "size"] >= min_cell
        else fallback
        for bucket in range(size)
    ]
    hourly = cells.groupby(["hour", "bucket"])["gap"].agg(["median", "size"])
    by_hour: dict[str, list[float]] = {}
    for hour in sorted(cells["hour"].unique()):
        row = [
            rounded(hourly.loc[(hour, bucket), "median"])
            if (hour, bucket) in hourly.index and hourly.loc[(hour, bucket), "size"] >= min_cell
            else minutes[bucket]
            for bucket in range(size)
        ]
        if row != minutes:
            by_hour[str(int(hour))] = row

    return {
        "format": TIME_MODEL_FORMAT,
        "note": "Median từ lịch sử check-in, sinh bằng research/time_model.py",
        "trained_on": {
            "from": pd.Timestamp(start).isoformat(),
            "to": pd.Timestamp(end).isoformat(),
            "first_visits": len(first),
            "transitions": len(moves),
        },
        "service_minutes": {"default": rounded(first["service_minutes"].median()), "by_case_type": by_case_type, "by_emp": by_emp},
        "transition_minutes": {"km_edges": list(KM_EDGES), "minutes": minutes, "by_hour": by_hour},
    }


def _errors(actual: pd.Series, predicted) -> dict:
    error = np.asarray(predicted, dtype="float64") - actual.to_numpy(dtype="float64")
    return {
        "n": int(error.size),
        "mae": round(float(np.mean(np.abs(error))), 1),
        "median_abs": round(float(np.median(np.abs(error))), 1),
        "bias": round(float(np.mean(error)), 1),
        "within_10_min_percent": round(float(np.mean(np.abs(error) <= 10) * 100), 1),
    }


def evaluate_time_model(model: dict, visits: pd.DataFrame, transitions: pd.DataFrame, *, start, end) -> dict:
    """Chấm bằng chính code routing: lỗi = dự đoán − thực tế (phút)."""

    first = _first_visits(visits, start, end)
    moves = transitions[_between(transitions["checkout_at"], start, end)]
    result = {}
    for name, config in ((CURRENT, RoutingConfig()), (LEARNED, time_model_config(model))):
        service = [
            config.service_minutes(
                JobInput(
                    job_id=job_id,
                    state=JobState.PENDING,
                    location=None,
                    case_type=None if pd.isna(case_type) else case_type,
                    address=None,
                    due_at=None,
                    priority=None,
                    started_at=None,
                ),
                account,
            )
            for job_id, case_type, account in zip(first["job_id"], first["case_type"], first["emp_account"])
        ]
        if config.transition is None:  # Planner mặc định: chim bay với tốc độ cố định.
            move = moves["km"] / config.average_speed_kmh * 60
        else:
            move = [
                config.transition.leg_minutes(km, departed.to_pydatetime())
                for km, departed in zip(moves["km"], moves["checkout_at"])
            ]
        result[name] = {
            "service_minutes": _errors(first["service_minutes"], service),
            "transition_minutes": _errors(moves["gap_minutes"], move),
        }
    return result


def print_report(model: dict) -> None:
    trained, service, transition = model["trained_on"], model["service_minutes"], model["transition_minutes"]
    print(f"Train {trained['from'][:10]} → {trained['to'][:10]}: {trained['first_visits']:,} lượt đầu, {trained['transitions']:,} cặp chuyển job")
    print(
        f"Thời gian làm (median): chung {service['default']} phút · theo loại việc {service['by_case_type']} · "
        f"{len(service['by_emp']):,} KTV có số riêng"
    )
    edges = transition["km_edges"]
    labels = [f"≤{edges[0]}", *(f"{a}–{b}" for a, b in zip(edges, edges[1:])), f">{edges[-1]}"]
    print("Thời gian chuyển job (median phút) theo km chim bay × giờ rời điểm trước:")
    print("  giờ    " + "".join(f"{label:>9}" for label in labels))
    print("  mọi    " + "".join(f"{value:>9.0f}" for value in transition["minutes"]))
    for hour, row in sorted(transition["by_hour"].items(), key=lambda item: int(item[0])):
        print(f"  {int(hour):>2}h    " + "".join(f"{value:>9.0f}" for value in row))

    evaluation = model["evaluation"]
    print(f"\nKiểm tra {evaluation['from'][:10]} → {evaluation['to'][:10]} (lỗi = dự đoán − thực tế, phút):")
    print(f"  {'':<16}{'cấu hình':<14}{'n':>9}{'MAE':>8}{'median|lỗi|':>13}{'lệch TB':>9}{'±10 phút':>10}")
    for target, label in (("service_minutes", "thời gian làm"), ("transition_minutes", "chuyển job")):
        for name in (CURRENT, LEARNED):
            metrics = evaluation[name][target]
            print(
                f"  {label:<16}{name:<14}{metrics['n']:>9,}{metrics['mae']:>8}{metrics['median_abs']:>13}"
                f"{metrics['bias']:>+9}{metrics['within_10_min_percent']:>9}%"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--encoding", default="utf-8-sig")
    parser.add_argument("--train-from", type=pd.Timestamp, default=pd.Timestamp("2026-06-01"))
    parser.add_argument("--test-from", type=pd.Timestamp, default=pd.Timestamp("2026-06-16"))
    parser.add_argument("--test-to", type=pd.Timestamp, default=pd.Timestamp("2026-07-01"))
    parser.add_argument("--min-samples", type=int, default=10, help="Số lượt tối thiểu để dùng median riêng của KTV")
    parser.add_argument("--min-cell", type=int, default=30, help="Số cặp tối thiểu cho một ô km × giờ")
    parser.add_argument("--out", type=Path, default=Path("artifacts/models/time_model.json"))
    args = parser.parse_args(argv)
    if not args.train_from < args.test_from < args.test_to:
        parser.error("cần train-from < test-from < test-to")

    visits = load_visits(
        args.data_dir / "QOS_MAINTENANCE_utf8.csv", args.data_dir / "QOS_MAINT_CHECKIN_INFO_utf8.csv", args.encoding
    )
    transitions = load_transitions(visits)
    model = fit_time_model(
        visits, transitions, start=args.train_from, end=args.test_from, min_samples=args.min_samples, min_cell=args.min_cell
    )
    model["evaluation"] = {
        "from": args.test_from.isoformat(),
        "to": args.test_to.isoformat(),
        **evaluate_time_model(model, visits, transitions, start=args.test_from, end=args.test_to),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(model, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print_report(model)
    print(f"\nĐã ghi {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
