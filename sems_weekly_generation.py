import datetime as dt
import json
import os
from pathlib import Path
from typing import Optional

import requests


LOGIN_URL = "https://www.semsportal.com/api/v2/Common/CrossLogin"
STATIONS_FILE = Path("sems_station_data.json")
OUTPUT_FILE = Path("sems_weekly_generation.json")


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


def parse_sems_date(value: str) -> dt.date:
    return dt.datetime.strptime(value, "%m/%d/%Y").date()


def main() -> None:
    stations = json.loads(STATIONS_FILE.read_text(encoding="utf-8"))["stations"]
    today = dt.date.today()
    week_start = today - dt.timedelta(days=today.weekday())
    week_end = week_start + dt.timedelta(days=6)
    month_start = today.replace(day=1)
    year_start = dt.date(2026, 1, 1)

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
    if login_body.get("hasError"):
        raise RuntimeError(f"SEMS login failed: {login_body.get('msg')}")

    api_base = login_body.get("api", "https://www.semsportal.com/api/")
    session.headers.update({"Token": token_header(login_body["data"])})

    rows = []
    for station in stations:
        response = session.post(
            f"{api_base}v2/PowerStationMonitor/GetPowerStationPowerAndIncomeByDay",
            json={"powerstation_id": station["powerstation_id"]},
            timeout=30,
        )
        response.raise_for_status()
        body = response.json()
        if body.get("hasError"):
            raise RuntimeError(
                f"Weekly fetch failed for {station.get('stationname')}: {body.get('msg')}"
            )

        by_date = {
            parse_sems_date(item["d"]): float(item.get("p") or 0)
            for item in body.get("data", [])
        }
        daily = []
        cursor = month_start
        while cursor <= today:
            day = cursor
            daily.append({"date": day.isoformat(), "generation_kwh": by_date.get(day, 0.0)})
            cursor += dt.timedelta(days=1)

        rows.append(
            {
                "brand": "GoodWe",
                "station_id": station["powerstation_id"],
                "name": station.get("stationname"),
                "status": station.get("status"),
                "daily": daily,
                "weekly_generation_kwh": round(
                    sum(
                        item["generation_kwh"]
                        for item in daily
                        if week_start <= dt.date.fromisoformat(item["date"]) <= min(week_end, today)
                    ),
                    3,
                ),
                "year_generation_kwh": round(
                    sum(
                        value
                        for day, value in by_date.items()
                        if year_start <= day <= today
                    ),
                    3,
                ),
            }
        )

    payload = {
        "generated_at": dt.datetime.now().replace(microsecond=0).isoformat(),
        "start_date": month_start.isoformat(),
        "end_date": today.isoformat(),
        "stations": rows,
    }
    OUTPUT_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Saved weekly generation for {len(rows)} GoodWe stations to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
