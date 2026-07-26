import datetime as dt
import json
import os
from pathlib import Path

import requests


BASE_URL = "https://www.auroravision.net"
OUTPUT_FILE = Path("fimer_generation.json")
PORTFOLIO_ID = os.getenv("FIMER_PORTFOLIO_ID", "31841756")


def require_env(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def iso_utc(value):
    return (
        value.astimezone(dt.timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def local_ranges():
    now = dt.datetime.now().astimezone()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "now": now,
        "today": today,
        "week": today - dt.timedelta(days=today.weekday()),
        "month": today.replace(day=1),
        "year": today.replace(month=1, day=1),
    }


def get_json(session, path, **params):
    response = session.get(f"{BASE_URL}{path}", params=params, timeout=30)
    body = None
    try:
        body = response.json()
    except ValueError:
        body = response.text
    return {"status": response.status_code, "body": body}


def main():
    username = require_env("FIMER_USERNAME")
    password = require_env("FIMER_PASSWORD")

    session = requests.Session()
    session.auth = (username, password)
    session.headers.update(
        {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en",
            "User-Agent": "Mozilla/5.0",
        }
    )

    login = get_json(session, "/ums/v1/login", setCookie="true")
    if login["status"] != 200:
        raise RuntimeError(f"FIMER backend login failed with HTTP {login['status']}")
    session.auth = None

    ranges = local_ranges()
    now = ranges["now"]
    plants = get_json(
        session,
        f"/asset/v1/portfolios/{PORTFOLIO_ID}/plants",
        includePerformanceProfiles="true",
    )

    energy = []
    for key in ("today", "week", "month", "year"):
        value = get_json(
            session,
            f"/telemetry/v1/plantGroups/{PORTFOLIO_ID}/energy/GenerationEnergy",
            sdt=iso_utc(ranges[key]),
            edt=iso_utc(now),
        )
        energy.append({"key": key, "value": value})

    plant_energy = []
    plant_rows = plants.get("body", []) if plants["status"] == 200 else []
    for plant in plant_rows:
        install_date = plant.get("configuration", {}).get("installDate")
        if install_date:
            total_start = dt.datetime.fromisoformat(
                install_date.replace("Z", "+00:00")
            ).astimezone()
        else:
            total_start = ranges["week"]

        values = {}
        for key, start in {
            "today": ranges["today"],
            "week": ranges["week"],
            "month": ranges["month"],
            "year": ranges["year"],
            "total": total_start,
        }.items():
            values[key] = get_json(
                session,
                f"/telemetry/v1/plants/{plant['entityID']}/energy/GenerationEnergy",
                agp="All",
                afx="Delta",
                sdt=iso_utc(start),
                edt=iso_utc(now),
            )
        plant_energy.append({"plant": plant, "values": values})

    payload = {
        "plants": plants,
        "energy": energy,
        "now": iso_utc(now),
        "rangeStarts": {
            key: iso_utc(value)
            for key, value in ranges.items()
            if key != "now"
        },
        "login": {"status": login["status"]},
        "plantEnergy": plant_energy,
    }

    OUTPUT_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Saved FIMER generation data to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
