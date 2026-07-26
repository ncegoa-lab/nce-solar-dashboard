import json
import os
from pathlib import Path
from typing import Optional

import requests


LOGIN_URL = "https://www.semsportal.com/api/v2/Common/CrossLogin"
OUTPUT_FILE = Path("sems_api_station_probe.json")


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def token_header(token_data: Optional[dict[str, object]] = None) -> str:
    header = {
        "version": "v2.1.0",
        "client": "ios",
        "language": "en",
    }
    if token_data:
        for key in ("uid", "timestamp", "token"):
            if key in token_data:
                header[key] = token_data[key]
    return json.dumps(header, separators=(",", ":"))


def scrub(value: object) -> object:
    if isinstance(value, dict):
        scrubbed = {}
        for key, item in value.items():
            lower_key = key.lower()
            if lower_key in {"pwd", "password", "token", "uid"}:
                scrubbed[key] = "<redacted>"
            else:
                scrubbed[key] = scrub(item)
        return scrubbed
    if isinstance(value, list):
        return [scrub(item) for item in value]
    return value


def post(session: requests.Session, url: str, payload: dict[str, object]) -> dict[str, object]:
    response = session.post(url, json=payload, timeout=30)
    try:
        body = response.json()
    except ValueError:
        body = {"text": response.text[:500]}
    return {
        "status_code": response.status_code,
        "url": url,
        "payload": payload,
        "body": body,
    }


def main() -> None:
    username = require_env("SEMS_USERNAME")
    password = require_env("SEMS_PASSWORD")

    session = requests.Session()
    session.headers.update(
        {
            "Content-Type": "application/json",
            "Token": token_header(),
            "User-Agent": "Mozilla/5.0",
        }
    )

    login_response = session.post(
        LOGIN_URL,
        json={"account": username, "pwd": password},
        timeout=30,
    )
    login_response.raise_for_status()
    login_body = login_response.json()
    token_data = login_body["data"]
    api_base = login_body.get("api", "https://www.semsportal.com/api/")

    session.headers.update({"Token": token_header(token_data)})

    candidates = [
        (
            f"{api_base}v2/PowerStationMonitor/QueryPowerStationMonitor",
            {"powerStationId": "", "key": "", "pageIndex": 1, "pageSize": 20},
        ),
        (
            f"{api_base}v2/PowerStation/GetPowerStationList",
            {"key": "", "pageIndex": 1, "pageSize": 20},
        ),
        (
            f"{api_base}v1/PowerStation/GetPowerStationList",
            {"key": "", "pageIndex": 1, "pageSize": 20},
        ),
        (
            f"{api_base}v2/PowerStation/QueryPowerStation",
            {"key": "", "pageIndex": 1, "pageSize": 20},
        ),
    ]

    results = {
        "login": scrub(login_body),
        "candidates": [scrub(post(session, url, payload)) for url, payload in candidates],
    }
    OUTPUT_FILE.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Saved sanitized station probe response to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
