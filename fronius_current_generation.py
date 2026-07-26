import datetime as dt
import json
import os
import re
from pathlib import Path

from selenium import webdriver


SYSTEMS_FILE = Path("fronius_systems.json")
OUTPUT_FILE = Path("fronius_current_generation.json")


def parse_energy_kwh(value: str) -> float:
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


def fetch_energy(driver, system_id: str, interval: str, report_date: dt.date) -> tuple[float, str]:
    url = (
        "https://www.solarweb.com/Chart/GetChartNew"
        f"?pvSystemId={system_id}"
        f"&year={report_date.year}&month={report_date.month}&day={report_date.day}"
        f"&interval={interval}&view=production"
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
        return 0.0, "0 Wh"
    source_value = result.get("body", {}).get("settings", {}).get("sumValue", "0 Wh")
    return parse_energy_kwh(source_value), source_value


def main() -> None:
    systems = json.loads(SYSTEMS_FILE.read_text(encoding="utf-8"))["systems"]
    report_date = dt.date.today()

    options = webdriver.ChromeOptions()
    options.add_experimental_option(
        "debuggerAddress",
        os.getenv("FRONIUS_DEBUGGER_ADDRESS", "127.0.0.1:9223"),
    )
    driver = webdriver.Chrome(options=options)

    try:
        rows = []
        for system in systems:
            today_kwh, today_source = fetch_energy(
                driver, system["system_id"], "day", report_date
            )
            total_kwh, total_source = fetch_energy(
                driver, system["system_id"], "all", report_date
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
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
