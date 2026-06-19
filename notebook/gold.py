"""
GOLD STAGE - independent, retryable on its own.
Reads the LATEST file from the silver container, builds the
Power BI-ready fact table and patrol-planning summary tables,
writes all of them to the gold container.

Run on its own: python gold.py
"""

import json
import pandas as pd
from datetime import datetime, timezone

from adls_helpers import (
    get_service_client,
    write_json_to_adls,
    read_json_from_adls,
    get_latest_file_path,
)


def run():
    service_client = get_service_client()

    silver_path = get_latest_file_path(service_client, "silver", "crime_incidents")
    print(f"GOLD: reading silver file {silver_path}")
    silver_records = json.loads(read_json_from_adls(service_client, "silver", silver_path))
    silver = pd.DataFrame(silver_records)
    silver["report_date"] = pd.to_datetime(silver["report_date"])
    silver["occurrence_date"] = pd.to_datetime(silver["occurrence_date"])

    gold_fact = silver[[
        "event_id", "report_date", "occurrence_date", "occurrence_hour",
        "report_year", "report_month",
        "offence", "crime_category", "division", "premises_type",
        "neighbourhood", "neighbourhood_id", "latitude", "longitude",
        "data_quality_flag"
    ]].copy()

    gold_fact["occurrence_date_only"] = gold_fact["occurrence_date"].dt.date.astype(str)
    gold_fact["day_of_week"] = gold_fact["occurrence_date"].dt.day_name()
    gold_fact["hour_of_day"] = gold_fact["occurrence_hour"]

    print("GOLD: fact table shape:", gold_fact.shape)

    gold_hotspots = (gold_fact
        .groupby(["neighbourhood", "hour_of_day"])
        .size()
        .reset_index(name="incident_count")
        .sort_values("incident_count", ascending=False))

    gold_neighbourhood_day = (gold_fact
        .groupby(["neighbourhood", "day_of_week"])
        .size()
        .reset_index(name="incident_count")
        .sort_values("incident_count", ascending=False))

    gold_hourly_trend = (gold_fact
        .groupby("hour_of_day")
        .size()
        .reset_index(name="incident_count")
        .sort_values("hour_of_day"))

    print("\n=== Top 5 highest-risk neighbourhood + hour combinations ===")
    print(gold_hotspots.head(5).to_string())

    print("\n=== Peak crime hours, citywide ===")
    print(gold_hourly_trend.to_string())

    extract_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    gold_tables = {
        "fact": gold_fact,
        "hotspots": gold_hotspots,
        "neighbourhood_day": gold_neighbourhood_day,
        "hourly_trend": gold_hourly_trend,
    }

    for name, df in gold_tables.items():
        file_name = f"{name}/{extract_ts}.json"
        write_json_to_adls(
            service_client, "gold", file_name,
            df.to_json(orient="records", date_format="iso")
        )

    print(f"GOLD: complete at {datetime.now(timezone.utc).isoformat()}")


if __name__ == "__main__":
    run()
    
