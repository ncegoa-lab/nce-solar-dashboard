#!/usr/bin/env python3
"""Local live solar dashboard app.

Run this script to start a browser-based app on the Mac. It serves live plant
data from the existing project files, can refresh cloud data, and can generate
one PDF report for all plants, a single plant, or any selected plants.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
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
from zoneinfo import ZoneInfo

import pandas as pd

from solar_performance_report_app import DEFAULT_LOGO, generate_compact_pdf, load_data

try:
    import psycopg
    from psycopg.rows import dict_row

    HAS_PSYCOPG = True
except Exception:  # pragma: no cover - local fallback when PostgreSQL driver is unavailable.
    psycopg = None
    dict_row = None
    HAS_PSYCOPG = False


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
HOURLY_HISTORY_FILE = PROJECT_DIR / "solar_generation_hourly_history.json"
BUNDLED_PYTHON = Path("/Users/sushil/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3")
VENV_PYTHON = PROJECT_DIR / ".venv/bin/python"
DEFAULT_CONFIG = {
    "output_dir": str(DEFAULT_OUTPUT_DIR),
    "auto_report_day": "Sunday",
    "auto_report_time": "20:00",
    "auto_refresh_on_open": True,
}
APP_VERSION = "2026-07-13-postgres-multi-user-v53"
IST = ZoneInfo("Asia/Kolkata")
VALID_ROLES = {"admin", "manager", "customer", "viewer"}
PLANT_COLUMNS = [
    "App ID",
    "Brand",
    "Site Name",
    "Plant Capacity (kW)",
    "Current Status",
    "Daily Generation (kWh)",
    "Weekly Generation (kWh)",
    "Year Generation (kWh)",
    "Current Power (kW)",
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


def ist_now() -> dt.datetime:
    return dt.datetime.now(IST)


def ist_today() -> dt.date:
    return ist_now().date()


def timestamp_to_ist_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) == 10:
        try:
            return dt.date.fromisoformat(text).isoformat()
        except ValueError:
            return text
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text[:10] if len(text) >= 10 else ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    return parsed.astimezone(IST).date().isoformat()


def parse_iso_date(value: Any) -> dt.date | None:
    text = str(value or "").strip()
    if len(text) >= 10:
        try:
            return dt.date.fromisoformat(text[:10])
        except ValueError:
            return None
    return None


def html_escape(value: Any) -> str:
    return (
        str(value if value is not None else "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def read_json_file(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def solis_capture_status() -> dict[str, Any]:
    path = PROJECT_DIR / "solis_network_capture.json"
    if not path.exists():
        return {"exists": False, "fresh": False, "message": "No Solis browser capture found."}
    payload = read_json_file(path)
    captured_date = parse_iso_date(payload.get("captured_at"))
    if captured_date == ist_today():
        return {"exists": True, "fresh": True, "message": f"Using Solis browser capture from {captured_date.isoformat()}."}
    captured_text = captured_date.isoformat() if captured_date else str(payload.get("captured_at") or "unknown date")
    return {
        "exists": True,
        "fresh": False,
        "message": f"Solis browser capture is stale ({captured_text}). Refresh Solis on the Mac and upload again.",
    }


def solax_capture_status() -> dict[str, Any]:
    path = PROJECT_DIR / "solax_network_capture.json"
    if not path.exists():
        return {"exists": False, "fresh": False, "message": "No SolaX browser capture found."}
    payload = read_json_file(path)
    captured_date = parse_iso_date(payload.get("captured_at"))
    if captured_date == ist_today():
        return {"exists": True, "fresh": True, "message": f"Using SolaX browser capture from {captured_date.isoformat()}."}
    captured_text = captured_date.isoformat() if captured_date else str(payload.get("captured_at") or "unknown date")
    return {
        "exists": True,
        "fresh": False,
        "message": f"SolaX browser capture is stale ({captured_text}). Refresh SolaX on the Mac and upload again.",
    }


def week_start(value: dt.date) -> dt.date:
    return value - dt.timedelta(days=value.weekday())


def normalize_role(value: Any) -> str:
    role = str(value or "viewer").strip().lower()
    return role if role in VALID_ROLES else "viewer"


def safe_username(value: Any) -> str:
    text = str(value or "user").strip()
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in text)
    return safe or "user"


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


def database_url() -> str:
    return os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or ""


def postgres_enabled() -> bool:
    return bool(database_url() and HAS_PSYCOPG)


def db_connect():
    if not postgres_enabled():
        raise RuntimeError("PostgreSQL is not configured. Set DATABASE_URL on Render.")
    return psycopg.connect(database_url(), row_factory=dict_row)


def migrate_database() -> None:
    if not postgres_enabled():
        return
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS solar_app_users (
                    id BIGSERIAL PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('admin', 'manager', 'customer', 'viewer')),
                    disabled BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS solar_app_user_plants (
                    user_id BIGINT NOT NULL REFERENCES solar_app_users(id) ON DELETE CASCADE,
                    plant_key TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (user_id, plant_key)
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_solar_app_user_plants_key ON solar_app_user_plants (plant_key)")
            cur.execute("SELECT COUNT(*) AS count FROM solar_app_users")
            if int(cur.fetchone()["count"] or 0) == 0:
                seed_users = load_file_users(include_env_admin=True)
                for user in seed_users.values():
                    seeded_hash = str(user["password_hash"] or "")
                    if seeded_hash and not seeded_hash.startswith("pbkdf2_sha256$"):
                        seeded_hash = hash_password(seeded_hash)
                    cur.execute(
                        """
                        INSERT INTO solar_app_users (username, password_hash, role, disabled)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (username) DO NOTHING
                        RETURNING id
                        """,
                        (
                            user["username"],
                            seeded_hash,
                            normalize_role(user.get("role")),
                            bool(user.get("disabled", False)),
                        ),
                    )
                    inserted = cur.fetchone()
                    if not inserted:
                        continue
                    for plant in user.get("plants") or []:
                        cur.execute(
                            "INSERT INTO solar_app_user_plants (user_id, plant_key) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                            (inserted["id"], str(plant)),
                        )
        conn.commit()


def load_file_users(include_env_admin: bool = True) -> dict[str, dict[str, Any]]:
    payload: dict[str, Any] = {}
    if USERS_FILE.exists():
        try:
            payload = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
    elif os.environ.get("NCE_USERS_JSON"):
        try:
            payload = json.loads(os.environ["NCE_USERS_JSON"])
        except json.JSONDecodeError:
            payload = {}

    users: dict[str, dict[str, Any]] = {}
    for item in payload.get("users", []):
        username = str(item.get("username") or "").strip()
        if not username:
            continue
        role = normalize_role(item.get("role") or "customer")
        users[username] = {
            "username": username,
            "password_hash": item.get("password_hash") or item.get("password") or "",
            "role": role,
            "plants": item.get("plants") or ([] if role != "admin" else ["*"]),
            "disabled": bool(item.get("disabled", False)),
        }

    if include_env_admin and AUTH_PASSWORD and AUTH_USER not in users:
        users[AUTH_USER] = {
            "username": AUTH_USER,
            "password_hash": AUTH_PASSWORD,
            "role": "admin",
            "plants": ["*"],
            "disabled": False,
        }
    return users


def load_db_users() -> dict[str, dict[str, Any]]:
    migrate_database()
    users: dict[str, dict[str, Any]] = {}
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT u.id, u.username, u.password_hash, u.role, u.disabled,
                       COALESCE(array_agg(p.plant_key ORDER BY p.plant_key) FILTER (WHERE p.plant_key IS NOT NULL), '{}') AS plants
                FROM solar_app_users u
                LEFT JOIN solar_app_user_plants p ON p.user_id = u.id
                GROUP BY u.id
                ORDER BY lower(u.username)
                """
            )
            for row in cur.fetchall():
                users[row["username"]] = {
                    "id": row["id"],
                    "username": row["username"],
                    "password_hash": row["password_hash"],
                    "role": normalize_role(row["role"]),
                    "plants": list(row["plants"] or []),
                    "disabled": bool(row["disabled"]),
                }
    return users


def load_users() -> dict[str, dict[str, Any]]:
    if postgres_enabled():
        try:
            return load_db_users()
        except Exception:
            if os.environ.get("RENDER"):
                raise
    return load_file_users()


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "username": user.get("username", ""),
        "role": normalize_role(user.get("role")),
        "plants": list(user.get("plants") or []),
        "disabled": bool(user.get("disabled", False)),
    }


def db_upsert_user(username: str, role: str, plants: list[str], password: str | None = None, disabled: bool = False) -> dict[str, Any]:
    if not postgres_enabled():
        raise RuntimeError("PostgreSQL is not configured.")
    migrate_database()
    username = str(username or "").strip()
    if not username:
        raise ValueError("Username is required.")
    role = normalize_role(role)
    plants = ["*"] if role == "admin" else sorted({str(plant) for plant in plants if str(plant).strip()})
    with db_connect() as conn:
        with conn.cursor() as cur:
            if password:
                cur.execute(
                    """
                    INSERT INTO solar_app_users (username, password_hash, role, disabled, updated_at)
                    VALUES (%s, %s, %s, %s, NOW())
                    ON CONFLICT (username)
                    DO UPDATE SET password_hash = EXCLUDED.password_hash,
                                  role = EXCLUDED.role,
                                  disabled = EXCLUDED.disabled,
                                  updated_at = NOW()
                    RETURNING id, username, role, disabled
                    """,
                    (username, hash_password(password), role, disabled),
                )
            else:
                cur.execute("SELECT id FROM solar_app_users WHERE username = %s", (username,))
                if not cur.fetchone():
                    raise ValueError("Password is required for a new user.")
                cur.execute(
                    """
                    UPDATE solar_app_users
                    SET role = %s,
                        disabled = %s,
                        updated_at = NOW()
                    WHERE username = %s
                    RETURNING id, username, role, disabled
                    """,
                    (role, disabled, username),
                )
            user_row = cur.fetchone()
            cur.execute("DELETE FROM solar_app_user_plants WHERE user_id = %s", (user_row["id"],))
            for plant in plants:
                cur.execute(
                    "INSERT INTO solar_app_user_plants (user_id, plant_key) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (user_row["id"], plant),
                )
        conn.commit()
    return {"ok": True, "user": {**dict(user_row), "plants": plants}}


def db_reset_user_password(username: str, password: str) -> dict[str, Any]:
    if not postgres_enabled():
        raise RuntimeError("PostgreSQL is not configured.")
    username = str(username or "").strip()
    if not username or not password:
        raise ValueError("Username and password are required.")
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE solar_app_users SET password_hash = %s, updated_at = NOW() WHERE username = %s RETURNING username",
                (hash_password(password), username),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("User not found.")
        conn.commit()
    return {"ok": True, "username": username}


def db_set_user_disabled(username: str, disabled: bool) -> dict[str, Any]:
    if not postgres_enabled():
        raise RuntimeError("PostgreSQL is not configured.")
    username = str(username or "").strip()
    if not username:
        raise ValueError("Username is required.")
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE solar_app_users SET disabled = %s, updated_at = NOW() WHERE username = %s RETURNING username, disabled",
                (disabled, username),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("User not found.")
        conn.commit()
    return {"ok": True, **dict(row)}


def user_can_access(user: dict[str, Any] | None, key: str) -> bool:
    if not user:
        return not bool(load_users())
    if user.get("disabled"):
        return False
    if normalize_role(user.get("role")) == "admin":
        return True
    allowed = set(user.get("plants") or [])
    return "*" in allowed or key in allowed


def is_admin(user: dict[str, Any] | None) -> bool:
    return not load_users() or bool(user and not user.get("disabled") and normalize_role(user.get("role")) == "admin")


def can_generate_report(user: dict[str, Any] | None) -> bool:
    if not load_users():
        return True
    return bool(user and not user.get("disabled") and normalize_role(user.get("role")) in {"admin", "manager", "customer"})


