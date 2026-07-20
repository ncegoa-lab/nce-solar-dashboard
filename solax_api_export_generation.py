#!/usr/bin/env python3
"""Export SolaX generation data using the SolaX cloud API token."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

from solax_capture_to_generation import (
    energy_total_from_response,
    fetch_solax_weekly,
    fetch_solax_year,
    solax_decrypt,
    solax_encrypt,
    solax_request,
    watts_to_kw,
)


PROJECT_DIR = Path(__file__).resolve().parent
OUTPUT_FILE = PROJECT_DIR / "solax_generation.json"
DEBUG_FILE = PROJECT_DIR / "solax_api_last_response.json"
DEFAULT_BASE = "https://global.solaxcloud.com"
WEB_BASE = "https://euapi.solaxcloud.com"
IST = ZoneInfo("Asia/Kolkata")


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


def encrypted_web_url(path: str, params: dict | None = None) -> str:
    params = {
        **(params or {}),
        "timeStamp": int(time.time() * 1000),
        "requestId": str(int(time.time() * 1000000))[-8:],
    }
    data = solax_encrypt(json.dumps(params, separators=(",", ":")))
    return WEB_BASE + path + "?" + urllib.parse.urlencode({"data": data})


def decrypt_web_payload(payload: dict) -> dict:
    if payload.get("data"):
        decoded = solax_decrypt(payload["data"])
        try:
            return json.loads(decoded)
        except json.JSONDecodeError:
            return {"success": False, "message": decoded}
    return payload


def web_post(path: str, params: dict | None = None, body: dict | None = None, token: str | None = None) -> dict:
    raw_body = json.dumps({"data": solax_encrypt(json.dumps(body or {}, separators=(",", ":")))})
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
    }
    if token:
        headers["token"] = token
    request = urllib.request.Request(
        encrypted_web_url(path, params),
        data=raw_body.encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        return decrypt_web_payload(json.loads(response.read().decode("utf-8")))


def fetch_web_token() -> str:
    username = require_env("SOLAX_USERNAME")
    password = require_env("SOLAX_PASSWORD")
    password_md5 = hashlib.md5(password.encode("utf-8")).hexdigest()
    payload = web_post(
        "/unionUser/web/v2/public/login",
        body={"loginName": username, "password": password_md5, "route": 1},
    )
    if not payload.get("success"):
        raise RuntimeError(payload.get("message") or payload.get("msg") or "SolaX web login failed")
    result = payload.get("result") or {}
    token = result.get("token") or result.get("TOKEN") or result.get("accessToken")
    if not token:
        raise RuntimeError("SolaX web login succeeded but returned no token")
    return token


def fetch_web_station_records(token: str) -> list[dict]:
    payload = solax_request(
        "/mesh/web/v1/station/page",
        token,
        body={"current": 1, "size": 100},
        method="POST",
    )
    records = (payload.get("result") or {}).get("records") or []
    if not records:
        raise RuntimeError("SolaX web station page returned no station records")
    return [record for record in records if isinstance(record, dict)]


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


def status_from_station(record: dict) -> str:
    status = str(record.get("stationStatus") or record.get("gridConnectStatus") or "").strip()
    if status == "1":
        return "Online"
    if status == "0":
        return "Offline"
    return status_from(record)


def system_from_station_record(record: dict, token: str, generated_at: str) -> dict:
    station_id = str(record.get("stationId") or "")
    system = {
        "name": record.get("stationName") or record.get("name") or station_id or "SolaX Plant",
        "status": status_from_station(record),
        "capacity_kw": numeric(record.get("pvCapacity") or record.get("capacity")),
        "current_power_kw": None,
        "today_generation_kwh": numeric(record.get("pvYield") or record.get("yieldToday") or record.get("todayYield")),
        "weekly_generation_kwh": None,
        "month_generation_kwh": None,
        "year_generation_kwh": None,
        "total_generation_kwh": None,
        "system_id": station_id,
        "source_sno": record.get("deviceSn") or record.get("registerNo") or record.get("stationUniqueId"),
        "data_timestamp": generated_at,
    }

    try:
        overview = solax_request(
            "/zeus/v1/overview/siteOverview",
            token,
            params={"siteId": station_id},
            method="GET",
        )
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
    except Exception as error:
        system.setdefault("backend_errors", []).append(f"siteOverview failed: {error}")

    try:
        weekly = fetch_solax_weekly(token, station_id, dt.datetime.now(IST).date())
        if weekly is not None:
            system["weekly_generation_kwh"] = weekly
    except Exception as error:
        system.setdefault("backend_errors", []).append(f"weekly energyInfo failed: {error}")

    try:
        year_total = fetch_solax_year(token, station_id, dt.datetime.now(IST).year)
        if year_total is not None:
            system["year_generation_kwh"] = year_total
    except Exception as error:
        system.setdefault("backend_errors", []).append(f"year energyInfo failed: {error}")

    return system


def export_from_web_login() -> dict:
    token = fetch_web_token()
    generated_at = dt.datetime.now(IST).replace(microsecond=0).isoformat()
    records = fetch_web_station_records(token)
    systems = [system_from_station_record(record, token, generated_at) for record in records]
    return {
        "source": "solax_web_api",
        "generated_at": generated_at,
        "captured_at": generated_at,
        "systems": systems,
        "notes": ["SolaX data was refreshed through the SolaX web API login."],
        "api_status": {
            "record_count": len(records),
            "live_record_count": len(records),
            "success": True,
            "code": 0,
        },
    }


def load_capture_baseline() -> tuple[dict[str, dict], str | None]:
    try:
        from solax_capture_to_generation import parse_capture

        payload = parse_capture()
        return (
            {
                str(system.get("name", "")): system
                for system in payload.get("systems", [])
                if system.get("name")
            },
            payload.get("captured_at") or payload.get("generated_at"),
        )
    except Exception:
        return {}, None


def main() -> None:
    load_env_file()
    if os.getenv("SOLAX_USERNAME") and os.getenv("SOLAX_PASSWORD"):
        payload = export_from_web_login()
        OUTPUT_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Saved {len(payload['systems'])} SolaX systems to {OUTPUT_FILE.name} using web API login")
        return

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
    capture_baseline, baseline_captured_at = load_capture_baseline()
    previous_by_name.update(capture_baseline)

    systems = []
    seen = set()
    live_count = 0
    for device in devices:
        sn = device_identifier(device)
        realtime = fetch_realtime(base_url, token_id, sn) if sn else None
        name = name_from(device, sn)
        system = system_from(device, realtime, previous_by_name.get(name))
        if realtime is None:
            system.setdefault("api_warning", "SolaX API did not return authorized live data for this serial; keeping last known values.")
        else:
            live_count += 1
        key = system.get("system_id") or system.get("name")
        if key in seen:
            continue
        seen.add(key)
        systems.append(system)
    generated_at = dt.datetime.now(IST).replace(microsecond=0).isoformat()
    captured_at = generated_at if live_count else (baseline_captured_at or generated_at)
    payload = {
        "source": "solax_api",
        "generated_at": generated_at,
        "captured_at": captured_at,
        "systems": systems,
        "notes": [
            "SolaX data was refreshed through the SolaX cloud API."
            if live_count
            else "SolaX API did not return authorized live values; showing last captured SolaX data."
        ],
        "api_status": {
            "record_count": len(devices),
            "live_record_count": live_count,
            "success": raw_payload.get("success") if isinstance(raw_payload, dict) else None,
            "code": raw_payload.get("code") if isinstance(raw_payload, dict) else None,
        },
    }
    OUTPUT_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Saved {len(systems)} SolaX systems to {OUTPUT_FILE.name} using API")


if __name__ == "__main__":
    main()
