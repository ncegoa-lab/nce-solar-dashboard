import datetime as dt
import json
import os
from pathlib import Path
from typing import Optional

import requests


LOGIN_URL = "https://www.semsportal.com/api/v2/Common/CrossLogin"
OUTPUT_FILE = Path("sems_station_data.json")


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def token_header(token_data: Optional[dict[str, object]] = None) -> str:
    header = {
        "version": "v2.1.0",
        "client": "ios",
        "language": "en",
    }
    if token_data:
        for key in ("uid", "timestamp", "token"):
            if key in token_data:
                header[key] = token_data[key]
    return json.dumps(header, separators=(",", ":"))


def main() -> None:
    session = requests.Session()
    session.headers.update(
        {
            "Content-Type": "application/json",
            "Token": token_header(),
            "User-Agent": "Mozilla/5.0",
        }
    )

    login_response = session.post(
        LOGIN_URL,
        json={
            "account": require_env("SEMS_USERNAME"),
            "pwd": require_env("SEMS_PASSWORD"),
        },
        timeout=30,
    )
    login_response.raise_for_status()
    login_body = login_response.json()
    if login_body.get("hasError"):
        raise RuntimeError(f"SEMS login failed: {login_body.get('msg')}")

    api_base = login_body.get("api", "https://www.semsportal.com/api/")
    session.headers.update({"Token": token_header(login_body["data"])})

    stations = []
    page_index = 1
    page_size = 100
    total_records = None

    while total_records is None or len(stations) < total_records:
        station_response = session.post(
            f"{api_base}v2/PowerStationMonitor/QueryPowerStationMonitor",
            json={
                "powerStationId": "",
                "key": "",
                "pageIndex": page_index,
                "pageSize": page_size,
            },
            timeout=30,
        )
        station_response.raise_for_status()
        station_body = station_response.json()
        if station_body.get("hasError"):
            raise RuntimeError(f"Station fetch failed: {station_body.get('msg')}")

        data = station_body.get("data", {})
        page_stations = data.get("list", [])
        total_records = data.get("record", len(page_stations))
        stations.extend(page_stations)

        if not page_stations:
            break
        page_index += 1

    payload = {
        "generated_at": dt.datetime.now().replace(microsecond=0).isoformat(),
        "api_base": api_base,
        "stations": stations,
    }
    OUTPUT_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Saved {len(payload['stations'])} stations to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