def can_refresh_data(user: dict[str, Any] | None) -> bool:
    return bool(user and not user.get("disabled") and normalize_role(user.get("role")) in {"admin", "manager"})


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
        today = ist_today().isoformat()
        rows = []
        for row in df.to_dict(orient="records"):
            key = plant_key(row["Brand"], row["Site Name"])
            if not user_can_access(user, key):
                continue
            timestamp = str(row.get("Timestamp") or "")
            data_date = timestamp_to_ist_date(timestamp)
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
                    "currentPower": float(row.get("Current Power (kW)") or 0),
                    "total": float(row["Total Generation (MWh)"] or 0),
                    "cuf": float(row.get("CUF (%)") or 0),
                    "avgDay": float(row["Average Daily Yield (kWh/kW/day)"] or 0),
                    "source": row.get("Year Generation Source", ""),
                    "timestamp": timestamp,
                    "dataDate": data_date,
                    "fresh": data_date == today,
                }
            )
        return rows

    def all_plant_options(self) -> list[dict[str, Any]]:
        df = self.plant_dataframe()
        options = []
        for row in df.to_dict(orient="records"):
            key = plant_key(row["Brand"], row["Site Name"])
            options.append({"plantKey": key, "brand": row["Brand"], "site": row["Site Name"]})
        return options

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

    def load_hourly_history(self) -> list[dict[str, Any]]:
        if not HOURLY_HISTORY_FILE.exists():
            return []
        try:
            data = json.loads(HOURLY_HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
        return data if isinstance(data, list) else []

    def save_hourly_history(self, rows: list[dict[str, Any]]) -> None:
        temp = HOURLY_HISTORY_FILE.with_suffix(".json.tmp")
        temp.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        temp.replace(HOURLY_HISTORY_FILE)

    def record_hourly_snapshot(self, current: list[dict[str, Any]]) -> None:
        now = ist_now().replace(minute=0, second=0, microsecond=0)
        today = now.date()
        rows = self.load_hourly_history()
        cutoff = today - dt.timedelta(days=45)
        rows = [
            row for row in rows
            if parse_iso_date(row.get("date")) and parse_iso_date(row.get("date")) >= cutoff
        ]
        by_key = {
            f"{row.get('plantKey')}::{row.get('hour')}": row
            for row in rows
            if row.get("plantKey") and row.get("hour")
        }
        for plant in current:
            if plant.get("dataDate") != today.isoformat():
                continue
            hour_key = now.isoformat()
            by_key[f"{plant['plantKey']}::{hour_key}"] = {
                "date": today.isoformat(),
                "hour": hour_key,
                "hourLabel": now.strftime("%H:00"),
                "brand": plant.get("brand", ""),
                "site": plant.get("site", ""),
                "plantKey": plant.get("plantKey", ""),
                "status": plant.get("status", ""),
                "daily": plant.get("daily", 0),
                "timestamp": plant.get("timestamp", ""),
                "recordedAt": ist_now().replace(microsecond=0).isoformat(),
            }
        self.save_hourly_history(sorted(by_key.values(), key=lambda row: (str(row.get("hour", "")), str(row.get("plantKey", "")))))

    def record_history_snapshot(self) -> dict[str, Any]:
        current = self.plant_payload({"role": "admin", "plants": ["*"]})
        if not current:
            return {"label": "History snapshot", "ok": False, "message": "No plant data available to record."}

        existing = self.load_history()
        by_key = {
            f"{row.get('plantKey')}::{row.get('date')}": row
            for row in existing
            if row.get("plantKey") and row.get("date")
        }
        today = ist_today().isoformat()
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
                "currentPower": plant.get("currentPower", 0),
                "total": plant.get("total", 0),
                "cuf": plant.get("cuf", 0),
                "timestamp": plant.get("timestamp", ""),
                "recordedAt": ist_now().replace(microsecond=0).isoformat(),
            }
            count += 1

        rows = sorted(by_key.values(), key=lambda row: (str(row.get("plantKey", "")), str(row.get("date", ""))))
        self.save_history(rows)
        self.record_hourly_snapshot(current)
        return {"label": "History snapshot", "ok": True, "message": f"Saved history for {count} plants."}

    def history_payload(self, plant_key_value: str, user: dict[str, Any] | None = None) -> dict[str, Any]:
        if not user_can_access(user, plant_key_value):
            return {"daily": [], "weekly": [], "yearly": []}

        rows = [
            row for row in self.load_history()
            if row.get("plantKey") == plant_key_value and parse_iso_date(row.get("date"))
        ]
        rows.sort(key=lambda row: str(row.get("date", "")), reverse=True)
        today = ist_today().isoformat()
        if not any(row.get("date") == today for row in rows):
            current = next(
                (plant for plant in self.plant_payload({"role": "admin", "plants": ["*"]}) if plant.get("plantKey") == plant_key_value),
                {},
            )
            has_current_today = current.get("dataDate") == today
            rows.insert(
                0,
                {
                    "date": today,
                    "brand": current.get("brand", ""),
                    "site": current.get("site", ""),
                    "plantKey": plant_key_value,
                    "status": current.get("status", "No data") if has_current_today else "No data",
                    "capacity": current.get("capacity", 0),
                    "daily": current.get("daily", 0) if has_current_today else 0,
                    "weekly": current.get("weekly", 0) if has_current_today else 0,
                    "year": current.get("year", 0),
                    "currentPower": current.get("currentPower", 0) if has_current_today else 0,
                    "total": current.get("total", 0),
                    "cuf": current.get("cuf", 0) if has_current_today else 0,
                    "timestamp": current.get("timestamp", "") if has_current_today else "",
                    "recordedAt": ist_now().replace(microsecond=0).isoformat(),
                },
            )

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
        started = ist_now()
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
                "finished": ist_now().isoformat(timespec="seconds"),
                "message": (result.stdout or result.stderr or "").strip()[-1200:],
            }
        except Exception as exc:
            return {
                "label": label,
                "ok": False,
                "started": started.isoformat(timespec="seconds"),
                "finished": ist_now().isoformat(timespec="seconds"),
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
        payload.setdefault("uploaded_at", ist_now().replace(microsecond=0).isoformat())
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temp.replace(target)

        started = ist_now().isoformat(timespec="seconds")
        self.last_refresh = {
            "started": started,
            "finished": None,
            "running": True,
            "steps": [{"label": f"{brand.title()} upload", "ok": True, "message": f"Saved {filename} with {len(payload['systems'])} systems."}],
        }
        self.append_refresh_step(self.record_history_snapshot())
        for step in self.rebuild_outputs():
            self.append_refresh_step(step)
        self.last_refresh["finished"] = ist_now().isoformat(timespec="seconds")
        self.last_refresh["running"] = False
        return {"ok": True, "file": filename, "systems": len(payload["systems"]), "last_refresh": self.last_refresh}

    def append_refresh_step(self, step: dict[str, Any]) -> None:
        self.last_refresh.setdefault("steps", []).append(step)

    def file_status_step(self, label: str, path: Path, ok_message: str, missing_message: str) -> dict[str, Any]:
        now = ist_now().isoformat(timespec="seconds")
        return {
            "label": label,
            "ok": path.exists(),
            "started": now,
            "finished": now,
            "message": ok_message if path.exists() else missing_message,
        }

    def refresh(self) -> dict[str, Any]:
        with self.refresh_lock:
            started = ist_now()
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

            if env.get("SOLIS_KEY_ID") and env.get("SOLIS_KEY_SECRET"):
                step = self.run_step("Solis API refresh", [refresh_py, "./solis_api_export_generation.py"], env)
                self.append_refresh_step(step)
                if not step.get("ok"):
                    solis_capture = solis_capture_status()
                    if solis_capture["exists"]:
                        self.append_refresh_step(self.run_step("Solis fallback import from latest capture", [refresh_py, "./solis_capture_to_generation.py"], env))
            else:
                solis_capture = solis_capture_status()
                if solis_capture["exists"]:
                    self.append_refresh_step(self.run_step("Solis import from latest capture", [refresh_py, "./solis_capture_to_generation.py"], env))
                else:
                    now = ist_now().isoformat(timespec="seconds")
                    has_saved_solis = (PROJECT_DIR / "solis_generation.json").exists()
                    self.append_refresh_step(
                        {
                            "label": "Solis refresh skipped",
                            "ok": False,
                            "started": now,
                            "finished": now,
                            "message": (
                                f"{solis_capture['message']} "
                                + ("Existing saved Solis file is still being shown." if has_saved_solis else "No Solis data file is available.")
                            ),
                        }
                    )

            if env.get("SOLAX_TOKEN_ID"):
                step = self.run_step("SolaX API refresh", [refresh_py, "./solax_api_export_generation.py"], env)
                self.append_refresh_step(step)
                if not step.get("ok"):
                    solax_capture = solax_capture_status()
                    if solax_capture["exists"]:
                        self.append_refresh_step(self.run_step("SolaX fallback import from latest capture", [refresh_py, "./solax_capture_to_generation.py"], env))
            else:
                solax_capture = solax_capture_status()
                if solax_capture["exists"]:
                    self.append_refresh_step(self.run_step("SolaX import from latest capture", [refresh_py, "./solax_capture_to_generation.py"], env))
                else:
                    now = ist_now().isoformat(timespec="seconds")
                    has_saved_solax = (PROJECT_DIR / "solax_generation.json").exists()
                    self.append_refresh_step(
                        {
                            "label": "SolaX refresh skipped",
                            "ok": False,
                            "started": now,
                            "finished": now,
                            "message": (
                                f"{solax_capture['message']} "
                                + ("Existing saved SolaX file is still being shown." if has_saved_solax else "No SolaX data file is available.")
                            ),
                        }
                    )

            self.append_refresh_step(self.record_history_snapshot())
            for step in self.rebuild_outputs(env):
                self.append_refresh_step(step)

            self.last_refresh["finished"] = ist_now().isoformat(timespec="seconds")
            self.last_refresh["running"] = False
            return self.last_refresh

    def refresh_async(self) -> dict[str, Any]:
        if self.refresh_lock.locked():
            return {**self.last_refresh, "accepted": False, "message": "Refresh already running"}
        self.last_refresh = {
            "started": ist_now().isoformat(timespec="seconds"),
            "finished": None,
            "running": True,
            "steps": [{"label": "Refresh queued", "ok": True, "message": "Starting background refresh"}],
        }
        threading.Thread(target=self.refresh, daemon=True).start()
        return {**self.last_refresh, "accepted": True}

    def stale_online_count(self) -> int:
        today = ist_today().isoformat()
        plants = self.plant_payload({"role": "admin", "plants": ["*"]})
        return sum(
            1
            for plant in plants
            if plant.get("dataDate") != today and str(plant.get("status", "")).lower() != "offline"
        )

    def brand_debug(self, brand: str, user: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return [
            {
                "site": plant.get("site"),
                "daily": plant.get("daily"),
                "weekly": plant.get("weekly"),
                "dataDate": plant.get("dataDate"),
                "timestamp": plant.get("timestamp"),
            }
            for plant in self.plant_payload(user or {"role": "admin", "plants": ["*"]})
            if str(plant.get("brand", "")).lower() == brand.lower()
        ]

    def monthly_generation_payload(
        self,
        plant_keys: list[str],
        user: dict[str, Any] | None = None,
        month: int | None = None,
        year: int | None = None,
    ) -> dict[str, Any]:
        today = ist_today()
        target_year = year if year and 2000 <= year <= 2100 else today.year
        target_month = month if month and 1 <= month <= 12 else today.month
        month_start = dt.date(target_year, target_month, 1)
        if target_month == 12:
            month_end = dt.date(target_year + 1, 1, 1) - dt.timedelta(days=1)
        else:
            month_end = dt.date(target_year, target_month + 1, 1) - dt.timedelta(days=1)
        if target_year == today.year and target_month == today.month:
            month_end = min(month_end, today)
        allowed_keys = set(plant_keys or [])
        current_rows = self.plant_payload(user or {"role": "admin", "plants": ["*"]})
        if allowed_keys:
            current_rows = [plant for plant in current_rows if plant.get("plantKey") in allowed_keys]
        visible_keys = {str(plant.get("plantKey") or "") for plant in current_rows}
        plant_meta = {
            str(plant.get("plantKey") or ""): {
                "plantKey": str(plant.get("plantKey") or ""),
                "site": plant.get("site", ""),
                "brand": plant.get("brand", ""),
                "capacity": plant.get("capacity", 0),
            }
            for plant in current_rows
        }

        by_day_plant: dict[tuple[str, str], float] = {}
        history_by_key: dict[str, list[dict[str, Any]]] = {}
        for row in self.load_history():
            key = row.get("plantKey")
            if key not in visible_keys or not user_can_access(user, key):
                continue
            row_date = parse_iso_date(row.get("date"))
            if not row_date:
                continue
            history_by_key.setdefault(str(key), []).append({**row, "_date": row_date})

        for key, rows_for_key in history_by_key.items():
            previous_year_total: float | None = None
            for row in sorted(rows_for_key, key=lambda item: item["_date"]):
                row_date = row["_date"]
                current_year_total = float(row.get("year") or 0)
                daily = float(row.get("daily") or 0)
                if daily <= 0 and previous_year_total is not None and current_year_total > previous_year_total:
                    daily = current_year_total - previous_year_total
                previous_year_total = current_year_total if current_year_total > 0 else previous_year_total
                if not (month_start <= row_date <= month_end):
                    continue
                by_day_plant[(row_date.isoformat(), key)] = daily

        if month_start <= today <= month_end:
            for plant in current_rows:
                key = str(plant.get("plantKey") or "")
                if plant.get("dataDate") == today.isoformat():
                    by_day_plant[(today.isoformat(), key)] = float(plant.get("daily") or 0)

        days = []
        cursor = month_start
        total = 0.0
        while cursor <= month_end:
            date_key = cursor.isoformat()
            values = []
            day_total = 0.0
            for key, meta in plant_meta.items():
                generation = round(by_day_plant.get((date_key, key), 0.0), 3)
                day_total += generation
                values.append({**meta, "generation": generation})
            total += day_total
            days.append({"date": date_key, "day": cursor.day, "generation": round(day_total, 3), "values": values})
            cursor += dt.timedelta(days=1)
        return {
            "month": month_start.strftime("%B %Y"),
            "monthNumber": target_month,
            "year": target_year,
            "plants": list(plant_meta.values()),
            "days": days,
            "total": round(total, 3),
        }

    def today_hourly_payload(
        self,
        plant_keys: list[str],
        user: dict[str, Any] | None = None,
        target_date: dt.date | None = None,
    ) -> dict[str, Any]:
        today = ist_today()
        graph_date = target_date or today
        allowed_keys = set(plant_keys or [])
        current_rows = self.plant_payload(user or {"role": "admin", "plants": ["*"]})
        if allowed_keys:
            current_rows = [plant for plant in current_rows if plant.get("plantKey") in allowed_keys]
        visible_keys = {plant.get("plantKey") for plant in current_rows}
        totals: dict[str, float] = {}
        for row in self.load_hourly_history():
            key = row.get("plantKey")
            if key not in visible_keys or not user_can_access(user, key):
                continue
            row_date = parse_iso_date(row.get("date"))
            if row_date != graph_date:
                continue
            hour = str(row.get("hourLabel") or str(row.get("hour", ""))[11:16] or "")
            if hour:
                totals[hour] = totals.get(hour, 0.0) + float(row.get("daily") or 0)

        now_hour = ist_now().replace(minute=0, second=0, microsecond=0)
        live_total = sum(
            float(plant.get("daily") or 0)
            for plant in current_rows
            if plant.get("dataDate") == today.isoformat()
        )
        if graph_date == today and live_total:
            totals[now_hour.strftime("%H:00")] = live_total

        hours = []
        for hour in range(24):
            label = f"{hour:02d}:00"
            if label in totals or graph_date < today or hour <= ist_now().hour:
                hours.append({"hour": label, "generation": round(totals.get(label, 0.0), 3)})
        previous = 0.0
        total_capacity = sum(float(plant.get("capacity") or 0) for plant in current_rows)
        live_power = sum(float(plant.get("currentPower") or 0) for plant in current_rows)
        for index, row in enumerate(hours):
            generation = float(row.get("generation") or 0)
            estimated_power = max(0.0, generation - previous)
            if index == len(hours) - 1 and live_power > 0:
                estimated_power = live_power
            if total_capacity > 0:
                estimated_power = min(estimated_power, total_capacity * 1.2)
            row["power"] = round(estimated_power, 3)
            previous = generation
        return {
            "date": graph_date.isoformat(),
            "hours": hours,
            "total": round(max((row["generation"] for row in hours), default=0.0), 3),
            "capacity": round(total_capacity, 3),
        }

    def chart_export_rows(
        self,
        chart_type: str,
        plant_keys: list[str],
        user: dict[str, Any] | None = None,
        month: int | None = None,
        year: int | None = None,
        target_date: dt.date | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        if chart_type == "perkw":
            allowed_keys = set(plant_keys or [])
            rows = self.plant_payload(user or {"role": "admin", "plants": ["*"]})
            if allowed_keys:
                rows = [plant for plant in rows if plant.get("plantKey") in allowed_keys]
            return "Today's Per-kW Generation", [
                {
                    "Plant": plant.get("site", ""),
                    "Capacity (kW)": round(float(plant.get("capacity") or 0), 3),
                    "Today's Generation (kWh)": round(float(plant.get("daily") or 0), 3),
                    "Per-kW Generation (kWh/kW)": round(
                        float(plant.get("daily") or 0) / float(plant.get("capacity") or 0)
                        if float(plant.get("capacity") or 0) > 0
                        else 0.0,
                        3,
                    ),
                }
                for plant in rows
            ]
        if chart_type == "monthly":
            payload = self.monthly_generation_payload(plant_keys, user, month=month, year=year)
            return f"Monthly Generation - {payload['month']}", [
                {"Date": item["date"], "Generation (kWh)": item["generation"]}
                for item in payload.get("days", [])
            ]
        payload = self.today_hourly_payload(plant_keys, user, target_date=target_date)
        previous = 0.0
        rows = []
        for item in payload.get("hours", []):
            generation = float(item.get("generation") or 0)
            increment = max(0.0, generation - previous)
            rows.append(
                {
                    "Hour": item.get("hour"),
                    "Power (kW)": round(float(item.get("power") or 0), 3),
                    "Cumulative Generation (kWh)": round(generation, 3),
                    "Hourly Generation (kWh)": round(increment, 3),
                }
            )
            previous = generation
        return f"Production - Selected Plant - {payload['date']}", rows

    def chart_csv_bytes(
        self,
        chart_type: str,
        plant_keys: list[str],
        user: dict[str, Any] | None = None,
        month: int | None = None,
        year: int | None = None,
        target_date: dt.date | None = None,
    ) -> bytes:
        _title, rows = self.chart_export_rows(chart_type, plant_keys, user, month=month, year=year, target_date=target_date)
        output = io.StringIO()
        fieldnames = list(rows[0].keys()) if rows else ["Period", "Generation (kWh)"]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        return output.getvalue().encode("utf-8")

    def chart_pdf_bytes(
        self,
        chart_type: str,
        plant_keys: list[str],
        user: dict[str, Any] | None = None,
        month: int | None = None,
        year: int | None = None,
        target_date: dt.date | None = None,
    ) -> bytes:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

        _title, rows = self.chart_export_rows(chart_type, plant_keys, user, month=month, year=year, target_date=target_date)
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=28, rightMargin=28, topMargin=28, bottomMargin=28)
        fieldnames = list(rows[0].keys()) if rows else ["Period", "Generation (kWh)"]
        table_data = [fieldnames] + [[row.get(field, "") for field in fieldnames] for row in rows]
        table = Table(table_data, repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#174f9c")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d7e0ec")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f7fb")]),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ALIGN", (1, 1), (1, -1), "RIGHT"),
        ]))
        doc.build([table])
        return buffer.getvalue()

    def refresh_on_open(self) -> None:
        if not self.config.get("auto_refresh_on_open"):
            return
        if self.refresh_lock.locked():
            return
        self.refresh_async()

    def refresh_on_open_result(self) -> dict[str, Any]:
        if not self.config.get("auto_refresh_on_open"):
            return {**self.last_refresh, "accepted": False, "message": "Auto refresh on open is disabled"}
        return self.refresh_async()

    def generate_selected_report(self, plant_ids: list[str], user: dict[str, Any] | None = None, all_plants: bool = False) -> dict[str, Any]:
        if not can_generate_report(user):
            return {"ok": False, "message": "Report generation is not allowed for this user role."}
        df = self.plant_dataframe()
        df["Plant Key"] = df.apply(lambda row: plant_key(row["Brand"], row["Site Name"]), axis=1)
        if user and normalize_role(user.get("role")) != "admin":
            df = df[df["Plant Key"].apply(lambda key: user_can_access(user, key))]
        if all_plants:
            selected = df.drop(columns=["App ID"])
        elif plant_ids:
            selected = df[df["App ID"].isin(plant_ids)].drop(columns=["App ID"])
        else:
            return {"ok": False, "message": "No plants selected"}
        if "Plant Key" in selected:
            selected = selected.drop(columns=["Plant Key"])
        if selected.empty:
            return {"ok": False, "message": "No plants selected"}
        if user and normalize_role(user.get("role")) != "admin":
            report_dir = self.output_dir / "User Reports" / safe_username(user.get("username"))
        else:
            report_dir = self.output_dir / "Selected Plant Reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        stamp = ist_now().strftime("%Y%m%d_%H%M")
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
            "viewer_url": f"/view-report?file={urllib.parse.quote(path.relative_to(self.output_dir).as_posix(), safe='')}",
            "count": int(len(selected)),
        }

    def user_can_access_report_path(self, user: dict[str, Any] | None, path: Path) -> bool:
        if is_admin(user):
            return True
        if not user:
            return False
        try:
            path.relative_to((self.output_dir / "User Reports" / safe_username(user.get("username"))).resolve())
            return True
        except ValueError:
            return False

    def latest_reports(self, user: dict[str, Any] | None = None, limit: int = 3) -> list[dict[str, Any]]:
        root = self.output_dir
        if not root.exists():
            return []
        reports = []
        for path in root.rglob("*.pdf"):
            if not path.is_file():
                continue
            if not self.user_can_access_report_path(user, path.resolve()):
                continue
            try:
                relative = path.relative_to(root).as_posix()
            except ValueError:
                continue
            reports.append(
                {
                    "name": path.name,
                    "url": f"/view-report?file={urllib.parse.quote(relative, safe='')}",
                    "download_url": f"/reports/{urllib.parse.quote(relative)}",
                    "modified": dt.datetime.fromtimestamp(path.stat().st_mtime, IST).isoformat(timespec="seconds"),
                    "size_kb": round(path.stat().st_size / 1024, 1),
                }
            )
        reports.sort(key=lambda row: row["modified"], reverse=True)
        return reports[:limit]

    def maybe_auto_run(self) -> None:
        while True:
            try:
                now = ist_now()
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
        if username and username in users and not users[username].get("disabled"):
            return users[username]
        header = self.headers.get("Authorization", "")
        if header.startswith("Basic "):
            try:
                userpass = base64.b64decode(header.split(" ", 1)[1]).decode("utf-8")
                username, _, password = userpass.partition(":")
                user = users.get(username)
                if user and not user.get("disabled") and verify_password(password, user.get("password_hash", "")):
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

    def send_reset_help_page(self) -> None:
        body = RESET_HELP_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_admin_users_page(self) -> None:
        body = ADMIN_USERS_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_report_viewer(self, relative: str, user: dict[str, Any] | None) -> None:
        assert APP is not None
        path = (APP.output_dir / relative).resolve()
        try:
            path.relative_to(APP.output_dir.resolve())
        except ValueError:
            self.send_json({"error": "Invalid report path"}, 400)
            return
        if not path.exists() or not path.is_file():
            self.send_json({"error": "Report not found"}, 404)
            return
        if not APP.user_can_access_report_path(user, path):
            self.send_json({"error": "You do not have access to this report."}, 403)
            return
        download_url = f"/reports/{urllib.parse.quote(relative)}"
        body = (
            REPORT_VIEWER_HTML
            .replace("__TITLE__", path.name)
            .replace("__DOWNLOAD_URL__", download_url)
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(self, body: bytes, content_type: str, filename: str | None = None) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
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
            elif parsed.path == "/api/version":
                self.send_json({"app_version": APP_VERSION})
            elif parsed.path == "/login":
                self.send_login_page()
            elif parsed.path == "/reset-password":
                self.send_reset_help_page()
            elif parsed.path == "/logout":
                self.send_response(302)
                self.send_header("Set-Cookie", f"{SESSION_COOKIE}=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax")
                self.send_header("Location", "/login")
                self.end_headers()
            elif parsed.path == "/":
                user = self.require_auth(html=True)
                if load_users() and not user:
                    return
                APP.refresh_on_open()
                bootstrap = {
                    "plants": APP.plant_payload(user),
                    "status": {
                        "auth_enabled": bool(load_users()),
                        "user": {
                            "username": (user or {}).get("username", "Local"),
                            "role": normalize_role((user or {}).get("role", "admin")),
                            "is_admin": is_admin(user),
                        },
                        "config": APP.config,
                        "app_version": APP_VERSION,
                        "last_refresh": APP.last_refresh,
                        "local_url": f"http://127.0.0.1:{APP.port}",
                        "mobile_url": f"http://{local_ip()}:{APP.port}",
                    },
                    "today": ist_today().isoformat(),
                }
                bootstrap_json = json.dumps(bootstrap).replace("</", "<\\/")
                body = (
                    LIVE_HTML.replace("__USER__", (user or {}).get("username", "Local"))
                    .replace("__BOOTSTRAP_JSON__", bootstrap_json)
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif parsed.path == "/admin/users" or parsed.path.startswith("/api/") or parsed.path.startswith("/reports/") or parsed.path in {"/view-report", "/chart-detail", "/chart-csv", "/chart-pdf"}:
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
            self.send_json({"plants": APP.plant_payload(user), "today": ist_today().isoformat()})
        elif parsed.path == "/api/history":
            query = urllib.parse.parse_qs(parsed.query)
            key = (query.get("plant_key") or [""])[0]
            self.send_json(APP.history_payload(key, user))
        elif parsed.path == "/api/reports":
            self.send_json({"reports": APP.latest_reports(user)})
        elif parsed.path == "/admin/users":
            if not is_admin(user):
                self.send_json({"error": "Admin access required"}, 403)
                return
            self.send_admin_users_page()
        elif parsed.path == "/api/admin/users":
            if not is_admin(user):
                self.send_json({"error": "Admin access required"}, 403)
                return
            self.send_json({"users": [public_user(row) for row in load_users().values()], "plants": APP.all_plant_options(), "roles": sorted(VALID_ROLES), "postgres": postgres_enabled()})
        elif parsed.path == "/api/monthly-generation":
            query = urllib.parse.parse_qs(parsed.query)
            keys = [value for value in (query.get("plant_key") or []) if value]
            month = int((query.get("month") or [0])[0] or 0)
            year = int((query.get("year") or [0])[0] or 0)
            self.send_json(APP.monthly_generation_payload(keys, user, month=month, year=year))
        elif parsed.path == "/api/today-hourly-generation":
            query = urllib.parse.parse_qs(parsed.query)
            keys = [value for value in (query.get("plant_key") or []) if value]
            target_date = parse_iso_date((query.get("date") or [""])[0])
            self.send_json(APP.today_hourly_payload(keys, user, target_date=target_date))
        elif parsed.path == "/api/status":
            self.send_json(
                {
                    "auth_enabled": bool(load_users()),
                    "user": {
                        "username": (user or {}).get("username", "Local"),
                        "role": normalize_role((user or {}).get("role", "admin")),
                        "is_admin": is_admin(user),
                    },
                    "config": APP.config,
                    "app_version": APP_VERSION,
                    "last_refresh": APP.last_refresh,
                    "solax_debug": APP.brand_debug("SolaX", user),
                    "local_url": f"http://127.0.0.1:{APP.port}",
                    "mobile_url": f"http://{local_ip()}:{APP.port}",
                }
            )
        elif parsed.path == "/view-report":
            query = urllib.parse.parse_qs(parsed.query)
            relative = (query.get("file") or [""])[0]
            self.send_report_viewer(relative, user)
        elif parsed.path == "/chart-detail":
            query = urllib.parse.parse_qs(parsed.query)
            chart_type = (query.get("type") or ["today"])[0]
            keys = [value for value in (query.get("plant_key") or []) if value]
            month = int((query.get("month") or [0])[0] or 0)
            year = int((query.get("year") or [0])[0] or 0)
            target_date = parse_iso_date((query.get("date") or [""])[0])
            title, rows = APP.chart_export_rows(chart_type, keys, user, month=month, year=year, target_date=target_date)
            base_items = [("type", chart_type)] + [("plant_key", key) for key in keys]
            if month:
                base_items.append(("month", str(month)))
            if year:
                base_items.append(("year", str(year)))
            if target_date:
                base_items.append(("date", target_date.isoformat()))
            base_query = urllib.parse.urlencode(base_items)
            headers = list(rows[0].keys()) if rows else ["Period", "Generation (kWh)"]
            header_html = "".join(f"<th>{html_escape(header)}</th>" for header in headers)
            body_rows = "".join(
                "<tr>" + "".join(f"<td>{html_escape(str(row.get(header, '')))}</td>" for header in headers) + "</tr>"
                for row in rows
            )
            body = (
                CHART_DETAIL_HTML.replace("__TITLE__", html_escape(title))
                .replace("__HEADERS__", header_html)
                .replace("__ROWS__", body_rows)
                .replace("__QUERY__", base_query)
            ).encode("utf-8")
            self.send_bytes(body, "text/html; charset=utf-8")
        elif parsed.path == "/chart-csv":
            query = urllib.parse.parse_qs(parsed.query)
            chart_type = (query.get("type") or ["today"])[0]
            keys = [value for value in (query.get("plant_key") or []) if value]
            month = int((query.get("month") or [0])[0] or 0)
            year = int((query.get("year") or [0])[0] or 0)
            target_date = parse_iso_date((query.get("date") or [""])[0])
            self.send_bytes(APP.chart_csv_bytes(chart_type, keys, user, month=month, year=year, target_date=target_date), "text/csv; charset=utf-8", f"{chart_type}_generation.csv")
        elif parsed.path == "/chart-pdf":
            query = urllib.parse.parse_qs(parsed.query)
            chart_type = (query.get("type") or ["today"])[0]
            keys = [value for value in (query.get("plant_key") or []) if value]
            month = int((query.get("month") or [0])[0] or 0)
            year = int((query.get("year") or [0])[0] or 0)
            target_date = parse_iso_date((query.get("date") or [""])[0])
            self.send_bytes(APP.chart_pdf_bytes(chart_type, keys, user, month=month, year=year, target_date=target_date), "application/pdf", f"{chart_type}_generation.pdf")
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
            if not APP.user_can_access_report_path(user, path):
                self.send_json({"error": "You do not have access to this report."}, 403)
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
                if not user or user.get("disabled") or not verify_password(password, user.get("password_hash", "")):
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
                if not can_refresh_data(user):
                    self.send_json({"error": "Admin or Manager access required"}, 403)
                    return
                self.send_json(APP.refresh_async())
            elif parsed.path == "/api/refresh-on-open":
                if not can_refresh_data(user):
                    self.send_json({"error": "Admin or Manager access required"}, 403)
                    return
                self.send_json(APP.refresh_on_open_result())
            elif parsed.path == "/api/report":
                payload = self.read_json()
                self.send_json(APP.generate_selected_report(payload.get("plant_ids") or [], user, bool(payload.get("all_plants"))))
            elif parsed.path == "/api/admin/users":
                if not is_admin(user):
                    self.send_json({"error": "Admin access required"}, 403)
                    return
                if not postgres_enabled():
                    self.send_json({"error": "PostgreSQL is not configured. Set DATABASE_URL on Render before using User Management."}, 503)
                    return
                payload = self.read_json()
                result = db_upsert_user(
                    payload.get("username"),
                    payload.get("role"),
                    payload.get("plants") or [],
                    payload.get("password") or None,
                    bool(payload.get("disabled", False)),
                )
                self.send_json(result)
            elif parsed.path == "/api/admin/users/reset":
                if not is_admin(user):
                    self.send_json({"error": "Admin access required"}, 403)
                    return
                if not postgres_enabled():
                    self.send_json({"error": "PostgreSQL is not configured. Set DATABASE_URL on Render before using User Management."}, 503)
                    return
                payload = self.read_json()
                self.send_json(db_reset_user_password(payload.get("username"), payload.get("password")))
            elif parsed.path == "/api/admin/users/disable":
                if not is_admin(user):
                    self.send_json({"error": "Admin access required"}, 403)
                    return
                if not postgres_enabled():
                    self.send_json({"error": "PostgreSQL is not configured. Set DATABASE_URL on Render before using User Management."}, 503)
                    return
                payload = self.read_json()
                if str(payload.get("username") or "") == str(user.get("username") or "") and bool(payload.get("disabled", False)):
                    self.send_json({"error": "You cannot disable your own active admin account."}, 400)
                    return
                self.send_json(db_set_user_disabled(payload.get("username"), bool(payload.get("disabled", False))))
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
input{width:100%;height:42px;border:1px solid #d7e0ec;border-radius:8px;padding:0 12px;font-size:15px}button{margin-top:18px;width:100%;height:42px;border:0;border-radius:8px;background:#174f9c;color:white;font-weight:900;font-size:15px}.reset{display:block;text-align:center;margin-top:12px;color:#174f9c;text-decoration:none;font-weight:850;font-size:13px}
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
  <a class="reset" href="/reset-password">Reset Password</a>
  <div class="error">__ERROR__</div>
</form>
</body>
</html>"""


RESET_HELP_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reset NCE Solar Password</title>
<style>
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;background:linear-gradient(145deg,#eaf5fb,#f6fbf7);color:#1e2b3f}
.box{width:min(520px,calc(100vw - 32px));background:white;border:1px solid #d7e0ec;border-radius:12px;box-shadow:0 14px 50px rgba(23,79,156,.16);padding:26px}
h1{font-size:24px;margin:0 0 8px;color:#174f9c}.sub{margin:0 0 18px;color:#647084;line-height:1.5}.step{border:1px solid #d7e0ec;border-radius:8px;background:#fbfdff;padding:12px;margin:10px 0}.step b{color:#174f9c}a.btn{display:block;text-align:center;margin-top:18px;background:#174f9c;color:white;text-decoration:none;border-radius:8px;padding:11px 12px;font-weight:900}.warn{color:#c73e3e;font-weight:850}
</style>
</head>
<body>
<main class="box">
  <h1>Reset Password</h1>
  <p class="sub">An Admin can reset user passwords from <b>Users</b> inside the dashboard.</p>
  <div class="step"><b>1.</b> Login as an Admin user.</div>
  <div class="step"><b>2.</b> Open <b>Users</b> from the dashboard header.</div>
  <div class="step"><b>3.</b> Press <b>Reset</b> next to the user and enter a new password.</div>
  <div class="step"><b>4.</b> If the Admin password itself is lost, update <b>NCE_APP_PASSWORD</b> in Render and restart before first database seeding, or use the Mac reset command for the local fallback.</div>
  <p class="warn">Never share passwords in chat or upload APP_LOGIN_DETAILS_PRIVATE.txt.</p>
  <a class="btn" href="/login">Back to Login</a>
</main>
</body>
</html>"""


ADMIN_USERS_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NCE Solar Users</title>
<style>
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;background:#eef3f8;color:#1e2b3f}
header{background:#174f9c;color:white;padding:14px 18px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}h1{font-size:20px;margin:0;flex:1}
a{color:#174f9c;background:white;text-decoration:none;border-radius:7px;padding:9px 12px;font-weight:900}.wrap{max-width:1200px;margin:auto;padding:16px}
.panel{background:white;border:1px solid #d7e0ec;border-radius:8px;padding:14px;margin-bottom:14px;box-shadow:0 1px 4px rgba(15,35,60,.05)}
.grid{display:grid;grid-template-columns:1fr 170px 1fr 130px;gap:10px;align-items:end}label{font-size:12px;font-weight:800;color:#647084;display:block;margin-bottom:5px}
input,select{height:38px;width:100%;border:1px solid #d7e0ec;border-radius:7px;padding:0 10px;background:white}button{height:38px;border:0;border-radius:7px;background:#174f9c;color:white;font-weight:900;padding:0 12px;cursor:pointer}
button.alt{background:#18b9d6}button.warn{background:#c73e3e}.plants{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:7px;margin-top:10px;max-height:260px;overflow:auto;border:1px solid #d7e0ec;border-radius:8px;padding:8px;background:#fbfdff}
.plant{display:flex;gap:8px;align-items:flex-start;font-size:12px}.plant input{width:auto;height:auto;margin-top:2px}table{width:100%;border-collapse:collapse;font-size:13px}th{background:#174f9c;color:white;text-align:left;padding:9px}td{border-bottom:1px solid #d7e0ec;padding:8px;vertical-align:top}
.pill{display:inline-block;border-radius:999px;padding:3px 8px;font-size:11px;font-weight:900;color:white;background:#16845f}.disabled{background:#c73e3e}.muted{color:#647084;font-size:12px}.msg{font-weight:900;color:#16845f}.err{font-weight:900;color:#c73e3e}
@media(max-width:760px){.grid{grid-template-columns:1fr}header{display:grid}a{text-align:center}table,thead,tbody,tr,td{display:block}thead{display:none}tr{border:1px solid #d7e0ec;border-radius:8px;background:white;margin:8px 0;padding:8px}td{border:0;padding:5px}td::before{content:attr(data-label);display:block;font-size:11px;color:#647084;font-weight:900}}
</style>
</head>
<body>
<header><h1>Admin User Management</h1><a href="/">Back to Dashboard</a><a href="/logout">Logout</a></header>
<main class="wrap">
  <section class="panel">
    <h2>Create or Edit User</h2>
    <div class="grid">
      <div><label>Username</label><input id="username" autocomplete="off"></div>
      <div><label>Role</label><select id="role"></select></div>
      <div><label>Password</label><input id="password" type="password" placeholder="Required for new user"></div>
      <button id="save">Save User</button>
    </div>
    <p class="muted">Admin sees all plants. Manager, Customer and Viewer see only selected plants. Leave password blank while editing if you do not want to change it.</p>
    <div class="plants" id="plants"></div>
    <p id="message"></p>
  </section>
  <section class="panel">
    <h2>Existing Users</h2>
    <table><thead><tr><th>Username</th><th>Role</th><th>Status</th><th>Plants</th><th>Actions</th></tr></thead><tbody id="users"></tbody></table>
  </section>
</main>
<script>
let state={users:[],plants:[],roles:[]};
const $=s=>document.querySelector(s);
function h(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function api(path,opt={}){const r=await fetch(path,{cache:'no-store',...opt,headers:{'Content-Type':'application/json','Cache-Control':'no-cache',...(opt.headers||{})}});const text=await r.text();const data=text?JSON.parse(text):{};if(!r.ok)throw new Error(data.error||text||r.status);return data}
function selectedPlants(){return [...document.querySelectorAll('.plant input:checked')].map(x=>x.value)}
function setMessage(text,bad=false){$('#message').className=bad?'err':'msg';$('#message').textContent=text||''}
function editUser(username){const u=state.users.find(x=>x.username===username);if(!u)return;$('#username').value=u.username;$('#role').value=u.role;$('#password').value='';const allowed=new Set(u.plants||[]);document.querySelectorAll('.plant input').forEach(cb=>{cb.checked=allowed.has('*')||allowed.has(cb.value)});window.scrollTo({top:0,behavior:'smooth'})}
async function disableUser(username,disabled){await api('/api/admin/users/disable',{method:'POST',body:JSON.stringify({username,disabled})});await load();setMessage(disabled?'User disabled.':'User enabled.')}
async function resetUser(username){const password=prompt('Enter new password for '+username);if(!password)return;await api('/api/admin/users/reset',{method:'POST',body:JSON.stringify({username,password})});setMessage('Password reset for '+username+'.')}
function render(){const role=$('#role');role.innerHTML=state.roles.map(r=>`<option value="${h(r)}">${h(r)}</option>`).join('');$('#plants').innerHTML=state.plants.map(p=>`<label class="plant"><input type="checkbox" value="${h(p.plantKey)}"><span><b>${h(p.site)}</b><br><span class="muted">${h(p.brand)} · ${h(p.plantKey)}</span></span></label>`).join('');$('#users').innerHTML=state.users.map(u=>`<tr><td data-label="Username"><b>${h(u.username)}</b></td><td data-label="Role">${h(u.role)}</td><td data-label="Status"><span class="pill ${u.disabled?'disabled':''}">${u.disabled?'Disabled':'Active'}</span></td><td data-label="Plants">${h((u.plants||[]).includes('*')?'All plants':(u.plants||[]).length+' plants')}</td><td data-label="Actions"><button class="alt" onclick="editUser('${h(u.username)}')">Edit</button> <button onclick="resetUser('${h(u.username)}')">Reset</button> <button class="warn" onclick="disableUser('${h(u.username)}',${!u.disabled})">${u.disabled?'Enable':'Disable'}</button></td></tr>`).join('');if(!state.postgres)setMessage('PostgreSQL is not configured. Add DATABASE_URL on Render to enable user management.',true)}
async function load(){state=await api('/api/admin/users');render()}
$('#save').onclick=async()=>{try{const body={username:$('#username').value.trim(),role:$('#role').value,password:$('#password').value,plants:selectedPlants(),disabled:false};await api('/api/admin/users',{method:'POST',body:JSON.stringify(body)});$('#password').value='';await load();setMessage('User saved.')}catch(e){setMessage(e.message,true)}}
load().catch(e=>setMessage(e.message,true));
</script>
</body>
</html>"""


REPORT_VIEWER_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;background:#eef3f8;color:#1e2b3f}
header{position:sticky;top:0;z-index:5;background:#174f9c;color:white;padding:10px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
h1{font-size:14px;margin:0;flex:1 1 220px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
a,button{border:0;border-radius:6px;padding:9px 10px;font-weight:900;font-size:13px;text-decoration:none;cursor:pointer}
a{background:white;color:#174f9c}button{background:#18b9d6;color:white}.print{background:#16845f}.share{background:#5c6f8b}
iframe{display:block;width:100%;height:calc(100vh - 58px);border:0;background:white}
@media(max-width:640px){header{display:grid;grid-template-columns:1fr 1fr;gap:6px}h1{grid-column:1 / -1}.back{grid-column:1 / -1;text-align:center}a,button{width:100%;text-align:center}iframe{height:calc(100vh - 148px)}}
</style>
</head>
<body>
<header>
  <a class="back" href="/">Back to App</a>
  <h1>__TITLE__</h1>
  <a id="download" href="__DOWNLOAD_URL__" target="_blank">Download</a>
  <button class="share" id="share">Share</button>
  <button class="print" onclick="frames.reportFrame.focus();frames.reportFrame.print()">Print</button>
</header>
<iframe name="reportFrame" src="__DOWNLOAD_URL__"></iframe>
<script>
document.querySelector('#share').onclick=async()=>{const url=new URL('__DOWNLOAD_URL__', location.origin).href;if(navigator.share){try{await navigator.share({title:document.title,url});return}catch(e){}}navigator.clipboard?.writeText(url);alert('Report link copied.');};
</script>
</body>
</html>"""


CHART_DETAIL_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;background:#eef3f8;color:#1e2b3f}
header{position:sticky;top:0;background:#174f9c;color:white;padding:12px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
h1{font-size:16px;margin:0;flex:1 1 260px}a{background:white;color:#174f9c;text-decoration:none;border-radius:6px;padding:9px 11px;font-weight:900}
main{max-width:900px;margin:auto;padding:14px}.panel{background:white;border:1px solid #d7e0ec;border-radius:8px;padding:14px}
table{width:100%;border-collapse:collapse;font-size:13px}th{background:#174f9c;color:white;text-align:left;padding:9px}td{border-bottom:1px solid #d7e0ec;padding:8px}td:last-child{text-align:right;font-weight:800}tr:nth-child(even){background:#f8fafc}
@media(max-width:640px){header{display:grid;grid-template-columns:1fr 1fr}h1{grid-column:1 / -1}a{text-align:center}}
</style>
</head>
<body>
<header>
  <a href="/">Back to App</a>
  <h1>__TITLE__</h1>
  <a href="/chart-csv?__QUERY__">CSV</a>
  <a href="/chart-pdf?__QUERY__" target="_blank">PDF</a>
</header>
<main><section class="panel">
<table><thead><tr>__HEADERS__</tr></thead><tbody>__ROWS__</tbody></table>
</section></main>
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
a.logout,.admin-link{color:white;text-decoration:none;border:1px solid rgba(255,255,255,.55);border-radius:6px;padding:7px 10px;font-weight:800;font-size:12px}
main{padding:16px;max-width:1440px;margin:auto}.toolbar{display:grid;grid-template-columns:1.2fr .8fr .8fr auto auto auto auto auto;gap:10px;align-items:end;margin-bottom:10px}
label{font-size:11px;color:var(--muted);font-weight:700;display:block;margin-bottom:5px}select,input{height:36px;border:1px solid var(--line);border-radius:6px;padding:0 10px;width:100%;background:white}
button{height:36px;border:0;border-radius:6px;padding:0 13px;background:var(--blue);color:white;font-weight:800;cursor:pointer;white-space:nowrap}button.alt{background:var(--cyan)}button.gray{background:#5c6f8b}
.update-strip{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:0 0 12px;color:var(--muted);font-size:12px}.update-strip b{color:var(--ink)}.loading{background:#e8f7fb;color:#0e7490;border:1px solid #a5e4ee;border-radius:999px;padding:4px 10px;font-weight:900}.warning{background:#fff7ed;color:#b45309;border:1px solid #fed7aa;border-radius:999px;padding:4px 10px;font-weight:900}.hidden{display:none!important}
.grid{display:grid;grid-template-columns:repeat(6,minmax(120px,1fr));gap:10px;margin-bottom:12px}.card,.panel{background:white;border:1px solid var(--line);border-radius:8px;box-shadow:0 1px 4px rgba(15,35,60,.05)}
.card{padding:12px;min-height:78px}.card span{display:block;color:var(--muted);font-size:11px;font-weight:700;margin-bottom:10px}.card strong{font-size:20px}
.panel{padding:14px}.split{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(340px,.85fr);gap:12px;align-items:start}h2{font-size:15px;margin:0 0 10px}
.main-charts{display:grid;grid-template-columns:minmax(0,7fr) minmax(280px,3fr);gap:12px;margin-bottom:12px;justify-content:stretch;align-items:start}.main-chart{margin-bottom:0;min-height:0;width:100%;max-width:none}.chart-card{background:white;border:1px solid var(--line);border-radius:8px;padding:14px;box-shadow:0 1px 4px rgba(15,35,60,.05);min-height:220px;cursor:pointer}.main-chart{height:300px;overflow:hidden;padding:10px 10px 8px}.side-chart{margin-top:12px;min-height:230px}.production-card{padding:0;overflow:hidden}.production-card .chart-head{padding:12px 14px;margin:0;border-bottom:1px solid var(--line)}.production-card .chart-head h2{letter-spacing:.08em;text-transform:uppercase}.production-card .chart-total{font-size:24px;color:var(--green);font-weight:750}.production-footer{border-top:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;gap:10px;padding:9px 14px;background:#fbfdff}.production-date{height:30px;width:142px;border:0;background:transparent;padding:0;font-size:13px;font-weight:900;color:var(--ink)}.production-tabs{display:flex;align-items:center;gap:3px}.production-tabs button{height:30px;background:transparent;color:var(--muted);border-radius:0;padding:0 8px;border-bottom:3px solid transparent}.production-tabs button.active{color:var(--blue);border-bottom-color:var(--blue)}.chart-card:hover{border-color:var(--cyan);box-shadow:0 2px 8px rgba(15,35,60,.10)}.chart-head{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:6px}.chart-head h2{margin:0}.chart-total{font-size:12px;font-weight:900;color:var(--green);white-space:nowrap}.bar-chart{height:180px;display:flex;align-items:end;gap:7px;border-left:1px solid var(--line);border-bottom:1px solid var(--line);padding:6px 6px 0;overflow-x:auto;overflow-y:hidden}.area-chart{height:180px;border-left:1px solid var(--line);border-bottom:1px solid var(--line);background:linear-gradient(180deg,#fbfdff,#f8fafc);border-radius:6px;padding:6px;overflow:hidden}.production-chart{height:300px;border:0;border-radius:0;background:#fff;padding:0 10px 8px;overflow:hidden}.area-chart svg{width:100%;height:100%;display:block}.main-chart .bar-chart{height:222px;max-height:222px;gap:5px}.bar-item{min-width:28px;flex:1;max-width:72px;display:flex;flex-direction:column;align-items:center;justify-content:end;height:100%;gap:5px}.main-chart .bar-item{flex:0 0 24px;min-width:24px;max-width:24px}.bar{width:100%;min-height:2px;border-radius:4px 4px 0 0;background:linear-gradient(180deg,var(--cyan),var(--blue));position:relative}.main-chart .bar{width:14px}.bar.candle{background:linear-gradient(180deg,#34d399,var(--green))}.bar.perkw{background:linear-gradient(180deg,#22c55e,var(--green))}.bar-label{font-size:10px;color:var(--muted);max-width:64px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-align:center}.bar-value{font-size:10px;font-weight:900;color:var(--ink);white-space:nowrap}#perKwChart{height:245px;max-height:245px;padding-bottom:52px;overflow-x:auto;overflow-y:visible}#perKwChart .bar-item{height:188px;justify-content:flex-end;overflow:visible}#perKwChart .bar-label{width:96px;max-width:96px;overflow:visible;text-overflow:clip;transform:rotate(-22deg);transform-origin:top right;text-align:right;color:var(--ink);margin-top:10px;line-height:1.1}.grouped-chart{align-items:end}.day-group{flex:0 0 22px;min-width:22px;max-width:22px;height:100%;display:flex;flex-direction:column;justify-content:end;gap:3px}.day-bars{height:calc(100% - 18px);display:flex;align-items:end;justify-content:center;gap:2px}.mini-bar{flex:0 0 12px;min-width:12px;max-width:12px;border-radius:4px 4px 0 0;background:var(--blue)}#monthlyChart{height:222px;max-height:222px}.selected-monthly-chart .day-group{flex-basis:20px;min-width:20px;max-width:20px}.month-controls{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:5px}.selected-plant-list{display:grid;gap:8px;max-height:260px;overflow:auto}.selected-card{border:1px solid var(--line);border-radius:7px;background:#fbfdff;padding:9px}.selected-card.online{background:#f0fdf4;border-color:#bbf7d0}.selected-card.offline{background:#f8fafc;border-color:#d7e0ec}.selected-card b{display:block;margin-bottom:5px}.selected-card span{display:block;color:var(--muted);font-size:11px;line-height:1.55}
table{width:100%;border-collapse:collapse;font-size:12px}th{background:var(--blue);color:white;text-align:left;padding:8px 7px}td{border-bottom:1px solid var(--line);padding:7px}tr:nth-child(even){background:#f8fafc}
.status{font-weight:800}.online{color:var(--green)}.offline,.stale{color:var(--red)}.fresh{color:var(--green)}.pill{display:inline-block;border-radius:999px;padding:2px 7px;font-size:10px;font-weight:800;color:white}.pill.fresh{background:var(--green);color:white}.pill.stale{background:var(--red);color:white}.pill.offline{background:#111827;color:white}
.plant-title{font-size:21px;font-weight:850}.details{display:grid;grid-template-columns:1fr 1fr;gap:8px}.detail{border:1px solid var(--line);border-radius:6px;background:#fbfdff;padding:10px}.detail span{display:block;color:var(--muted);font-size:11px;font-weight:700;margin-bottom:7px}
.checkcell{width:34px}.report-link{font-size:12px;color:var(--muted);margin-top:8px;word-break:break-all}.download-btn{display:inline-block;margin-top:8px;background:var(--green);color:white;text-decoration:none;border-radius:6px;padding:9px 12px;font-weight:900}.report-list{margin-top:8px;display:grid;gap:6px}.report-item{display:block;border:1px solid var(--line);border-radius:6px;background:#fbfdff;padding:8px;color:var(--blue);text-decoration:none;font-weight:800}.report-item span{display:block;color:var(--muted);font-size:11px;font-weight:700;margin-top:3px}.log{font-family:ui-monospace,Menlo,monospace;font-size:11px;white-space:pre-wrap;max-height:180px;overflow:auto;background:#f8fafc;border:1px solid var(--line);padding:8px;border-radius:6px}
.history-block{margin-top:12px}.history-block h3{font-size:13px;margin:10px 0 6px}.history-scroll{max-height:160px;overflow:auto;border:1px solid var(--line);border-radius:6px}.history-scroll table{font-size:11px}.history-scroll th{position:sticky;top:0}.empty-history{color:var(--muted);font-size:12px;padding:8px;border:1px solid var(--line);border-radius:6px;background:#fbfdff}.fold{border-top:1px solid var(--line);padding-top:10px;margin-top:12px}.fold summary{cursor:pointer;font-weight:900;color:var(--blue);list-style:none}.fold summary::-webkit-details-marker{display:none}.fold summary::after{content:'+';float:right}.fold[open] summary::after{content:'-'}.plant-daily{display:none}
@media(max-width:980px){header{position:static}.toolbar,.grid,.split,.main-charts{grid-template-columns:1fr}.main-charts{justify-content:stretch}.main-chart{max-width:none}table{font-size:11px}th:nth-child(5),td:nth-child(5),th:nth-child(7),td:nth-child(7){display:none}}
@media(max-width:640px){
body{background:#f3f7fb}
header{display:grid;grid-template-columns:1fr auto;gap:8px;padding:12px 14px;align-items:start}
h1{font-size:18px;line-height:1.15}.meta{grid-column:1 / -1;margin:0;text-align:left;font-size:11px;opacity:.95}a.logout,.admin-link{padding:8px 10px}
main{padding:10px;max-width:none;overflow-x:hidden}.toolbar{display:grid;grid-template-columns:1fr;gap:8px}.toolbar button{width:100%;height:42px}
.update-strip{font-size:11px;gap:6px;margin-bottom:8px}.update-strip span{max-width:100%}
.grid{grid-template-columns:1fr 1fr;gap:8px}.card{min-height:68px;padding:10px}.card span{margin-bottom:6px}.card strong{font-size:17px}
.chart-card{padding:10px;min-height:0}.main-chart{height:255px;padding:9px}.side-chart{margin-top:8px}.production-card{padding:0}.production-card .chart-head{padding:10px}.production-card .chart-total{font-size:18px}.production-footer{padding:8px 10px;align-items:flex-start}.production-date{font-size:12px;width:136px}.production-tabs{flex-wrap:wrap;justify-content:flex-end}.production-tabs button{height:28px;padding:0 6px}.chart-head{align-items:flex-start}.chart-head h2{font-size:14px}.bar-chart{height:190px;gap:5px}.production-chart{height:220px}.main-chart .bar-chart{height:170px;max-height:170px}.grouped-chart{height:155px}.bar-item{min-width:26px}.main-chart .bar-item{flex-basis:22px;min-width:22px;max-width:22px}.main-chart .bar{width:12px}.day-group{flex-basis:20px;min-width:20px;max-width:20px}.mini-bar{flex-basis:10px;min-width:10px;max-width:10px}.bar-label{font-size:9px;max-width:50px}#perKwChart{height:202px;max-height:202px;padding-bottom:46px}#perKwChart .bar-item{height:146px}#perKwChart .bar-label{width:74px;max-width:74px;margin-top:10px;transform:rotate(-22deg)}#monthlyChart{height:170px;max-height:170px}.bar-value{display:none}.selected-plant-list{max-height:none}.selected-card{padding:8px}.month-controls select{height:36px}
.panel{padding:12px;border-radius:8px}.split{gap:10px}.details{grid-template-columns:1fr}.detail{padding:9px}
section.panel table,section.panel thead,section.panel tbody,section.panel tr,section.panel td{display:block;width:100%}
section.panel table{border-collapse:separate;border-spacing:0}
section.panel thead{display:none}
section.panel tr{border:1px solid #dce8f2;border-radius:7px;background:#fbfdff;margin:5px 0;padding:2px 8px;box-shadow:0 1px 2px rgba(15,35,60,.04)}
section.panel tr:nth-child(even){background:#fbfdff}
section.panel tr.open{background:white}
section.panel td{border:0;padding:4px 4px;display:grid;grid-template-columns:92px 1fr;gap:8px;align-items:center;font-size:12px}
section.panel td::before{content:attr(data-label);color:var(--muted);font-size:11px;font-weight:800}
section.panel td:first-child{display:block;padding-bottom:2px}
section.panel td:first-child::before{content:''}
section.panel td:nth-child(3){font-size:13px;padding:3px 2px}.checkcell{width:auto}
section.panel tr:not(.open) td:not(:first-child):not(:nth-child(3)){display:none}
section.panel tr:not(.open) td:first-child{display:none}
section.panel tr td:nth-child(3)::after{content:'+';float:right;color:var(--blue);font-weight:900}
section.panel tr.open td:nth-child(3)::after{content:'-'}
.plant-line{display:flex;align-items:center;justify-content:space-between;gap:8px}.plant-line b{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.plant-daily{display:inline;font-size:12px;font-weight:900;white-space:nowrap;margin-right:18px}.plant-line.online b{color:#064E3B}.plant-line.online .plant-daily{color:#16A34A}.plant-line.offline b,.plant-line.offline .plant-daily{color:#111827}
.history-picker{display:grid;grid-template-columns:1fr;gap:8px;margin-top:8px}.picked{border:1px solid var(--line);border-radius:6px;background:#fbfdff;padding:9px;margin-top:8px;font-size:12px}.picked b{font-size:15px}
.history-scroll{max-height:220px}.history-scroll table{display:table}.history-scroll thead{display:table-header-group}.history-scroll tbody{display:table-row-group}.history-scroll tr{display:table-row;border:0;box-shadow:none;margin:0;padding:0}.history-scroll td,.history-scroll th{display:table-cell;width:auto;padding:7px;font-size:11px}.history-scroll td::before{content:none}
.report-item{font-size:12px}.log{max-height:140px}.plant-title{font-size:18px}
.mobile-fold{display:block}.mobile-fold:not([open]){padding-bottom:10px}.mobile-fold summary{font-size:14px}
}
</style>
</head>
<body>
<header><h1>NCE Live Solar App</h1><div class="meta"><div>Signed in: __USER__</div><div id="dateLine"></div><div id="versionLine"></div><div id="mobileLine"></div></div><a id="adminLink" class="admin-link hidden" href="/admin/users">Users</a><a class="logout" href="/logout">Logout</a></header>
<main>
  <div class="toolbar">
    <div><label>Search</label><input id="search" placeholder="Search any plant"></div>
    <div><label>Brand</label><select id="brand"></select></div>
    <div><label>Status</label><select id="status"></select></div>
    <button id="refresh" class="alt">Refresh now</button>
    <button id="reportAll">All Plants Report</button>
    <button id="reportPlant">Plant Report</button>
    <button id="report">Selected Report</button>
    <button id="selectAll" class="gray">Select All</button>
  </div>
  <div class="update-strip">
    <span>Last updated: <b id="lastUpdated">Not loaded</b></span>
    <span id="loadingIndicator" class="loading hidden">Updating live data...</span>
    <span id="warningLine" class="warning hidden"></span>
  </div>
  <div class="grid" id="cards"></div>
  <div class="main-charts">
    <section class="chart-card main-chart" id="perKwChartCard" title="Open today's hourly details">
      <div class="chart-head"><h2>Today's Per-kW Generation</h2><span class="chart-total" id="perKwChartTotal"></span></div>
      <div class="bar-chart" id="perKwChart"></div>
    </section>
    <section class="chart-card main-chart" id="monthlyChartCard" title="Open daily monthly details">
      <div class="chart-head">
        <h2>Monthly Generation</h2>
        <span class="chart-total" id="monthlyChartTotal"></span>
      </div>
      <div class="month-controls">
        <select id="monthSelect"></select>
        <select id="yearSelect"></select>
      </div>
      <div class="bar-chart grouped-chart" id="monthlyChart"></div>
    </section>
  </div>
  <div class="split dashboard-split">
    <section class="panel">
      <h2>Plants</h2>
      <table><thead><tr><th class="checkcell"></th><th>Brand</th><th>Plant</th><th>Status</th><th>Date</th><th>Daily</th><th>Weekly</th><th>Yearly</th><th>CUF</th></tr></thead><tbody id="rows"></tbody></table>
    </section>
    <aside class="panel side-panel">
      <details class="fold mobile-fold" open>
        <summary>Selected Plant Details</summary>
        <div id="selectedPlantDetails" class="selected-plant-list"></div>
      </details>
      <section class="chart-card side-chart production-card" id="selectedTodayChartCard" title="Open hourly details">
        <div class="chart-head"><h2>Production - Selected Plant</h2><span class="chart-total" id="selectedTodayTotal"></span></div>
        <div class="area-chart production-chart" id="selectedTodayChart"></div>
        <div class="production-footer">
          <input class="production-date" id="productionDatePick" type="date">
          <span class="production-tabs" id="productionTabs">
            <button type="button" data-mode="day" class="active">DAY</button>
            <button type="button" data-mode="month">MONTH</button>
            <button type="button" data-mode="year">YEAR</button>
            <button type="button" data-mode="total">TOTAL</button>
          </span>
        </div>
      </section>
      <details class="fold mobile-fold" open>
        <summary>Selected Plant</summary>
        <div id="detail"></div>
      </details>
      <details class="fold mobile-fold">
        <summary>Auto Report Time</summary>
        <div class="details">
          <div><label>Day</label><select id="autoDay"><option>Sunday</option><option>Monday</option><option>Tuesday</option><option>Wednesday</option><option>Thursday</option><option>Friday</option><option>Saturday</option></select></div>
          <div><label>Time</label><input id="autoTime" type="time"></div>
        </div>
        <button id="saveSchedule" style="margin-top:10px">Save Schedule</button>
      </details>
      <div class="report-link" id="reportResult"></div>
      <details class="fold mobile-fold">
        <summary>Latest Reports</summary>
        <div class="report-list" id="reportList">No reports loaded.</div>
      </details>
      <details class="fold mobile-fold">
        <summary>Refresh Log</summary>
        <div class="log" id="log">Ready.</div>
      </details>
    </aside>
  </div>
</main>
<script>window.__BOOTSTRAP__=__BOOTSTRAP_JSON__;</script>
<script>
let plants=[], selected=new Set(), statusData={}, activePlantId=null, activeHistoryKey='', activeTodayChartKey='', openRefreshStarted=false, monthlyChartKey='', refreshInFlight=false, autoRefreshTimer=null, productionMode='day', productionDate='';
const searchInput=document.querySelector('#search');
const brandFilter=document.querySelector('#brand');
const statusFilter=document.querySelector('#status');
const cardsEl=document.querySelector('#cards');
const perKwChartEl=document.querySelector('#perKwChart');
const perKwChartCardEl=document.querySelector('#perKwChartCard');
const perKwChartTotalEl=document.querySelector('#perKwChartTotal');
const selectedTodayChartEl=document.querySelector('#selectedTodayChart');
const selectedTodayChartCardEl=document.querySelector('#selectedTodayChartCard');
const selectedTodayTotalEl=document.querySelector('#selectedTodayTotal');
const productionDatePickEl=document.querySelector('#productionDatePick');
const productionTabsEl=document.querySelector('#productionTabs');
const monthlyChartEl=document.querySelector('#monthlyChart');
const monthlyChartCardEl=document.querySelector('#monthlyChartCard');
const monthlyChartTotalEl=document.querySelector('#monthlyChartTotal');
const monthSelectEl=document.querySelector('#monthSelect');
const yearSelectEl=document.querySelector('#yearSelect');
const rowsEl=document.querySelector('#rows');
const detailEl=document.querySelector('#detail');
const selectedPlantDetailsEl=document.querySelector('#selectedPlantDetails');
const refreshBtn=document.querySelector('#refresh');
const reportBtn=document.querySelector('#report');
const reportAllBtn=document.querySelector('#reportAll');
const reportPlantBtn=document.querySelector('#reportPlant');
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
const lastUpdatedEl=document.querySelector('#lastUpdated');
const loadingIndicatorEl=document.querySelector('#loadingIndicator');
const warningLineEl=document.querySelector('#warningLine');
const adminLinkEl=document.querySelector('#adminLink');
function istParts(){const values={};new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Kolkata',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).formatToParts(new Date()).forEach(p=>{values[p.type]=p.value});return values}
function todayText(){const p=istParts();return `${p.year}-${p.month}-${p.day}`}
function istNowText(){const p=istParts();return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute} IST`}
function f(v,d=2){return Number(v||0).toLocaleString('en-IN',{minimumFractionDigits:d,maximumFractionDigits:d})}
function cls(s){s=String(s||'').toLowerCase();return (s.includes('online')||s.includes('normal'))?'online':'offline'}
function fresh(p){return p.dataDate===todayText()}
function offline(p){return cls(p.status)==='offline'}
function pillText(p){return fresh(p)?'TODAY':(offline(p)?'OFFLINE':'STALE')}
function pillClass(p){return fresh(p)?'fresh':(offline(p)?'offline':'stale')}
function staleNote(p){return !fresh(p)&&!offline(p)&&String(p.brand||'').toLowerCase()==='solis'?'Solis data is from the last saved Mac capture. Refresh Solis on the Mac to make this current.':''}
function uniq(a){return [...new Set(a)].filter(Boolean).sort()}
function h(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function weightedCuf(rows){const cap=rows.reduce((a,p)=>a+Number(p.capacity||0),0),year=rows.reduce((a,p)=>a+Number(p.year||0),0);const p=istParts();const days=Math.max(1,Math.floor((Date.UTC(Number(p.year),Number(p.month)-1,Number(p.day))-Date.UTC(2026,0,1))/86400000)+1);return cap&&year?year/(cap*24*days)*100:0}
async function api(path,opt={}){const sep=path.includes('?')?'&':'?';const url=path+sep+'_ts='+Date.now();const r=await fetch(url,{cache:'no-store',...opt,headers:{'Cache-Control':'no-cache',...(opt.headers||{})}});const text=await r.text();let data={};try{data=text?JSON.parse(text):{};}catch(e){throw new Error(`${path} returned ${r.status}: ${text.slice(0,240)||'empty response'}`)}if(r.status===401){window.location='/login';throw new Error('Login required')}if(!r.ok){throw new Error(data.error||`${path} returned ${r.status}`)}return data}
function filtered(){const q=searchInput.value.toLowerCase(), b=brandFilter.value, s=statusFilter.value;return plants.filter(p=>(b==='all'||p.brand===b)&&(s==='all'||p.status===s)&&(`${p.site} ${p.brand}`.toLowerCase().includes(q)))}
function selectedRows(){return plants.filter(p=>selected.has(p.id))}
function chartQuery(type,rows){rows=rows||filtered();let query='type='+encodeURIComponent(type);if(!rows.length){query+='&plant_key=__none__'}else{rows.forEach(p=>query+='&plant_key='+encodeURIComponent(p.plantKey))}if(type==='monthly'){query+='&month='+encodeURIComponent(monthSelectEl.value||'')+'&year='+encodeURIComponent(yearSelectEl.value||'')}if(type==='today'){query+='&date='+encodeURIComponent(productionDateValue())}return query}
function productionDateValue(){return productionDatePickEl.value||productionDate||todayText()}
function productionYear(){return Number(productionDateValue().slice(0,4))||Number(todayText().slice(0,4))}
function productionMonth(){return Number(productionDateValue().slice(5,7))||Number(todayText().slice(5,7))}
function setLoading(on){loadingIndicatorEl.classList.toggle('hidden',!on);refreshBtn.disabled=on;refreshBtn.textContent=on?'Refreshing...':'Refresh now'}
function applyRoleUi(){const role=String(statusData.user?.role||'admin').toLowerCase();const isAdmin=!!statusData.user?.is_admin||role==='admin';const canRefresh=isAdmin||role==='manager';const canReport=isAdmin||role==='manager'||role==='customer';adminLinkEl.classList.toggle('hidden',!isAdmin);refreshBtn.classList.toggle('hidden',!canRefresh);saveScheduleBtn.classList.toggle('hidden',!isAdmin);autoDayEl.disabled=!isAdmin;autoTimeEl.disabled=!isAdmin;[reportAllBtn,reportPlantBtn,reportBtn].forEach(btn=>btn.classList.toggle('hidden',!canReport));selectAllBtn.classList.toggle('hidden',!canReport)}
function setWarning(message){warningLineEl.textContent=message||'';warningLineEl.classList.toggle('hidden',!message)}
function refreshWarning(r){const bad=(r.steps||[]).filter(s=>!s.ok && !String(s.label||'').toLowerCase().includes('skipped'));return bad.length?'Some inverter/API refreshes failed. Showing last successful data: '+bad.slice(0,2).map(s=>s.label).join(', '):''}
function backendUpdatedText(s){const finished=s?.last_refresh?.finished, started=s?.last_refresh?.started;if(finished)return finished.replace('T',' ')+' IST';if(started)return 'Refresh running since '+started.replace('T',' ')+' IST';return istNowText()}
function staleOnlineRows(){return plants.filter(p=>!fresh(p)&&!offline(p))}
function setupDateSelectors(){const monthNames=['January','February','March','April','May','June','July','August','September','October','November','December'];const p=istParts();const currentMonth=Number(p.month), currentYear=Number(p.year);monthSelectEl.innerHTML=monthNames.map((m,i)=>`<option value="${i+1}" ${i+1===currentMonth?'selected':''}>${m}</option>`).join('');let years=[];for(let y=currentYear;y>=2026;y--)years.push(y);yearSelectEl.innerHTML=years.map(y=>`<option value="${y}" ${y===currentYear?'selected':''}>${y}</option>`).join('')}
function renderFilters(){const oldBrand=brandFilter.value||'all', oldStatus=statusFilter.value||'all';const brands=uniq(plants.map(p=>p.brand)), statuses=uniq(plants.map(p=>p.status));brandFilter.innerHTML='<option value="all">All Brands</option>'+brands.map(x=>`<option>${x}</option>`).join('');statusFilter.innerHTML='<option value="all">All Status</option>'+statuses.map(x=>`<option>${x}</option>`).join('');brandFilter.value=brands.includes(oldBrand)?oldBrand:'all';statusFilter.value=statuses.includes(oldStatus)?oldStatus:'all'}
function shortName(name){return String(name||'').replace(/\b(plant|solar|spv|kw)\b/gi,'').trim().slice(0,16)||'Plant'}
function renderBars(el,totalEl,rows,key,unit='kWh',barClass=''){const top=[...rows].sort((a,b)=>Number(b[key]||0)-Number(a[key]||0));const total=rows.reduce((a,p)=>a+Number(p[key]||0),0);const max=Math.max(...top.map(p=>Number(p[key]||0)),1);totalEl.textContent=`${f(total)} ${unit}`;el.innerHTML=top.length?top.map(p=>{const value=Number(p[key]||0);const height=Math.max(value>0?2:1,value/max*100);return `<div class="bar-item" title="${h(p.site)} | Capacity: ${f(p.capacity)} kW | ${f(value)} ${unit}"><div class="bar-value">${f(value,1)}</div><div class="bar ${barClass}" style="height:${height}%"></div><div class="bar-label">${h(shortName(p.site))}</div></div>`}).join(''):'<div class="empty-history">Select one or more plants.</div>'}
function renderPerKw(rows){const data=rows.map(p=>({...p,perKw:Number(p.capacity||0)>0?Number(p.daily||0)/Number(p.capacity||0):0}));const max=Math.max(...data.map(p=>p.perKw),1);perKwChartTotalEl.textContent=data.length?'All visible · kWh/kW':'No plant data';perKwChartEl.innerHTML=data.length?data.map(p=>{const height=Math.max(p.perKw>0?2:1,p.perKw/max*100);const cap=Number(p.capacity||0);const title=cap>0?`${h(p.site)}: ${f(p.perKw)} kWh/kW (${f(p.daily)} kWh / ${f(cap)} kW)`:`${h(p.site)}: Capacity missing, shown as 0.00 kWh/kW`;return `<div class="bar-item" title="${title}"><div class="bar-value">${f(p.perKw)}</div><div class="bar perkw" style="height:${height}%"></div><div class="bar-label">${h(shortName(p.site))}</div></div>`}).join(''):'<div class="empty-history">No visible plant data.</div>'}
function colorFor(i){return ['#174f9c','#18b9d6','#16845f','#f59e0b','#7c3aed','#ef4444','#0891b2','#4f46e5'][i%8]}
function renderMonthlyGrouped(data){const days=data.days||[], plantsList=data.plants||[];const max=Math.max(...days.map(d=>Number(d.generation||0)),1);monthlyChartTotalEl.textContent=`${h(data.month||'Month')} · ${f(data.total)} kWh`;if(!plantsList.length){monthlyChartEl.innerHTML='<div class="empty-history">No visible plant data.</div>';return}monthlyChartEl.innerHTML=days.length?days.map(d=>{const value=Number(d.generation||0);const height=Math.max(value>0?2:1,value/max*100);return `<div class="day-group" title="${h(d.date)} total all visible plants: ${f(value)} kWh"><div class="day-bars"><div class="mini-bar" style="height:${height}%;background:linear-gradient(180deg,#34d399,var(--green))" title="${h(d.date)} · ${f(value)} kWh"></div></div><div class="bar-label">${h(d.day)}</div></div>`}).join(''):'<div class="empty-history">No monthly data</div>'}
async function loadMonthlyChart(rows){const key=rows.map(p=>`${p.plantKey}:${p.dataDate}:${p.daily}`).sort().join('|')+`|${monthSelectEl.value}|${yearSelectEl.value}`;if(key===monthlyChartKey)return;monthlyChartKey=key;monthlyChartEl.innerHTML='<div class="empty-history">Loading month...</div>';const query=rows.map(p=>'plant_key='+encodeURIComponent(p.plantKey)).join('&')+'&month='+encodeURIComponent(monthSelectEl.value||'')+'&year='+encodeURIComponent(yearSelectEl.value||'');try{renderMonthlyGrouped(await api('/api/monthly-generation?'+query))}catch(error){monthlyChartEl.innerHTML='<div class="empty-history">Monthly graph failed: '+h(error.message)+'</div>'}}
function setProductionMode(mode){productionMode=mode;productionTabsEl.querySelectorAll('button').forEach(btn=>btn.classList.toggle('active',btn.dataset.mode===mode))}
function monthName(n){return ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][Number(n)-1]||String(n)}
function renderProductionBars(label, items, total, unit='kWh'){selectedTodayTotalEl.textContent=`${f(total)} ${unit}`;productionDatePickEl.value=productionDateValue();const w=640,hgt=300,left=42,right=10,top=28,bottom=42,plotW=w-left-right,plotH=hgt-top-bottom;const max=Math.max(...items.map(x=>Number(x.value||0)),1);const yMax=Math.ceil(max/5)*5||5;const gap=4,barW=Math.max(8,Math.min(28,(plotW/items.length)-gap));const yTicks=[0,.25,.5,.75,1].map(pct=>{const y=top+plotH-(pct*plotH);const value=yMax*pct;return `<line x1="${left}" y1="${y.toFixed(1)}" x2="${left+plotW}" y2="${y.toFixed(1)}" stroke="#e8eaed"></line><text x="8" y="${(y+4).toFixed(1)}" fill="#647084" font-size="11">${f(value,0)}</text>`}).join('');const bars=items.map((item,i)=>{const x=left+(items.length===1?plotW/2-barW/2:(i/(items.length-1))*plotW-barW/2);const height=Math.max(Number(item.value||0)>0?2:1,(Number(item.value||0)/yMax)*plotH);const y=top+plotH-height;const showLabel=items.length<=14||i%Math.ceil(items.length/12)===0;return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${height.toFixed(1)}" rx="3" fill="#18b9d6"><title>${h(item.label)} | ${f(item.value)} ${unit}</title></rect>${showLabel?`<text x="${(x+barW/2).toFixed(1)}" y="${hgt-12}" text-anchor="middle" fill="#647084" font-size="10">${h(item.short||item.label)}</text>`:''}`}).join('');selectedTodayChartEl.innerHTML=`<svg viewBox="0 0 ${w} ${hgt}" role="img" aria-label="Selected plant generation graph"><rect x="0" y="0" width="${w}" height="${hgt}" fill="#fff"></rect><text x="${left}" y="18" fill="#647084" font-size="12">${h(unit)}</text><rect x="${left+plotW/2-42}" y="10" width="9" height="9" fill="#18b9d6"></rect><text x="${left+plotW/2-28}" y="18" fill="#1e2b3f" font-size="12">Generation</text>${yTicks}<line x1="${left}" y1="${top+plotH}" x2="${left+plotW}" y2="${top+plotH}" stroke="#d7e0ec"></line>${bars}</svg>`}
function renderAreaChart(data, plant){const hours=data.hours||[];selectedTodayTotalEl.textContent=`${f(data.total)} kWh`;productionDatePickEl.value=data.date||productionDateValue();if(!plant){selectedTodayChartEl.innerHTML='<div class="empty-history">Click a plant name.</div>';return}if(!hours.length){selectedTodayChartEl.innerHTML='<div class="empty-history">Generation has not started today.</div>';return}const byHour=new Map(hours.map(x=>[String(x.hour||''),x]));const series=[];for(let hour=0;hour<24;hour++){const label=`${String(hour).padStart(2,'0')}:00`;const item=byHour.get(label)||{hour:label,generation:0,power:0};series.push({...item,hour:label,power:Number(item.power||0),generation:Number(item.generation||0)})}const w=640,hgt=300,left=42,right=10,top=28,bottom=42,plotW=w-left-right,plotH=hgt-top-bottom;const capacity=Number(data.capacity||plant.capacity||0);const maxPower=Math.max(capacity>0?capacity:0,...series.map(x=>x.power),1);const yMax=Math.ceil(maxPower/5)*5||5;const points=series.map((x,i)=>{const px=left+(i/23)*plotW;const py=top+plotH-(Math.min(x.power,yMax)/yMax)*plotH;return [px,py,x]});function smoothPath(items){let d=`M ${items[0][0].toFixed(1)} ${items[0][1].toFixed(1)}`;for(let i=1;i<items.length;i++){const prev=items[i-1],cur=items[i];const cx=(prev[0]+cur[0])/2;d+=` C ${cx.toFixed(1)} ${prev[1].toFixed(1)} ${cx.toFixed(1)} ${cur[1].toFixed(1)} ${cur[0].toFixed(1)} ${cur[1].toFixed(1)}`}return d}const line=smoothPath(points);const area=`${line} L ${left+plotW} ${top+plotH} L ${left} ${top+plotH} Z`;const yTicks=[0,.2,.4,.6,.8,1].map(pct=>{const y=top+plotH-(pct*plotH);const value=yMax*pct;return `<line x1="${left}" y1="${y.toFixed(1)}" x2="${left+plotW}" y2="${y.toFixed(1)}" stroke="#e8eaed" stroke-width="1"></line><text x="8" y="${(y+4).toFixed(1)}" fill="#647084" font-size="11">${f(value,0)}</text>`}).join('');const xTicks=[1,3,5,7,9,11,13,15,17,19,21,23].map(hour=>{const x=left+(hour/23)*plotW;const label=hour<12?`${String(hour).padStart(2,'0')}:00 AM`:hour===12?'12:00 PM':`${String(hour-12).padStart(2,'0')}:00 PM`;return `<line x1="${x.toFixed(1)}" y1="${top+plotH}" x2="${x.toFixed(1)}" y2="${top+plotH+5}" stroke="#dfe3e8"></line><text x="${x.toFixed(1)}" y="${hgt-12}" text-anchor="middle" fill="#647084" font-size="11">${h(label)}</text>`}).join('');const title=`${h(plant.site)} | Capacity: ${f(capacity)} kW`;selectedTodayChartEl.innerHTML=`<svg viewBox="0 0 ${w} ${hgt}" role="img" aria-label="Selected plant production power graph"><rect x="0" y="0" width="${w}" height="${hgt}" fill="#fff"></rect><text x="${left}" y="18" fill="#647084" font-size="12">kW</text><rect x="${left+plotW/2-42}" y="10" width="9" height="9" fill="#18b9d6"></rect><text x="${left+plotW/2-28}" y="18" fill="#1e2b3f" font-size="12">Production</text>${yTicks}<line x1="${left}" y1="${top+plotH}" x2="${left+plotW}" y2="${top+plotH}" stroke="#d7e0ec"></line><path d="${area}" fill="rgba(24,185,214,.30)"></path><path d="${line}" fill="none" stroke="#174f9c" stroke-width="2" stroke-linejoin="round"></path>${points.map(p=>`<circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="6" fill="transparent"><title>${h(p[2].hour)} | Power: ${f(p[2].power)} kW | Today: ${f(p[2].generation)} kWh | ${title}</title></circle>`).join('')}${xTicks}</svg>`}
async function loadProductionChart(active){if(!active){activeTodayChartKey='';selectedTodayTotalEl.textContent='';productionDatePickEl.value=productionDateValue();selectedTodayChartEl.innerHTML='<div class="empty-history">Click a plant name.</div>';return}productionTabsEl.querySelectorAll('button').forEach(btn=>btn.classList.toggle('active',btn.dataset.mode===productionMode));const pickedDate=productionDateValue();const key=`${productionMode}|${active.plantKey}|${active.dataDate}|${active.daily}|${pickedDate}`;if(key===activeTodayChartKey)return;activeTodayChartKey=key;selectedTodayChartEl.innerHTML='<div class="empty-history">Loading graph...</div>';try{if(productionMode==='day'){renderAreaChart(await api('/api/today-hourly-generation?plant_key='+encodeURIComponent(active.plantKey)+'&date='+encodeURIComponent(pickedDate)),active);return}if(productionMode==='month'){const data=await api('/api/monthly-generation?plant_key='+encodeURIComponent(active.plantKey)+'&month='+encodeURIComponent(productionMonth())+'&year='+encodeURIComponent(productionYear()));renderProductionBars(data.month||'Month',(data.days||[]).map(d=>({label:d.date,short:String(d.day),value:d.generation})),Number(data.total||0),'kWh');return}const data=await api('/api/history?plant_key='+encodeURIComponent(active.plantKey));if(productionMode==='year'){const selectedYear=String(productionYear());const monthly={};(data.daily||[]).forEach(row=>{if(String(row.date||'').slice(0,4)===selectedYear){const m=String(row.date).slice(5,7);monthly[m]=(monthly[m]||0)+Number(row.daily||0)}});const items=Array.from({length:12},(_,i)=>{const m=String(i+1).padStart(2,'0');return {label:`${monthName(i+1)} ${selectedYear}`,short:monthName(i+1),value:monthly[m]||0}});renderProductionBars(selectedYear,items,items.reduce((a,x)=>a+Number(x.value||0),0),'kWh');return}const items=(data.yearly||[]).map(row=>({label:String(row.year),short:String(row.year),value:Number(row.yearKwh||0)}));renderProductionBars('Total',items,items.reduce((a,x)=>a+Number(x.value||0),0),'kWh')}catch(error){selectedTodayChartEl.innerHTML='<div class="empty-history">Graph failed: '+h(error.message)+'</div>'}}
async function loadActivePlantCharts(active){selectedTodayChartCardEl.classList.toggle('hidden',!active);if(!active){activeTodayChartKey='';selectedTodayTotalEl.textContent='';productionDatePickEl.value=productionDateValue();selectedTodayChartEl.innerHTML='<div class="empty-history">Click a plant name.</div>';return}loadProductionChart(active)}
function historyTable(title, rows, cols, open=false){if(!rows?.length)return `<details class="fold history-block"><summary>${title}</summary><div class="empty-history">No previous data yet. It will build after refreshes/uploads.</div></details>`;return `<details class="fold history-block" ${open?'open':''}><summary>${title}</summary><div class="history-scroll"><table><thead><tr>${cols.map(c=>`<th>${c[0]}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${cols.map(c=>`<td>${c[2]?f(r[c[1]]):h(r[c[1]])}</td>`).join('')}</tr>`).join('')}</tbody></table></div></details>`}
function opt(rows,key){return (rows||[]).map((r,i)=>`<option value="${i}">${h(r[key]||'')}</option>`).join('')}
function pickedLine(type,row,fallback=''){if(type==='day'){const item=row||{date:fallback||todayText(),daily:0,status:'No data'};return `${h(item.date)} · ${f(item.daily)} kWh · ${h(item.status)}`}if(!row)return '0.00 kWh · No data for this selection.';if(type==='week')return `${h(row.week)} · ${f(row.weekly || row.dailySum)} kWh`;return `${h(row.year)} · ${f(row.yearKwh)} kWh · latest ${h(row.lastDate)}`}
function renderPickedHistory(data){const pickedDate=document.querySelector('#dailyDatePick')?.value||todayText();const dayRow=(data.daily||[]).find(r=>r.date===pickedDate);const weekIndex=Number(document.querySelector('#weekPick')?.value||0);const yearIndex=Number(document.querySelector('#yearPick')?.value||0);document.querySelector('#pickedHistory').innerHTML=`<div class="picked"><b>Daily</b><br>${pickedLine('day',dayRow,pickedDate)}</div><div class="picked"><b>Week</b><br>${pickedLine('week',(data.weekly||[])[weekIndex])}</div><div class="picked"><b>Year</b><br>${pickedLine('year',(data.yearly||[])[yearIndex])}</div>`}
function wireHistoryPickers(data){const d=document.querySelector('#dailyDatePick'), w=document.querySelector('#weekPick'), y=document.querySelector('#yearPick');[d,w,y].forEach(el=>{if(el)el.onchange=()=>renderPickedHistory(data)});renderPickedHistory(data)}
function renderHistory(data){const daily=data.daily||[], weekly=data.weekly||[], yearly=data.yearly||[];const latestDay=todayText();return `<details class="fold history-block" open><summary>Past History</summary><div class="history-picker"><div><label>Date</label><input id="dailyDatePick" type="date" value="${h(latestDay)}"></div><div><label>Week</label><select id="weekPick">${opt(weekly,'week')}</select></div><div><label>Year</label><select id="yearPick">${opt(yearly,'year')}</select></div></div><div id="pickedHistory"></div></details>`+[
historyTable('All Daily',daily,[['Date','date'],['Daily kWh','daily',1],['Status','status']],false),
historyTable('All Weekly',weekly,[['Week','week'],['Daily Sum','dailySum',1],['Weekly kWh','weekly',1]],false),
historyTable('All Yearly',yearly,[['Year','year'],['Year kWh','yearKwh',1],['Latest Date','lastDate']],false)
].join('')}
async function loadHistory(active){const key=active?.plantKey||'';activeHistoryKey=key;const box=document.querySelector('#historyBox');if(!box||!key)return;box.innerHTML='<div class="empty-history">Loading previous data...</div>';try{const data=await api('/api/history?plant_key='+encodeURIComponent(key));if(activeHistoryKey===key){box.innerHTML=renderHistory(data);wireHistoryPickers(data)}}catch(error){if(activeHistoryKey===key)box.innerHTML='<div class="empty-history">History failed: '+h(error.message)+'</div>';}}
function renderDetail(active){if(!active){detailEl.innerHTML='<div class="empty-history">Tap a plant name to view details.</div>';return}detailEl.innerHTML=`<div class="plant-title">${h(active.site)}</div><p>${h(active.brand)} · <span class="status ${cls(active.status)}">${h(active.status)}</span></p>${staleNote(active)?`<p class="stale">${h(staleNote(active))}</p>`:''}<div class="details"><div class="detail"><span>Data Date</span><b>${h(active.dataDate||'Unknown')}</b></div><div class="detail"><span>Capacity</span><b>${f(active.capacity)} kW</b></div><div class="detail"><span>Daily</span><b>${f(active.daily)} kWh</b></div><div class="detail"><span>Weekly</span><b>${f(active.weekly)} kWh</b></div><div class="detail"><span>Yearly</span><b>${f(active.year)} kWh</b></div><div class="detail"><span>CUF</span><b>${f(active.cuf)}%</b></div><div class="detail"><span>Total</span><b>${f(active.total)} MWh</b></div></div><div id="historyBox" class="history-block"></div>`;loadHistory(active)}
function renderSelectedPlantDetails(rows){selectedPlantDetailsEl.innerHTML=rows.length?rows.map(p=>`<div class="selected-card ${cls(p.status)}"><b>${h(p.site)}</b><span>Capacity: ${f(p.capacity)} kW</span><span>Current Power: ${f(p.currentPower)} kW</span><span>Today's Generation: ${f(p.daily)} kWh</span><span>Status: ${h(p.status)}</span><span>Last Updated: ${h(p.timestamp||p.dataDate||'Unavailable')}</span></div>`).join(''):'<div class="empty-history">Click a plant name to see details and graphs.</div>'}
function render(){const rows=filtered(), chosen=selectedRows();let active=plants.find(p=>p.id===activePlantId);if(active && !rows.some(p=>p.id===active.id)){activePlantId=null;active=null}cardsEl.innerHTML=[['Visible',rows.length],['Selected',chosen.length],['Daily',f(rows.reduce((a,p)=>a+p.daily,0))+' kWh'],['Weekly',f(rows.reduce((a,p)=>a+p.weekly,0))+' kWh'],['Yearly',f(rows.reduce((a,p)=>a+p.year,0))+' kWh'],['CUF',f(weightedCuf(rows))+' %']].map(x=>`<div class="card"><span>${x[0]}</span><strong>${x[1]}</strong></div>`).join('');
renderPerKw(rows);renderSelectedPlantDetails(active?[active]:[]);loadActivePlantCharts(active);loadMonthlyChart(rows);
rowsEl.innerHTML=rows.map(p=>`<tr data-id="${p.id}" style="cursor:pointer"><td data-label=""><input type="checkbox" data-id="${p.id}" ${selected.has(p.id)?'checked':''}></td><td data-label="Brand">${h(p.brand)}</td><td data-label="Plant"><span class="plant-line ${cls(p.status)}"><b>${h(p.site)}</b><span class="plant-daily">${f(p.daily)} kWh</span></span></td><td data-label="Status" class="status ${cls(p.status)}">${h(p.status)}</td><td data-label="Date" title="${h(staleNote(p))}">${h(p.dataDate||'')} <span class="pill ${pillClass(p)}">${pillText(p)}</span></td><td data-label="Daily">${f(p.daily)}</td><td data-label="Weekly">${f(p.weekly)}</td><td data-label="Yearly">${f(p.year)}</td><td data-label="CUF">${f(p.cuf)}%</td></tr>`).join('');
rowsEl.querySelectorAll('tr[data-id]').forEach(tr=>{if(tr.dataset.id===activePlantId)tr.classList.add('open');tr.onclick=()=>{activePlantId=tr.dataset.id===activePlantId?null:tr.dataset.id;render()}});
rowsEl.querySelectorAll('input[type=checkbox][data-id]').forEach(cb=>{cb.onclick=e=>e.stopPropagation();cb.onchange=()=>{cb.checked?selected.add(cb.dataset.id):selected.delete(cb.dataset.id);render()}});
renderDetail(active);
}
function refreshText(r){const lines=(r.steps||[]).map(s=>`${s.ok?'OK':'SKIP'} - ${s.label}: ${s.message||''}`);if(r.running)lines.push('RUNNING - Refresh still in progress...');if(r.finished)lines.push('DONE - Finished '+r.finished);return lines.join('\\n')||'Ready.'}
async function loadReports(){try{const r=await api('/api/reports');reportListEl.innerHTML=(r.reports||[]).length?(r.reports||[]).map(x=>`<a class="report-item" href="${x.url}">${h(x.name)}<span>${h(x.modified)} · ${h(x.size_kb)} KB</span></a>`).join(''):'No reports generated yet.';}catch(error){reportListEl.textContent='Could not load reports: '+error.message;}}
async function triggerOpenRefresh(){if(openRefreshStarted)return;openRefreshStarted=true;return startRefresh('open')}
function applyStatus(s){statusData=s||{};dateLineEl.textContent=istNowText();versionLineEl.textContent='Build: '+(statusData.app_version||'old');mobileLineEl.textContent='iPhone: '+(statusData.mobile_url||'');lastUpdatedEl.textContent=backendUpdatedText(statusData);if(statusData.config){autoDayEl.value=statusData.config.auto_report_day||autoDayEl.value;autoTimeEl.value=statusData.config.auto_report_time||autoTimeEl.value}applyRoleUi();logEl.textContent=refreshText(statusData.last_refresh||{})}
function applyPlants(nextPlants,opts={}){const keep=new Set(selected);plants=nextPlants||[];if(opts.preserveSelection!==false){const ids=new Set(plants.map(p=>p.id));selected=new Set([...keep].filter(id=>ids.has(id)))}else{selected=new Set()}renderFilters();render()}
async function load(opts={}){const p=await api('/api/plants');applyPlants(p.plants||[],opts);const s=await api('/api/status');applyStatus(s);if(opts.reports!==false)loadReports();const staleRows=staleOnlineRows();if(opts.openCheck!==false && s.config?.auto_refresh_on_open && !openRefreshStarted){if(staleRows.length)setWarning(`${staleRows.length} plants show previous-day data. Refreshing latest data now...`);setTimeout(triggerOpenRefresh,50);}}
async function pollRefresh(){let finalStatus={};for(let i=0;i<90;i++){const s=await api('/api/status');finalStatus=s.last_refresh||{};logEl.textContent=refreshText(finalStatus);await load({preserveSelection:true,reports:false});if(!finalStatus.running)return finalStatus;await new Promise(r=>setTimeout(r,3000));}return finalStatus}
async function startRefresh(reason='manual'){const role=String(statusData.user?.role||'admin').toLowerCase();const canRefresh=role==='admin'||role==='manager';if(!canRefresh){if(reason==='manual')setWarning('Your user role can view data but cannot refresh inverter clouds.');return}if(refreshInFlight)return;if(reason==='auto' && document.hidden)return;refreshInFlight=true;setLoading(true);setWarning('');try{logEl.textContent='Starting background refresh...';const r=await api('/api/refresh',{method:'POST'});logEl.textContent=refreshText(r);const finalStatus=(r.accepted||r.running)?await pollRefresh():r;const warn=refreshWarning(finalStatus);if(warn)setWarning(warn);await load({preserveSelection:true,reports:reason!=='auto'});}catch(error){setWarning('Live refresh failed. Last successful data is still shown. '+error.message);logEl.textContent='Refresh failed: '+error.message;}finally{setLoading(false);refreshInFlight=false;}}
function startAutoRefresh(){if(autoRefreshTimer)clearInterval(autoRefreshTimer);autoRefreshTimer=setInterval(()=>startRefresh('auto'),60000)}
refreshBtn.onclick=()=>startRefresh('manual');
async function generateReport(ids,label,all=false){reportResultEl.textContent='Generating '+label+'...';const r=await api('/api/report',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({plant_ids:ids,all_plants:all})});reportResultEl.innerHTML=r.ok?`Saved ${r.count} plant report.<br><a class="download-btn" href="${r.viewer_url}">Open Report</a>`:'Failed: '+h(r.message);if(r.ok)loadReports();}
reportAllBtn.onclick=()=>generateReport([],'all plants report',true);
reportPlantBtn.onclick=()=>{if(!activePlantId){reportResultEl.textContent='Tap a plant name first.';return}generateReport([activePlantId],'plant report')}
reportBtn.onclick=()=>{if(!selected.size){reportResultEl.textContent='Tick one or more plants first.';return}generateReport([...selected],'selected report')};
selectAllBtn.onclick=()=>{const visible=filtered();const all=visible.every(p=>selected.has(p.id));visible.forEach(p=>all?selected.delete(p.id):selected.add(p.id));render()}
saveScheduleBtn.onclick=async()=>{const r=await api('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({auto_report_day:autoDayEl.value,auto_report_time:autoTimeEl.value})});logEl.textContent='Saved schedule: '+r.config.auto_report_day+' '+r.config.auto_report_time}
perKwChartCardEl.onclick=()=>window.open('/chart-detail?'+chartQuery('perkw',filtered()),'_blank');
monthlyChartCardEl.onclick=()=>window.open('/chart-detail?'+chartQuery('monthly',filtered()),'_blank');
selectedTodayChartCardEl.onclick=e=>{if(e.target.closest('.production-footer'))return;const active=plants.find(p=>p.id===activePlantId);if(!active)return;let query='type='+(productionMode==='month'?'monthly':'today')+'&plant_key='+encodeURIComponent(active.plantKey);if(productionMode==='month')query+='&month='+encodeURIComponent(productionMonth())+'&year='+encodeURIComponent(productionYear());else query+='&date='+encodeURIComponent(productionDateValue());window.open('/chart-detail?'+query,'_blank')};
productionTabsEl.querySelectorAll('button').forEach(btn=>{btn.onclick=e=>{e.stopPropagation();setProductionMode(btn.dataset.mode);activeTodayChartKey='';render()}});
productionDatePickEl.onchange=e=>{productionDate=e.target.value||todayText();activeTodayChartKey='';if(productionMode==='month'||productionMode==='year'){monthSelectEl.value=String(productionMonth());yearSelectEl.value=String(productionYear())}render()};
monthSelectEl.onchange=()=>{monthlyChartKey='';render()};yearSelectEl.onchange=()=>{monthlyChartKey='';render()};
document.addEventListener('visibilitychange',()=>{if(!document.hidden){load({preserveSelection:true,reports:false}).catch(error=>setWarning('Could not reload dashboard after returning: '+error.message));startRefresh('resume');}});
searchInput.oninput=render;brandFilter.onchange=render;statusFilter.onchange=render;productionDate=todayText();productionDatePickEl.value=productionDate;setupDateSelectors();const boot=window.__BOOTSTRAP__||{};if(Array.isArray(boot.plants)){applyPlants(boot.plants,{preserveSelection:false});if(boot.status)applyStatus(boot.status)}startAutoRefresh();load({preserveSelection:true}).catch(error=>{setWarning('App load failed. Showing last loaded data. '+error.message);logEl.textContent='App load failed: '+error;});
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
