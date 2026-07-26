import json
import os
from pathlib import Path
from typing import Optional

import requests


BASE_URL = os.getenv("SEMS_BASE_URL", "https://www.semsportal.com")
OUTPUT_FILE = Path("sems_api_login_probe.json")


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

    response = session.post(
        f"{BASE_URL}/api/v2/Common/CrossLogin",
        json={"account": username, "pwd": password},
        timeout=30,
    )
    response.raise_for_status()

    body = response.json()
    OUTPUT_FILE.write_text(json.dumps(scrub(body), indent=2), encoding="utf-8")
    print(f"Saved sanitized login response to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
