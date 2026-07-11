#!/usr/bin/env python3
"""Export SolaX generation data using the SolaX cloud API token."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
OUTPUT_FILE = PROJECT_DIR / "solax_generation.json"
DEBUG_FILE = PROJECT_DIR / "solax_api_last_response.json"
DEFAULT_BASE = "https://global.solaxcloud.com"


def load_env_file() -> None:
    env_path = PROJECT_DIR / ".solar_report_env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def numeric(value):
    try:
        if value in (None, ""):
            return None
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def public_get(base_url: str, path: str, params: dict[str, object]) -> dict:
    url = base_url.rstrip("/") + path + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "NCE-Solar-Dashboard/1.0"})
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def find_dicts(value: object, predicate) -> list[dict]:
    found: list[dict] = []
    if isinstance(value, dict):
        if predicate(value):
            found.append(value)
        for child in value.values():
            found.extend(find_dicts(child, predicate))
    elif isinstance(value, list):
        for child in value:
            found.extend(find_dicts(child, predicate))
    return found


def device_identifier(row: dict) -> str | None:
    for key in ("sn", "SN", "inverterSN", "inverterSn", "registrationNo", "regNo", "serialNum", "deviceSn"):
        value = row.get(key)
        if value:
            return str(value)
    return None


def looks_like_device(row: dict) -> bool:
    return bool(device_identifier(row)) or any(key in row for key in ("yieldtoday", "yieldToday", "powerdc1", "acpower", "plantName"))


def fetch_device_list(base_url: str, token_id: str) -> tuple[list[dict], dict]:
    attempts = [
        "/proxyApp/proxy/api/getAllDeviceList.do",
        "/proxyApp/proxy/api/getDeviceList.do",
        "/proxyApp/proxy/api/getPlantList.do",
        "/proxyApp/proxy/api/getAllPlantList.do",
        "/proxy/api/getAllDeviceList.do",
        "/proxy/api/getDeviceList.do",
    ]
    errors: list[str] = []
    for path in attempts:
        try:
            payload = public_get(base_url, path, {"tokenId": token_id})
            DEBUG_FILE.write_text(json.dumps({"path": path, "response": payload}, indent=2), encoding="utf-8")
            rows = find_dicts(payload, looks_like_device)
            if rows:
                return rows, payload
            errors.append(f"{path}: no devices in response")
        except urllib.error.HTTPError as error:
            errors.append(f"{path}: HTTP {error.code}")
        except Exception as error:
            errors.append(f"{path}: {error}")
    raise RuntimeError("SolaX API device list failed. " + " | ".join(errors))


def configured_devices() -> list[dict]:
    raw = os.getenv("SOLAX_DEVICE_SNS", "").strip()
    devices: list[dict] = []
    for item in [part.strip() for part in raw.split(",") if part.strip()]:
        if "=" in item:
            name, sn = item.split("=", 1)
            devices.append({"plantName": name.strip(), "sn": sn.strip()})
        elif "|" in item:
            name, sn = item.split("|", 1)
            devices.append({"plantName": name.strip(), "sn": sn.strip()})
        else:
            devices.append({"sn": item})
    return devices


def fetch_realtime(base_url: str, token_id: str, sn: str) -> dict | None:
    attempts = [
        "/proxyApp/proxy/api/getRealtimeInfo.do",
        "/proxy/api/getRealtimeInfo.do",
    ]
    for path in attempts:
        try:
            payload = public_get(base_url, path, {"tokenId": token_id, "sn": sn})
            if payload.get("success") is False and str(payload.get("result", "")).lower() in {"no auth!", "no auth"}:
                continue
            return payload
        except Exception:
            continue
    return None


def unwrap_data(payload: object) -> dict:
    if isinstance(payload, dict):
        for key in ("result", "data", "response"):
            if isinstance(payload.get(key), dict):
                return payload[key]
    return payload if isinstance(payload, dict) else {}


def first_value(row: dict, keys: tuple[str, ...]):
    for key in keys:
        if key in row and row.get(key) not in (None, ""):
            return row.get(key)
    return None


def status_from(row: dict) -> str:
    raw = first_value(row, ("status", "inverterStatus", "state", "statusText", "onlineStatus"))
    text = str(raw or "").strip()
    if text.lower() in {"1", "2", "102", "online", "normal", "working", "active"}:
        return "Online"
    if text.lower() in {"0", "109", "110", "offline"}:
        return "Offline"
    if text.lower() in {"100", "101", "105", "106", "107", "108", "warning", "standby", "wait", "checking"}:
        return "Warning"
    if text.lower() in {"103", "104", "fault", "failure"}:
        return "Fault"
    return text or "Unknown"


def name_from(row: dict, fallback_sn: str | None) -> str:
    return str(
        first_value(row, ("plantName", "stationName", "siteName", "name", "deviceName", "inverterName"))
        or fallback_sn
        or "SolaX Plant"
    )


def system_from(device: dict, realtime: dict | None, previous: dict | None = None) -> dict:
    live = unwrap_data(realtime or {})
    previous = previous or {}
    row = {**previous, **device, **live}
    sn = device_identifier(row)
    power = numeric(first_value(row, ("acpower", "acPower", "power", "pac", "powerdc1", "inverterPower")))
    if power is not None and abs(power) > 100:
        power = round(power / 1000, 3)
    total = numeric(first_value(row, ("total_generation_kwh", "yieldtotal", "yieldTotal", "totalYield", "etotal", "allEnergy")))
    if total is not None:
        unit_blob = json.dumps(row).lower()
        if re.search(r'"yieldtotalunit"\s*:\s*"mwh"|total.*mwh', unit_blob):
            total *= 1000
    return {
        "name": name_from(row, sn),
        "status": status_from(row),
        "capacity_kw": numeric(first_value(row, ("capacity_kw", "capacity", "capacityKw", "installedCapacity", "pvCapacity"))),
        "current_power_kw": power if power is not None else numeric(first_value(row, ("current_power_kw",))),
        "today_generation_kwh": numeric(first_value(row, ("today_generation_kwh", "yieldtoday", "yieldToday", "todayYield", "eToday", "dayEnergy"))),
        "weekly_generation_kwh": numeric(first_value(row, ("weekly_generation_kwh", "yieldWeek", "weekYield", "weeklyYield", "weekEnergy"))),
        "month_generation_kwh": numeric(first_value(row, ("month_generation_kwh", "yieldMonth", "monthYield", "monthlyYield", "monthEnergy"))),
        "year_generation_kwh": numeric(first_value(row, ("year_generation_kwh", "yieldYear", "yearYield", "yearlyYield", "yearEnergy"))),
        "total_generation_kwh": total,
        "system_id": first_value(row, ("plantId", "stationId", "siteId", "id")) or sn,
        "source_sno": sn,
        "data_timestamp": first_value(row, ("uploadTime", "updateTime", "lastUpdateTime", "time")),
    }


def load_capture_baseline() -> dict[str, dict]:
    try:
        from solax_capture_to_generation import parse_capture

        payload = parse_capture()
        return {
            str(system.get("name", "")): system
            for system in payload.get("systems", [])
            if system.get("name")
        }
    except Exception:
        return {}


def main() -> None:
    load_env_file()
    base_url = os.getenv("SOLAX_API_BASE", DEFAULT_BASE).strip().rstrip("/")
    token_id = require_env("SOLAX_TOKEN_ID")
    devices = configured_devices()
    raw_payload = {"source": "SOLAX_DEVICE_SNS", "success": True}
    if not devices:
        devices, raw_payload = fetch_device_list(base_url, token_id)
    previous_by_name: dict[str, dict] = {}
    if OUTPUT_FILE.exists():
        try:
            previous_payload = json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
            previous_by_name = {
                str(system.get("name", "")): system
                for system in previous_payload.get("systems", [])
                if system.get("name")
            }
        except Exception:
            previous_by_name = {}
    previous_by_name.update(load_capture_baseline())

    systems = []
    seen = set()
    for device in devices:
        sn = device_identifier(device)
        realtime = fetch_realtime(base_url, token_id, sn) if sn else None
        name = name_from(device, sn)
        system = system_from(device, realtime, previous_by_name.get(name))
        if realtime is None:
            system.setdefault("api_warning", "SolaX API did not return authorized live data for this serial; keeping last known values.")
        key = system.get("system_id") or system.get("name")
        if key in seen:
            continue
        seen.add(key)
        systems.append(system)
    generated_at = dt.datetime.now().astimezone().replace(microsecond=0).isoformat()
    payload = {
        "source": "solax_api",
        "generated_at": generated_at,
        "captured_at": generated_at,
        "systems": systems,
        "notes": ["SolaX data was refreshed through the SolaX cloud API."],
        "api_status": {
            "record_count": len(devices),
            "success": raw_payload.get("success") if isinstance(raw_payload, dict) else None,
            "code": raw_payload.get("code") if isinstance(raw_payload, dict) else None,
        },
    }
    OUTPUT_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Saved {len(systems)} SolaX systems to {OUTPUT_FILE.name} using API")


if __name__ == "__main__":
    main()
