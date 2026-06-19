"""
BRONZE STAGE - independent, retryable on its own.
Fetches live data from the Toronto Police API and lands it raw, untouched,
in the bronze container. Does no cleaning or transformation.

Run on its own: python bronze.py
"""

import os
import json
import requests
from datetime import datetime, timezone

from adls_helpers import get_service_client, write_json_to_adls

TORONTO_API_URL = os.environ.get("TORONTO_API_URL")

if not TORONTO_API_URL:
    raise ValueError("Missing required environment variable: TORONTO_API_URL")


def run():
    print("BRONZE: fetching live data from Toronto Police API...")
    response = requests.get(TORONTO_API_URL)
    response.raise_for_status()
    data = response.json()
    print(f"BRONZE: records fetched: {len(data['features'])}")

    service_client = get_service_client()
    extract_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    file_name = f"crime_incidents/{extract_ts}.json"

    write_json_to_adls(service_client, "bronze", file_name, json.dumps(data))
    print(f"BRONZE: complete at {datetime.now(timezone.utc).isoformat()}")
    return file_name


if __name__ == "__main__":
    run()
