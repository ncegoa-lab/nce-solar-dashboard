import datetime as dt
import json
from pathlib import Path

from fronius_backend_weekly_generation import chart_energy
from fronius_backend_session import solarweb_session


SYSTEMS_FILE = Path("fronius_systems.json")
OUTPUT_FILE = Path("fronius_current_generation.json")


def main():
    systems = json.loads(SYSTEMS_FILE.read_text(encoding="utf-8"))["systems"]
    report_date = dt.date.today()
    session = solarweb_session()

    rows = []
    for system in systems:
        today_kwh, today_source = chart_energy(
            session, system["system_id"], report_date, "day"
        )
        total_kwh, total_source = chart_energy(
            session, system["system_id"], report_date, "all"
        )
        rows.append(
            {
                "brand": "Fronius",
                "system_id": system["system_id"],
                "name": system["name"],
                "status": system.get("status", ""),
                "date": report_date.isoformat(),
                "today_generation_kwh": today_kwh,
                "today_source_value": today_source,
                "total_generation_kwh": total_kwh,
                "total_source_value": total_source,
            }
        )

    payload = {
        "generated_at": dt.datetime.now().replace(microsecond=0).isoformat(),
        "date": report_date.isoformat(),
        "systems": rows,
    }
    OUTPUT_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Saved current generation for {len(rows)} Fronius systems to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
