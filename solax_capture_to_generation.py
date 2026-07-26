import datetime as dt
import json
import re
import csv
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path


CAPTURE_FILE = Path("solax_network_capture.json")
OUTPUT_FILE = Path("solax_generation.json")
HISTORY_FILE = Path("solax_daily_history.csv")
SOLAX_API_HOST = "https://euapi.solaxcloud.com"
SOLAX_AES_KEY_HEX = "hj7x22H$yuBI0456".encode().hex()
SOLAX_AES_IV_HEX = "NIfb&74GUY86Gfgh".encode().hex()


def number_after(lines, label):
    for index, line in enumerate(lines):
        if line.strip().lower() != label.lower():
            continue
        for candidate in lines[index + 1 : index + 5]:
            match = re.search(r"-?\d+(?:\.\d+)?", candidate.replace(",", ""))
            if match:
                return float(match.group(0))
    return None


def value_before(lines, label):
    for index, line in enumerate(lines):
        if line.strip().lower() != label.lower() or index == 0:
            continue
        match = re.search(r"-?\d+(?:\.\d+)?", lines[index - 1].replace(",", ""))
        if match:
            return float(match.group(0))
    return None


def first_account_name(lines):
    for index, line in enumerate(lines):
        if line.strip().lower() != "overview":
            continue
        for candidate in lines[index + 1 : index + 8]:
            cleaned = candidate.strip()
            if cleaned and cleaned.lower() not in {
                "overview",
                "plants",
                "devices",
                "alarm",
                "applications",
                "report",
                "benefits",
                "account",
                "system",
                "help center",
            }:
                return cleaned
    return "SolaX"


def parse_number(text):
    match = re.search(r"-?\d+(?:,\d{3})*(?:\.\d+)?", text or "")
    return float(match.group(0).replace(",", "")) if match else None


def solax_encrypt(text):
    process = subprocess.run(
        [
            "openssl",
            "enc",
            "-aes-128-cbc",
            "-base64",
            "-A",
            "-K",
            SOLAX_AES_KEY_HEX,
            "-iv",
            SOLAX_AES_IV_HEX,
        ],
        input=text.encode("utf-8"),
        capture_output=True,
        check=True,
    )
    return process.stdout.decode("utf-8")


def solax_decrypt(cipher_text):
    process = subprocess.run(
        [
            "openssl",
            "enc",
            "-aes-128-cbc",
            "-d",
            "-base64",
            "-A",
            "-K",
            SOLAX_AES_KEY_HEX,
            "-iv",
            SOLAX_AES_IV_HEX,
        ],
        input=(cipher_text or "").encode("utf-8"),
        capture_output=True,
        check=True,
    )
    return process.stdout.decode("utf-8")


def solax_request_token(capture):
    storage = capture.get("browserStorage") or {}
    for storage_name in ("localStorage", "sessionStorage"):
        token = (storage.get(storage_name) or {}).get("TOKEN") or (storage.get(storage_name) or {}).get("LASTTOKEN")
        if token:
            return token
    return None


def solax_storage_value(capture, key, default=None):
    storage = capture.get("browserStorage") or {}
    for storage_name in ("localStorage", "sessionStorage"):
        value = (storage.get(storage_name) or {}).get(key)
        if value not in (None, ""):
            return value
    params = (storage.get("sessionStorage") or {}).get("userCenterUrlParams") or ""
    parsed = urllib.parse.parse_qs(params.lstrip("?"))
    if parsed.get(key):
        return parsed[key][0]
    return default


