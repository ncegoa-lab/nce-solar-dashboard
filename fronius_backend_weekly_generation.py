import datetime as dt
import json
import re
from pathlib import Path

from fronius_backend_session import SOLARWEB_BASE, solarweb_session


SYSTEMS_FILE = Path("fronius_systems.json")
OUTPUT_FILE = Path("fronius_weekly_generation.json")


def parse_energy(value):
    match = re.search(r"([-+]?\d+(?:,\d{3})*(?:\.\d+)?)\s*([kKmMwW]?)[wW]h", value or "")
    if not match:
        return 0.0
    amount = float(match.group(1).replace(",", ""))
    prefix = match.group(2).lower()
    if prefix == "m":
        return amount * 1000
    if prefix == "k":
        return amount
    return amount / 1000


def chart_energy(session, system_id, day, interval):
    response = session.get(
        f"{SOLARWEB_BASE}/Chart/GetChartNew",
        params={
            "pvSystemId": system_id,
            "year": day.year,
            "month": day.month,
            "day": day.day,
            "interval": interval,
            "view": "production",
        },
        headers={"Accept": "application/json, text/javascript, */*; q=0.01"},
        timeout=30,
    )
    response.raise_for_status()
    source_value = response.json().get("settings", {}).get("sumValue", "0 Wh")
    return parse_energy(source_value), source_value


def main():
    systems = json.loads(SYSTEMS_FILE.read_text(encoding="utf-8"))["systems"]
    today = dt.date.today()
    week_start = today - dt.timedelta(days=today.weekday())
    week_end = week_start + dt.timedelta(days=6)
    month_start = today.replace(day=1)
    dates = [month_start + dt.timedelta(days=offset) for offset in range((today - month_start).days + 1)]
    session = solarweb_session()

    rows = []
    for system in systems:
        daily = []
        for day in dates:
            generation_kwh, source_value = chart_energy(
                session, system["system_id"], day, "day"
            )
            daily.append(
                {
                    "date": day.isoformat(),
                    "generation_kwh": generation_kwh,
                    "source_value": source_value,
                }
            )
        try:
            year_generation_kwh, year_source_value = chart_energy(
                session, system["system_id"], dt.date(2026, 1, 1), "year"
            )
        except Exception:
            year_generation_kwh, year_source_value = 0.0, "0 Wh"
        rows.append(
            {
                "brand": "Fronius",
                "system_id": system["system_id"],
                "name": system["name"],
                "status": system.get("status", ""),
                "daily": daily,
                "weekly_generation_kwh": round(
                    sum(
                        item["generation_kwh"]
                        for item in daily
                        if week_start <= dt.date.fromisoformat(item["date"]) <= min(week_end, today)
                    ),
                    3,
                ),
                "year_generation_kwh": round(year_generation_kwh, 3),
                "year_source_value": year_source_value,
            }
        )

    payload = {
        "generated_at": dt.datetime.now().replace(microsecond=0).isoformat(),
        "start_date": dates[0].isoformat(),
        "end_date": dates[-1].isoformat(),
        "systems": rows,
    }
    OUTPUT_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Saved weekly generation for {len(rows)} Fronius systems to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
