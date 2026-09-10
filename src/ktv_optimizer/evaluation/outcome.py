"""End-of-day evaluator built from persisted operational run artifacts.

The evaluator deliberately separates actual outcomes from the current plan:
``FLAG_ON_TIME``/``FINISH_DATE`` and check-in/GPS describe what happened;
route estimates in the latest snapshot only describe remaining work.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

import numpy as np
import pandas as pd

from ktv_optimizer.data.mappers import normalize_checkin_frame
from ktv_optimizer.data.mappers.qos_mapper import parse_datetime_series


COMPLETION_COLUMNS = (
    "CHECKLIST_ID",
    "COMPLETION_STATUS",
    "COMPLETION_EVENT_TIME",
    "BRANCH_NAME",
    "CASE_TYPE",
    "OBJ_LOCATION",
    "LATITUDE",
    "LONGITUDE",
    "EMP_ACCOUNT",
    "CREATED_AT",
    "DUE_AT",
    "FINISH_DATE",
    "FLAG_ON_TIME",
    "WARD_CODE",
    "WARD_NAME",
    "PROVINCE_NAME",
)
DETAIL_COLUMNS = (
    *COMPLETION_COLUMNS,
    "RUN_ID",
    "SNAPSHOT_ID",
    "OUTCOME_TYPE",
    "SLA_ON_TIME",
    "SLA_SOURCE",
    "OUTCOME_TIME",
    "CHECKIN_DATE",
    "CHECKOUT_DATE",
    "SERVICE_MINUTES",
    "VISIT_EMP_CODE",
    "TECHNICIAN_ID",
    "CLUSTER_KEY",
    "COMPLETED_IN_SHIFT",
    "SOURCE_OUTCOME_MATCH",
)
TRAVEL_COLUMNS = (
    "TECHNICIAN_ID",
    "FROM_CHECKLIST_ID",
    "TO_CHECKLIST_ID",
    "DEPARTURE_TIME",
    "ARRIVAL_TIME",
    "INTER_JOB_WINDOW_MINUTES",
    "GPS_POINTS",
    "GPS_VALID_SEGMENTS",
    "GPS_DISTANCE_KM",
    "GPS_MOVING_MINUTES",
    "ESTIMATED_WAIT_MINUTES",
    "GPS_EVALUABLE",
    "EXCLUSION_REASON",
)


@dataclass(frozen=True, slots=True)
class DailyOutcomeResult:
    report_date: date
    summary: dict
    checklist_outcomes: pd.DataFrame
    travel_legs: pd.DataFrame
    technician_outcomes: pd.DataFrame


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator * 100, 2)


def _timestamp(value: object) -> pd.Timestamp | pd.NaT:
    return pd.to_datetime(value, errors="coerce")


def _clean_string(series: pd.Series) -> pd.Series:
    return series.astype("string").str.strip().mask(lambda item: item.eq(""))


def _haversine_km(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    radius_km = 6371.0088
    lat_a, lat_b = radians(latitude_a), radians(latitude_b)
    delta_lat = lat_b - lat_a
    delta_lng = radians(longitude_b - longitude_a)
    value = (
        sin(delta_lat / 2) ** 2
        + cos(lat_a) * cos(lat_b) * sin(delta_lng / 2) ** 2
    )
    return radius_km * 2 * asin(sqrt(value))


def _percentile_95(values: list[float]) -> float | None:
    if not values:
        return None
    return round(float(np.percentile(values, 95)), 6)


class DailyOutcomeEvaluator:
    """Aggregate one business day from immutable snapshot run outputs.

    Optional source exports enrich the report. Missing optional sources never
    become zero: the summary exposes an evaluable count and coverage instead.
    """

    def __init__(
        self,
        *,
        runs_root: str | Path,
        maintenance_path: str | Path | None = None,
        checkin_path: str | Path | None = None,
        gps_path: str | Path | None = None,
        roster_path: str | Path | None = None,
        max_leg_gap_minutes: float = 240.0,
        max_gps_gap_minutes: float = 15.0,
        min_moving_speed_kmh: float = 5.0,
        max_speed_kmh: float = 120.0,
    ) -> None:
        self.runs_root = Path(runs_root)
        self.maintenance_path = (
            Path(maintenance_path) if maintenance_path else None
        )
        self.checkin_path = Path(checkin_path) if checkin_path else None
        self.gps_path = Path(gps_path) if gps_path else None
        self.roster_path = Path(roster_path) if roster_path else None
        self.max_leg_gap_minutes = max_leg_gap_minutes
        self.max_gps_gap_minutes = max_gps_gap_minutes
        self.min_moving_speed_kmh = min_moving_speed_kmh
        self.max_speed_kmh = max_speed_kmh

    def evaluate(self, report_date: date) -> DailyOutcomeResult:
        completions, run_rows, run_summaries = self._load_daily_runs(
            report_date
        )
        outcomes = self._canonicalize_completions(completions)
        outcomes = self._enrich_maintenance(outcomes)
        gps, employee_to_account = self._load_gps(report_date)
        outcomes = self._enrich_visits(
            outcomes, employee_to_account, report_date
        )
        outcomes = self._classify_outcomes(outcomes)
        outcomes = self._attribute_shift(outcomes)
        outcomes = self._add_cluster_keys(outcomes)
        travel_legs = self._build_travel_legs(outcomes, gps)
        technician_outcomes = self._technician_summary(
            outcomes, travel_legs
        )
        summary = self._daily_summary(
            report_date,
            outcomes,
            travel_legs,
            run_rows,
            run_summaries,
        )
        return DailyOutcomeResult(
            report_date=report_date,
            summary=summary,
            checklist_outcomes=outcomes.reindex(columns=DETAIL_COLUMNS),
            travel_legs=travel_legs.reindex(columns=TRAVEL_COLUMNS),
            technician_outcomes=technician_outcomes,
        )

    def _load_daily_runs(
        self, report_date: date
    ) -> tuple[pd.DataFrame, list[dict], list[dict]]:
        if not self.runs_root.is_dir():
            raise FileNotFoundError(
                f"Runs root không tồn tại: {self.runs_root}"
            )
        frames: list[pd.DataFrame] = []
        run_rows: list[dict] = []
        summaries: list[dict] = []
        for metadata_path in sorted(
            self.runs_root.rglob("run_metadata.json")
        ):
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            snapshot_time = _timestamp(metadata.get("snapshot_time"))
            if (
                metadata.get("status") != "SUCCESS"
                or pd.isna(snapshot_time)
                or snapshot_time.date() != report_date
            ):
                continue
            run_dir = metadata_path.parent
            run_rows.append(metadata)
            summary_path = run_dir / "summary.json"
            if summary_path.is_file():
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                summary["_snapshot_time"] = snapshot_time.isoformat()
                summaries.append(summary)
            completion_path = run_dir / "completed_jobs.csv"
            if not completion_path.is_file():
                continue
            frame = pd.read_csv(
                completion_path,
                encoding="utf-8-sig",
                dtype="string",
                keep_default_na=False,
            )
            if frame.empty:
                continue
            for column in COMPLETION_COLUMNS:
                if column not in frame:
                    frame[column] = pd.NA
            frame["RUN_ID"] = metadata.get("run_id", run_dir.name)
            frame["SNAPSHOT_ID"] = metadata.get("snapshot_id")
            frames.append(frame[[*COMPLETION_COLUMNS, "RUN_ID", "SNAPSHOT_ID"]])
        if not frames:
            empty = pd.DataFrame(
                columns=[*COMPLETION_COLUMNS, "RUN_ID", "SNAPSHOT_ID"]
            )
            return empty, run_rows, summaries
        return pd.concat(frames, ignore_index=True), run_rows, summaries

    @staticmethod
    def _canonicalize_completions(frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty:
            return frame.copy()
        result = frame.copy()
        result["CHECKLIST_ID"] = _clean_string(result["CHECKLIST_ID"])
        result["COMPLETION_EVENT_TIME"] = pd.to_datetime(
            result["COMPLETION_EVENT_TIME"], errors="coerce"
        )
        result = result.dropna(subset=["CHECKLIST_ID"])
        # One checklist contributes once per day even if an upstream terminal
        # webhook is retried or artifacts are copied into the runs root.
        return (
            result.sort_values("COMPLETION_EVENT_TIME", kind="stable")
            .drop_duplicates("CHECKLIST_ID", keep="first")
            .reset_index(drop=True)
        )

    def _enrich_maintenance(self, outcomes: pd.DataFrame) -> pd.DataFrame:
        result = outcomes.copy()
        result["SOURCE_OUTCOME_MATCH"] = pd.Series(
            pd.NA, index=result.index, dtype="boolean"
        )
        if (
            result.empty
            or self.maintenance_path is None
            or not self.maintenance_path.is_file()
        ):
            return result
        wanted = {
            "CHECKLIST_ID",
            "CHECKLIST_STATUS",
            "FINISH_DATE",
            "FLAG_ON_TIME",
            "EMP_ACCOUNT",
        }
        available = set(
            pd.read_csv(
                self.maintenance_path,
                encoding="utf-8-sig",
                nrows=0,
            ).columns
        )
        if "CHECKLIST_ID" not in available:
            return result
        source = pd.read_csv(
            self.maintenance_path,
            encoding="utf-8-sig",
            dtype="string",
            keep_default_na=False,
            usecols=sorted(wanted & available),
        )
        source["CHECKLIST_ID"] = _clean_string(source["CHECKLIST_ID"])
        source = source[source["CHECKLIST_ID"].isin(result["CHECKLIST_ID"])]
        source = source.drop_duplicates("CHECKLIST_ID", keep="first")
        source = source.rename(
            columns={
                column: f"{column}_SOURCE"
                for column in source.columns
                if column != "CHECKLIST_ID"
            }
        )
        result = result.merge(source, on="CHECKLIST_ID", how="left")
        source_finish = parse_datetime_series(
            result.get(
                "FINISH_DATE_SOURCE",
                pd.Series(pd.NA, index=result.index, dtype="string"),
            )
        )
        event_time = pd.to_datetime(
            result["COMPLETION_EVENT_TIME"], errors="coerce"
        )
        source_flag = _clean_string(
            result.get(
                "FLAG_ON_TIME_SOURCE",
                pd.Series(pd.NA, index=result.index, dtype="string"),
            )
        ).str.upper()
        source_status = _clean_string(
            result.get(
                "CHECKLIST_STATUS_SOURCE",
                pd.Series(pd.NA, index=result.index, dtype="string"),
            )
        )
        same_finish_day = source_finish.dt.date.eq(event_time.dt.date)
        valid_cancellation = source_flag.eq("NA") & source_status.eq(
            "Đóng checklist"
        )
        source_match = (same_finish_day | valid_cancellation).fillna(False)
        source_present = source_flag.notna() | source_finish.notna()
        result.loc[source_present, "SOURCE_OUTCOME_MATCH"] = source_match.loc[
            source_present
        ]

        # The received event wins. The day-end export only fills sparse event
        # fields when it describes the same operational day.
        original_finish = _clean_string(result["FINISH_DATE"])
        result["FINISH_DATE"] = original_finish.combine_first(
            source_finish.astype("string").where(source_match)
        )
        original_flag = _clean_string(result["FLAG_ON_TIME"])
        result["FLAG_ON_TIME"] = original_flag.combine_first(
            source_flag.where(source_match)
        )
        if "EMP_ACCOUNT_SOURCE" in result:
            result["EMP_ACCOUNT"] = _clean_string(
                result["EMP_ACCOUNT"]
            ).combine_first(_clean_string(result["EMP_ACCOUNT_SOURCE"]))
        return result

    def _load_gps(
        self, report_date: date
    ) -> tuple[pd.DataFrame, dict[str, str]]:
        columns = [
            "EMPLOYEECODE",
            "ACCOUNTEMP",
            "CREATEDATE",
            "LATITUDE",
            "LONGITUDE",
        ]
        if self.gps_path is None or not self.gps_path.is_file():
            return pd.DataFrame(columns=columns), {}
        available = set(
            pd.read_csv(
                self.gps_path, encoding="utf-8-sig", nrows=0
            ).columns
        )
        required = {"EMPLOYEECODE", "ACCOUNTEMP", "COORDINATE", "CREATEDATE"}
        if not required.issubset(available):
            return pd.DataFrame(columns=columns), {}
        gps = pd.read_csv(
            self.gps_path,
            encoding="utf-8-sig",
            dtype="string",
            keep_default_na=False,
            usecols=sorted(required),
        )
        coordinates = gps["COORDINATE"].str.extract(
            r"\(?\s*([-\d.]+)\s*,\s*([-\d.]+)\s*\)?"
        )
        gps["LATITUDE"] = pd.to_numeric(coordinates[0], errors="coerce")
        gps["LONGITUDE"] = pd.to_numeric(coordinates[1], errors="coerce")
        gps["CREATEDATE"] = pd.to_datetime(gps["CREATEDATE"], errors="coerce")
        gps["EMPLOYEECODE"] = _clean_string(gps["EMPLOYEECODE"])
        gps["ACCOUNTEMP"] = _clean_string(gps["ACCOUNTEMP"])
        mapping = (
            gps.dropna(subset=["EMPLOYEECODE", "ACCOUNTEMP"])
            .drop_duplicates("EMPLOYEECODE", keep="last")
            .set_index("EMPLOYEECODE")["ACCOUNTEMP"]
            .to_dict()
        )
        gps = gps[
            gps["CREATEDATE"].dt.date.eq(report_date)
            & gps["LATITUDE"].between(8, 24)
            & gps["LONGITUDE"].between(102, 110)
        ]
        return gps[columns].sort_values("CREATEDATE"), mapping

    def _enrich_visits(
        self,
        outcomes: pd.DataFrame,
        employee_to_account: dict[str, str],
        report_date: date,
    ) -> pd.DataFrame:
        result = outcomes.copy()
        for column in (
            "CHECKIN_DATE",
            "CHECKOUT_DATE",
            "SERVICE_MINUTES",
            "VISIT_EMP_CODE",
            "VISIT_EMP_ACCOUNT",
        ):
            result[column] = pd.NA
        if (
            result.empty
            or self.checkin_path is None
            or not self.checkin_path.is_file()
        ):
            return result
        visits = pd.read_csv(
            self.checkin_path,
            encoding="utf-8-sig",
            dtype="string",
            keep_default_na=False,
            usecols=[
                "CHECKLIST_ID",
                "CHECKIN_DATE",
                "CHECKOUT_DATE",
                "EMP_CODE",
                "LAT_LNG_IN",
                "LAT_LNG_OUT",
            ],
        )
        visits = normalize_checkin_frame(visits)
        visits = visits[visits["CHECKLIST_ID"].isin(result["CHECKLIST_ID"])]
        visits = visits[
            visits["CHECKIN_DATE"].dt.date.eq(report_date)
            | visits["CHECKOUT_DATE"].dt.date.eq(report_date)
        ]
        if visits.empty:
            return result
        visits["_HAS_CHECKOUT"] = visits["CHECKOUT_DATE"].notna()
        visits = (
            visits.sort_values(
                ["CHECKLIST_ID", "_HAS_CHECKOUT", "CHECKOUT_DATE", "CHECKIN_DATE"],
                kind="stable",
                na_position="first",
            )
            .drop_duplicates("CHECKLIST_ID", keep="last")
            .rename(columns={"EMP_CODE": "VISIT_EMP_CODE"})
        )
        visits["VISIT_EMP_ACCOUNT"] = visits["VISIT_EMP_CODE"].map(
            employee_to_account
        )
        service = (
            visits["CHECKOUT_DATE"] - visits["CHECKIN_DATE"]
        ).dt.total_seconds() / 60
        visits["SERVICE_MINUTES"] = service.where(service.between(0, 1440))
        result = result.drop(
            columns=[
                "CHECKIN_DATE",
                "CHECKOUT_DATE",
                "SERVICE_MINUTES",
                "VISIT_EMP_CODE",
                "VISIT_EMP_ACCOUNT",
            ]
        ).merge(
            visits[
                [
                    "CHECKLIST_ID",
                    "CHECKIN_DATE",
                    "CHECKOUT_DATE",
                    "SERVICE_MINUTES",
                    "VISIT_EMP_CODE",
                    "VISIT_EMP_ACCOUNT",
                ]
            ],
            on="CHECKLIST_ID",
            how="left",
        )
        return result

    @staticmethod
    def _classify_outcomes(outcomes: pd.DataFrame) -> pd.DataFrame:
        result = outcomes.copy()
        if result.empty:
            for column in (
                "OUTCOME_TYPE",
                "SLA_ON_TIME",
                "SLA_SOURCE",
                "OUTCOME_TIME",
                "TECHNICIAN_ID",
            ):
                result[column] = pd.Series(dtype="object")
            return result
        flag = _clean_string(result["FLAG_ON_TIME"]).str.upper()
        status = _clean_string(result["COMPLETION_STATUS"])
        finish = parse_datetime_series(result["FINISH_DATE"])
        due = pd.to_datetime(result["DUE_AT"], errors="coerce")
        event_time = pd.to_datetime(
            result["COMPLETION_EVENT_TIME"], errors="coerce"
        )
        result["FINISH_DATE"] = finish
        result["DUE_AT"] = due
        result["OUTCOME_TIME"] = finish.combine_first(event_time)
        result["OUTCOME_TYPE"] = np.select(
            [
                flag.isin(["YES", "NO"]).fillna(False).to_numpy(bool),
                (
                    flag.eq("NA") | status.eq("Đóng checklist")
                ).fillna(False).to_numpy(bool),
                status.eq("Đã xử lý").fillna(False).to_numpy(bool),
            ],
            ["COMPLETED", "CANCELLED", "COMPLETED"],
            default="UNKNOWN",
        )
        sla = pd.Series(pd.NA, index=result.index, dtype="boolean")
        sla_source = pd.Series(pd.NA, index=result.index, dtype="string")
        official = flag.isin(["YES", "NO"])
        sla.loc[official] = flag.loc[official].eq("YES")
        sla_source.loc[official] = "FLAG_ON_TIME"
        fallback_finish = (~official) & finish.notna() & due.notna()
        sla.loc[fallback_finish] = finish.loc[fallback_finish] <= due.loc[
            fallback_finish
        ]
        sla_source.loc[fallback_finish] = "FINISH_DATE_VS_DUE_AT"
        fallback_event = (
            (~official)
            & (~fallback_finish)
            & result["OUTCOME_TYPE"].eq("COMPLETED")
            & event_time.notna()
            & due.notna()
        )
        sla.loc[fallback_event] = event_time.loc[fallback_event] <= due.loc[
            fallback_event
        ]
        sla_source.loc[fallback_event] = "EVENT_TIME_VS_DUE_AT"
        result["SLA_ON_TIME"] = sla
        result["SLA_SOURCE"] = sla_source
        event_account = _clean_string(result["EMP_ACCOUNT"])
        visit_account = _clean_string(result["VISIT_EMP_ACCOUNT"])
        result["TECHNICIAN_ID"] = event_account.combine_first(visit_account)
        return result

    def _attribute_shift(self, outcomes: pd.DataFrame) -> pd.DataFrame:
        result = outcomes.copy()
        result["COMPLETED_IN_SHIFT"] = pd.Series(
            pd.NA, index=result.index, dtype="boolean"
        )
        if (
            result.empty
            or self.roster_path is None
            or not self.roster_path.is_file()
        ):
            return result
        roster = pd.read_csv(
            self.roster_path, encoding="utf-8-sig", dtype="string"
        )
        required = {"EMP_ACCOUNT", "SHIFT_START", "SHIFT_END"}
        if not required.issubset(roster.columns):
            return result
        roster["EMP_ACCOUNT"] = _clean_string(roster["EMP_ACCOUNT"])
        roster["SHIFT_START"] = pd.to_datetime(
            roster["SHIFT_START"], errors="coerce"
        )
        roster["SHIFT_END"] = pd.to_datetime(
            roster["SHIFT_END"], errors="coerce"
        )
        by_account = {
            account: group
            for account, group in roster.dropna(
                subset=["EMP_ACCOUNT", "SHIFT_START", "SHIFT_END"]
            ).groupby("EMP_ACCOUNT")
        }
        for index, row in result.iterrows():
            account = row.get("TECHNICIAN_ID")
            outcome_time = row.get("OUTCOME_TIME")
            if pd.isna(account) or pd.isna(outcome_time):
                continue
            shifts = by_account.get(str(account))
            if shifts is None:
                continue
            result.at[index, "COMPLETED_IN_SHIFT"] = bool(
                (
                    (shifts["SHIFT_START"] <= outcome_time)
                    & (outcome_time <= shifts["SHIFT_END"])
                ).any()
            )
        return result

    @staticmethod
    def _add_cluster_keys(outcomes: pd.DataFrame) -> pd.DataFrame:
        result = outcomes.copy()
        ward_code = _clean_string(result["WARD_CODE"])
        ward_name = _clean_string(result["WARD_NAME"])
        latitude = pd.to_numeric(result["LATITUDE"], errors="coerce")
        longitude = pd.to_numeric(result["LONGITUDE"], errors="coerce")
        coordinate_cluster = pd.Series(pd.NA, index=result.index, dtype="string")
        has_coordinate = latitude.notna() & longitude.notna()
        coordinate_cluster.loc[has_coordinate] = (
            "GPS_"
            + latitude.loc[has_coordinate].round(3).astype(str)
            + "_"
            + longitude.loc[has_coordinate].round(3).astype(str)
        )
        result["CLUSTER_KEY"] = ward_code.combine_first(ward_name).combine_first(
            coordinate_cluster
        )
        return result

    def _build_travel_legs(
        self, outcomes: pd.DataFrame, gps: pd.DataFrame
    ) -> pd.DataFrame:
        rows: list[dict] = []
        eligible = outcomes[
            outcomes["OUTCOME_TYPE"].eq("COMPLETED")
            & outcomes["TECHNICIAN_ID"].notna()
            & outcomes["CHECKIN_DATE"].notna()
        ].copy()
        if eligible.empty:
            return pd.DataFrame(columns=TRAVEL_COLUMNS)
        eligible = eligible.sort_values(
            ["TECHNICIAN_ID", "CHECKIN_DATE"], kind="stable"
        )
        for technician_id, group in eligible.groupby("TECHNICIAN_ID"):
            visits = group.reset_index(drop=True)
            technician_gps = gps[gps["ACCOUNTEMP"].eq(technician_id)]
            for index in range(1, len(visits)):
                previous = visits.iloc[index - 1]
                current = visits.iloc[index]
                departure = previous["CHECKOUT_DATE"]
                arrival = current["CHECKIN_DATE"]
                row = {
                    "TECHNICIAN_ID": technician_id,
                    "FROM_CHECKLIST_ID": previous["CHECKLIST_ID"],
                    "TO_CHECKLIST_ID": current["CHECKLIST_ID"],
                    "DEPARTURE_TIME": departure,
                    "ARRIVAL_TIME": arrival,
                    "INTER_JOB_WINDOW_MINUTES": pd.NA,
                    "GPS_POINTS": 0,
                    "GPS_VALID_SEGMENTS": 0,
                    "GPS_DISTANCE_KM": pd.NA,
                    "GPS_MOVING_MINUTES": pd.NA,
                    "ESTIMATED_WAIT_MINUTES": pd.NA,
                    "GPS_EVALUABLE": False,
                    "EXCLUSION_REASON": pd.NA,
                }
                if pd.isna(departure) or pd.isna(arrival):
                    row["EXCLUSION_REASON"] = "MISSING_CHECKOUT_OR_CHECKIN"
                    rows.append(row)
                    continue
                window_minutes = (arrival - departure).total_seconds() / 60
                row["INTER_JOB_WINDOW_MINUTES"] = round(window_minutes, 2)
                if window_minutes < 0:
                    row["EXCLUSION_REASON"] = "OVERLAPPING_VISITS"
                    rows.append(row)
                    continue
                if window_minutes > self.max_leg_gap_minutes:
                    row["EXCLUSION_REASON"] = "INTER_JOB_GAP_TOO_LONG"
                    rows.append(row)
                    continue
                points = technician_gps[
                    technician_gps["CREATEDATE"].between(departure, arrival)
                ].sort_values("CREATEDATE")
                row["GPS_POINTS"] = len(points)
                if len(points) < 2:
                    row["EXCLUSION_REASON"] = "INSUFFICIENT_GPS_POINTS"
                    rows.append(row)
                    continue
                distance_km = 0.0
                moving_minutes = 0.0
                valid_segments = 0
                point_rows = list(points.itertuples(index=False))
                for point_a, point_b in zip(point_rows, point_rows[1:]):
                    gap_minutes = (
                        point_b.CREATEDATE - point_a.CREATEDATE
                    ).total_seconds() / 60
                    if gap_minutes <= 0 or gap_minutes > self.max_gps_gap_minutes:
                        continue
                    segment_distance = _haversine_km(
                        float(point_a.LATITUDE),
                        float(point_a.LONGITUDE),
                        float(point_b.LATITUDE),
                        float(point_b.LONGITUDE),
                    )
                    speed = segment_distance / (gap_minutes / 60)
                    if not (
                        self.min_moving_speed_kmh
                        <= speed
                        <= self.max_speed_kmh
                    ):
                        continue
                    valid_segments += 1
                    distance_km += segment_distance
                    moving_minutes += gap_minutes
                row.update(
                    {
                        "GPS_VALID_SEGMENTS": valid_segments,
                        "GPS_DISTANCE_KM": round(distance_km, 3),
                        "GPS_MOVING_MINUTES": round(moving_minutes, 2),
                        "ESTIMATED_WAIT_MINUTES": round(
                            max(0.0, window_minutes - moving_minutes), 2
                        ),
                        "GPS_EVALUABLE": True,
                        "EXCLUSION_REASON": pd.NA,
                    }
                )
                rows.append(row)
        return pd.DataFrame(rows, columns=TRAVEL_COLUMNS)

    @staticmethod
    def _technician_summary(
        outcomes: pd.DataFrame, travel_legs: pd.DataFrame
    ) -> pd.DataFrame:
        columns = [
            "TECHNICIAN_ID",
            "TERMINAL_EVENTS",
            "COMPLETED_JOBS",
            "CANCELLED_JOBS",
            "SLA_EVALUABLE_JOBS",
            "SLA_ON_TIME_JOBS",
            "SLA_ON_TIME_RATE_PERCENT",
            "SERVICE_MINUTES",
            "UNIQUE_CLUSTERS",
            "SAME_AREA_REVISITS",
            "GPS_EVALUABLE_LEGS",
            "GPS_DISTANCE_KM",
            "GPS_MOVING_MINUTES",
            "ESTIMATED_WAIT_MINUTES",
        ]
        assigned = outcomes[outcomes["TECHNICIAN_ID"].notna()].copy()
        if assigned.empty:
            return pd.DataFrame(columns=columns)
        rows: list[dict] = []
        for technician_id, group in assigned.groupby("TECHNICIAN_ID"):
            completed = group[group["OUTCOME_TYPE"].eq("COMPLETED")]
            cluster_sequence = [
                value
                for value in completed.sort_values("OUTCOME_TIME")[
                    "CLUSTER_KEY"
                ].tolist()
                if pd.notna(value)
            ]
            compressed = [
                value
                for index, value in enumerate(cluster_sequence)
                if index == 0 or value != cluster_sequence[index - 1]
            ]
            seen: set[str] = set()
            revisits = 0
            for cluster in compressed:
                if cluster in seen:
                    revisits += 1
                seen.add(cluster)
            sla = completed["SLA_ON_TIME"].dropna()
            legs = travel_legs[
                travel_legs["TECHNICIAN_ID"].eq(technician_id)
                & travel_legs["GPS_EVALUABLE"].eq(True)  # noqa: E712
            ]
            rows.append(
                {
                    "TECHNICIAN_ID": technician_id,
                    "TERMINAL_EVENTS": len(group),
                    "COMPLETED_JOBS": len(completed),
                    "CANCELLED_JOBS": int(
                        group["OUTCOME_TYPE"].eq("CANCELLED").sum()
                    ),
                    "SLA_EVALUABLE_JOBS": len(sla),
                    "SLA_ON_TIME_JOBS": int(sla.sum()),
                    "SLA_ON_TIME_RATE_PERCENT": _rate(int(sla.sum()), len(sla)),
                    "SERVICE_MINUTES": round(
                        float(completed["SERVICE_MINUTES"].sum()), 2
                    ),
                    "UNIQUE_CLUSTERS": len(set(cluster_sequence)),
                    "SAME_AREA_REVISITS": revisits,
                    "GPS_EVALUABLE_LEGS": len(legs),
                    "GPS_DISTANCE_KM": round(
                        float(legs["GPS_DISTANCE_KM"].sum()), 3
                    ),
                    "GPS_MOVING_MINUTES": round(
                        float(legs["GPS_MOVING_MINUTES"].sum()), 2
                    ),
                    "ESTIMATED_WAIT_MINUTES": round(
                        float(legs["ESTIMATED_WAIT_MINUTES"].sum()), 2
                    ),
                }
            )
        return pd.DataFrame(rows, columns=columns).sort_values("TECHNICIAN_ID")

    @staticmethod
    def _area_metrics(outcomes: pd.DataFrame) -> dict:
        completed = outcomes[
            outcomes["OUTCOME_TYPE"].eq("COMPLETED")
            & outcomes["TECHNICIAN_ID"].notna()
        ].copy()
        unique_clusters = set(completed["CLUSTER_KEY"].dropna())
        technician_visits = 0
        revisits = 0
        for _, group in completed.groupby("TECHNICIAN_ID"):
            sequence = [
                item
                for item in group.sort_values("OUTCOME_TIME")[
                    "CLUSTER_KEY"
                ].tolist()
                if pd.notna(item)
            ]
            technician_visits += len(set(sequence))
            compressed = [
                value
                for index, value in enumerate(sequence)
                if index == 0 or value != sequence[index - 1]
            ]
            seen: set[str] = set()
            for cluster in compressed:
                if cluster in seen:
                    revisits += 1
                seen.add(cluster)
        return {
            "unique_clusters": len(unique_clusters),
            "technician_cluster_visits": technician_visits,
            "same_area_revisit_count": revisits,
            "cluster_evaluable_jobs": int(completed["CLUSTER_KEY"].notna().sum()),
        }

    def _daily_summary(
        self,
        report_date: date,
        outcomes: pd.DataFrame,
        travel_legs: pd.DataFrame,
        run_rows: list[dict],
        run_summaries: list[dict],
    ) -> dict:
        completed = outcomes[outcomes["OUTCOME_TYPE"].eq("COMPLETED")]
        sla = completed["SLA_ON_TIME"].dropna()
        flags = _clean_string(outcomes["FLAG_ON_TIME"]).str.upper()
        eligible_legs = travel_legs[
            travel_legs["INTER_JOB_WINDOW_MINUTES"].notna()
            & ~travel_legs["EXCLUSION_REASON"].isin(
                ["OVERLAPPING_VISITS", "INTER_JOB_GAP_TOO_LONG"]
            )
        ]
        gps_legs = travel_legs[
            travel_legs["GPS_EVALUABLE"].eq(True)  # noqa: E712
        ]
        planning_values = [
            float(summary["evaluation_metrics"]["ai_planning_seconds"])
            for summary in run_summaries
            if summary.get("evaluation_metrics", {}).get(
                "ai_planning_seconds"
            )
            is not None
        ]
        ordered_summaries = sorted(
            run_summaries, key=lambda item: item.get("_snapshot_time", "")
        )
        latest_plan = (
            ordered_summaries[-1].get("evaluation_metrics", {})
            if ordered_summaries
            else {}
        )
        shift_values = completed["COMPLETED_IN_SHIFT"].dropna()
        report = {
            "report_date": report_date.isoformat(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": {
                "runs_root": str(self.runs_root.resolve()),
                "successful_runs": len(run_rows),
                "maintenance": (
                    str(self.maintenance_path.resolve())
                    if self.maintenance_path and self.maintenance_path.is_file()
                    else None
                ),
                "checkin": (
                    str(self.checkin_path.resolve())
                    if self.checkin_path and self.checkin_path.is_file()
                    else None
                ),
                "gps": (
                    str(self.gps_path.resolve())
                    if self.gps_path and self.gps_path.is_file()
                    else None
                ),
                "roster": (
                    str(self.roster_path.resolve())
                    if self.roster_path and self.roster_path.is_file()
                    else None
                ),
            },
            "checklist_outcomes": {
                "terminal_events": len(outcomes),
                "completed_jobs": len(completed),
                "cancelled_jobs": int(
                    outcomes["OUTCOME_TYPE"].eq("CANCELLED").sum()
                ),
                "unknown_outcomes": int(
                    outcomes["OUTCOME_TYPE"].eq("UNKNOWN").sum()
                ),
                "official_flag_coverage_percent": _rate(
                    int(flags.isin(["YES", "NO", "NA"]).sum()), len(outcomes)
                ),
                "source_outcome_date_mismatches": int(
                    outcomes["SOURCE_OUTCOME_MATCH"].eq(False).sum()
                ),
                "sla_evaluable_jobs": len(sla),
                "sla_on_time_jobs": int(sla.sum()),
                "sla_on_time_rate_percent": _rate(int(sla.sum()), len(sla)),
                "service_time_evaluable_jobs": int(
                    completed["SERVICE_MINUTES"].notna().sum()
                ),
                "total_service_minutes": round(
                    float(completed["SERVICE_MINUTES"].sum()), 2
                ),
            },
            "travel_actual_gps_estimate": {
                "candidate_inter_job_legs": len(travel_legs),
                "eligible_inter_job_legs": len(eligible_legs),
                "gps_evaluable_legs": len(gps_legs),
                "gps_coverage_percent": _rate(len(gps_legs), len(eligible_legs)),
                "total_distance_km": (
                    round(float(gps_legs["GPS_DISTANCE_KM"].sum()), 3)
                    if len(gps_legs)
                    else None
                ),
                "total_travel_minutes": (
                    round(float(gps_legs["GPS_MOVING_MINUTES"].sum()), 2)
                    if len(gps_legs)
                    else None
                ),
                "total_wait_between_jobs_minutes": (
                    round(float(gps_legs["ESTIMATED_WAIT_MINUTES"].sum()), 2)
                    if len(gps_legs)
                    else None
                ),
                "inter_job_window_upper_bound_minutes": (
                    round(
                        float(eligible_legs["INTER_JOB_WINDOW_MINUTES"].sum()),
                        2,
                    )
                    if len(eligible_legs)
                    else None
                ),
                "method": (
                    "GPS segments 5-120 km/h, gap <=15m inside "
                    "checkout(A)->checkin(B); wait=window-moving"
                ),
            },
            "area_actual": self._area_metrics(outcomes),
            "shift_actual": {
                "evaluable_completed_jobs": len(shift_values),
                "completed_jobs_in_shift": int(shift_values.sum()),
                "attribution_missing": len(completed) - len(shift_values),
            },
            "ai_planning": {
                "evaluated_snapshots": len(planning_values),
                "target_seconds": 5.0,
                "average_seconds": (
                    round(float(np.mean(planning_values)), 6)
                    if planning_values
                    else None
                ),
                "p95_seconds": _percentile_95(planning_values),
                "max_seconds": (
                    round(max(planning_values), 6) if planning_values else None
                ),
                "target_met_snapshots": sum(
                    value <= 5.0 for value in planning_values
                ),
                "target_met_rate_percent": _rate(
                    sum(value <= 5.0 for value in planning_values),
                    len(planning_values),
                ),
            },
            # This is the remaining plan at the last snapshot, not an actual
            # end-of-day outcome and not a sum across snapshots.
            "latest_snapshot_plan": latest_plan,
            "limitations": [
                "GPS distance/travel only covers technicians and legs present in the supplied GPS sample.",
                "GPS moving time is a filtered estimate, not road-engine map matching.",
                "Wait includes stationary/break time inside checkout-to-next-checkin windows.",
                "Shift KPI is null/missing without a roster containing SHIFT_START and SHIFT_END.",
            ],
        }
        return report


def _write_csv_atomic(path: Path, frame: pd.DataFrame) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8-sig")
    temporary.replace(path)


def _write_json_atomic(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def write_daily_outcome(output_dir: str | Path, result: DailyOutcomeResult) -> None:
    """Write a complete report directory; each exposed file is atomic."""

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv_atomic(
        output_dir / "checklist_outcomes.csv", result.checklist_outcomes
    )
    _write_csv_atomic(output_dir / "travel_legs.csv", result.travel_legs)
    _write_csv_atomic(
        output_dir / "technician_outcomes.csv", result.technician_outcomes
    )
    _write_json_atomic(output_dir / "daily_summary.json", result.summary)