def solax_request(path, token, params=None, body=None, method="POST"):
    params = params or {}
    encrypted_params = solax_encrypt(
        json.dumps(
            {
                **params,
                "timeStamp": int(time.time() * 1000),
                "requestId": str(int(time.time() * 1000000))[-8:],
            },
            separators=(",", ":"),
        )
    )
    url = f"{SOLAX_API_HOST}{path}?{urllib.parse.urlencode({'data': encrypted_params})}"
    headers = {
        "Content-Type": "application/json",
        "Lang": "en_US",
        "deviceType": "3",
        "websiteType": "0",
        "source": "0",
        "crytoVer": "1",
        "version": "green",
        "Permission-Version": "v7.2.0",
        "platform": "4",
        "slx-base-ver": "",
        "token": token,
    }
    data = None
    if method.upper() == "POST":
        data = json.dumps({"data": solax_encrypt(json.dumps(body or {}, separators=(",", ":")))}).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    with urllib.request.urlopen(request, timeout=30) as response:
        response_payload = json.loads(response.read().decode("utf-8"))
    if response_payload.get("data"):
        decoded = json.loads(solax_decrypt(response_payload["data"]))
        if decoded.get("success") is False:
            raise RuntimeError(decoded.get("message") or decoded.get("msg") or decoded.get("code"))
        return decoded
    if response_payload.get("success") is False or response_payload.get("code") not in (None, 0):
        raise RuntimeError(response_payload.get("message") or response_payload.get("msg") or response_payload.get("code"))
    return response_payload


def numeric(value):
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def watts_to_kw(value):
    value = numeric(value)
    if value is None:
        return None
    return round(value / 1000, 3) if abs(value) > 100 else value


def energy_total_from_response(result):
    candidates = [
        result.get("yieldTotal"),
        result.get("pvYieldTotal"),
        (result.get("energyCard") or {}).get("yieldTotal"),
        (result.get("energyCard") or {}).get("pvYieldTotal"),
        (result.get("yield") or {}).get("totalYield"),
        (result.get("inverterYieldResp") or {}).get("totalYield"),
        result.get("yieldValue"),
    ]
    for candidate in candidates:
        value = numeric(candidate)
        if value is not None:
            return value
    return None


def daily_records_total(result, start_date, end_date):
    records = result.get("records") or {}
    for key in ("acYield", "pvYield", "load", "epsYield"):
        rows = records.get(key)
        if not isinstance(rows, list):
            continue
        total = 0.0
        matched = False
        for row in rows:
            day = numeric(row.get("xais"))
            value = numeric(row.get("yais"))
            if day is None or value is None:
                continue
            try:
                row_date = dt.date(start_date.year, start_date.month, int(day))
            except ValueError:
                continue
            if start_date <= row_date <= end_date:
                total += value
                matched = True
        if matched:
            return round(total, 3)
    return None


def fetch_solax_weekly(token, station_id, end_date):
    start_date = end_date - dt.timedelta(days=end_date.weekday())
    week_end = start_date + dt.timedelta(days=6)
    total = 0.0
    matched = False
    month_cursor = dt.date(start_date.year, start_date.month, 1)
    while month_cursor <= week_end:
        response = solax_request(
            "/zeus/v1/overview/energyInfo",
            token,
            body={
                "siteId": station_id,
                "dimension": 2,
                "year": month_cursor.year,
                "month": month_cursor.month,
            },
            method="POST",
        )
        result = response.get("result") or {}
        month_start = max(start_date, month_cursor)
        if month_cursor.month == 12:
            next_month = dt.date(month_cursor.year + 1, 1, 1)
        else:
            next_month = dt.date(month_cursor.year, month_cursor.month + 1, 1)
        month_end = min(week_end, next_month - dt.timedelta(days=1))
        value = daily_records_total(result, month_start, month_end)
        if value is not None:
            total += value
            matched = True
        month_cursor = next_month
    return round(total, 3) if matched else None


def fetch_solax_year(token, station_id, year=2026):
    response = solax_request(
        "/zeus/v1/overview/energyInfo",
        token,
        body={"siteId": station_id, "dimension": 3, "year": year},
        method="POST",
    )
    return energy_total_from_response(response.get("result") or response)


def decoded_solax_responses(capture):
    decoded = []
    for item in capture.get("responses") or []:
        body = item.get("body")
        if not body:
            continue
        try:
            encrypted = json.loads(body).get("data")
        except Exception:
            continue
        if not encrypted:
            continue
        try:
            decoded.append(
                {
                    "url": item.get("url", ""),
                    "payload": json.loads(solax_decrypt(encrypted)),
                }
            )
        except Exception:
            continue
    return decoded


def latest_station_records(capture):
    records_by_id = {}
    for item in decoded_solax_responses(capture):
        if "/station/page" not in item["url"]:
            continue
        result = item.get("payload", {}).get("result") or {}
        for record in result.get("records") or []:
            station_id = str(record.get("stationId") or "")
            if station_id:
                records_by_id[station_id] = record
    return records_by_id


