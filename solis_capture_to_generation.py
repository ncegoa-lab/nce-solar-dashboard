import datetime as dt
import json
from pathlib import Path


CAPTURE_FILE = Path("solis_network_capture.json")
OUTPUT_FILE = Path("solis_generation.json")


STATUS_MAP = {
    1: "Online",
    2: "Offline",
    3: "Fault",
}


def load_station_records():
    capture = json.loads(CAPTURE_FILE.read_text(encoding="utf-8"))
    latest_records = None
    latest_direct_records = None
    for response in capture.get("responses", []):
        if not response.get("url", "").endswith("/api/station/list"):
            continue
        body = response.get("body")
        if not body:
            continue
        payload = json.loads(body) if isinstance(body, str) else body
        records = payload.get("data", {}).get("page", {}).get("records", [])
        if records:
            if response.get("directFetch"):
                latest_direct_records = records
            else:
                latest_records = records
    if latest_direct_records:
        return latest_direct_records, capture.get("captured_at")
    if latest_records:
        return latest_records, capture.get("captured_at")
    raise RuntimeError("No Solis station records found in solis_network_capture.json")


def total_kwh(record):
    value = record.get("allEnergy1")
    if value is not None:
        return value
    value = record.get("allEnergy")
    unit = record.get("allEnergyStr", "")
    if value is None:
        return None
    if "mwh" in unit.lower():
        return float(value) * 1000
    return float(value)


def main():
    records, captured_at = load_station_records()
    systems = []
    for record in records:
        systems.append(
            {
                "name": record.get("stationName"),
                "status": STATUS_MAP.get(record.get("state"), str(record.get("state"))),
                "capacity_kw": record.get("capacity1") or record.get("capacity"),
                "current_power_kw": record.get("power") or 0,
                "today_generation_kwh": record.get("dayEnergy1")
                if record.get("dayEnergy1") is not None
                else record.get("dayEnergy"),
                "weekly_generation_kwh": None,
                "month_generation_kwh": record.get("monthEnergy1")
                if record.get("monthEnergy1") is not None
                else record.get("monthEnergy"),
                "year_generation_kwh": record.get("yearEnergy1")
                if record.get("yearEnergy1") is not None
                else record.get("yearEnergy"),
                "total_generation_kwh": total_kwh(record),
                "system_id": record.get("id"),
                "source_sno": record.get("sno"),
                "data_timestamp": record.get("dataTimestampStr"),
            }
        )

    payload = {
        "source": str(CAPTURE_FILE),
        "generated_at": dt.datetime.now().replace(microsecond=0).isoformat(),
        "captured_at": captured_at,
        "systems": systems,
        "notes": [
            "SolisCloud station list provides today, month, year, total, and current power.",
            "Weekly generation is left blank until a weekly SolisCloud endpoint is captured.",
        ],
    }
    OUTPUT_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Saved {len(systems)} Solis systems to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
