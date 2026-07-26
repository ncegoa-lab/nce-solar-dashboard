import datetime as dt
import json
import os
import re
from pathlib import Path

from selenium import webdriver


SYSTEMS_FILE = Path("fronius_systems.json")
OUTPUT_FILE = Path("fronius_weekly_generation.json")


def parse_energy(value: str) -> float:
    match = re.search(r"([-+]?\d+(?:\.\d+)?)\s*([kKmMwW]?)[wW]h", value or "")
    if not match:
        return 0.0
    amount = float(match.group(1))
    prefix = match.group(2).lower()
    if prefix == "m":
        return amount * 1000
    if prefix == "k":
        return amount
    return amount / 1000


def main() -> None:
    systems = json.loads(SYSTEMS_FILE.read_text(encoding="utf-8"))["systems"]
    today = dt.date.today()
    start_date = today - dt.timedelta(days=today.weekday())
    end_date = start_date + dt.timedelta(days=6)
    dates = [start_date + dt.timedelta(days=offset) for offset in range(7)]

    options = webdriver.ChromeOptions()
    options.add_experimental_option(
        "debuggerAddress",
        os.getenv("FRONIUS_DEBUGGER_ADDRESS", "127.0.0.1:9223"),
    )
    driver = webdriver.Chrome(options=options)

    try:
        rows = []
        for system in systems:
            daily = []
            for day in dates:
                url = (
                    "https://www.solarweb.com/Chart/GetChartNew"
                    f"?pvSystemId={system['system_id']}"
                    f"&year={day.year}&month={day.month}&day={day.day}"
                    "&interval=day&view=production"
                )
                result = driver.execute_async_script(
                    """
                    const url = arguments[0];
                    const done = arguments[1];
                    fetch(url, { credentials: 'include' })
                      .then(r => r.json().then(body => ({ status: r.status, body })))
                      .then(done)
                      .catch(err => done({ error: String(err) }));
                    """,
                    url,
                )
                if result.get("error") or result.get("status") != 200:
                    energy_text = "0 Wh"
                else:
                    energy_text = result.get("body", {}).get("settings", {}).get(
                        "sumValue", "0 Wh"
                    )
                daily.append(
                    {
                        "date": day.isoformat(),
                        "generation_kwh": parse_energy(energy_text),
                        "source_value": energy_text,
                    }
                )
            year_url = (
                "https://www.solarweb.com/Chart/GetChartNew"
                f"?pvSystemId={system['system_id']}"
                "&year=2026&month=1&day=1"
                "&interval=year&view=production"
            )
            year_result = driver.execute_async_script(
                """
                const url = arguments[0];
                const done = arguments[1];
                fetch(url, { credentials: 'include' })
                  .then(r => r.json().then(body => ({ status: r.status, body })))
                  .then(done)
                  .catch(err => done({ error: String(err) }));
                """,
                year_url,
            )
            if year_result.get("error") or year_result.get("status") != 200:
                year_energy_text = "0 Wh"
            else:
                year_energy_text = year_result.get("body", {}).get("settings", {}).get(
                    "sumValue", "0 Wh"
                )

            rows.append(
                {
                    "brand": "Fronius",
                    "system_id": system["system_id"],
                    "name": system["name"],
                    "status": system.get("status", ""),
                    "daily": daily,
                    "weekly_generation_kwh": round(
                        sum(item["generation_kwh"] for item in daily), 3
                    ),
                    "year_generation_kwh": round(parse_energy(year_energy_text), 3),
                    "year_source_value": year_energy_text,
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
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