def enrich_with_solax_backend(capture, systems):
    token = solax_request_token(capture)
    station_records = latest_station_records(capture)
    if not token or not station_records:
        return

    by_name = {system.get("name"): system for system in systems}
    try:
        end_date = dt.date.fromisoformat((capture.get("captured_at") or dt.date.today().isoformat())[:10])
    except ValueError:
        end_date = dt.date.today()
    for station_id, record in station_records.items():
        system = by_name.get(record.get("stationName"))
        if not system:
            continue
        system["system_id"] = station_id
        system["capacity_kw"] = record.get("pvCapacity") or system.get("capacity_kw")
        system["today_generation_kwh"] = record.get("pvYield") or system.get("today_generation_kwh")
        try:
            overview = solax_request(
                "/zeus/v1/overview/siteOverview",
                token,
                params={"siteId": station_id},
                method="GET",
            )
        except Exception as error:
            system.setdefault("backend_errors", []).append(f"siteOverview failed: {error}")
            continue

        result = overview.get("result") or overview
        energy_card = result.get("energyCard") or {}
        view_chart = result.get("viewChart") or {}
        system["current_power_kw"] = (
            watts_to_kw(energy_card.get("realTimePower"))
            or watts_to_kw(energy_card.get("pvPower"))
            or watts_to_kw(view_chart.get("pvPower"))
            or system.get("current_power_kw")
        )
        system["today_generation_kwh"] = (
            numeric(energy_card.get("yieldToday"))
            or numeric(energy_card.get("pvYieldToday"))
            or system.get("today_generation_kwh")
        )
        system["total_generation_kwh"] = energy_total_from_response(result) or system.get("total_generation_kwh")
        try:
            weekly = fetch_solax_weekly(token, station_id, end_date)
            if weekly is not None:
                system["weekly_generation_kwh"] = weekly
        except Exception as error:
            system.setdefault("backend_errors", []).append(f"weekly energyInfo failed: {error}")
        try:
            year_total = fetch_solax_year(token, station_id, 2026)
            if year_total is not None:
                system["year_generation_kwh"] = year_total
        except Exception as error:
            system.setdefault("backend_errors", []).append(f"2026 energyInfo failed: {error}")


def value_near_label(lines, labels):
    lowered_labels = tuple(label.lower() for label in labels)
    for index, line in enumerate(lines):
        lowered = line.lower()
        if not any(label in lowered for label in lowered_labels):
            continue
        inline = parse_number(line)
        if inline is not None:
            return inline
        for candidate in lines[index + 1 : index + 4]:
            value = parse_number(candidate)
            if value is not None:
                return value
    return None


def energy_value_near_label(lines, labels):
    lowered_labels = tuple(label.lower() for label in labels)
    for index, line in enumerate(lines):
        lowered = line.lower()
        if not any(label in lowered for label in lowered_labels):
            continue
        window = " ".join(lines[index : index + 4])
        value = parse_number(window)
        if value is None:
            continue
        if re.search(r"\bmwh\b", window, re.IGNORECASE):
            return value * 1000
        return value
    return None


def detail_text_by_name(capture):
    detail_map = {}
    for detail in capture.get("plantDetails") or []:
        text_parts = [detail.get("bodyText", "")]
        for item in detail.get("visibleTables") or []:
            text_parts.append(item.get("text", ""))
        detail_map[detail.get("name")] = "\n".join(part for part in text_parts if part)
    return detail_map


def detail_metrics(text):
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return {
        "current_power_kw": value_near_label(lines, ("current power", "real-time power", "realtime power", "power")),
        "weekly_generation_kwh": energy_value_near_label(
            lines,
            ("weekly generation", "week generation", "weekly yield", "week yield", "this week"),
        ),
        "total_generation_kwh": energy_value_near_label(
            lines,
            ("total generation", "total yield", "lifetime generation", "cumulative generation", "all yield"),
        ),
    }


def unit_value(text, unit_pattern):
    match = re.search(
        rf"(-?\d+(?:,\d{{3}})*(?:\.\d+)?)\s*(?:{unit_pattern})",
        text or "",
        re.IGNORECASE,
    )
    return float(match.group(1).replace(",", "")) if match else None


