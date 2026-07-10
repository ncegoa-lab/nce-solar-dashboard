#!/usr/bin/env python3
"""Refresh local inverter data and upload generation JSON to the Render app."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
VENV_PYTHON = PROJECT_DIR / ".venv/bin/python"
BRANDS = {
    "solis": {
        "capture": PROJECT_DIR / "solis_manual_login_capture.py",
        "convert": PROJECT_DIR / "solis_capture_to_generation.py",
        "json": PROJECT_DIR / "solis_generation.json",
    },
    "solax": {
        "capture": PROJECT_DIR / "solax_manual_login_capture.py",
        "convert": PROJECT_DIR / "solax_capture_to_generation.py",
        "json": PROJECT_DIR / "solax_generation.json",
    },
}


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def run_script(path: Path) -> None:
    python = str(VENV_PYTHON if VENV_PYTHON.exists() else sys.executable)
    env = os.environ.copy()
    env["PYTHONPYCACHEPREFIX"] = str(PROJECT_DIR / ".pycache")
    subprocess.run([python, str(path)], cwd=str(PROJECT_DIR), env=env, check=True)


def upload_generation(brand: str, json_path: Path, app_url: str, token: str) -> dict:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    body = json.dumps({"brand": brand, "data": data}).encode("utf-8")
    request = urllib.request.Request(
        app_url.rstrip("/") + "/api/upload-generation",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Upload-Token": token,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        message = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Upload failed with HTTP {error.code}: {message}") from error


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload fresh inverter generation JSON to Render.")
    parser.add_argument("--brand", choices=sorted(BRANDS), default="solis")
    parser.add_argument("--skip-capture", action="store_true", help="Upload the existing generation JSON without opening the portal.")
    args = parser.parse_args()

    app_url = require_env("RENDER_APP_URL")
    token = require_env("SOLAR_UPLOAD_TOKEN")
    brand = args.brand
    config = BRANDS[brand]

    if not args.skip_capture:
        run_script(config["capture"])
        run_script(config["convert"])

    result = upload_generation(brand, config["json"], app_url, token)
    if not result.get("ok"):
        raise RuntimeError(result.get("message") or result)
    print(f"Uploaded {brand.title()} data to Render: {result.get('systems')} systems")


if __name__ == "__main__":
    main()
