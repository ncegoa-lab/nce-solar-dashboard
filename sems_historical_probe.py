import datetime as dt
import json
import os
from pathlib import Path
from typing import Optional

import requests


LOGIN_URL = "https://www.semsportal.com/api/v2/Common/CrossLogin"
STATIONS_FILE = Path("sems_station_data.json")
OUTPUT_FILE = Path("sems_historical_probe.json")


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def token_header(token_data: Optional[dict[str, object]] = None) -> str:
    header = {"version": "v2.1.0", "client": "ios", "language": "en"}
    if token_data:
        for key in ("uid", "timestamp", "token"):
            if key in token_data:
                header[key] = token_data[key]
    return json.dumps(header, separators=(",", ":"))


def main() -> None:
    stations = json.loads(STATIONS_FILE.read_text(encoding="utf-8"))["stations"]
    station = stations[0]
    station_id = station["powerstation_id"]
    today = dt.date.today()
    start = today - dt.timedelta(days=6)

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
        json={"account": require_env("SEMS_USERNAME"), "pwd": require_env("SEMS_PASSWORD")},
        timeout=30,
    )
    login_response.raise_for_status()
    login_body = login_response.json()
    api_base = login_body.get("api", "https://www.semsportal.com/api/")
    session.headers.update({"Token": token_header(login_body["data"])})

    payloads = [
        {"powerStationId": station_id},
        {"powerstation_id": station_id},
        {"id": station_id},
        {"powerStationId": station_id, "date": today.isoformat()},
        {"powerStationId": station_id, "startDate": start.isoformat(), "endDate": today.isoformat()},
        {"powerStationId": station_id, "start": start.isoformat(), "end": today.isoformat()},
        {"powerStationId": station_id, "date": today.isoformat(), "dateType": 1},
        {"powerStationId": station_id, "year": today.year, "month": today.month, "day": today.day},
    ]
    endpoint_names = [
        "v2/PowerStationMonitor/GetMonitorDetailByPowerstationId",
        "v2/PowerStationMonitor/GetPowerStationMonitorDetailByPowerstationId",
        "v2/PowerStationMonitor/GetPowerStationDetail",
        "v2/PowerStationMonitor/GetPowerStationData",
        "v2/PowerStationMonitor/GetPowerStationChart",
        "v2/PowerStationMonitor/GetPowerStationCurve",
        "v2/PowerStationMonitor/GetPowerStationStatistics",
        "v2/PowerStationMonitor/GetPowerStationPowerAndIncome",
        "v2/PowerStationMonitor/GetPowerStationPowerAndIncomeByDay",
        "v2/PowerStationMonitor/QueryPowerStationChart",
        "v2/Statistics/GetPowerStationChart",
        "v2/Statistics/GetPowerStationStatistics",
        "v2/Statistics/GetPowerStationPowerAndIncome",
        "v2/PowerStation/GetPowerStationPowerAndIncome",
    ]

    results = []
    for endpoint in endpoint_names:
        for payload in payloads:
            url = f"{api_base}{endpoint}"
            try:
                response = session.post(url, json=payload, timeout=20)
                text = response.text[:1500]
                try:
                    body = response.json()
                except ValueError:
                    body = {"text": text}
                results.append(
                    {
                        "endpoint": endpoint,
                        "payload": payload,
                        "status_code": response.status_code,
                        "body": body,
                    }
                )
            except Exception as exc:
                results.append({"endpoint": endpoint, "payload": payload, "error": str(exc)})

    OUTPUT_FILE.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Saved SEMS historical probe to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