def looks_like_plant_row(text):
    lowered = text.lower()
    if not text or len(text) < 6:
        return False
    if "overview\nplants\ndevices" in lowered:
        return False
    return any(token in lowered for token in ("kw", "kwh", "normal", "offline", "online", "warning", "failure"))


def plant_name_from_lines(lines):
    ignored = {
        "plant name",
        "name",
        "status",
        "normal",
        "offline",
        "online",
        "warning",
        "failure",
        "pv capacity",
        "capacity",
        "current power",
        "today generation",
        "today yield",
        "total generation",
        "total yield",
        "update time",
    }
    for line in lines:
        lowered = line.lower()
        if lowered in ignored:
            continue
        if re.fullmatch(r"[-+0-9.,\s]+", line):
            continue
        if re.fullmatch(r"(kw|kwp|kwh|mwh|w|mw)", lowered):
            continue
        if any(unit in lowered for unit in (" kwh", " kw", " kwp", " mwh", " mw")):
            continue
        return line
    return None


def status_from_text(text):
    for status in ("Normal", "Online", "Partially offline", "Offline", "Warning", "Failure", "Connecting"):
        if re.search(rf"\b{re.escape(status)}\b", text or "", re.IGNORECASE):
            return status
    return None


def parse_visible_plant_rows(capture):
    systems = []
    seen = set()
    details = detail_text_by_name(capture)

    table_items = capture.get("visibleTables") or []
    for item in table_items:
        text = item.get("text", "")
        if not looks_like_plant_row(text):
            continue
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        name = plant_name_from_lines(lines)
        if not name:
            continue

        if len(lines) >= 15:
            capacity_kw = parse_number(lines[9])
            today_generation_kwh = parse_number(lines[11])
            system = {
                "name": lines[0],
                "status": lines[2],
                "capacity_kw": capacity_kw,
                "current_power_kw": None,
                "today_generation_kwh": today_generation_kwh,
                "weekly_generation_kwh": None,
                "total_generation_kwh": None,
                "system_id": None,
            }
            metrics = detail_metrics(details.get(system["name"]))
            for key, value in metrics.items():
                if value is not None:
                    system[key] = value
            key = (system["name"], system["capacity_kw"], system["today_generation_kwh"])
            if key not in seen:
                seen.add(key)
                systems.append(system)
            continue

        system = {
            "name": name,
            "status": status_from_text(text),
            "capacity_kw": unit_value(text, "kWp|kW"),
            "current_power_kw": value_near_label(lines, ("current power", "power")),
            "today_generation_kwh": value_near_label(lines, ("today generation", "today yield", "today")),
            "weekly_generation_kwh": value_near_label(lines, ("week generation", "weekly generation", "week yield")),
            "total_generation_kwh": value_near_label(lines, ("total generation", "total yield", "total")),
            "system_id": None,
        }
        metrics = detail_metrics(details.get(system["name"]))
        for key, value in metrics.items():
            if value is not None:
                system[key] = value
        key = (system["name"], system["capacity_kw"], system["today_generation_kwh"])
        if key not in seen:
            seen.add(key)
            systems.append(system)

    return systems


