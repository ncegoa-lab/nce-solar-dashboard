#!/usr/bin/env python3
"""Local live solar dashboard app.

Run this script to start a browser-based app on the Mac. It serves live plant
data from the existing project files, can refresh cloud data, and can generate
one PDF report for all plants, a single plant, or any selected plants.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import base64
import hashlib
import hmac
import mimetypes
import os
import secrets
import socket
import subprocess
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pandas as pd

from solar_performance_report_app import DEFAULT_LOGO, generate_compact_pdf, load_data


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = Path(
    os.environ.get(
        "SOLAR_OUTPUT_DIR",
        "/Users/sushil/Library/Mobile Documents/com~apple~CloudDocs/Weekly Solar Plant Report",
    )
)
CONFIG_FILE = PROJECT_DIR / "solar_live_app_config.json"
ENV_FILE = PROJECT_DIR / ".solar_report_env"
USERS_FILE = PROJECT_DIR / "solar_users.json"
HISTORY_FILE = PROJECT_DIR / "solar_generation_history.json"
BUNDLED_PYTHON = Path("/Users/sushil/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3")
VENV_PYTHON = PROJECT_DIR / ".venv/bin/python"
DEFAULT_CONFIG = {
    "output_dir": str(DEFAULT_OUTPUT_DIR),
    "auto_report_day": "Sunday",
    "auto_report_time": "20:00",
    "auto_refresh_on_open": True,
}
APP_VERSION = "2026-07-10-report-links-v5"
PLANT_COLUMNS = [
    "App ID",
    "Brand",
    "Site Name",
    "Plant Capacity (kW)",
    "Current Status",
    "Daily Generation (kWh)",
    "Weekly Generation (kWh)",
    "Year Generation (kWh)",
    "Total Generation (MWh)",
    "2026 Yield (kWh/kW)",
    "Average Daily Yield (kWh/kW/day)",
    "Year Generation Source",
    "Timestamp",
]
AUTH_USER = os.environ.get("NCE_APP_USER") or os.environ.get("SOLAR_APP_USER") or "admin"
AUTH_PASSWORD = os.environ.get("NCE_APP_PASSWORD") or os.environ.get("SOLAR_APP_PASSWORD") or ""
SESSION_COOKIE = "nce_solar_session"
SESSION_SECONDS = 12 * 60 * 60
SESSION_SECRET = (
    os.environ.get("NCE_SESSION_SECRET")
    or os.environ.get("SOLAR_SESSION_SECRET")
    or hashlib.sha256((str(PROJECT_DIR) + AUTH_USER + AUTH_PASSWORD).encode("utf-8")).hexdigest()
)
UPLOAD_TOKEN = os.environ.get("SOLAR_UPLOAD_TOKEN") or os.environ.get("NCE_UPLOAD_TOKEN") or ""
UPLOAD_GENERATION_FILES = {
    "solis": "solis_generation.json",
    "solax": "solax_generation.json",
}


def plant_key(brand: Any, site: Any) -> str:
    return f"{str(brand).strip()}::{str(site).strip()}"


def parse_iso_date(value: Any) -> dt.date | None:
    text = str(value or "").strip()
    if len(text) >= 10:
        try:
            return dt.date.fromisoformat(text[:10])
        except ValueError:
            return None
    return None


def week_start(value: dt.date) -> dt.date:
    return value - dt.timedelta(days=value.weekday())


def hash_password(password: str, salt: str | None = None, iterations: int = 260000) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${base64.b64encode(digest).decode('ascii')}"


def verify_password(password: str, stored: str) -> bool:
    if stored.startswith("pbkdf2_sha256$"):
        try:
            _, iterations, salt, expected = stored.split("$", 3)
            actual = hash_password(password, salt=salt, iterations=int(iterations)).rsplit("$", 1)[1]
            return hmac.compare_digest(actual, expected)
        except Exception:
            return False
    return hmac.compare_digest(password, stored)


def load_users() -> dict[str, dict[str, Any]]:
    payload: dict[str, Any] = {}
    if os.environ.get("NCE_USERS_JSON"):
        try:
            payload = json.loads(os.environ["NCE_USERS_JSON"])
        except json.JSONDecodeError:
            payload = {}
    elif USERS_FILE.exists():
        try:
            payload = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}

    users: dict[str, dict[str, Any]] = {}
    for item in payload.get("users", []):
        username = str(item.get("username") or "").strip()
        if not username:
            continue
        users[username] = {
            "username": username,
            "password_hash": item.get("password_hash") or item.get("password") or "",
            "role": item.get("role") or "customer",
            "plants": item.get("plants") or [],
        }

    if AUTH_PASSWORD and AUTH_USER not in users:
        users[AUTH_USER] = {
            "username": AUTH_USER,
            "password_hash": AUTH_PASSWORD,
            "role": "admin",
            "plants": ["*"],
        }
    return users


def user_can_access(user: dict[str, Any] | None, key: str) -> bool:
    if not user:
        return not bool(load_users())
    if user.get("role") == "admin":
        return True
    allowed = set(user.get("plants") or [])
    return "*" in allowed or key in allowed


def is_admin(user: dict[str, Any] | None) -> bool:
    return not load_users() or bool(user and user.get("role") == "admin")


def sign_session(username: str, expires: int) -> str:
    body = f"{username}|{expires}"
    signature = hmac.new(SESSION_SECRET.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{body}|{signature}".encode("utf-8")).decode("ascii")


def read_session(value: str) -> str | None:
    try:
        decoded = base64.urlsafe_b64decode(value.encode("ascii")).decode("utf-8")
        username, expires, signature = decoded.rsplit("|", 2)
        body = f"{username}|{expires}"
        expected = hmac.new(SESSION_SECRET.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        if int(expires) < int(time.time()):
            return None
        return username
    except Exception:
        return None


def load_config() -> dict[str, Any]:
    if CONFIG_FILE.exists():
        try:
            config = {**DEFAULT_CONFIG, **json.loads(CONFIG_FILE.read_text(encoding="utf-8"))}
            if os.environ.get("SOLAR_OUTPUT_DIR"):
                config["output_dir"] = os.environ["SOLAR_OUTPUT_DIR"]
            if os.environ.get("SOLAR_AUTO_REFRESH_ON_OPEN"):
                config["auto_refresh_on_open"] = os.environ["SOLAR_AUTO_REFRESH_ON_OPEN"].lower() in {"1", "true", "yes"}
            return config
        except Exception:
            pass
    config = dict(DEFAULT_CONFIG)
    if os.environ.get("SOLAR_AUTO_REFRESH_ON_OPEN"):
        config["auto_refresh_on_open"] = os.environ["SOLAR_AUTO_REFRESH_ON_OPEN"].lower() in {"1", "true", "yes"}
    return config


def save_config(config: dict[str, Any]) -> None:
    CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")


def load_env_file() -> dict[str, str]:
    env: dict[str, str] = {}
    if not ENV_FILE.exists():
        return env
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip("\"'")
    return env


def report_python() -> str:
    return str(BUNDLED_PYTHON if BUNDLED_PYTHON.exists() else VENV_PYTHON if VENV_PYTHON.exists() else "python3")


def refresh_python() -> str:
    return str(VENV_PYTHON if VENV_PYTHON.exists() else BUNDLED_PYTHON if BUNDLED_PYTHON.exists() else "python3")


def local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except Exception:
        return "127.0.0.1"


class SolarLiveApp:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.config = load_config()
        self.last_refresh: dict[str, Any] = {"started": None, "finished": None, "running": False, "steps": []}
        self.refresh_lock = threading.Lock()
        self.last_auto_key = ""

    @property
    def output_dir(self) -> Path:
        return Path(self.config.get("output_dir") or DEFAULT_OUTPUT_DIR)

    def plant_dataframe(self) -> pd.DataFrame:
        try:
            df = load_data(current_project=True)
        except Exception:
            return pd.DataFrame(columns=PLANT_COLUMNS)
        df = df.sort_values(["Brand", "Site Name"]).reset_index(drop=True)
        df.insert(0, "App ID", [f"plant_{index}" for index in range(len(df))])
        return df

    def plant_payload(self, user: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        df = self.plant_dataframe()
        today = dt.date.today().isoformat()
        rows = []
        for row in df.to_dict(orient="records"):
            key = plant_key(row["Brand"], row["Site Name"])
            if not user_can_access(user, key):
                continue
            timestamp = str(row.get("Timestamp") or "")
            data_date = timestamp[:10] if len(timestamp) >= 10 else ""
            rows.append(
                {
                    "id": row["App ID"],
                    "plantKey": key,
                    "brand": row["Brand"],
                    "site": row["Site Name"],
                    "status": row["Current Status"],
                    "capacity": float(row["Plant Capacity (kW)"] or 0),
                    "daily": float(row["Daily Generation (kWh)"] or 0),
                    "weekly": float(row["Weekly Generation (kWh)"] or 0),
                    "year": float(row["Year Generation (kWh)"] or 0),
                    "total": float(row["Total Generation (MWh)"] or 0),
                    "yield2026": float(row["2026 Yield (kWh/kW)"] or 0),
                    "avgDay": float(row["Average Daily Yield (kWh/kW/day)"] or 0),
                    "source": row.get("Year Generation Source", ""),
                    "timestamp": timestamp,
                    "dataDate": data_date,
                    "fresh": data_date == today,
                }
            )
        return rows

    def load_history(self) -> list[dict[str, Any]]:
        if not HISTORY_FILE.exists():
            return []
        try:
            data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
        return data if isinstance(data, list) else []

    def save_history(self, rows: list[dict[str, Any]]) -> None:
        temp = HISTORY_FILE.with_suffix(".json.tmp")
        temp.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        temp.replace(HISTORY_FILE)

    def record_history_snapshot(self) -> dict[str, Any]:
        current = self.plant_payload(None)
        if not current:
            return {"label": "History snapshot", "ok": False, "message": "No plant data available to record."}

        existing = self.load_history()
        by_key = {
            f"{row.get('plantKey')}::{row.get('date')}": row
            for row in existing
            if row.get("plantKey") and row.get("date")
        }
        today = dt.date.today().isoformat()
        count = 0
        for plant in current:
            date_text = plant.get("dataDate") or today
            key = f"{plant['plantKey']}::{date_text}"
            by_key[key] = {
                "date": date_text,
                "brand": plant.get("brand", ""),
                "site": plant.get("site", ""),
                "plantKey": plant.get("plantKey", ""),
                "status": plant.get("status", ""),
                "capacity": plant.get("capacity", 0),
                "daily": plant.get("daily", 0),
                "weekly": plant.get("weekly", 0),
                "year": plant.get("year", 0),
                "total": plant.get("total", 0),
                "yield2026": plant.get("yield2026", 0),
                "timestamp": plant.get("timestamp", ""),
                "recordedAt": dt.datetime.now().replace(microsecond=0).isoformat(),
            }
            count += 1

        rows = sorted(by_key.values(), key=lambda row: (str(row.get("plantKey", "")), str(row.get("date", ""))))
        self.save_history(rows)
        return {"label": "History snapshot", "ok": True, "message": f"Saved history for {count} plants."}

    def history_payload(self, plant_key_value: str, user: dict[str, Any] | None = None) -> dict[str, Any]:
        if not user_can_access(user, plant_key_value):
            return {"daily": [], "weekly": [], "yearly": []}

        rows = [
            row for row in self.load_history()
            if row.get("plantKey") == plant_key_value and parse_iso_date(row.get("date"))
        ]
        rows.sort(key=lambda row: str(row.get("date", "")), reverse=True)

        weekly: dict[str, dict[str, Any]] = {}
        yearly: dict[str, dict[str, Any]] = {}
        for row in rows:
            row_date = parse_iso_date(row.get("date"))
            if not row_date:
                continue
            week_start_date = week_start(row_date)
            week_end_date = week_start_date + dt.timedelta(days=6)
            week_label = f"{week_start_date.isoformat()} to {week_end_date.isoformat()}"
            week_row = weekly.setdefault(
                week_label,
                {"week": week_label, "dailySum": 0.0, "weekly": 0.0, "days": 0, "lastDate": row_date.isoformat()},
            )
            week_row["dailySum"] += float(row.get("daily") or 0)
            week_row["weekly"] = max(float(week_row.get("weekly") or 0), float(row.get("weekly") or 0))
            week_row["days"] += 1
            if row_date.isoformat() > week_row["lastDate"]:
                week_row["lastDate"] = row_date.isoformat()

            year_label = str(row_date.year)
            year_row = yearly.setdefault(
                year_label,
                {"year": year_label, "yearKwh": 0.0, "totalMwh": 0.0, "lastDate": row_date.isoformat()},
            )
            if row_date.isoformat() >= year_row["lastDate"]:
                year_row["yearKwh"] = float(row.get("year") or 0)
                year_row["totalMwh"] = float(row.get("total") or 0)
                year_row["lastDate"] = row_date.isoformat()

        return {
            "daily": rows[:120],
            "weekly": sorted(weekly.values(), key=lambda row: row["lastDate"], reverse=True)[:80],
            "yearly": sorted(yearly.values(), key=lambda row: row["year"], reverse=True),
        }

    def run_step(self, label: str, command: list[str], env: dict[str, str]) -> dict[str, Any]:
        started = dt.datetime.now()
        try:
            result = subprocess.run(
                command,
                cwd=str(PROJECT_DIR),
                env=env,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
            return {
                "label": label,
                "ok": result.returncode == 0,
                "started": started.isoformat(timespec="seconds"),
                "finished": dt.datetime.now().isoformat(timespec="seconds"),
                "message": (result.stdout or result.stderr or "").strip()[-1200:],
            }
        except Exception as exc:
            return {
                "label": label,
                "ok": False,
                "started": started.isoformat(timespec="seconds"),
                "finished": dt.datetime.now().isoformat(timespec="seconds"),
                "message": str(exc),
            }

    def rebuild_outputs(self, env: dict[str, str] | None = None) -> list[dict[str, Any]]:
        env = env or os.environ.copy()
        env.update(load_env_file())
        env["PYTHONPYCACHEPREFIX"] = str(PROJECT_DIR / ".pycache")
        report_py = report_python()
        steps: list[dict[str, Any]] = []
        if not self.plant_dataframe().empty:
            steps.append(
                self.run_step(
                    "Rebuild master PDF",
                    [report_py, "./solar_performance_report_app.py", "--current-project", "--output-dir", str(self.output_dir), "--plant-reports"],
                    env,
                )
            )
            steps.append(
                self.run_step(
                    "Rebuild dashboard app",
                    [report_py, "./build_solar_dashboard_app.py", "--output-dir", str(self.output_dir)],
                    env,
                )
            )
        else:
            steps.append({"label": "Load plant data", "ok": False, "message": "No plant data available after upload."})
        return steps

    def save_uploaded_generation(self, brand: str, payload: dict[str, Any]) -> dict[str, Any]:
        key = brand.strip().lower()
        if key not in UPLOAD_GENERATION_FILES:
            return {"ok": False, "message": "Only Solis and SolaX generation uploads are supported."}
        if not isinstance(payload.get("systems"), list):
            return {"ok": False, "message": "Uploaded generation JSON must contain a systems list."}

        filename = UPLOAD_GENERATION_FILES[key]
        target = PROJECT_DIR / filename
        payload.setdefault("uploaded_at", dt.datetime.now().replace(microsecond=0).isoformat())
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temp.replace(target)

        started = dt.datetime.now().isoformat(timespec="seconds")
        self.last_refresh = {
            "started": started,
            "finished": None,
            "running": True,
            "steps": [{"label": f"{brand.title()} upload", "ok": True, "message": f"Saved {filename} with {len(payload['systems'])} systems."}],
        }
        self.append_refresh_step(self.record_history_snapshot())
        for step in self.rebuild_outputs():
            self.append_refresh_step(step)
        self.last_refresh["finished"] = dt.datetime.now().isoformat(timespec="seconds")
        self.last_refresh["running"] = False
        return {"ok": True, "file": filename, "systems": len(payload["systems"]), "last_refresh": self.last_refresh}

    def append_refresh_step(self, step: dict[str, Any]) -> None:
        self.last_refresh.setdefault("steps", []).append(step)

    def file_status_step(self, label: str, path: Path, ok_message: str, missing_message: str) -> dict[str, Any]:
        now = dt.datetime.now().isoformat(timespec="seconds")
        return {
            "label": label,
            "ok": path.exists(),
            "started": now,
            "finished": now,
            "message": ok_message if path.exists() else missing_message,
        }

    def refresh(self) -> dict[str, Any]:
        with self.refresh_lock:
            started = dt.datetime.now()
            self.last_refresh = {
                "started": started.isoformat(timespec="seconds"),
                "finished": None,
                "running": True,
                "steps": [],
            }
            env = os.environ.copy()
            env.update(load_env_file())
            env["PYTHONPYCACHEPREFIX"] = str(PROJECT_DIR / ".pycache")
            refresh_py = refresh_python()
            report_py = report_python()

            if env.get("SEMS_USERNAME") and env.get("SEMS_PASSWORD"):
                self.append_refresh_step(self.run_step("GoodWe station refresh", [refresh_py, "./sems_export_json.py"], env))
                self.append_refresh_step(self.run_step("GoodWe weekly refresh", [refresh_py, "./sems_weekly_generation.py"], env))
            else:
                self.append_refresh_step({"label": "GoodWe refresh", "ok": False, "message": "Missing SEMS_USERNAME/SEMS_PASSWORD"})

            if env.get("FRONIUS_USERNAME") and env.get("FRONIUS_PASSWORD"):
                self.append_refresh_step(self.run_step("Fronius current refresh", [refresh_py, "./fronius_backend_current_generation.py"], env))
                self.append_refresh_step(self.run_step("Fronius weekly refresh", [refresh_py, "./fronius_backend_weekly_generation.py"], env))
            else:
                self.append_refresh_step({"label": "Fronius refresh", "ok": False, "message": "Missing FRONIUS_USERNAME/FRONIUS_PASSWORD"})

            if env.get("FIMER_USERNAME") and env.get("FIMER_PASSWORD"):
                self.append_refresh_step(self.run_step("FIMER refresh", [refresh_py, "./fimer_backend_export_generation.py"], env))
            else:
                self.append_refresh_step({"label": "FIMER refresh", "ok": False, "message": "Missing FIMER_USERNAME/FIMER_PASSWORD"})

            if (PROJECT_DIR / "solis_network_capture.json").exists():
                self.append_refresh_step(self.run_step("Solis import from latest capture", [refresh_py, "./solis_capture_to_generation.py"], env))
            else:
                self.append_refresh_step(
                    self.file_status_step(
                        "Solis saved data",
                        PROJECT_DIR / "solis_generation.json",
                        "Using uploaded solis_generation.json. Cloud live refresh needs official Solis API access or a fresh browser capture from the Mac.",
                        "No Solis data found. Upload solis_generation.json, or configure official Solis API access.",
                    )
                )

            if (PROJECT_DIR / "solax_network_capture.json").exists():
                self.append_refresh_step(self.run_step("SolaX import from latest capture", [refresh_py, "./solax_capture_to_generation.py"], env))
            else:
                self.append_refresh_step(
                    self.file_status_step(
                        "SolaX saved data",
                        PROJECT_DIR / "solax_generation.json",
                        "Using uploaded solax_generation.json. Cloud live refresh needs official SolaX API/backend access or a fresh browser capture from the Mac.",
                        "No SolaX data found. Upload solax_generation.json, or configure official SolaX API/backend access.",
                    )
                )

            self.append_refresh_step(self.record_history_snapshot())
            for step in self.rebuild_outputs(env):
                self.append_refresh_step(step)

            self.last_refresh["finished"] = dt.datetime.now().isoformat(timespec="seconds")
            self.last_refresh["running"] = False
            return self.last_refresh

    def refresh_async(self) -> dict[str, Any]:
        if self.refresh_lock.locked():
            return {**self.last_refresh, "accepted": False, "message": "Refresh already running"}
        self.last_refresh = {
            "started": dt.datetime.now().isoformat(timespec="seconds"),
            "finished": None,
            "running": True,
            "steps": [{"label": "Refresh queued", "ok": True, "message": "Starting background refresh"}],
        }
        threading.Thread(target=self.refresh, daemon=True).start()
        return {**self.last_refresh, "accepted": True}

    def generate_selected_report(self, plant_ids: list[str], user: dict[str, Any] | None = None) -> dict[str, Any]:
        df = self.plant_dataframe()
        df["Plant Key"] = df.apply(lambda row: plant_key(row["Brand"], row["Site Name"]), axis=1)
        if user and user.get("role") != "admin":
            df = df[df["Plant Key"].apply(lambda key: user_can_access(user, key))]
        if plant_ids:
            selected = df[df["App ID"].isin(plant_ids)].drop(columns=["App ID"])
        else:
            selected = df.drop(columns=["App ID"])
        if "Plant Key" in selected:
            selected = selected.drop(columns=["Plant Key"])
        if selected.empty:
            return {"ok": False, "message": "No plants selected"}
        report_dir = self.output_dir / "Selected Plant Reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M")
        if len(selected) == len(df):
            name = f"Solar_Report_All_Plants_{stamp}.pdf"
        elif len(selected) == 1:
            site = "".join(ch if ch.isalnum() else "_" for ch in str(selected.iloc[0]["Site Name"]))[:70]
            name = f"Solar_Report_{site}_{stamp}.pdf"
        else:
            name = f"Solar_Report_Selected_{len(selected)}_Plants_{stamp}.pdf"
        path = report_dir / name
        logo_path = str(DEFAULT_LOGO) if DEFAULT_LOGO.exists() else None
        generate_compact_pdf(selected, path, logo_path=logo_path)
        return {
            "ok": True,
            "path": str(path),
            "download_url": f"/reports/{path.relative_to(self.output_dir).as_posix()}",
            "count": int(len(selected)),
        }

    def latest_reports(self, limit: int = 12) -> list[dict[str, Any]]:
        root = self.output_dir
        if not root.exists():
            return []
        reports = []
        for path in root.rglob("*.pdf"):
            if not path.is_file():
                continue
            try:
                relative = path.relative_to(root).as_posix()
            except ValueError:
                continue
            reports.append(
                {
                    "name": path.name,
                    "url": f"/reports/{urllib.parse.quote(relative)}",
                    "modified": dt.datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
                    "size_kb": round(path.stat().st_size / 1024, 1),
                }
            )
        reports.sort(key=lambda row: row["modified"], reverse=True)
        return reports[:limit]

    def maybe_auto_run(self) -> None:
        while True:
            try:
                now = dt.datetime.now()
                day = self.config.get("auto_report_day", "Sunday")
                time_text = self.config.get("auto_report_time", "20:00")
                key = f"{now.date()}-{time_text}"
                if now.strftime("%A") == day and now.strftime("%H:%M") == time_text and self.last_auto_key != key:
                    self.last_auto_key = key
                    self.refresh()
            except Exception:
                pass
            time.sleep(30)


APP: SolarLiveApp | None = None


class Handler(BaseHTTPRequestHandler):
    def cookie_value(self, name: str) -> str:
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            key, _, value = part.strip().partition("=")
            if key == name:
                return urllib.parse.unquote(value)
        return ""

    def current_user(self) -> dict[str, Any] | None:
        users = load_users()
        if not users:
            return None
        username = read_session(self.cookie_value(SESSION_COOKIE))
        if username and username in users:
            return users[username]
        header = self.headers.get("Authorization", "")
        if header.startswith("Basic "):
            try:
                userpass = base64.b64decode(header.split(" ", 1)[1]).decode("utf-8")
                username, _, password = userpass.partition(":")
                user = users.get(username)
                if user and verify_password(password, user.get("password_hash", "")):
                    return user
            except Exception:
                return None
        return None

    def require_auth(self, html: bool = False) -> dict[str, Any] | None:
        users = load_users()
        if not users:
            return None
        user = self.current_user()
        if user:
            return user
        if html:
            self.send_response(302)
            self.send_header("Location", "/login")
            self.end_headers()
            return None
        body = json.dumps({"error": "Authentication required"}).encode("utf-8")
        self.send_response(401)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return None

    def send_login_page(self, error: str = "") -> None:
        body = LOGIN_HTML.replace("__ERROR__", error).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def send_exception(self, exc: Exception) -> None:
        status = 500
        if self.path.startswith("/api/"):
            self.send_json({"error": str(exc), "type": exc.__class__.__name__}, status)
            return
        body = f"Server error: {exc}".encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        try:
            assert APP is not None
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/healthz":
                self.send_json({"ok": True})
            elif parsed.path == "/login":
                self.send_login_page()
            elif parsed.path == "/logout":
                self.send_response(302)
                self.send_header("Set-Cookie", f"{SESSION_COOKIE}=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax")
                self.send_header("Location", "/login")
                self.end_headers()
            elif parsed.path == "/":
                user = self.require_auth(html=True)
                if load_users() and not user:
                    return
                body = LIVE_HTML.replace("__USER__", (user or {}).get("username", "Local")).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif parsed.path.startswith("/api/") or parsed.path.startswith("/reports/"):
                user = self.require_auth()
                if load_users() and not user:
                    return
                self.handle_authenticated_get(parsed, user)
            else:
                self.send_json({"error": "Not found"}, 404)
        except Exception as exc:
            self.send_exception(exc)

    def handle_authenticated_get(self, parsed: urllib.parse.ParseResult, user: dict[str, Any] | None) -> None:
        assert APP is not None
        if parsed.path == "/api/plants":
            self.send_json({"plants": APP.plant_payload(user), "today": dt.date.today().isoformat()})
        elif parsed.path == "/api/history":
            query = urllib.parse.parse_qs(parsed.query)
            key = (query.get("plant_key") or [""])[0]
            self.send_json(APP.history_payload(key, user))
        elif parsed.path == "/api/reports":
            self.send_json({"reports": APP.latest_reports()})
        elif parsed.path == "/api/status":
            self.send_json(
                {
                    "auth_enabled": bool(load_users()),
                    "user": {"username": (user or {}).get("username", "Local"), "role": (user or {}).get("role", "admin")},
                    "config": APP.config,
                    "app_version": APP_VERSION,
                    "last_refresh": APP.last_refresh,
                    "local_url": f"http://127.0.0.1:{APP.port}",
                    "mobile_url": f"http://{local_ip()}:{APP.port}",
                }
            )
        elif parsed.path.startswith("/reports/"):
            relative = urllib.parse.unquote(parsed.path[len("/reports/") :])
            path = (APP.output_dir / relative).resolve()
            try:
                path.relative_to(APP.output_dir.resolve())
            except ValueError:
                self.send_json({"error": "Invalid report path"}, 400)
                return
            if not path.exists() or not path.is_file():
                self.send_json({"error": "Report not found"}, 404)
                return
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_json({"error": "Not found"}, 404)

    def handle_generation_upload(self, parsed: urllib.parse.ParseResult) -> None:
        assert APP is not None
        provided = self.headers.get("X-Upload-Token") or urllib.parse.parse_qs(parsed.query).get("token", [""])[0]
        if not UPLOAD_TOKEN:
            self.send_json({"error": "Generation upload is disabled. Set SOLAR_UPLOAD_TOKEN in Render first."}, 403)
            return
        if not hmac.compare_digest(provided, UPLOAD_TOKEN):
            self.send_json({"error": "Invalid upload token"}, 403)
            return
        payload = self.read_json()
        brand = str(payload.get("brand") or "").strip()
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        if not brand:
            brand = str(data.get("brand") or data.get("source") or "").split("_")[0]
        self.send_json(APP.save_uploaded_generation(brand, data))

    def do_POST(self) -> None:
        try:
            assert APP is not None
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/login":
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8")
                form = urllib.parse.parse_qs(raw)
                username = (form.get("username") or [""])[0].strip()
                password = (form.get("password") or [""])[0]
                user = load_users().get(username)
                if not user or not verify_password(password, user.get("password_hash", "")):
                    self.send_login_page("Invalid username or password")
                    return
                expires = int(time.time()) + SESSION_SECONDS
                secure = " Secure;" if self.headers.get("X-Forwarded-Proto") == "https" else ""
                self.send_response(302)
                self.send_header("Set-Cookie", f"{SESSION_COOKIE}={sign_session(username, expires)}; Path=/; HttpOnly;{secure} SameSite=Lax; Max-Age={SESSION_SECONDS}")
                self.send_header("Location", "/")
                self.end_headers()
                return

            if parsed.path == "/api/upload-generation":
                self.handle_generation_upload(parsed)
                return

            user = self.require_auth()
            if load_users() and not user:
                return
            if parsed.path == "/api/refresh":
                if not is_admin(user):
                    self.send_json({"error": "Admin access required"}, 403)
                    return
                self.send_json(APP.refresh_async())
            elif parsed.path == "/api/report":
                payload = self.read_json()
                self.send_json(APP.generate_selected_report(payload.get("plant_ids") or [], user))
            elif parsed.path == "/api/config":
                if not is_admin(user):
                    self.send_json({"error": "Admin access required"}, 403)
                    return
                payload = self.read_json()
                APP.config.update({key: value for key, value in payload.items() if key in DEFAULT_CONFIG})
                save_config(APP.config)
                self.send_json({"ok": True, "config": APP.config})
            else:
                self.send_json({"error": "Not found"}, 404)
        except Exception as exc:
            self.send_exception(exc)

    def log_message(self, format: str, *args: Any) -> None:
        return


LOGIN_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NCE Solar Login</title>
<style>
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;background:linear-gradient(145deg,#eaf5fb,#f6fbf7);color:#1e2b3f}
.login{width:min(420px,calc(100vw - 32px));background:white;border:1px solid #d7e0ec;border-radius:12px;box-shadow:0 14px 50px rgba(23,79,156,.16);padding:26px}
h1{font-size:24px;margin:0 0 6px;color:#174f9c}.sub{margin:0 0 22px;color:#647084}label{display:block;font-size:12px;font-weight:800;color:#647084;margin:14px 0 6px}
input{width:100%;height:42px;border:1px solid #d7e0ec;border-radius:8px;padding:0 12px;font-size:15px}button{margin-top:18px;width:100%;height:42px;border:0;border-radius:8px;background:#174f9c;color:white;font-weight:900;font-size:15px}
.error{margin:12px 0 0;color:#c73e3e;font-weight:800;font-size:13px}.mark{height:5px;width:90px;background:#18b9d6;border-radius:99px;margin-bottom:18px}
</style>
</head>
<body>
<form class="login" method="post" action="/login">
  <div class="mark"></div>
  <h1>NCE Solar Dashboard</h1>
  <p class="sub">Secure access for plant reports and live performance.</p>
  <label>Username</label>
  <input name="username" autocomplete="username" required>
  <label>Password</label>
  <input name="password" type="password" autocomplete="current-password" required>
  <button type="submit">Sign In</button>
  <div class="error">__ERROR__</div>
</form>
</body>
</html>"""


