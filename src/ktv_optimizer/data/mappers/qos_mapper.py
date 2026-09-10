"""Normalization rules for the supplied QOS CSV exports."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pandas as pd

from ktv_optimizer.domain import GeoPoint, Job, Visit


DATE_SENTINELS = {
    "-1",
    "1000-01-01",
    "1000-01-01 00:00:00",
}
COORDINATE_PATTERN = r"\(\s*([-\d.]+)\s*,\s*([-\d.]+)\s*\)"
COUNT_COLUMNS = (
    "NUM_DISCUSSION",
    "NUM_SOS_DISCUSSION",
    "NUM_APPOINTMENT",
    "APPOINTTIMES_ASSIGNED",
)


def _clean_text(series: pd.Series) -> pd.Series:
    result = series.astype("string").str.strip()
    return result.mask(result.eq(""))


def parse_datetime_series(series: pd.Series) -> pd.Series:
    cleaned = _clean_text(series).mask(
        lambda values: values.isin(DATE_SENTINELS)
    )
    return pd.to_datetime(cleaned, format="mixed", errors="coerce")


def normalize_maintenance_frame(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for column in (
        "OBJ_ID",
        "BRANCH_NAME",
        "CHECKLIST_ID",
        "CHECKLIST_STATUS",
        "CASE_TYPE",
        "SERVICES_LIST",
        "OBJ_LOCATION",
        "OBJ_TYPE_LV1",
        "OBJ_TYPE_VIP",
        "FLAG_ON_TIME",
        "EMP_ACCOUNT",
        "EMP_LEVEL",
        "PROCESS_NOTE",
    ):
        if column in result:
            result[column] = _clean_text(result[column])

    for column in ("CREATE_DATE", "FINISH_DATE"):
        if column in result:
            result[column] = parse_datetime_series(result[column])

    for column in COUNT_COLUMNS:
        if column in result:
            result[column] = (
                pd.to_numeric(result[column], errors="coerce")
                .fillna(0)
                .astype("Int64")
            )
    return result


def normalize_checkin_frame(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for column in (
        "CHECKLIST_ID",
        "LAT_LNG_IN",
        "LAT_LNG_OUT",
        "CHECKIN_DATE",
        "CHECKOUT_DATE",
        "EMP_CODE",
    ):
        if column in result:
            result[column] = _clean_text(result[column])

    for column in ("CHECKIN_DATE", "CHECKOUT_DATE"):
        if column in result:
            result[column] = parse_datetime_series(result[column])

    for source, latitude, longitude in (
        ("LAT_LNG_IN", "LATITUDE_IN", "LONGITUDE_IN"),
        ("LAT_LNG_OUT", "LATITUDE_OUT", "LONGITUDE_OUT"),
    ):
        if source not in result:
            continue
        coordinates = result[source].str.extract(COORDINATE_PATTERN)
        result[latitude] = pd.to_numeric(coordinates[0], errors="coerce")
        result[longitude] = pd.to_numeric(coordinates[1], errors="coerce")
    return result


def _merge_services(values: Iterable[Any]) -> str | pd.NA:
    services: set[str] = set()
    for value in values:
        if pd.isna(value):
            continue
        services.update(
            item.strip()
            for item in str(value).split("|")
            if item.strip()
        )
    return " | ".join(sorted(services)) if services else pd.NA


def collapse_maintenance_checklists(frame: pd.DataFrame) -> pd.DataFrame:
    """Return one row per checklist without arbitrary many-to-many joins.

    In the supplied CSV, repeated checklist rows agree on the documented
    fields and differ in ``SERVICES_LIST``. Services are unioned and the
    original row multiplicity is retained in ``SOURCE_RECORD_COUNT``.
    """

    if "CHECKLIST_ID" not in frame:
        raise KeyError("CHECKLIST_ID is required")

    result = frame.drop_duplicates("CHECKLIST_ID", keep="first").copy()
    grouped = frame.groupby("CHECKLIST_ID", dropna=False, sort=False)
    result["SOURCE_RECORD_COUNT"] = (
        result["CHECKLIST_ID"].map(grouped.size()).astype("Int64")
    )
    if "SERVICES_LIST" in frame:
        services = grouped["SERVICES_LIST"].agg(_merge_services)
        result["SERVICES_LIST"] = result["CHECKLIST_ID"].map(services)
    return result.reset_index(drop=True)


def _optional(value: Any) -> Any | None:
    return None if pd.isna(value) else value


def _integer(value: Any) -> int:
    return 0 if pd.isna(value) else int(value)


def _services(value: Any) -> tuple[str, ...]:
    if pd.isna(value):
        return ()
    return tuple(item.strip() for item in str(value).split("|") if item.strip())


def row_to_job(row: pd.Series) -> Job:
    return Job(
        checklist_id=str(row["CHECKLIST_ID"]),
        object_id=_optional(row.get("OBJ_ID")),
        branch_name=_optional(row.get("BRANCH_NAME")),
        status=_optional(row.get("CHECKLIST_STATUS")),
        created_at=_optional(row.get("CREATE_DATE")),
        finished_at=_optional(row.get("FINISH_DATE")),
        services=_services(row.get("SERVICES_LIST")),
        object_location=_optional(row.get("OBJ_LOCATION")),
        case_type=_optional(row.get("CASE_TYPE")),
        customer_type=_optional(row.get("OBJ_TYPE_LV1")),
        vip_type=_optional(row.get("OBJ_TYPE_VIP")),
        on_time_flag=_optional(row.get("FLAG_ON_TIME")),
        discussion_count=_integer(row.get("NUM_DISCUSSION")),
        sos_discussion_count=_integer(row.get("NUM_SOS_DISCUSSION")),
        appointment_count=_integer(row.get("NUM_APPOINTMENT")),
        technician_account=_optional(row.get("EMP_ACCOUNT")),
        technician_level=_optional(row.get("EMP_LEVEL")),
        reassignment_count=_integer(row.get("APPOINTTIMES_ASSIGNED")),
    )


def _point(row: pd.Series, latitude: str, longitude: str) -> GeoPoint | None:
    lat, lng = row.get(latitude), row.get(longitude)
    if pd.isna(lat) or pd.isna(lng):
        return None
    return GeoPoint(float(lat), float(lng))


def row_to_visit(row: pd.Series) -> Visit:
    return Visit(
        checklist_id=str(row["CHECKLIST_ID"]),
        technician_code=_optional(row.get("EMP_CODE")),
        checkin_at=_optional(row.get("CHECKIN_DATE")),
        checkout_at=_optional(row.get("CHECKOUT_DATE")),
        checkin_location=_point(row, "LATITUDE_IN", "LONGITUDE_IN"),
        checkout_location=_point(row, "LATITUDE_OUT", "LONGITUDE_OUT"),
    )