def parse_capture():
    capture = json.loads(CAPTURE_FILE.read_text(encoding="utf-8"))
    separate_systems = parse_visible_plant_rows(capture)
    if separate_systems:
        enrich_with_solax_backend(capture, separate_systems)
        return {
            "source": str(CAPTURE_FILE),
            "generated_at": dt.datetime.now().replace(microsecond=0).isoformat(),
            "captured_at": capture.get("captured_at"),
            "systems": separate_systems,
            "notes": [
                "SolaX systems were split from the visible Plants page table captured in the browser.",
                "Any blank values were not visible in the captured SolaX plant table.",
            ],
        }

    text_blocks = [
        capture.get("plantsBodyText", ""),
        capture.get("bodyText", ""),
    ]
    body_text = "\n".join(block for block in text_blocks if block)
    lines = [line.strip() for line in body_text.splitlines() if line.strip()]

    capacity_kw = number_after(lines, "PV Capacity")
    total_plants = value_before(lines, "Total")
    normal_plants = number_after(lines, "Normal")
    warning_plants = number_after(lines, "Warning")
    failure_plants = number_after(lines, "Failure")
    offline_plants = number_after(lines, "Offline")

    status_parts = []
    if normal_plants is not None:
        status_parts.append(f"Normal {int(normal_plants)}")
    if warning_plants:
        status_parts.append(f"Warning {int(warning_plants)}")
    if failure_plants:
        status_parts.append(f"Failure {int(failure_plants)}")
    if offline_plants:
        status_parts.append(f"Offline {int(offline_plants)}")

    system_name = first_account_name(lines)
    if total_plants and total_plants > 1:
        system_name = f"{system_name} ({int(total_plants)} plants)"

    if not capacity_kw and not total_plants:
        raise RuntimeError(
            "Could not find SolaX plant data in solax_network_capture.json. "
            "Open SolaXCloud on the dashboard or plants page and run solax_manual_login_capture.py again."
        )

    notes = [
        "SolaXCloud returned encrypted API data in this capture, so this row is built from visible dashboard text.",
        "Current power, today generation, weekly generation, and total generation stay blank until the plant detail page exposes those values in the capture.",
    ]

    return {
        "source": str(CAPTURE_FILE),
        "generated_at": dt.datetime.now().replace(microsecond=0).isoformat(),
        "captured_at": capture.get("captured_at"),
        "systems": [
            {
                "name": system_name,
                "status": ", ".join(status_parts) if status_parts else None,
                "capacity_kw": capacity_kw,
                "current_power_kw": None,
                "today_generation_kwh": None,
                "weekly_generation_kwh": None,
                "total_generation_kwh": None,
                "system_id": None,
            }
        ],
        "notes": notes,
    }


def capture_date(payload):
    value = payload.get("captured_at") or payload.get("generated_at")
    if value:
        return value[:10]
    return dt.date.today().isoformat()


def load_history():
    if not HISTORY_FILE.exists():
        return []
    with HISTORY_FILE.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def save_history(rows):
    fieldnames = ["date", "name", "daily_generation_kwh", "capacity_kw", "status"]
    with HISTORY_FILE.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def update_history_and_weekly(payload):
    rows = load_history()
    current_date = capture_date(payload)
    by_key = {(row["date"], row["name"]): row for row in rows}

    for system in payload.get("systems", []):
        daily = system.get("today_generation_kwh")
        if daily is None:
            continue
        by_key[(current_date, system["name"])] = {
            "date": current_date,
            "name": system["name"],
            "daily_generation_kwh": daily,
            "capacity_kw": system.get("capacity_kw") or "",
            "status": system.get("status") or "",
        }

    rows = sorted(by_key.values(), key=lambda row: (row["date"], row["name"]))
    save_history(rows)

    end_date = dt.date.fromisoformat(current_date)
    start_date = end_date - dt.timedelta(days=end_date.weekday())
    week_end = start_date + dt.timedelta(days=6)
    weekly_by_name = {}
    for row in rows:
        try:
            row_date = dt.date.fromisoformat(row["date"])
        except ValueError:
            continue
        if not (start_date <= row_date <= week_end):
            continue
        weekly_by_name[row["name"]] = weekly_by_name.get(row["name"], 0.0) + float(
            row.get("daily_generation_kwh") or 0
        )

    used_local_weekly = False
    for system in payload.get("systems", []):
        if system.get("weekly_generation_kwh") is None and system.get("name") in weekly_by_name:
            system["weekly_generation_kwh"] = round(weekly_by_name[system["name"]], 3)
            used_local_weekly = True

    if used_local_weekly:
        payload.setdefault("notes", []).append(
            "SolaX weekly generation is calculated from locally stored daily captures when the backend does not expose weekly values."
        )
    if all(system.get("total_generation_kwh") is None for system in payload.get("systems", [])):
        payload.setdefault("notes", []).append(
            "SolaX total generation is still blank because the captured SolaX pages do not expose lifetime totals."
        )
    return payload


def main():
    payload = update_history_and_weekly(parse_capture())
    OUTPUT_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Saved {len(payload['systems'])} SolaX systems to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