LIVE_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NCE Live Solar App</title>
<style>
:root{--blue:#174f9c;--cyan:#18b9d6;--green:#16845f;--red:#c73e3e;--ink:#1e2b3f;--muted:#647084;--line:#d7e0ec;--soft:#f3f7fb}
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;background:#eef3f8;color:var(--ink)}
header{background:var(--blue);color:white;padding:14px 22px;display:flex;gap:16px;align-items:center;position:sticky;top:0;z-index:10}
h1{font-size:20px;margin:0}.meta{margin-left:auto;text-align:right;font-size:12px;line-height:1.4}
a.logout{color:white;text-decoration:none;border:1px solid rgba(255,255,255,.55);border-radius:6px;padding:7px 10px;font-weight:800;font-size:12px}
main{padding:16px;max-width:1440px;margin:auto}.toolbar{display:grid;grid-template-columns:1.2fr .8fr .8fr auto auto auto;gap:10px;align-items:end;margin-bottom:12px}
label{font-size:11px;color:var(--muted);font-weight:700;display:block;margin-bottom:5px}select,input{height:36px;border:1px solid var(--line);border-radius:6px;padding:0 10px;width:100%;background:white}
button{height:36px;border:0;border-radius:6px;padding:0 13px;background:var(--blue);color:white;font-weight:800;cursor:pointer;white-space:nowrap}button.alt{background:var(--cyan)}button.gray{background:#5c6f8b}
.grid{display:grid;grid-template-columns:repeat(6,minmax(120px,1fr));gap:10px;margin-bottom:12px}.card,.panel{background:white;border:1px solid var(--line);border-radius:8px;box-shadow:0 1px 4px rgba(15,35,60,.05)}
.card{padding:12px;min-height:78px}.card span{display:block;color:var(--muted);font-size:11px;font-weight:700;margin-bottom:10px}.card strong{font-size:20px}
.panel{padding:14px}.split{display:grid;grid-template-columns:minmax(0,1.65fr) minmax(340px,.9fr);gap:12px}h2{font-size:15px;margin:0 0 10px}
table{width:100%;border-collapse:collapse;font-size:12px}th{background:var(--blue);color:white;text-align:left;padding:8px 7px}td{border-bottom:1px solid var(--line);padding:7px}tr:nth-child(even){background:#f8fafc}
.status{font-weight:800}.online{color:var(--green)}.offline,.stale{color:var(--red)}.fresh{color:var(--green)}.pill{display:inline-block;border-radius:999px;padding:2px 7px;font-size:10px;font-weight:800;color:white}.pill.fresh{background:var(--green);color:white}.pill.stale{background:var(--red);color:white}
.plant-title{font-size:21px;font-weight:850}.details{display:grid;grid-template-columns:1fr 1fr;gap:8px}.detail{border:1px solid var(--line);border-radius:6px;background:#fbfdff;padding:10px}.detail span{display:block;color:var(--muted);font-size:11px;font-weight:700;margin-bottom:7px}
.checkcell{width:34px}.report-link{font-size:12px;color:var(--muted);margin-top:8px;word-break:break-all}.download-btn{display:inline-block;margin-top:8px;background:var(--green);color:white;text-decoration:none;border-radius:6px;padding:9px 12px;font-weight:900}.report-list{margin-top:8px;display:grid;gap:6px}.report-item{display:block;border:1px solid var(--line);border-radius:6px;background:#fbfdff;padding:8px;color:var(--blue);text-decoration:none;font-weight:800}.report-item span{display:block;color:var(--muted);font-size:11px;font-weight:700;margin-top:3px}.log{font-family:ui-monospace,Menlo,monospace;font-size:11px;white-space:pre-wrap;max-height:180px;overflow:auto;background:#f8fafc;border:1px solid var(--line);padding:8px;border-radius:6px}
.history-block{margin-top:12px}.history-block h3{font-size:13px;margin:10px 0 6px}.history-scroll{max-height:160px;overflow:auto;border:1px solid var(--line);border-radius:6px}.history-scroll table{font-size:11px}.history-scroll th{position:sticky;top:0}.empty-history{color:var(--muted);font-size:12px;padding:8px;border:1px solid var(--line);border-radius:6px;background:#fbfdff}
@media(max-width:980px){header{position:static}.toolbar,.grid,.split{grid-template-columns:1fr}table{font-size:11px}th:nth-child(5),td:nth-child(5),th:nth-child(7),td:nth-child(7){display:none}}
</style>
</head>
<body>
<header><h1>NCE Live Solar App</h1><div class="meta"><div>Signed in: __USER__</div><div id="dateLine"></div><div id="versionLine"></div><div id="mobileLine"></div></div><a class="logout" href="/logout">Logout</a></header>
<main>
  <div class="toolbar">
    <div><label>Search</label><input id="search" placeholder="Search any plant"></div>
    <div><label>Brand</label><select id="brand"></select></div>
    <div><label>Status</label><select id="status"></select></div>
    <button id="refresh" class="alt">Refresh Live</button>
    <button id="report">Generate Selected Report</button>
    <button id="selectAll" class="gray">Select All</button>
  </div>
  <div class="grid" id="cards"></div>
  <div class="split">
    <section class="panel">
      <h2>Plants</h2>
      <table><thead><tr><th class="checkcell"></th><th>Brand</th><th>Plant</th><th>Status</th><th>Date</th><th>Daily</th><th>Weekly</th><th>2026/kW</th></tr></thead><tbody id="rows"></tbody></table>
    </section>
    <aside class="panel">
      <h2>Selected Plant</h2>
      <div id="detail"></div>
      <h2 style="margin-top:14px">Auto Report Time</h2>
      <div class="details">
        <div><label>Day</label><select id="autoDay"><option>Sunday</option><option>Monday</option><option>Tuesday</option><option>Wednesday</option><option>Thursday</option><option>Friday</option><option>Saturday</option></select></div>
        <div><label>Time</label><input id="autoTime" type="time"></div>
      </div>
      <button id="saveSchedule" style="margin-top:10px">Save Schedule</button>
      <div class="report-link" id="reportResult"></div>
      <h2 style="margin-top:14px">Latest Reports</h2>
      <div class="report-list" id="reportList">No reports loaded.</div>
      <h2 style="margin-top:14px">Refresh Log</h2>
      <div class="log" id="log">Ready.</div>
    </aside>
  </div>
</main>
<script>
let plants=[], selected=new Set(), statusData={}, activePlantId=null, activeHistoryKey='';
const searchInput=document.querySelector('#search');
const brandFilter=document.querySelector('#brand');
const statusFilter=document.querySelector('#status');
const cardsEl=document.querySelector('#cards');
const rowsEl=document.querySelector('#rows');
const detailEl=document.querySelector('#detail');
const refreshBtn=document.querySelector('#refresh');
const reportBtn=document.querySelector('#report');
const selectAllBtn=document.querySelector('#selectAll');
const saveScheduleBtn=document.querySelector('#saveSchedule');
const dateLineEl=document.querySelector('#dateLine');
const versionLineEl=document.querySelector('#versionLine');
const mobileLineEl=document.querySelector('#mobileLine');
const autoDayEl=document.querySelector('#autoDay');
const autoTimeEl=document.querySelector('#autoTime');
const reportResultEl=document.querySelector('#reportResult');
const reportListEl=document.querySelector('#reportList');
const logEl=document.querySelector('#log');
const todayText=()=>new Date().toISOString().slice(0,10);
function f(v,d=2){return Number(v||0).toLocaleString('en-IN',{minimumFractionDigits:d,maximumFractionDigits:d})}
function cls(s){s=String(s||'').toLowerCase();return (s.includes('online')||s.includes('normal'))?'online':'offline'}
function fresh(p){return p.dataDate===todayText()}
function staleNote(p){return !fresh(p)&&String(p.brand||'').toLowerCase()==='solis'?'Solis data is from the last saved Mac capture. Refresh Solis on the Mac to make this current.':''}
function uniq(a){return [...new Set(a)].filter(Boolean).sort()}
function h(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function api(path,opt){const r=await fetch(path,opt);const text=await r.text();let data={};try{data=text?JSON.parse(text):{};}catch(e){throw new Error(`${path} returned ${r.status}: ${text.slice(0,240)||'empty response'}`)}if(!r.ok){throw new Error(data.error||`${path} returned ${r.status}`)}return data}
function filtered(){const q=searchInput.value.toLowerCase(), b=brandFilter.value, s=statusFilter.value;return plants.filter(p=>(b==='all'||p.brand===b)&&(s==='all'||p.status===s)&&(`${p.site} ${p.brand}`.toLowerCase().includes(q)))}
function selectedRows(){return plants.filter(p=>selected.has(p.id))}
function renderFilters(){brandFilter.innerHTML='<option value="all">All Brands</option>'+uniq(plants.map(p=>p.brand)).map(x=>`<option>${x}</option>`).join('');statusFilter.innerHTML='<option value="all">All Status</option>'+uniq(plants.map(p=>p.status)).map(x=>`<option>${x}</option>`).join('')}
function historyTable(title, rows, cols){if(!rows?.length)return `<div class="history-block"><h3>${title}</h3><div class="empty-history">No previous data yet. It will build after refreshes/uploads.</div></div>`;return `<div class="history-block"><h3>${title}</h3><div class="history-scroll"><table><thead><tr>${cols.map(c=>`<th>${c[0]}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${cols.map(c=>`<td>${c[2]?f(r[c[1]]):h(r[c[1]])}</td>`).join('')}</tr>`).join('')}</tbody></table></div></div>`}
function selectorOptions(rows,key){return (rows||[]).map((r,i)=>`<option value="${i}">${h(r[key]||'')}</option>`).join('')}
function selectedSummary(type,row){if(!row)return '<div class="empty-history">No data for this selection yet.</div>';if(type==='daily')return `<div class="details"><div class="detail"><span>Date</span><b>${h(row.date)}</b></div><div class="detail"><span>Daily</span><b>${f(row.daily)} kWh</b></div><div class="detail"><span>Status</span><b>${h(row.status)}</b></div><div class="detail"><span>Total</span><b>${f(row.total)} MWh</b></div></div>`;if(type==='weekly')return `<div class="details"><div class="detail"><span>Week</span><b>${h(row.week)}</b></div><div class="detail"><span>Daily Sum</span><b>${f(row.dailySum)} kWh</b></div><div class="detail"><span>Weekly</span><b>${f(row.weekly)} kWh</b></div><div class="detail"><span>Days Stored</span><b>${h(row.days)}</b></div></div>`;return `<div class="details"><div class="detail"><span>Year</span><b>${h(row.year)}</b></div><div class="detail"><span>Latest Date</span><b>${h(row.lastDate)}</b></div><div class="detail"><span>Year Generation</span><b>${f(row.yearKwh)} kWh</b></div><div class="detail"><span>Total</span><b>${f(row.totalMwh)} MWh</b></div></div>`}
function renderHistory(data){return `<div class="history-block">
<h3>Select Previous Data</h3>
<div class="details">
  <div><label>Daily Date</label><select id="dailyPick">${selectorOptions(data.daily,'date')}</select></div>
  <div><label>Week</label><select id="weekPick">${selectorOptions(data.weekly,'week')}</select></div>
  <div><label>Year</label><select id="yearPick">${selectorOptions(data.yearly,'year')}</select></div>
</div>
<div id="pickedDaily" class="history-block"></div>
<div id="pickedWeekly" class="history-block"></div>
<div id="pickedYearly" class="history-block"></div>
</div>`+[
historyTable('All Daily Date-wise',data.daily,[['Date','date'],['Daily kWh','daily',1],['Status','status'],['Total MWh','total',1]]),
historyTable('All Weekly Week-wise',data.weekly,[['Week','week'],['Daily Sum','dailySum',1],['Weekly kWh','weekly',1],['Days','days']]),
historyTable('All Yearly Year-wise',data.yearly,[['Year','year'],['Latest Date','lastDate'],['Year kWh','yearKwh',1],['Total MWh','totalMwh',1]])
].join('')}
function wireHistorySelectors(data){const daily=document.querySelector('#dailyPick'), week=document.querySelector('#weekPick'), year=document.querySelector('#yearPick');const renderPicked=()=>{const d=data.daily?.[Number(daily?.value||0)], w=data.weekly?.[Number(week?.value||0)], y=data.yearly?.[Number(year?.value||0)];document.querySelector('#pickedDaily').innerHTML='<h3>Selected Daily</h3>'+selectedSummary('daily',d);document.querySelector('#pickedWeekly').innerHTML='<h3>Selected Week</h3>'+selectedSummary('weekly',w);document.querySelector('#pickedYearly').innerHTML='<h3>Selected Year</h3>'+selectedSummary('yearly',y)};[daily,week,year].forEach(el=>{if(el)el.onchange=renderPicked});renderPicked()}
async function loadHistory(active){const key=active?.plantKey||'';activeHistoryKey=key;const box=document.querySelector('#historyBox');if(!box||!key)return;box.innerHTML='<div class="empty-history">Loading previous data...</div>';try{const data=await api('/api/history?plant_key='+encodeURIComponent(key));if(activeHistoryKey===key){box.innerHTML=renderHistory(data);wireHistorySelectors(data)}}catch(error){if(activeHistoryKey===key)box.innerHTML='<div class="empty-history">History failed: '+h(error.message)+'</div>';}}
function renderDetail(active){if(!active){detailEl.innerHTML='No plant';return}detailEl.innerHTML=`<div class="plant-title">${h(active.site)}</div><p>${h(active.brand)} · <span class="status ${cls(active.status)}">${h(active.status)}</span></p>${staleNote(active)?`<p class="stale">${h(staleNote(active))}</p>`:''}<div class="details"><div class="detail"><span>Data Date</span><b>${h(active.dataDate||'Unknown')}</b></div><div class="detail"><span>Capacity</span><b>${f(active.capacity)} kW</b></div><div class="detail"><span>Daily</span><b>${f(active.daily)} kWh</b></div><div class="detail"><span>Weekly</span><b>${f(active.weekly)} kWh</b></div><div class="detail"><span>2026/kW</span><b>${f(active.yield2026)}</b></div><div class="detail"><span>Total</span><b>${f(active.total)} MWh</b></div></div><div id="historyBox" class="history-block"></div>`;loadHistory(active)}
function render(){const rows=filtered(), chosen=selectedRows();const active=plants.find(p=>p.id===activePlantId)||chosen[0]||rows[0]||plants[0];if(active)activePlantId=active.id;cardsEl.innerHTML=[['Visible',rows.length],['Selected',chosen.length],['Daily',f(rows.reduce((a,p)=>a+p.daily,0))+' kWh'],['Weekly',f(rows.reduce((a,p)=>a+p.weekly,0))+' kWh'],['Capacity',f(rows.reduce((a,p)=>a+p.capacity,0))+' kW'],['Fresh',rows.filter(fresh).length+'/'+rows.length]].map(x=>`<div class="card"><span>${x[0]}</span><strong>${x[1]}</strong></div>`).join('');
rowsEl.innerHTML=rows.map(p=>`<tr data-id="${p.id}" style="cursor:pointer"><td><input type="checkbox" data-id="${p.id}" ${selected.has(p.id)?'checked':''}></td><td>${h(p.brand)}</td><td><b>${h(p.site)}</b></td><td class="status ${cls(p.status)}">${h(p.status)}</td><td title="${h(staleNote(p))}">${h(p.dataDate||'')} <span class="pill ${fresh(p)?'fresh':'stale'}">${fresh(p)?'TODAY':'STALE'}</span></td><td>${f(p.daily)}</td><td>${f(p.weekly)}</td><td>${f(p.yield2026)}</td></tr>`).join('');
rowsEl.querySelectorAll('tr[data-id]').forEach(tr=>tr.onclick=()=>{activePlantId=tr.dataset.id;render()});
rowsEl.querySelectorAll('input[type=checkbox][data-id]').forEach(cb=>{cb.onclick=e=>e.stopPropagation();cb.onchange=()=>{cb.checked?selected.add(cb.dataset.id):selected.delete(cb.dataset.id);render()}});
renderDetail(active);
}
function refreshText(r){const lines=(r.steps||[]).map(s=>`${s.ok?'OK':'SKIP'} - ${s.label}: ${s.message||''}`);if(r.running)lines.push('RUNNING - Refresh still in progress...');if(r.finished)lines.push('DONE - Finished '+r.finished);return lines.join('\\n')||'Ready.'}
async function loadReports(){try{const r=await api('/api/reports');reportListEl.innerHTML=(r.reports||[]).length?(r.reports||[]).map(x=>`<a class="report-item" href="${x.url}" target="_blank">${h(x.name)}<span>${h(x.modified)} · ${h(x.size_kb)} KB</span></a>`).join(''):'No reports generated yet.';}catch(error){reportListEl.textContent='Could not load reports: '+error.message;}}
async function load(){const p=await api('/api/plants');plants=p.plants;selected=new Set(plants.map(p=>p.id));renderFilters();render();const s=await api('/api/status');statusData=s;dateLineEl.textContent='Today '+todayText();versionLineEl.textContent='Build: '+(s.app_version||'old');mobileLineEl.textContent='iPhone: '+s.mobile_url;autoDayEl.value=s.config.auto_report_day;autoTimeEl.value=s.config.auto_report_time;logEl.textContent=refreshText(s.last_refresh||{});loadReports();}
async function pollRefresh(){for(let i=0;i<90;i++){const s=await api('/api/status');logEl.textContent=refreshText(s.last_refresh||{});await load();if(!s.last_refresh?.running)return;await new Promise(r=>setTimeout(r,3000));}}
refreshBtn.onclick=async()=>{logEl.textContent='Starting background refresh...';const r=await api('/api/refresh',{method:'POST'});logEl.textContent=refreshText(r);pollRefresh().catch(error=>{logEl.textContent='Refresh status failed: '+error;});}
reportBtn.onclick=async()=>{const ids=[...selected];reportResultEl.textContent='Generating report...';const r=await api('/api/report',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({plant_ids:ids})});reportResultEl.innerHTML=r.ok?`Saved ${r.count} plant report.<br><a class="download-btn" href="${r.download_url}" target="_blank">Download PDF</a>`:'Failed: '+h(r.message);if(r.ok)loadReports();}
selectAllBtn.onclick=()=>{const visible=filtered();const all=visible.every(p=>selected.has(p.id));visible.forEach(p=>all?selected.delete(p.id):selected.add(p.id));render()}
saveScheduleBtn.onclick=async()=>{const r=await api('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({auto_report_day:autoDayEl.value,auto_report_time:autoTimeEl.value})});logEl.textContent='Saved schedule: '+r.config.auto_report_day+' '+r.config.auto_report_time}
searchInput.oninput=render;brandFilter.onchange=render;statusFilter.onchange=render;load().catch(error=>{logEl.textContent='App load failed: '+error;});
</script>
</body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    global APP
    APP = SolarLiveApp(args.host, args.port)
    APP.record_history_snapshot()
    if APP.config.get("auto_refresh_on_open"):
        threading.Thread(target=APP.refresh, daemon=True).start()
    threading.Thread(target=APP.maybe_auto_run, daemon=True).start()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"Solar Live App running: {url}")
    print(f"iPhone on same Wi-Fi: http://{local_ip()}:{args.port}")
    cloud_mode = any(os.environ.get(key) for key in ("PORT", "RENDER", "RAILWAY_ENVIRONMENT", "FLY_APP_NAME"))
    if not args.no_browser and not cloud_mode:
        webbrowser.open(url)
    server.serve_forever()


if __name__ == "__main__":
    main()
