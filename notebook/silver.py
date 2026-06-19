"""
SILVER STAGE - independent, retryable on its own.
Reads the LATEST file from the bronze container (does not depend on
bronze.py having run in the same process), applies production-grade
cleaning and validation, writes to the silver container.

Run on its own: python silver.py
"""

import json
import pandas as pd
import numpy as np
from datetime import datetime, timezone

from adls_helpers import (
    get_service_client,
    write_json_to_adls,
    read_json_from_adls,
    get_latest_file_path,
)

EXPECTED_COLS = {
    "EVENT_UNIQUE_ID", "REPORT_DATE", "OCC_DATE", "OCC_HOUR", "REPORT_YEAR", "REPORT_MONTH",
    "OFFENCE", "CSI_CATEGORY", "DIVISION", "PREMISES_TYPE", "LOCATION_TYPE",
    "NEIGHBOURHOOD_158", "HOOD_158", "LONG_WGS84", "LAT_WGS84"
}


def run():
    service_client = get_service_client()

    bronze_path = get_latest_file_path(service_client, "bronze", "crime_incidents")
    print(f"SILVER: reading bronze file {bronze_path}")
    raw = json.loads(read_json_from_adls(service_client, "bronze", bronze_path))

    records = [f["attributes"] for f in raw["features"]]
    bronze_df = pd.DataFrame(records)
    print("SILVER: bronze shape:", bronze_df.shape)

    missing_cols = EXPECTED_COLS - set(bronze_df.columns)
    if missing_cols:
        raise ValueError(f"SCHEMA DRIFT: missing expected columns: {missing_cols}")

    silver = bronze_df[[
        "EVENT_UNIQUE_ID", "REPORT_DATE", "OCC_DATE", "OCC_HOUR", "REPORT_YEAR", "REPORT_MONTH",
        "OFFENCE", "CSI_CATEGORY", "DIVISION", "PREMISES_TYPE", "LOCATION_TYPE",
        "NEIGHBOURHOOD_158", "HOOD_158", "LONG_WGS84", "LAT_WGS84"
    ]].rename(columns={
        "EVENT_UNIQUE_ID": "event_id",
        "REPORT_DATE": "report_date",
        "OCC_DATE": "occurrence_date",
        "OCC_HOUR": "occurrence_hour",
        "REPORT_YEAR": "report_year",
        "REPORT_MONTH": "report_month",
        "OFFENCE": "offence",
        "CSI_CATEGORY": "crime_category",
        "DIVISION": "division",
        "PREMISES_TYPE": "premises_type",
        "LOCATION_TYPE": "location_type",
        "NEIGHBOURHOOD_158": "neighbourhood",
        "HOOD_158": "neighbourhood_id",
        "LONG_WGS84": "longitude",
        "LAT_WGS84": "latitude",
    }).copy()

    silver["report_date"] = pd.to_datetime(silver["report_date"], unit="ms", errors="coerce")
    silver["occurrence_date"] = pd.to_datetime(silver["occurrence_date"], unit="ms", errors="coerce")
    silver["report_year"] = pd.to_numeric(silver["report_year"], errors="coerce").astype("Int64")
    silver["longitude"] = pd.to_numeric(silver["longitude"], errors="coerce")
    silver["latitude"] = pd.to_numeric(silver["latitude"], errors="coerce")
    silver["occurrence_hour"] = pd.to_numeric(silver["occurrence_hour"], errors="coerce").astype("Int64")
    silver["neighbourhood_id"] = silver["neighbourhood_id"].astype(str).str.strip()

    text_cols = ["offence", "crime_category", "division", "premises_type",
                 "location_type", "neighbourhood"]
    for col in text_cols:
        silver[col] = silver[col].astype(str).str.strip()
        silver[col] = silver[col].replace({"None": np.nan, "nan": np.nan, "": np.nan})

    silver["premises_type"] = silver["premises_type"].fillna("Unknown")
    silver["location_type"] = silver["location_type"].fillna("Unknown")

    before = len(silver)
    silver = silver.dropna(subset=["event_id", "offence", "division", "report_date"])
    dropped = before - len(silver)
    if dropped > 0:
        print(f"SILVER WARNING: dropped {dropped} rows missing critical fields")

    before = len(silver)
    silver = silver.drop_duplicates(subset=["event_id"], keep="last")
    deduped = before - len(silver)
    if deduped > 0:
        print(f"SILVER INFO: removed {deduped} duplicate event_id(s)")

    now = pd.Timestamp(datetime.now(timezone.utc)).tz_localize(None)
    silver["data_quality_flag"] = np.where(silver["report_date"] > now, "future_date", "ok")
    future_count = (silver["data_quality_flag"] == "future_date").sum()
    if future_count > 0:
        print(f"SILVER WARNING: {future_count} record(s) flagged future_date")

    bad_geo = ~silver["latitude"].between(43.0, 44.5) | ~silver["longitude"].between(-80.0, -78.5)
    if bad_geo.sum() > 0:
        print(f"SILVER WARNING: {bad_geo.sum()} record(s) flagged bad_geo")
        silver.loc[bad_geo, "data_quality_flag"] = "bad_geo"

    bad_hour = ~silver["occurrence_hour"].between(0, 23)
    if bad_hour.sum() > 0:
        print(f"SILVER WARNING: {bad_hour.sum()} record(s) have invalid occurrence_hour")
        silver.loc[bad_hour, "data_quality_flag"] = "bad_hour"

    silver["_source"] = "toronto_police_mci_api"
    silver["_source_file"] = f"bronze/{bronze_path}"
    silver["_ingested_at"] = pd.Timestamp(datetime.now(timezone.utc)).tz_localize(None)

    print("\nSILVER: summary")
    print(f"Final row count: {len(silver)}")
    print(f"Date range: {silver['report_date'].min()} to {silver['report_date'].max()}")
    print(f"Data quality flags:\n{silver['data_quality_flag'].value_counts()}")

    extract_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    silver_path = f"crime_incidents/{extract_ts}.json"
    write_json_to_adls(
        service_client, "silver", silver_path,
        silver.to_json(orient="records", date_format="iso")
    )
    print(f"SILVER: complete at {datetime.now(timezone.utc).isoformat()}")
    return silver_path


if __name__ == "__main__":
    run()
    
