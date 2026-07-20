#!/usr/bin/env python3
"""Export SolisCloud generation data using the official API credentials."""

from __future__ import annotations

import base64
import datetime as dt
import email.utils
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo


PROJECT_DIR = Path(__file__).resolve().parent
OUTPUT_FILE = PROJECT_DIR / "solis_generation.json"
DEBUG_FILE = PROJECT_DIR / "solis_api_last_response.json"
DEFAULT_BASE = "https://www.soliscloud.com:13333"
STATUS_MAP = {1: "Online", 2: "Offline", 3: "Fault"}
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


def solis_total_kwh(record: dict):
    value = numeric(record.get("allEnergy1"))
    if value is not None:
        return value
    value = numeric(record.get("allEnergy"))
    if value is None:
        return None
    unit = str(record.get("allEnergyStr") or record.get("allEnergyUnit") or "")
    return value * 1000 if "mwh" in unit.lower() else value


def station_records_from_payload(payload: object) -> list[dict]:
    if isinstance(payload, dict):
        direct = (
            payload.get("data", {})
            .get("page", {})
            .get("records", [])
        )
        if isinstance(direct, list) and direct:
            return [row for row in direct if isinstance(row, dict)]
        for key in ("records", "list", "stationList", "rows"):
            rows = payload.get(key)
            if isinstance(rows, list) and rows:
                return [row for row in rows if isinstance(row, dict)]
        for value in payload.values():
            records = station_records_from_payload(value)
            if records:
                return records
    if isinstance(payload, list):
        rows = [row for row in payload if isinstance(row, dict)]
        if rows and any("stationName" in row or "name" in row for row in rows):
            return rows
        for value in payload:
            records = station_records_from_payload(value)
            if records:
                return records
    return []


def signed_post(base_url: str, path: str, body: dict, key_id: str, key_secret: str) -> dict:
    body_bytes = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    content_md5 = base64.b64encode(hashlib.md5(body_bytes).digest()).decode("ascii")
    content_type = "application/json"
    date_header = email.utils.formatdate(usegmt=True)
    string_to_sign = f"POST\n{content_md5}\n{content_type}\n{date_header}\n{path}"
    signature = base64.b64encode(
        hmac.new(key_secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha1).digest()
    ).decode("ascii")
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=body_bytes,
        headers={
            "Content-Type": content_type,
            "Content-MD5": content_md5,
            "Date": date_header,
            "Authorization": f"API {key_id}:{signature}",
            "User-Agent": "NCE-Solar-Dashboard/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_station_list(base_url: str, key_id: str, key_secret: str) -> tuple[list[dict], dict]:
    attempts = [
        ("/v1/api/stationList", {"pageNo": 1, "pageSize": 100}),
        ("/v1/api/stationList", {"pageNo": 1, "pageSize": 100, "timezone": "5.5"}),
        ("/v1/api/stationList", {"pageNo": 1, "pageSize": 100, "timeZone": "Asia/Kolkata"}),
        ("/v1/api/userStationList", {"pageNo": 1, "pageSize": 100}),
        ("/v1/api/station/list", {"pageNo": 1, "pageSize": 100}),
        ("/v2/api/stationList", {"pageNo": 1, "pageSize": 100}),
        ("/v2/api/userStationList", {"pageNo": 1, "pageSize": 100}),
        ("/v2/api/station/list", {"pageNo": 1, "pageSize": 100}),
        ("/v3/api/station/list", {"pageNo": 1, "pageSize": 100}),
        ("/api/station/list", {"pageNo": 1, "pageSize": 100}),
    ]
    errors: list[str] = []
    for path, body in attempts:
        try:
            payload = signed_post(base_url, path, body, key_id, key_secret)
            DEBUG_FILE.write_text(json.dumps({"path": path, "body": body, "response": payload}, indent=2), encoding="utf-8")
            records = station_records_from_payload(payload)
            if records:
                return records, payload
            errors.append(f"{path}: no station records in response")
        except urllib.error.HTTPError as error:
            errors.append(f"{path}: HTTP {error.code}")
        except Exception as error:
            errors.append(f"{path}: {error}")
    raise RuntimeError("Solis API station list failed. " + " | ".join(errors))


def system_from_record(record: dict) -> dict:
    state = record.get("state")
    status = STATUS_MAP.get(state, record.get("status") or record.get("stateName") or str(state or "Unknown"))
    return {
        "name": record.get("stationName") or record.get("name") or record.get("plantName"),
        "status": status,
        "capacity_kw": numeric(record.get("capacity1") or record.get("capacity") or record.get("installedCapacity")),
        "current_power_kw": numeric(record.get("power") or record.get("pac") or record.get("currentPower")) or 0,
        "today_generation_kwh": numeric(record.get("dayEnergy1") if record.get("dayEnergy1") is not None else record.get("dayEnergy")),
        "weekly_generation_kwh": None,
        "month_generation_kwh": numeric(record.get("monthEnergy1") if record.get("monthEnergy1") is not None else record.get("monthEnergy")),
        "year_generation_kwh": numeric(record.get("yearEnergy1") if record.get("yearEnergy1") is not None else record.get("yearEnergy")),
        "total_generation_kwh": solis_total_kwh(record),
        "system_id": record.get("id") or record.get("stationId"),
        "source_sno": record.get("sno"),
        "data_timestamp": record.get("dataTimestampStr") or record.get("updateTime") or record.get("time"),
    }


def main() -> None:
    load_env_file()
    base_url = os.getenv("SOLIS_API_BASE", DEFAULT_BASE).strip().rstrip("/")
    key_id = require_env("SOLIS_KEY_ID")
    key_secret = require_env("SOLIS_KEY_SECRET")
    records, raw_payload = fetch_station_list(base_url, key_id, key_secret)
    systems = [system_from_record(record) for record in records]
    generated_at = dt.datetime.now(IST).replace(microsecond=0).isoformat()
    payload = {
        "source": "solis_api",
        "generated_at": generated_at,
        "captured_at": generated_at,
        "systems": systems,
        "notes": [
            "Solis data was refreshed through the SolisCloud API.",
            "Weekly generation is calculated from dashboard history when a weekly API value is not returned.",
        ],
        "api_status": {
            "record_count": len(records),
            "code": raw_payload.get("code") if isinstance(raw_payload, dict) else None,
            "msg": raw_payload.get("msg") or raw_payload.get("message") if isinstance(raw_payload, dict) else None,
        },
    }
    OUTPUT_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Saved {len(systems)} Solis systems to {OUTPUT_FILE.name} using API")


if __name__ == "__main__":
    main()
