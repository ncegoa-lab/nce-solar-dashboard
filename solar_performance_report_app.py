#!/usr/bin/env python3
"""
Solar Plant Performance PDF Report application.

The app accepts JSON, CSV, API, or the current local GOODWE project data and
generates a professional multi-page PDF with summaries, tables, charts, best
plant analysis, status distribution, and automated recommendations.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import math
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo
from xml.sax.saxutils import escape

import pandas as pd
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image as RLImage,
    KeepTogether,
    LongTable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    HAS_MATPLOTLIB = True
except Exception:  # pragma: no cover - exercised only when matplotlib is absent.
    plt = None
    HAS_MATPLOTLIB = False


APP_NAME = "Solar Plant Performance Report"
LOGGER = logging.getLogger("solar_report")
OUTPUT_DIR = Path("outputs/solar_performance")
DEFAULT_LOGO = Path("assets/nce_logo.png")
REPORT_YEAR_START = dt.date(2026, 1, 1)
IST = ZoneInfo("Asia/Kolkata")


def ist_now() -> dt.datetime:
    return dt.datetime.now(IST)


def ist_today() -> dt.date:
    return ist_now().date()

BLUE = "#1F63B5"
GREEN = "#18B9D6"
LIGHT_GREEN = "#E8F8FB"
LIGHT_BLUE = "#EEF6FF"
GRID = "#C8D5E6"
TEXT = "#253247"
MUTED = "#6D7480"
ORANGE = "#F2994A"
RED = "#D64545"
YELLOW = "#F2C94C"


def report_elapsed_days(today: dt.date | None = None) -> int:
    """Return inclusive days elapsed from the fixed 2026 report baseline."""

    report_date = today or ist_today()
    return max((report_date - REPORT_YEAR_START).days + 1, 1)


def completed_week_days(today: dt.date | None = None) -> int:
    """Return completed days in the current Monday-Sunday reporting week."""

    report_date = today or ist_today()
    return min(report_date.weekday() + 1, 7)


@dataclass(frozen=True)
class ReportAssets:
    """File paths for generated charts and the final PDF."""

    chart_dir: Path
    charts: dict[str, Path]
    status_chart: Path | None
    pdf_path: Path


def setup_logging() -> None:
    """Configure compact application logging."""

    class ISTFormatter(logging.Formatter):
        def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
            value = dt.datetime.fromtimestamp(record.created, IST)
            return value.strftime(datefmt or "%Y-%m-%d %H:%M:%S")

    handler = logging.StreamHandler()
    handler.setFormatter(ISTFormatter("%(asctime)s | %(levelname)s | %(message)s", "%H:%M:%S"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def safe_float(value: Any, default: float = 0.0) -> float:
    """Convert values such as '--', None, strings, and numbers into floats."""

    if value is None or value == "":
        return default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if math.isnan(value):
            return default
        return float(value)
    text = str(value).replace(",", "").strip()
    if text in {"", "--", "None", "nan", "NaN"}:
        return default
    try:
        return float(text)
    except ValueError:
        match = re.search(r"-?\d+(?:,\d{3})*(?:\.\d+)?", text)
        return float(match.group(0).replace(",", "")) if match else default


def first_available(row: dict[str, Any], keys: Iterable[str], default: Any = None) -> Any:
    """Return the first non-empty value from possible field names."""

    for key in keys:
        value = row.get(key)
        if value not in (None, "", "--"):
            return value
    return default


def parse_vendor_timestamp(value: Any, fallback: Any = None) -> str:
    """Normalize common portal timestamp formats to an ISO-like string."""

    text = str(value or "").strip()
    if not text:
        return str(fallback or "")

    cleaned = re.sub(r"\s*\(.*?\)\s*$", "", text).strip()
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return dt.datetime.strptime(cleaned, fmt).isoformat(timespec="seconds")
        except ValueError:
            continue
    return text


def normalize_status(value: Any) -> str:
    """Map vendor-specific status text into Online, Offline, Warning, or Fault."""

    text = str(value or "").strip().lower()
    if text in {"1", "true"}:
        return "Online"
    if text in {"-1", "0", "false"}:
        return "Offline"
    if any(token in text for token in ("fault", "failure", "error", "alarm")):
        return "Fault"
    if any(token in text for token in ("warn", "partial")):
        return "Warning"
    if "offline" in text or "disconnected" in text:
        return "Offline"
    if any(token in text for token in ("online", "normal", "active", "on-grid")):
        return "Online"
    return "Online" if not text else str(value)


def normalize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize all accepted input formats into one report schema."""

    records: list[dict[str, Any]] = []
    for raw in df.fillna("").to_dict(orient="records"):
        total_kwh = first_available(
            raw,
            (
                "Total Generation (kWh)",
                "total_generation_kwh",
                "Total Generation kWh",
            ),
        )
        total_mwh = first_available(
            raw,
            (
                "Total Generation (MWh)",
                "Total Generation MWh",
                "total_generation_mwh",
            ),
        )
        total_mwh_value = safe_float(total_mwh, default=None) if total_mwh not in (None, "") else None
        if total_mwh_value is None:
            total_mwh_value = safe_float(total_kwh) / 1000.0

        capacity_kw = safe_float(
            first_available(
                raw,
                (
                    "Plant Capacity (kW)",
                    "Plant Capacity (kW/MW)",
                    "Plant Capacity",
                    "Capacity (kW)",
                    "capacity_kw",
                    "capacity",
                ),
            )
        )
        daily_kwh = safe_float(
            first_available(raw, ("Daily Generation (kWh)", "Today Generation (kWh)", "today_generation_kwh"))
        )
        weekly_kwh = safe_float(
            first_available(raw, ("Weekly Generation (kWh)", "weekly_generation_kwh"))
        )
        year_kwh = safe_float(
            first_available(raw, ("Year Generation (kWh)", "year_generation_kwh", "YTD Generation (kWh)"))
        )
        current_power_kw = safe_float(
            first_available(raw, ("Current Power (kW)", "current_power_kw", "currentPower", "Current Power"))
        )
        year_source = "Portal YTD" if year_kwh > 0 else "Unavailable"
        if year_kwh <= 0 and weekly_kwh > 0:
            year_kwh = weekly_kwh / completed_week_days() * report_elapsed_days()
            year_source = "Estimated from week"

        current_status = normalize_status(first_available(raw, ("Current Status", "Status", "status")))
        if current_status not in {"Online", "Offline", "Warning", "Fault"}:
            current_status = "Online" if daily_kwh > 0 else "Offline"

        records.append(
            {
                "Brand": str(first_available(raw, ("Brand", "brand"), "Unknown")),
                "Site Name": str(first_available(raw, ("Site Name", "System Name", "name", "station_name"), "Unnamed Site")),
                "Plant Capacity (kW)": capacity_kw,
                "Current Status": current_status,
                "Daily Generation (kWh)": daily_kwh,
                "Weekly Generation (kWh)": weekly_kwh,
                "Year Generation (kWh)": year_kwh,
                "Current Power (kW)": current_power_kw,
                "Year Generation Source": year_source,
                "Total Generation (MWh)": total_mwh_value,
                "Timestamp": str(first_available(raw, ("Timestamp", "timestamp", "captured_at"), ist_now().isoformat())),
            }
        )

    normalized = pd.DataFrame.from_records(records)
    if normalized.empty:
        raise ValueError("No plant rows found in the input data.")

    normalized["Specific Yield (kWh/kWp)"] = normalized.apply(
        lambda row: row["Daily Generation (kWh)"] / row["Plant Capacity (kW)"]
        if row["Plant Capacity (kW)"] > 0
        else 0,
        axis=1,
    )
    normalized["2026 Yield (kWh/kW)"] = normalized.apply(
        lambda row: row["Year Generation (kWh)"] / row["Plant Capacity (kW)"]
        if row["Year Generation (kWh)"] > 0 and row["Plant Capacity (kW)"] > 0
        else 0,
        axis=1,
    )
    normalized["Average Daily Yield (kWh/kW/day)"] = normalized["2026 Yield (kWh/kW)"] / report_elapsed_days()
    normalized["CUF (%)"] = normalized.apply(
        lambda row: (row["Year Generation (kWh)"] / (row["Plant Capacity (kW)"] * 24 * report_elapsed_days()) * 100)
        if row["Year Generation (kWh)"] > 0 and row["Plant Capacity (kW)"] > 0
        else ((row["Daily Generation (kWh)"] / (row["Plant Capacity (kW)"] * 24) * 100) if row["Plant Capacity (kW)"] > 0 else 0),
        axis=1,
    )
    normalized["PR (%)"] = normalized["CUF (%)"].clip(lower=0, upper=100)
    return normalized


def load_data(input_path: str | None = None, api_url: str | None = None, current_project: bool = False) -> pd.DataFrame:
    """Load plant data from JSON, CSV, API endpoint, or local project files."""

    if api_url:
        try:
            import requests
        except Exception as exc:
            raise RuntimeError("API input requires the requests package.") from exc
        LOGGER.info("Loading plant data from API: %s", api_url)
        response = requests.get(api_url, timeout=30)
        response.raise_for_status()
        payload = response.json()
        data = payload.get("plants", payload) if isinstance(payload, dict) else payload
        return normalize_dataframe(pd.DataFrame(data))

    if current_project:
        LOGGER.info("Loading plant data from local GOODWE project JSON files")
        return normalize_dataframe(pd.DataFrame(_load_current_project_rows()))

    if not input_path:
        raise ValueError("Provide --input, --api-url, or --current-project.")

    path = Path(input_path)
    LOGGER.info("Loading plant data from %s", path)
    if path.suffix.lower() == ".csv":
        return normalize_dataframe(pd.read_csv(path))
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        data = payload.get("plants", payload.get("systems", payload)) if isinstance(payload, dict) else payload
        return normalize_dataframe(pd.DataFrame(data))
    raise ValueError(f"Unsupported input format: {path.suffix}")


def _read_json(path: str) -> Any:
    file_path = Path(path)
    return json.loads(file_path.read_text(encoding="utf-8")) if file_path.exists() else None


def _capacity_from_name(name: str | None) -> float:
    import re

    match = re.search(r"(\d+(?:\.\d+)?)\s*k\s*w", name or "", re.IGNORECASE)
    return float(match.group(1)) if match else 0.0


def _load_current_project_rows() -> list[dict[str, Any]]:
    """Collect all currently captured vendor data into one flat list."""

    rows: list[dict[str, Any]] = []
    goodwe = _read_json("sems_station_data.json") or {}
    goodwe_weekly = _read_json("sems_weekly_generation.json") or {}
    weekly_by_id = {item.get("station_id"): item for item in goodwe_weekly.get("stations", [])}
    for station in goodwe.get("stations", []):
        weekly = weekly_by_id.get(station.get("powerstation_id"), {})
        rows.append(
            {
                "Brand": "GoodWe",
                "Site Name": station.get("stationname"),
                "Plant Capacity (kW)": station.get("capacity"),
                "Current Status": station.get("status"),
                "Daily Generation (kWh)": station.get("eday"),
                "Weekly Generation (kWh)": weekly.get("weekly_generation_kwh"),
                "Year Generation (kWh)": weekly.get("year_generation_kwh"),
                "Current Power (kW)": station.get("power") or station.get("pac") or station.get("current_power_kw"),
                "Total Generation (kWh)": station.get("etotal"),
                "Timestamp": station.get("date") or goodwe.get("generated_at"),
            }
        )

    fronius_weekly = _read_json("fronius_weekly_generation.json") or {}
    fronius_current = _read_json("fronius_current_generation.json") or {}
    current_by_id = {item.get("system_id"): item for item in fronius_current.get("systems", [])}
    for system in fronius_weekly.get("systems", []):
        current = current_by_id.get(system.get("system_id"), {})
        rows.append(
            {
                "Brand": "Fronius",
                "Site Name": system.get("name"),
                "Plant Capacity (kW)": _capacity_from_name(system.get("name")),
                "Current Status": system.get("status"),
                "Daily Generation (kWh)": current.get("today_generation_kwh"),
                "Weekly Generation (kWh)": system.get("weekly_generation_kwh"),
                "Year Generation (kWh)": system.get("year_generation_kwh"),
                "Current Power (kW)": current.get("current_power_kw") or current.get("current_power"),
                "Total Generation (kWh)": current.get("total_generation_kwh"),
                "Timestamp": current.get("date") or fronius_current.get("date") or fronius_current.get("generated_at"),
            }
        )

    fimer = _read_json("fimer_generation.json") or {}
    for item in fimer.get("plantEnergy", []):
        plant = item.get("plant", {})

        def energy_value(key: str) -> float:
            value = item.get("values", {}).get(key, {})
            body = value.get("body", [])
            return body[0].get("value") if value.get("status") == 200 and body else 0

        rows.append(
            {
                "Brand": "FIMER",
                "Site Name": plant.get("name"),
                "Plant Capacity (kW)": plant.get("configuration", {}).get("panelsNominalPower"),
                "Current Status": plant.get("state"),
                "Daily Generation (kWh)": energy_value("today"),
                "Weekly Generation (kWh)": energy_value("week"),
                "Year Generation (kWh)": energy_value("year"),
                "Current Power (kW)": plant.get("power") or plant.get("currentPower") or plant.get("current_power_kw"),
                "Total Generation (kWh)": energy_value("total"),
                "Timestamp": fimer.get("generatedAt") or fimer.get("now"),
            }
        )

    for brand, path in (("Solis", "solis_generation.json"), ("SolaX", "solax_generation.json")):
        payload = _read_json(path) or {}
        live_count = (payload.get("api_status") or {}).get("live_record_count")
        for system in payload.get("systems", []):
            if brand == "SolaX" and live_count == 0:
                timestamp = payload.get("captured_at") or payload.get("generated_at") or payload.get("uploaded_at")
            else:
                timestamp = payload.get("uploaded_at") or payload.get("generated_at") or payload.get("captured_at")
            rows.append(
                {
                    "Brand": brand,
                    "Site Name": system.get("name") or system.get("station_name"),
                    "Plant Capacity (kW)": system.get("capacity_kw") or system.get("capacity"),
                    "Current Status": system.get("status"),
                    "Daily Generation (kWh)": system.get("today_generation_kwh"),
                    "Weekly Generation (kWh)": system.get("weekly_generation_kwh"),
                    "Year Generation (kWh)": system.get("year_generation_kwh"),
                    "Current Power (kW)": system.get("current_power_kw") or system.get("current_power"),
                    "Total Generation (kWh)": system.get("total_generation_kwh"),
                    "Timestamp": timestamp,
                }
            )
    if not rows:
        raise RuntimeError("No local project plant data files were found.")
    return rows


def calculate_summary(df: pd.DataFrame) -> dict[str, float]:
    """Calculate portfolio-level report metrics."""

    status_counts = df["Current Status"].value_counts().to_dict()
    capacity_kw = float(df["Plant Capacity (kW)"].sum())
    year_kwh = float(df["Year Generation (kWh)"].sum())
    cuf_percent = year_kwh / (capacity_kw * 24 * report_elapsed_days()) * 100 if capacity_kw > 0 and year_kwh > 0 else 0.0
    return {
        "total_plants": int(len(df)),
        "online_plants": int(status_counts.get("Online", 0)),
        "offline_plants": int(status_counts.get("Offline", 0)),
        "warning_plants": int(status_counts.get("Warning", 0)),
        "fault_plants": int(status_counts.get("Fault", 0)),
        "capacity_kw": capacity_kw,
        "daily_kwh": float(df["Daily Generation (kWh)"].sum()),
        "weekly_kwh": float(df["Weekly Generation (kWh)"].sum()),
        "year_kwh": year_kwh,
        "total_mwh": float(df["Total Generation (MWh)"].sum()),
        "cuf_percent": cuf_percent,
    }


def generate_brand_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Group all plants by brand with totals."""

    summary = (
        df.groupby("Brand", dropna=False)
        .agg(
            **{
                "No. of Plants": ("Site Name", "count"),
                "Installed Capacity (kW)": ("Plant Capacity (kW)", "sum"),
                "Daily Generation (kWh)": ("Daily Generation (kWh)", "sum"),
                "Weekly Generation (kWh)": ("Weekly Generation (kWh)", "sum"),
                "Year Generation (kWh)": ("Year Generation (kWh)", "sum"),
                "Total Generation (MWh)": ("Total Generation (MWh)", "sum"),
            }
        )
        .reset_index()
        .sort_values("Brand")
    )
    summary["CUF (%)"] = summary.apply(
        lambda row: row["Year Generation (kWh)"] / (row["Installed Capacity (kW)"] * 24 * report_elapsed_days()) * 100
        if row["Installed Capacity (kW)"] > 0 and row["Year Generation (kWh)"] > 0
        else 0,
        axis=1,
    )
    totals = {
        "Brand": "TOTAL",
        "No. of Plants": int(summary["No. of Plants"].sum()),
        "Installed Capacity (kW)": summary["Installed Capacity (kW)"].sum(),
        "Daily Generation (kWh)": summary["Daily Generation (kWh)"].sum(),
        "Weekly Generation (kWh)": summary["Weekly Generation (kWh)"].sum(),
        "Year Generation (kWh)": summary["Year Generation (kWh)"].sum(),
        "Total Generation (MWh)": summary["Total Generation (MWh)"].sum(),
    }
    totals["CUF (%)"] = (
        totals["Year Generation (kWh)"] / (totals["Installed Capacity (kW)"] * 24 * report_elapsed_days()) * 100
        if totals["Installed Capacity (kW)"] > 0 and totals["Year Generation (kWh)"] > 0
        else 0
    )
    return pd.concat([summary, pd.DataFrame([totals])], ignore_index=True)


def calculate_best_performing(df: pd.DataFrame) -> tuple[pd.Series, pd.DataFrame]:
    """Find the highest daily-generation plant and top five plants."""

    ordered = df.sort_values("CUF (%)", ascending=False).reset_index(drop=True)
    if ordered.empty:
        raise ValueError("Cannot calculate best plant from empty data.")
    return ordered.iloc[0], ordered.head(5)


def _site_labels(df: pd.DataFrame) -> list[str]:
    return [str(name)[:24] for name in df["Site Name"].tolist()]


def _save_matplotlib_chart(kind: str, df: pd.DataFrame, brand_summary: pd.DataFrame, output: Path) -> None:
    """Create a chart with Matplotlib when available."""

    plt.style.use("seaborn-v0_8-whitegrid")
    fig, ax = plt.subplots(figsize=(10.5, 5.6), dpi=180)
    if kind == "daily_bar":
        top = df.sort_values("Daily Generation (kWh)", ascending=False).head(20)
        ax.bar(_site_labels(top), top["Daily Generation (kWh)"], color=GREEN, label="Daily kWh")
        ax.set_ylabel("kWh")
        ax.set_xlabel("Plant")
        ax.set_title("Daily Generation by Plant")
        ax.tick_params(axis="x", rotation=70, labelsize=7)
        ax.legend()
    elif kind == "weekly_barh":
        top = df.sort_values("Weekly Generation (kWh)", ascending=True).tail(20)
        ax.barh(_site_labels(top), top["Weekly Generation (kWh)"], color=BLUE, label="Weekly kWh")
        ax.set_xlabel("kWh")
        ax.set_ylabel("Plant")
        ax.set_title("Weekly Generation by Plant")
        ax.legend()
    elif kind == "total_line":
        top = df.sort_values("Total Generation (MWh)", ascending=False).head(20)
        ax.plot(_site_labels(top), top["Total Generation (MWh)"], color=GREEN, marker="o", label="Total MWh")
        ax.set_ylabel("MWh")
        ax.set_xlabel("Plant")
        ax.set_title("Total Generation by Plant")
        ax.tick_params(axis="x", rotation=70, labelsize=7)
        ax.legend()
    elif kind == "brand_pie":
        brands = brand_summary[brand_summary["Brand"] != "TOTAL"]
        ax.pie(
            brands["Daily Generation (kWh)"],
            labels=brands["Brand"],
            autopct="%1.1f%%",
            startangle=140,
            colors=[GREEN, BLUE, "#4F8BC9", "#68B984", "#9AD0C2", ORANGE, YELLOW],
        )
        ax.set_title("Brand-wise Daily Generation Share")
    elif kind == "capacity_scatter":
        ax.scatter(df["Plant Capacity (kW)"], df["Daily Generation (kWh)"], s=55, color=GREEN, alpha=0.75, label="Plants")
        ax.set_xlabel("Capacity (kW)")
        ax.set_ylabel("Daily Generation (kWh)")
        ax.set_title("Capacity vs Daily Generation")
        ax.legend()
    elif kind == "status_donut":
        counts = df["Current Status"].value_counts()
        ax.pie(
            counts.values,
            labels=counts.index,
            autopct="%1.0f%%",
            startangle=90,
            wedgeprops={"width": 0.42},
            colors=[GREEN, RED, ORANGE, YELLOW, MUTED],
        )
        ax.set_title("Plant Status Distribution")
    fig.tight_layout()
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def _save_fallback_chart(kind: str, df: pd.DataFrame, brand_summary: pd.DataFrame, output: Path) -> None:
    """Draw simple high-resolution charts when Matplotlib is not installed."""

    width, height = 1800, 980
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()

    def title(text: str) -> None:
        draw.rectangle((0, 0, width, 88), fill=BLUE)
        draw.text((40, 30), text, fill="white", font=font)

    def axes() -> tuple[int, int, int, int]:
        left, top, right, bottom = 160, 130, width - 80, height - 150
        draw.line((left, top, left, bottom), fill=GRID, width=3)
        draw.line((left, bottom, right, bottom), fill=GRID, width=3)
        return left, top, right, bottom

    if kind == "daily_bar":
        title("Daily Generation by Plant")
        left, top, right, bottom = axes()
        top_df = df.sort_values("Daily Generation (kWh)", ascending=False).head(18)
        max_value = max(float(top_df["Daily Generation (kWh)"].max()), 1)
        bar_w = max(18, int((right - left) / max(len(top_df), 1) * 0.65))
        for idx, (_, row) in enumerate(top_df.iterrows()):
            value = float(row["Daily Generation (kWh)"])
            x = left + idx * int((right - left) / max(len(top_df), 1)) + 10
            y = bottom - int((value / max_value) * (bottom - top))
            draw.rectangle((x, y, x + bar_w, bottom), fill=GREEN)
            draw.text((x, bottom + 12), str(row["Site Name"])[:12], fill=TEXT, font=font)
    elif kind == "weekly_barh":
        title("Weekly Generation by Plant")
        left, top, right, bottom = axes()
        top_df = df.sort_values("Weekly Generation (kWh)", ascending=True).tail(15)
        max_value = max(float(top_df["Weekly Generation (kWh)"].max()), 1)
        row_h = max(28, int((bottom - top) / max(len(top_df), 1) * 0.65))
        for idx, (_, row) in enumerate(top_df.iterrows()):
            value = float(row["Weekly Generation (kWh)"])
            y = top + idx * int((bottom - top) / max(len(top_df), 1)) + 8
            x2 = left + int((value / max_value) * (right - left))
            draw.text((20, y), str(row["Site Name"])[:22], fill=TEXT, font=font)
            draw.rectangle((left, y, x2, y + row_h), fill=BLUE)
    elif kind == "total_line":
        title("Total Generation by Plant")
        left, top, right, bottom = axes()
        top_df = df.sort_values("Total Generation (MWh)", ascending=False).head(20)
        max_value = max(float(top_df["Total Generation (MWh)"].max()), 1)
        points = []
        for idx, (_, row) in enumerate(top_df.iterrows()):
            value = float(row["Total Generation (MWh)"])
            x = left + int(idx * (right - left) / max(len(top_df) - 1, 1))
            y = bottom - int((value / max_value) * (bottom - top))
            points.append((x, y))
        if len(points) > 1:
            draw.line(points, fill=GREEN, width=5)
        for point in points:
            draw.ellipse((point[0] - 7, point[1] - 7, point[0] + 7, point[1] + 7), fill=GREEN)
    elif kind in {"brand_pie", "status_donut"}:
        title("Brand-wise Generation Share" if kind == "brand_pie" else "Plant Status Distribution")
        source = (
            brand_summary[brand_summary["Brand"] != "TOTAL"].set_index("Brand")["Daily Generation (kWh)"]
            if kind == "brand_pie"
            else df["Current Status"].value_counts()
        )
        values = source[source > 0] if source.sum() > 0 else source + 1
        colors_list = [GREEN, BLUE, ORANGE, YELLOW, "#68B984", "#4F8BC9"]
        start = 0
        box = (560, 170, 1240, 850)
        total = float(values.sum()) or 1
        for idx, (label, value) in enumerate(values.items()):
            extent = value / total * 360
            draw.pieslice(box, start, start + extent, fill=colors_list[idx % len(colors_list)])
            draw.text((80, 160 + idx * 44), f"{label}: {value:.1f}", fill=TEXT, font=font)
            start += extent
        if kind == "status_donut":
            draw.ellipse((730, 340, 1070, 680), fill="white")
    elif kind == "capacity_scatter":
        title("Capacity vs Daily Generation")
        left, top, right, bottom = axes()
        max_x = max(float(df["Plant Capacity (kW)"].max()), 1)
        max_y = max(float(df["Daily Generation (kWh)"].max()), 1)
        for _, row in df.iterrows():
            x = left + int(float(row["Plant Capacity (kW)"]) / max_x * (right - left))
            y = bottom - int(float(row["Daily Generation (kWh)"]) / max_y * (bottom - top))
            draw.ellipse((x - 9, y - 9, x + 9, y + 9), fill=GREEN)
    image.save(output, quality=95)


def create_charts(df: pd.DataFrame, brand_summary: pd.DataFrame, chart_dir: Path) -> dict[str, Path]:
    """Create the five required performance charts."""

    chart_dir.mkdir(parents=True, exist_ok=True)
    charts = {
        "daily_bar": chart_dir / "daily_generation_by_plant.png",
        "weekly_barh": chart_dir / "weekly_generation_by_plant.png",
        "total_line": chart_dir / "total_generation_by_plant.png",
        "brand_pie": chart_dir / "brand_generation_share.png",
        "capacity_scatter": chart_dir / "capacity_vs_daily_generation.png",
    }
    for kind, path in charts.items():
        if HAS_MATPLOTLIB:
            _save_matplotlib_chart(kind, df, brand_summary, path)
        else:
            _save_fallback_chart(kind, df, brand_summary, path)
    return charts


def _history_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Load saved daily history for report trend charts, with a current-day fallback."""

    history_path = Path("solar_generation_history.json")
    keys = {f"{row['Brand']}::{row['Site Name']}" for _, row in df.iterrows()}
    rows: list[dict[str, Any]] = []
    if history_path.exists():
        try:
            payload = json.loads(history_path.read_text(encoding="utf-8"))
        except Exception:
            payload = []
        for item in payload if isinstance(payload, list) else []:
            if item.get("plantKey") not in keys:
                continue
            try:
                date_value = pd.to_datetime(item.get("date")).date()
            except Exception:
                date_value = None
            if not date_value:
                continue
            rows.append(
                {
                    "date": date_value,
                    "daily": safe_float(item.get("daily")),
                    "capacity": safe_float(item.get("capacity")),
                }
            )

    if not rows:
        today = ist_today()
        for _, row in df.iterrows():
            rows.append(
                {
                    "date": today,
                    "daily": safe_float(row.get("Daily Generation (kWh)")),
                    "capacity": safe_float(row.get("Plant Capacity (kW)")),
                }
            )
    history = pd.DataFrame(rows)
    history["date"] = pd.to_datetime(history["date"])
    return history


def _today_hourly_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Load today's cumulative hourly generation for report area chart."""

    hourly_path = Path("solar_generation_hourly_history.json")
    keys = {f"{row['Brand']}::{row['Site Name']}" for _, row in df.iterrows()}
    today = ist_today()
    rows: list[dict[str, Any]] = []
    if hourly_path.exists():
        try:
            payload = json.loads(hourly_path.read_text(encoding="utf-8"))
        except Exception:
            payload = []
        for item in payload if isinstance(payload, list) else []:
            if item.get("plantKey") not in keys:
                continue
            try:
                row_date = pd.to_datetime(item.get("date")).date()
            except Exception:
                row_date = None
            if row_date != today:
                continue
            hour_label = str(item.get("hourLabel") or str(item.get("hour", ""))[11:16] or "")
            if not hour_label:
                continue
            rows.append({"hour": hour_label, "generation": safe_float(item.get("daily"))})

    if not rows:
        rows.append({"hour": ist_now().strftime("%H:00"), "generation": safe_float(df["Daily Generation (kWh)"].sum())})

    hourly = pd.DataFrame(rows)
    grouped = hourly.groupby("hour", as_index=False)["generation"].sum().sort_values("hour")
    first = grouped[grouped["generation"] > 0].index.min()
    if pd.notna(first):
        grouped = grouped.loc[first:].reset_index(drop=True)
    return grouped


def _save_trend_matplotlib(kind: str, history: pd.DataFrame, output: Path, df: pd.DataFrame | None = None) -> None:
    """Create the three requested report trend charts with Matplotlib."""

    plt.style.use("seaborn-v0_8-whitegrid")
    fig, ax = plt.subplots(figsize=(10.8, 4.0), dpi=190)
    today = pd.Timestamp(ist_today())
    if kind == "today_area":
        hourly = _today_hourly_dataframe(df if df is not None else pd.DataFrame())
        ax.fill_between(hourly["hour"], hourly["generation"], color=GREEN, alpha=0.22)
        ax.plot(hourly["hour"], hourly["generation"], color=BLUE, linewidth=2.3)
        ax.set_title("Today's Generation - Since Start")
        ax.set_xlabel("Hour")
        ax.set_ylabel("kWh")
        ax.tick_params(axis="x", rotation=35, labelsize=7)
    elif kind == "monthly_daywise":
        current = history[(history["date"].dt.year == today.year) & (history["date"].dt.month == today.month)]
        series = current.groupby(history.loc[current.index, "date"].dt.day)["daily"].sum()
        days = list(range(1, today.day + 1))
        values = [float(series.get(day, 0.0)) for day in days]
        ax.bar(days, values, color=GREEN, width=0.68)
        ax.set_title("Monthly Generation - Day Wise")
        ax.set_xlabel("Day")
        ax.set_ylabel("kWh")
        ax.set_xticks(days[:: max(1, len(days) // 12)])
    elif kind == "yearly_monthwise":
        current = history[history["date"].dt.year == today.year]
        series = current.groupby(history.loc[current.index, "date"].dt.month)["daily"].sum()
        months = list(range(1, today.month + 1))
        values = [float(series.get(month, 0.0)) for month in months]
        labels = [dt.date(today.year, month, 1).strftime("%b") for month in months]
        ax.bar(labels, values, color=BLUE, width=0.62)
        ax.set_title("Yearly Generation - Month Wise")
        ax.set_xlabel("Month")
        ax.set_ylabel("kWh")
    else:
        current = history[history["date"].dt.year == today.year]
        grouped = current.groupby("date").agg({"daily": "sum", "capacity": "sum"}).reset_index()
        grouped["per_kw"] = grouped.apply(
            lambda row: row["daily"] / row["capacity"] if row["capacity"] > 0 else 0.0,
            axis=1,
        )
        ax.fill_between(grouped["date"], grouped["per_kw"], color=GREEN, alpha=0.22)
        ax.plot(grouped["date"], grouped["per_kw"], color=BLUE, linewidth=2.2)
        ax.set_title("Per-kW Generation - Year Daily Trend")
        ax.set_xlabel("Date")
        ax.set_ylabel("kWh/kW")
        ax.tick_params(axis="x", rotation=30, labelsize=7)
    ax.grid(True, axis="y", alpha=0.28)
    fig.tight_layout()
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def _save_trend_fallback(kind: str, history: pd.DataFrame, output: Path, df: pd.DataFrame | None = None) -> None:
    """Fallback trend chart renderer when Matplotlib is not available."""

    width, height = 1500, 560
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    today = pd.Timestamp(ist_today())

    def title(text: str) -> None:
        draw.rectangle((0, 0, width, 62), fill=BLUE)
        draw.text((32, 22), text, fill="white", font=font)

    def axes() -> tuple[int, int, int, int]:
        left, top, right, bottom = 86, 86, width - 42, height - 76
        draw.line((left, top, left, bottom), fill=GRID, width=2)
        draw.line((left, bottom, right, bottom), fill=GRID, width=2)
        return left, top, right, bottom

    if kind == "today_area":
        title("Today's Generation - Since Start")
        hourly = _today_hourly_dataframe(df if df is not None else pd.DataFrame())
        labels = list(range(len(hourly)))
        values = [float(value) for value in hourly["generation"].tolist()]
        fill = GREEN
    elif kind == "monthly_daywise":
        title("Monthly Generation - Day Wise")
        current = history[(history["date"].dt.year == today.year) & (history["date"].dt.month == today.month)]
        series = current.groupby(history.loc[current.index, "date"].dt.day)["daily"].sum()
        labels = list(range(1, today.day + 1))
        values = [float(series.get(day, 0.0)) for day in labels]
        fill = GREEN
    elif kind == "yearly_monthwise":
        title("Yearly Generation - Month Wise")
        current = history[history["date"].dt.year == today.year]
        series = current.groupby(history.loc[current.index, "date"].dt.month)["daily"].sum()
        labels = list(range(1, today.month + 1))
        values = [float(series.get(month, 0.0)) for month in labels]
        fill = BLUE
    else:
        title("Per-kW Generation - Year Daily Trend")
        current = history[history["date"].dt.year == today.year]
        grouped = current.groupby("date").agg({"daily": "sum", "capacity": "sum"}).reset_index()
        labels = list(range(len(grouped)))
        values = [
            float(row["daily"]) / float(row["capacity"]) if float(row["capacity"]) > 0 else 0.0
            for _, row in grouped.iterrows()
        ]
        fill = GREEN

    left, top, right, bottom = axes()
    max_value = max(values or [1], default=1) or 1
    if kind in {"perkw_year_daily", "today_area"} and len(values) > 1:
        points = [
            (
                left + int(idx * (right - left) / max(len(values) - 1, 1)),
                bottom - int(value / max_value * (bottom - top)),
            )
            for idx, value in enumerate(values)
        ]
        draw.polygon(points + [(right, bottom), (left, bottom)], fill="#DDF3EA")
        draw.line(points, fill=BLUE, width=4)
    else:
        step = max(1, int((right - left) / max(len(values), 1)))
        bar_w = max(6, int(step * 0.58))
        for idx, value in enumerate(values):
            x = left + idx * step + int((step - bar_w) / 2)
            y = bottom - int(value / max_value * (bottom - top))
            draw.rectangle((x, y, x + bar_w, bottom), fill=fill)
    image.save(output, quality=95)


def create_report_trend_charts(df: pd.DataFrame, chart_dir: Path) -> dict[str, Path]:
    """Create the three requested compact report charts."""

    chart_dir.mkdir(parents=True, exist_ok=True)
    history = _history_dataframe(df)
    charts = {
        "today_area": chart_dir / "report_today_generation_area.png",
        "monthly_daywise": chart_dir / "report_monthly_generation_daywise.png",
        "yearly_monthwise": chart_dir / "report_yearly_generation_monthwise.png",
        "perkw_year_daily": chart_dir / "report_perkw_generation_year_daily.png",
    }
    for kind, path in charts.items():
        if HAS_MATPLOTLIB:
            _save_trend_matplotlib(kind, history, path, df=df)
        else:
            _save_trend_fallback(kind, history, path, df=df)
    return charts


def create_status_chart(df: pd.DataFrame, chart_dir: Path) -> Path:
    """Create a donut chart for plant status distribution."""

    path = chart_dir / "status_distribution_donut.png"
    if HAS_MATPLOTLIB:
        _save_matplotlib_chart("status_donut", df, pd.DataFrame(), path)
    else:
        _save_fallback_chart("status_donut", df, pd.DataFrame(), path)
    return path


def fmt(value: Any, decimals: int = 2, suffix: str = "") -> str:
    """Format numbers consistently for PDF tables."""

    try:
        return f"{float(value):,.{decimals}f}{suffix}"
    except (TypeError, ValueError):
        return str(value or "")


def status_color(status: str) -> colors.Color:
    """Return the configured status color."""

    return {
        "Online": colors.HexColor(GREEN),
        "Offline": colors.HexColor(RED),
        "Warning": colors.HexColor(ORANGE),
        "Fault": colors.HexColor(YELLOW),
    }.get(status, colors.HexColor(MUTED))


def add_header_footer(canvas, doc) -> None:
    """Draw a corporate header/footer on every page."""

    canvas.saveState()
    width, height = landscape(A4)
    canvas.setFillColor(colors.HexColor(BLUE))
    canvas.rect(0, height - 13 * mm, width, 13 * mm, stroke=0, fill=1)
    canvas.setFont("Helvetica-Bold", 9)
    canvas.setFillColor(colors.white)
    canvas.drawString(12 * mm, height - 8 * mm, APP_NAME)
    canvas.setFont("Helvetica", 8)
    canvas.drawRightString(width - 12 * mm, height - 8 * mm, "Company Logo")
    canvas.setFillColor(colors.HexColor(MUTED))
    canvas.drawString(12 * mm, 8 * mm, f"Generated {ist_now():%Y-%m-%d %H:%M} IST")
    canvas.drawRightString(width - 12 * mm, 8 * mm, f"Page {doc.page}")
    canvas.restoreState()


def make_styles() -> dict[str, ParagraphStyle]:
    """Create reusable ReportLab paragraph styles."""

    styles = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "Title",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=24,
            textColor=colors.HexColor(BLUE),
            alignment=TA_CENTER,
            spaceAfter=10,
        ),
        "h1": ParagraphStyle(
            "H1",
            parent=styles["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=17,
            textColor=colors.HexColor(BLUE),
            spaceAfter=8,
        ),
        "h2": ParagraphStyle(
            "H2",
            parent=styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=12,
            textColor=colors.HexColor(BLUE),
            spaceAfter=6,
        ),
        "normal": ParagraphStyle("NormalText", parent=styles["Normal"], fontSize=8.5, leading=10.5),
        "small": ParagraphStyle("Small", parent=styles["Normal"], fontSize=7.5, leading=9),
        "center": ParagraphStyle("Center", parent=styles["Normal"], fontSize=8.5, alignment=TA_CENTER),
        "card": ParagraphStyle("Card", parent=styles["Normal"], fontSize=9, alignment=TA_CENTER, leading=12),
    }


def metric_card(label: str, value: str, styles: dict[str, ParagraphStyle]) -> Table:
    """Create a compact KPI card."""

    table = Table(
        [[Paragraph(label, styles["small"])], [Paragraph(f"<b>{value}</b>", styles["card"])]],
        colWidths=[52 * mm],
        rowHeights=[10 * mm, 15 * mm],
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(LIGHT_BLUE)),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor(GRID)),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ]
        )
    )
    return table


def table_from_dataframe(df: pd.DataFrame, styles: dict[str, ParagraphStyle], col_widths: list[float]) -> LongTable:
    """Create a styled multi-page table from a dataframe."""

    data = [[Paragraph(str(col), styles["small"]) for col in df.columns]]
    for row in df.itertuples(index=False):
        data.append([Paragraph(str(value), styles["small"]) for value in row])

    table = LongTable(data, repeatRows=1, colWidths=col_widths)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(BLUE)),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor(GRID)),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F6F8FA")]),
            ]
        )
    )
    return table


def generate_recommendations(df: pd.DataFrame) -> list[str]:
    """Generate automatic operational recommendations."""

    recommendations = []
    offline = df[df["Current Status"] == "Offline"]
    warnings = df[df["Current Status"].isin(["Warning", "Fault"])]
    low_output = df[(df["Plant Capacity (kW)"] > 0) & (df["Specific Yield (kWh/kWp)"] < 2.0)]
    if not offline.empty:
        recommendations.append(f"Inspect {len(offline)} offline plant(s) and verify inverter/grid connectivity.")
    if not warnings.empty:
        recommendations.append(f"Review alarms for {len(warnings)} plant(s) showing warning or fault status.")
    if not low_output.empty:
        recommendations.append("Clean modules and check shading for plants producing below expected specific yield.")
    recommendations.extend(
        [
            "Schedule preventive maintenance for high-capacity plants before the next weekly report.",
            "Compare brand-wise efficiency and prioritize follow-up where daily yield per kWp is weakest.",
            "Monitor inverter alarms and confirm communication gateways remain online.",
            "Add weather irradiation data to refine Performance Ratio and CUF interpretation.",
        ]
    )
    return recommendations


def generate_pdf(
    df: pd.DataFrame,
    output_path: Path,
    logo_path: str | None = None,
    portal_url: str | None = None,
    plant_report_links: dict[tuple[str, str], Path] | None = None,
) -> ReportAssets:
    """Generate the complete professional PDF report."""

    summary = calculate_summary(df)
    brand_summary = generate_brand_summary(df)
    best, top5 = calculate_best_performing(df)
    chart_dir = OUTPUT_DIR / "charts"
    charts = create_charts(df, brand_summary, chart_dir)
    trend_charts = create_report_trend_charts(df, chart_dir)
    status_chart = create_status_chart(df, chart_dir)
    styles = make_styles()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=landscape(A4),
        leftMargin=13 * mm,
        rightMargin=13 * mm,
        topMargin=20 * mm,
        bottomMargin=15 * mm,
        title=APP_NAME,
    )
    story: list[Any] = []

    # Cover page.
    story.append(Spacer(1, 24 * mm))
    story.append(Paragraph("Solar Power Plant Performance Report", styles["title"]))
    story.append(Spacer(1, 8 * mm))
    logo_cell: Any = Paragraph("Company Logo Placeholder", styles["center"])
    if logo_path and Path(logo_path).exists():
        logo_cell = RLImage(logo_path, width=36 * mm, height=20 * mm)
    cover_data = [
        [logo_cell],
        [Paragraph(f"Report Date: <b>{ist_today():%Y-%m-%d}</b>", styles["center"])],
        [Paragraph(f"Total Number of Plants: <b>{summary['total_plants']}</b>", styles["center"])],
        [Paragraph(f"Total Installed Capacity: <b>{fmt(summary['capacity_kw'])} kW</b>", styles["center"])],
        [Paragraph(f"Report Generated On: <b>{ist_now():%Y-%m-%d %H:%M} IST</b>", styles["center"])],
    ]
    cover_table = Table(cover_data, colWidths=[150 * mm], rowHeights=[26 * mm, 12 * mm, 12 * mm, 12 * mm, 12 * mm])
    cover_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F7FBFD")),
                ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor(GRID)),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ]
        )
    )
    story.append(cover_table)
    story.append(PageBreak())

    # Executive summary.
    story.append(Paragraph("Executive Summary", styles["h1"]))
    card_rows = [
        [
            metric_card("Total Plants", str(summary["total_plants"]), styles),
            metric_card("Online Plants", str(summary["online_plants"]), styles),
            metric_card("Offline Plants", str(summary["offline_plants"]), styles),
        ],
        [
            metric_card("Installed Capacity", f"{fmt(summary['capacity_kw'])} kW", styles),
            metric_card("Daily Generation", f"{fmt(summary['daily_kwh'])} kWh", styles),
            metric_card("Weekly Generation", f"{fmt(summary['weekly_kwh'])} kWh", styles),
        ],
        [
            metric_card("Lifetime Generation", f"{fmt(summary['total_mwh'])} MWh", styles),
            metric_card("Warnings", str(summary["warning_plants"]), styles),
            metric_card("Faults", str(summary["fault_plants"]), styles),
        ],
        [
            metric_card("Yearly Generation", f"{fmt(summary['year_kwh'])} kWh", styles),
            metric_card("CUF", f"{fmt(summary['cuf_percent'])}%", styles),
            metric_card("Specific Yield", f"{fmt(df['Specific Yield (kWh/kWp)'].mean())} kWh/kW", styles),
        ],
    ]
    story.append(Table(card_rows, colWidths=[70 * mm, 70 * mm, 70 * mm], rowHeights=[30 * mm] * len(card_rows)))
    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph("Weather Information Placeholder: irradiation, temperature, wind, and humidity can be added when weather data is available.", styles["normal"]))
    story.append(PageBreak())

    # Brand-wise summary.
    story.append(Paragraph("Brand-wise Summary", styles["h1"]))
    formatted_brand = brand_summary.copy()
    for col in formatted_brand.columns:
        if col != "Brand":
            formatted_brand[col] = formatted_brand[col].map(lambda value: fmt(value, 2))
    story.append(
        table_from_dataframe(
            formatted_brand,
            styles,
            [30 * mm, 18 * mm, 30 * mm, 30 * mm, 30 * mm, 30 * mm, 30 * mm, 26 * mm],
        )
    )
    story.append(PageBreak())

    # Plant performance table.
    story.append(Paragraph("Plant Performance Table", styles["h1"]))
    table_df = df[
        [
            "Brand",
            "Site Name",
            "Plant Capacity (kW)",
            "Current Status",
            "Daily Generation (kWh)",
            "Weekly Generation (kWh)",
            "Year Generation (kWh)",
            "Total Generation (MWh)",
            "CUF (%)",
        ]
    ].copy()
    for col in [
        "Plant Capacity (kW)",
        "Daily Generation (kWh)",
        "Weekly Generation (kWh)",
        "Year Generation (kWh)",
        "Total Generation (MWh)",
        "CUF (%)",
    ]:
        table_df[col] = table_df[col].map(lambda value: fmt(value, 2))
    data = [[Paragraph(str(col), styles["small"]) for col in table_df.columns]]
    for row in table_df.itertuples(index=False):
        data.append([Paragraph(str(value), styles["small"]) for value in row])
    plant_table = LongTable(
        data,
        repeatRows=1,
        colWidths=[18 * mm, 46 * mm, 22 * mm, 20 * mm, 25 * mm, 26 * mm, 28 * mm, 26 * mm, 26 * mm],
    )
    plant_style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(BLUE)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor(GRID)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F6F8FA")]),
    ]
    for index, status in enumerate(table_df["Current Status"].tolist(), start=1):
        plant_style.append(("TEXTCOLOR", (3, index), (3, index), status_color(status)))
        plant_style.append(("FONTNAME", (3, index), (3, index), "Helvetica-Bold"))
    plant_table.setStyle(TableStyle(plant_style))
    story.append(plant_table)
    story.append(PageBreak())

    # Charts.
    story.append(Paragraph("Charts", styles["h1"]))
    chart_order = [
        ("Daily Generation by Plant", charts["daily_bar"]),
        ("Weekly Generation by Plant", charts["weekly_barh"]),
        ("Total Generation by Plant", charts["total_line"]),
        ("Brand-wise Generation Share", charts["brand_pie"]),
        ("Capacity vs Daily Generation", charts["capacity_scatter"]),
    ]
    for index, (label, path) in enumerate(chart_order):
        story.append(KeepTogether([Paragraph(label, styles["h2"]), RLImage(str(path), width=122 * mm, height=66 * mm)]))
        if index in {1, 3}:
            story.append(PageBreak())
        else:
            story.append(Spacer(1, 4 * mm))
    story.append(PageBreak())

    # Best performing plant.
    story.append(Paragraph("Best Performing Plant", styles["h1"]))
    performance_percentage = (
        best["Daily Generation (kWh)"] / (best["Plant Capacity (kW)"] * 5.0) * 100
        if best["Plant Capacity (kW)"] > 0
        else 0
    )
    best_box = Table(
        [
            [Paragraph("<b>Best Performing Plant</b>", styles["h2"])],
            [Paragraph(f"Site Name: <b>{best['Site Name']}</b>", styles["normal"])],
            [Paragraph(f"Brand: <b>{best['Brand']}</b>", styles["normal"])],
            [Paragraph(f"Capacity: <b>{fmt(best['Plant Capacity (kW)'])} kW</b>", styles["normal"])],
            [Paragraph(f"Daily Generation: <b>{fmt(best['Daily Generation (kWh)'])} kWh</b>", styles["normal"])],
            [Paragraph(f"Weekly Generation: <b>{fmt(best['Weekly Generation (kWh)'])} kWh</b>", styles["normal"])],
            [Paragraph(f"2026 Generation: <b>{fmt(best['Year Generation (kWh)'])} kWh</b>", styles["normal"])],
            [Paragraph(f"Total Generation: <b>{fmt(best['Total Generation (MWh)'])} MWh</b>", styles["normal"])],
            [Paragraph(f"CUF: <b>{fmt(best['CUF (%)'])}%</b>", styles["normal"])],
            [Paragraph(f"Performance Percentage: <b>{fmt(performance_percentage)}%</b>", styles["normal"])],
        ],
        colWidths=[130 * mm],
    )
    best_box.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(LIGHT_GREEN)),
                ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor(GREEN)),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.append(best_box)
    story.append(Spacer(1, 8 * mm))
    top5_df = top5[
        [
            "Brand",
            "Site Name",
            "Plant Capacity (kW)",
            "Daily Generation (kWh)",
            "Year Generation (kWh)",
            "CUF (%)",
        ]
    ].copy()
    top5_df.insert(0, "Rank", range(1, len(top5_df) + 1))
    for col in [
        "Plant Capacity (kW)",
        "Daily Generation (kWh)",
        "Year Generation (kWh)",
        "CUF (%)",
    ]:
        top5_df[col] = top5_df[col].map(lambda value: fmt(value, 2))
    story.append(Paragraph("Top 5 Performing Plants", styles["h2"]))
    story.append(table_from_dataframe(top5_df, styles, [16 * mm, 28 * mm, 62 * mm, 32 * mm, 36 * mm, 36 * mm, 36 * mm]))
    story.append(PageBreak())

    # Status summary.
    story.append(Paragraph("Plant Status Summary", styles["h1"]))
    status_rows = [
        ["Online Count", summary["online_plants"]],
        ["Offline Count", summary["offline_plants"]],
        ["Warning Count", summary["warning_plants"]],
        ["Fault Count", summary["fault_plants"]],
    ]
    status_table = Table(
        [[Paragraph(str(a), styles["normal"]), Paragraph(str(b), styles["center"])] for a, b in status_rows],
        colWidths=[50 * mm, 30 * mm],
    )
    status_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(LIGHT_BLUE)),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor(GRID)),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(Table([[status_table, RLImage(str(status_chart), width=132 * mm, height=74 * mm)]], colWidths=[88 * mm, 142 * mm]))
    story.append(PageBreak())

    # Recommendations.
    story.append(Paragraph("Recommendations", styles["h1"]))
    for recommendation in generate_recommendations(df):
        story.append(Paragraph(f"- {recommendation}", styles["normal"]))
        story.append(Spacer(1, 2 * mm))
    if portal_url:
        story.append(Spacer(1, 5 * mm))
        story.append(Paragraph(f"Monitoring Portal QR Placeholder: {portal_url}", styles["normal"]))
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph("Monthly Trend Graph Placeholder: add month-wise input columns to enable monthly trend analysis.", styles["normal"]))

    doc.build(story, onFirstPage=add_header_footer, onLaterPages=add_header_footer)
    LOGGER.info("Saved PDF report to %s", output_path)
    return ReportAssets(chart_dir=chart_dir, charts=charts, status_chart=status_chart, pdf_path=output_path)


def _compact_styles() -> dict[str, ParagraphStyle]:
    """Styles tuned for a dense three-page executive PDF."""

    styles = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "CompactTitle",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=18,
            leading=21,
            textColor=colors.HexColor(BLUE),
            alignment=TA_LEFT,
        ),
        "h1": ParagraphStyle(
            "CompactH1",
            parent=styles["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=13,
            textColor=colors.HexColor(BLUE),
            spaceAfter=4,
        ),
        "h2": ParagraphStyle(
            "CompactH2",
            parent=styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=9,
            textColor=colors.HexColor(BLUE),
            spaceAfter=2,
        ),
        "normal": ParagraphStyle("CompactNormal", parent=styles["Normal"], fontSize=6.6, leading=8),
        "small": ParagraphStyle("CompactSmall", parent=styles["Normal"], fontSize=5.5, leading=6.5),
        "center": ParagraphStyle("CompactCenter", parent=styles["Normal"], fontSize=6.2, leading=7.2, alignment=TA_CENTER),
        "card": ParagraphStyle("CompactCard", parent=styles["Normal"], fontSize=7.2, leading=8.5, alignment=TA_CENTER),
    }


def _compact_header_footer(logo_path: str | None):
    """Return a canvas callback with logo, bookmarks, page nav, and footer."""

    destinations = {1: ("summary", "Summary"), 2: ("plants", "Plants"), 3: ("insights", "Insights")}

    def draw(canvas, doc) -> None:
        width, height = landscape(A4)
        page_key, page_label = destinations.get(doc.page, (f"page{doc.page}", f"Page {doc.page}"))
        canvas.saveState()
        canvas.bookmarkPage(page_key)
        canvas.addOutlineEntry(page_label, page_key, level=0, closed=False)

        canvas.setFillColor(colors.HexColor(BLUE))
        canvas.rect(0, height - 12 * mm, width, 12 * mm, stroke=0, fill=1)
        if logo_path and Path(logo_path).exists():
            canvas.drawImage(
                logo_path,
                10 * mm,
                height - 10.5 * mm,
                width=26 * mm,
                height=9 * mm,
                preserveAspectRatio=True,
                mask="auto",
            )
            title_x = 40 * mm
        else:
            title_x = 11 * mm
        canvas.setFont("Helvetica-Bold", 8)
        canvas.setFillColor(colors.white)
        canvas.drawString(title_x, height - 7.5 * mm, "Solar Power Plant Performance Report")

        nav = [("Summary", "summary"), ("Plants", "plants"), ("Insights", "insights")]
        x = width - 84 * mm
        for label, dest in nav:
            canvas.setFont("Helvetica-Bold", 7)
            canvas.drawString(x, height - 7.5 * mm, label)
            canvas.linkRect("", dest, (x - 1, height - 10 * mm, x + 22 * mm, height - 4 * mm), relative=0, thickness=0)
            x += 27 * mm

        canvas.setFillColor(colors.HexColor(MUTED))
        canvas.setFont("Helvetica", 7)
        canvas.drawString(11 * mm, 7 * mm, f"Generated {ist_now():%Y-%m-%d %H:%M} IST")
        canvas.drawRightString(width - 11 * mm, 7 * mm, f"Page {doc.page} of 3")
        canvas.restoreState()

    return draw


def _plant_report_header_footer(logo_path: str | None):
    """Return a canvas callback for individual plant PDFs."""

    def draw(canvas, doc) -> None:
        width, height = landscape(A4)
        canvas.saveState()
        canvas.setFillColor(colors.HexColor(BLUE))
        canvas.rect(0, height - 12 * mm, width, 12 * mm, stroke=0, fill=1)
        if logo_path and Path(logo_path).exists():
            canvas.drawImage(
                logo_path,
                10 * mm,
                height - 10.5 * mm,
                width=26 * mm,
                height=9 * mm,
                preserveAspectRatio=True,
                mask="auto",
            )
            title_x = 40 * mm
        else:
            title_x = 11 * mm
        canvas.setFont("Helvetica-Bold", 8)
        canvas.setFillColor(colors.white)
        canvas.drawString(title_x, height - 7.5 * mm, "Individual Solar Plant Performance Report")
        canvas.setFillColor(colors.HexColor(MUTED))
        canvas.setFont("Helvetica", 7)
        canvas.drawString(11 * mm, 7 * mm, f"Generated {ist_now():%Y-%m-%d %H:%M} IST")
        canvas.drawRightString(width - 11 * mm, 7 * mm, f"Page {doc.page}")
        canvas.restoreState()

    return draw


def compact_metric(label: str, value: str, styles: dict[str, ParagraphStyle]) -> Table:
    """Create a very compact KPI tile."""

    table = Table(
        [[Paragraph(label, styles["small"])], [Paragraph(f"<b>{value}</b>", styles["card"])]],
        colWidths=[34 * mm],
        rowHeights=[7 * mm, 10 * mm],
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(LIGHT_BLUE)),
                ("BOX", (0, 0), (-1, -1), 0.35, colors.HexColor(GRID)),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ]
        )
    )
    return table


def _compact_table(data: list[list[Any]], col_widths: list[float], header: bool = True) -> Table:
    """Build a compact styled table for dense pages."""

    table = Table(data, colWidths=col_widths, repeatRows=1 if header else 0)
    style = [
        ("GRID", (0, 0), (-1, -1), 0.2, colors.HexColor(GRID)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7FBFF")]),
    ]
    if header:
        style.extend(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(BLUE)),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ]
        )
    table.setStyle(TableStyle(style))
    return table


def generate_compact_pdf(
    df: pd.DataFrame,
    output_path: Path,
    logo_path: str | None = None,
    portal_url: str | None = None,
    plant_report_links: dict[tuple[str, str], Path] | None = None,
) -> ReportAssets:
    """Generate a professional interactive PDF constrained to three pages."""

    summary = calculate_summary(df)
    brand_summary = generate_brand_summary(df)
    best, top5 = calculate_best_performing(df)
    chart_dir = OUTPUT_DIR / "charts"
    charts = create_charts(df, brand_summary, chart_dir)
    trend_charts = create_report_trend_charts(df, chart_dir)
    status_chart = create_status_chart(df, chart_dir)
    styles = _compact_styles()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=landscape(A4),
        leftMargin=9 * mm,
        rightMargin=9 * mm,
        topMargin=16 * mm,
        bottomMargin=11 * mm,
        title=APP_NAME,
    )
    story: list[Any] = []

    logo = logo_path if logo_path and Path(logo_path).exists() else None
    logo_cell: Any = Paragraph("<b>NCE</b><br/>Beyond Imagination", styles["center"])
    if logo:
        logo_cell = RLImage(logo, width=32 * mm, height=20 * mm)

    page1_left = [
        [logo_cell],
        [Paragraph("<b>Solar Power Plant Performance Report</b>", styles["title"])],
        [Paragraph(f"Report Date: <b>{ist_today():%Y-%m-%d}</b>", styles["normal"])],
        [Paragraph(f"Generated On: <b>{ist_now():%Y-%m-%d %H:%M} IST</b>", styles["normal"])],
        [Paragraph(f"Year basis: <b>{REPORT_YEAR_START:%Y-%m-%d}</b> to report date. Week basis: <b>Monday-Sunday</b>.", styles["normal"])],
        [Paragraph("2026 values use portal YTD where available; otherwise estimated from the current week.", styles["normal"])],
        [Paragraph("Weather: placeholder for irradiation, temperature, wind, and humidity.", styles["normal"])],
    ]
    page1_left_table = Table(page1_left, colWidths=[83 * mm])
    page1_left_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FBFF")),
                ("BOX", (0, 0), (-1, -1), 0.45, colors.HexColor(GRID)),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    kpi_grid = Table(
        [
            [
                compact_metric("Plants", str(summary["total_plants"]), styles),
                compact_metric("Online", str(summary["online_plants"]), styles),
                compact_metric("Offline", str(summary["offline_plants"]), styles),
                compact_metric("Capacity", f"{fmt(summary['capacity_kw'])} kW", styles),
            ],
            [
                compact_metric("Daily", f"{fmt(summary['daily_kwh'])} kWh", styles),
                compact_metric("Weekly", f"{fmt(summary['weekly_kwh'])} kWh", styles),
                compact_metric("Yearly", f"{fmt(summary['year_kwh'])} kWh", styles),
                compact_metric("Alerts", str(summary["warning_plants"] + summary["fault_plants"]), styles),
            ],
            [
                compact_metric("Lifetime", f"{fmt(summary['total_mwh'])} MWh", styles),
                compact_metric("CUF", f"{fmt(summary['cuf_percent'])}%", styles),
                compact_metric("Best CUF", f"{fmt(best['CUF (%)'])}%", styles),
                compact_metric("Avg Yield", f"{fmt(df['Specific Yield (kWh/kWp)'].mean())}", styles),
            ],
        ],
        colWidths=[37 * mm] * 4,
        rowHeights=[18 * mm, 18 * mm, 18 * mm],
    )
    story.append(Table([[page1_left_table, kpi_grid]], colWidths=[88 * mm, 160 * mm]))
    story.append(Spacer(1, 4 * mm))

    brand_fmt = brand_summary.copy()
    for col in brand_fmt.columns:
        if col != "Brand":
            brand_fmt[col] = brand_fmt[col].map(lambda value: fmt(value, 1))
    brand_data = [[Paragraph(str(col), styles["small"]) for col in brand_fmt.columns]]
    for row in brand_fmt.itertuples(index=False):
        brand_data.append([Paragraph(str(value), styles["small"]) for value in row])
    story.append(Paragraph("Brand-wise Summary", styles["h1"]))
    story.append(_compact_table(brand_data, [25 * mm, 15 * mm, 30 * mm, 29 * mm, 30 * mm, 30 * mm, 30 * mm, 24 * mm]))
    story.append(Spacer(1, 4 * mm))

    performance_percentage = (
        best["Daily Generation (kWh)"] / (best["Plant Capacity (kW)"] * 5.0) * 100
        if best["Plant Capacity (kW)"] > 0
        else 0
    )
    best_rows = [
        [Paragraph("<b>Best Performing Plant</b>", styles["h2"])],
        [Paragraph(f"{best['Site Name']} | {best['Brand']} | {fmt(best['Plant Capacity (kW)'])} kW", styles["normal"])],
        [Paragraph(f"Daily {fmt(best['Daily Generation (kWh)'])} kWh | Yearly {fmt(best['Year Generation (kWh)'])} kWh | CUF {fmt(best['CUF (%)'])}%", styles["normal"])],
    ]
    best_table = Table(best_rows, colWidths=[248 * mm])
    best_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(LIGHT_GREEN)),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor(GREEN)),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(best_table)
    story.append(PageBreak())

    story.append(Paragraph("Plant Performance Table", styles["h1"]))
    table_df = df[
        [
            "Brand",
            "Site Name",
            "Plant Capacity (kW)",
            "Current Status",
            "Daily Generation (kWh)",
            "Weekly Generation (kWh)",
            "Year Generation (kWh)",
            "Total Generation (MWh)",
            "CUF (%)",
        ]
    ].copy()
    table_df.columns = ["Brand", "Site", "Cap", "Status", "Daily", "Weekly", "Yearly", "Total", "CUF"]
    if plant_report_links:
        linked_sites = []
        for original in df[["Brand", "Site Name"]].itertuples(index=False):
            site = str(original[1])
            link_path = plant_report_links.get((str(original[0]), site))
            if link_path:
                linked_sites.append(
                    f'<a href="{link_path.resolve().as_uri()}"><font color="{BLUE}"><u>{pdf_markup_escape(site)}</u></font></a>'
                )
            else:
                linked_sites.append(pdf_markup_escape(site))
        table_df["Site"] = linked_sites
    for col in ["Cap", "Daily", "Weekly", "Yearly", "Total", "CUF"]:
        table_df[col] = table_df[col].map(lambda value: fmt(value, 2))
    data = [[Paragraph(str(col), styles["small"]) for col in table_df.columns]]
    for row in table_df.itertuples(index=False):
        data.append([Paragraph(str(value), styles["small"]) for value in row])
    plant_table = _compact_table(data, [17 * mm, 56 * mm, 17 * mm, 18 * mm, 21 * mm, 22 * mm, 24 * mm, 22 * mm, 18 * mm])
    plant_style = []
    for index, status in enumerate(table_df["Status"].tolist(), start=1):
        plant_style.append(("TEXTCOLOR", (3, index), (3, index), status_color(status)))
        plant_style.append(("FONTNAME", (3, index), (3, index), "Helvetica-Bold"))
    plant_table.setStyle(TableStyle(plant_table._cellStyles and plant_style or plant_style))
    story.append(plant_table)
    story.append(PageBreak())

    story.append(Paragraph("Insights, Trends, Status and Recommendations", styles["h1"]))
    chart_cells = [
        [
            RLImage(str(trend_charts["today_area"]), width=118 * mm, height=34 * mm),
            RLImage(str(trend_charts["monthly_daywise"]), width=118 * mm, height=34 * mm),
        ],
        [
            RLImage(str(trend_charts["yearly_monthwise"]), width=118 * mm, height=34 * mm),
            RLImage(str(trend_charts["perkw_year_daily"]), width=118 * mm, height=34 * mm),
        ],
        [
            RLImage(str(status_chart), width=54 * mm, height=30 * mm),
            Paragraph(
                "<b>Status Summary</b><br/>Online: %s<br/>Offline: %s<br/>Warning: %s<br/>Fault: %s"
                % (summary["online_plants"], summary["offline_plants"], summary["warning_plants"], summary["fault_plants"]),
                styles["small"],
            ),
        ],
    ]
    chart_table = Table(chart_cells, colWidths=[123 * mm, 123 * mm], rowHeights=[36 * mm, 36 * mm, 30 * mm])
    chart_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("BOX", (0, 0), (-1, -1), 0.25, colors.HexColor(GRID)),
                ("INNERGRID", (0, 0), (-1, -1), 0.15, colors.HexColor(GRID)),
            ]
        )
    )
    story.append(chart_table)
    story.append(Spacer(1, 2 * mm))

    top5_df = top5[
        [
            "Brand",
            "Site Name",
            "Plant Capacity (kW)",
            "Year Generation (kWh)",
            "CUF (%)",
            "Daily Generation (kWh)",
        ]
    ].copy()
    top5_df.insert(0, "Rank", range(1, len(top5_df) + 1))
    top5_df.columns = ["Rank", "Brand", "Site", "Cap", "Yearly", "CUF", "Daily"]
    for col in ["Cap", "Yearly", "CUF", "Daily"]:
        top5_df[col] = top5_df[col].map(lambda value: fmt(value, 1))
    top_data = [[Paragraph(str(col), styles["small"]) for col in top5_df.columns]]
    for row in top5_df.itertuples(index=False):
        top_data.append([Paragraph(str(value), styles["small"]) for value in row])

    recommendations = generate_recommendations(df)[:5]
    rec_text = "<br/>".join(f"- {item}" for item in recommendations)
    bottom = Table(
        [
            [
                [Paragraph("Top 5 Plants by CUF", styles["h2"]), _compact_table(top_data, [10 * mm, 20 * mm, 48 * mm, 17 * mm, 24 * mm, 18 * mm, 18 * mm])],
                [Paragraph("Recommendations", styles["h2"]), Paragraph(rec_text, styles["small"])],
            ]
        ],
        colWidths=[160 * mm, 82 * mm],
    )
    bottom.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(bottom)

    header_footer = _compact_header_footer(logo)
    doc.build(story, onFirstPage=header_footer, onLaterPages=header_footer)
    LOGGER.info("Saved compact interactive PDF report to %s", output_path)
    return ReportAssets(chart_dir=chart_dir, charts=charts, status_chart=status_chart, pdf_path=output_path)


def output_pdf_name(output_dir: Path) -> Path:
    """Return the required dated report filename."""

    return output_dir / f"Solar_Performance_Report_{ist_today():%Y%m%d}.pdf"


def save_clean_inputs(df: pd.DataFrame, output_dir: Path) -> None:
    """Save normalized input data for audit and reuse."""

    output_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_dir / "solar_plants_normalized.csv", index=False)
    (output_dir / "solar_plants_normalized.json").write_text(
        json.dumps(df.to_dict(orient="records"), indent=2),
        encoding="utf-8",
    )


def slugify_filename(value: str) -> str:
    """Create a safe, readable filename segment."""

    slug = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    return slug[:80] or "plant"


def pdf_markup_escape(value: str) -> str:
    """Escape text used inside ReportLab Paragraph markup."""

    return escape(value, {"\"": "&quot;"})


def individual_report_dir(output_dir: Path) -> Path:
    """Return the dated folder for one-plant reports."""

    return output_dir / "Individual Plant Reports" / f"{ist_today():%Y%m%d}"


def individual_report_path(output_dir: Path, brand: str, site: str) -> Path:
    """Return the expected one-plant report PDF path."""

    return individual_report_dir(output_dir) / f"{slugify_filename(brand)}_{slugify_filename(site)}_{ist_today():%Y%m%d}.pdf"


def generate_individual_plant_reports(
    df: pd.DataFrame,
    output_dir: Path,
    logo_path: str | None = None,
) -> dict[tuple[str, str], Path]:
    """Generate one customer-ready PDF for each plant."""

    report_dir = individual_report_dir(output_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    styles = _compact_styles()
    manifest_rows: list[dict[str, str]] = []
    links: dict[tuple[str, str], Path] = {}

    for row in df.sort_values(["Brand", "Site Name"]).to_dict(orient="records"):
        brand = str(row["Brand"])
        site = str(row["Site Name"])
        pdf_path = individual_report_path(output_dir, brand, site)
        links[(brand, site)] = pdf_path
        doc = SimpleDocTemplate(
            str(pdf_path),
            pagesize=landscape(A4),
            leftMargin=12 * mm,
            rightMargin=12 * mm,
            topMargin=16 * mm,
            bottomMargin=12 * mm,
            title=f"{site} Solar Plant Report",
        )

        logo_cell: Any = Paragraph("<b>NCE</b><br/>Beyond Imagination", styles["center"])
        if logo_path and Path(logo_path).exists():
            logo_cell = RLImage(logo_path, width=30 * mm, height=18 * mm)

        header = Table(
            [
                [
                    logo_cell,
                    Paragraph(f"<b>{site}</b><br/>{brand} Solar Plant Performance", styles["title"]),
                    Paragraph(f"Report Date<br/><b>{ist_today():%Y-%m-%d}</b>", styles["center"]),
                ]
            ],
            colWidths=[38 * mm, 168 * mm, 48 * mm],
        )
        header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))

        kpis = [
            ("Status", row["Current Status"]),
            ("Capacity", f"{fmt(row['Plant Capacity (kW)'])} kW"),
            ("Daily Generation", f"{fmt(row['Daily Generation (kWh)'])} kWh"),
            ("Weekly Generation", f"{fmt(row['Weekly Generation (kWh)'])} kWh"),
            ("2026 Generation", f"{fmt(row['Year Generation (kWh)'])} kWh"),
            ("Total Generation", f"{fmt(row['Total Generation (MWh)'])} MWh"),
            ("Yearly Generation", f"{fmt(row['Year Generation (kWh)'])} kWh"),
            ("CUF", f"{fmt(row['CUF (%)'])}%"),
            ("Specific Yield Today", f"{fmt(row['Specific Yield (kWh/kWp)'])} kWh/kW"),
            ("CUF Today", f"{fmt(row['CUF (%)'])}%"),
            ("PR Today", f"{fmt(row['PR (%)'])}%"),
            ("2026 Source", str(row.get("Year Generation Source", ""))),
        ]
        kpi_rows = []
        for index in range(0, len(kpis), 4):
            kpi_rows.append([compact_metric(label, str(value), styles) for label, value in kpis[index : index + 4]])
        kpi_table = Table(kpi_rows, colWidths=[46 * mm] * 4, rowHeights=[20 * mm] * len(kpi_rows))

        notes = [
            f"Year basis: {REPORT_YEAR_START:%Y-%m-%d} to report date.",
            "Week basis: Monday-Sunday.",
            "2026 values use portal YTD where available; otherwise estimated from the current week.",
        ]
        if row["Current Status"] != "Online":
            notes.append("Plant is not online in the latest data and should be checked.")
        if row["Daily Generation (kWh)"] <= 0 and row["Current Status"] == "Online":
            notes.append("Online plant has zero daily generation in the latest data; verify inverter status and meter communication.")

        note_text = "<br/>".join(f"- {item}" for item in notes)
        detail_table = Table(
            [
                [
                    Paragraph("<b>Plant Summary</b><br/>This one-page report is prepared for customer sharing on request.", styles["normal"]),
                    Paragraph(f"<b>Notes</b><br/>{note_text}", styles["normal"]),
                ]
            ],
            colWidths=[108 * mm, 128 * mm],
        )
        detail_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FBFF")),
                    ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor(GRID)),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 7),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ]
            )
        )

        story = [header, Spacer(1, 6 * mm), kpi_table, Spacer(1, 6 * mm), detail_table]
        plant_footer = _plant_report_header_footer(logo_path)
        doc.build(story, onFirstPage=plant_footer, onLaterPages=plant_footer)
        manifest_rows.append({"Brand": brand, "Site Name": site, "PDF": str(pdf_path)})

    pd.DataFrame(manifest_rows).to_csv(report_dir / "plant_report_index.csv", index=False)
    LOGGER.info("Saved %d individual plant reports to %s", len(manifest_rows), report_dir)
    return links


def build_arg_parser() -> argparse.ArgumentParser:
    """Create the command-line interface."""

    parser = argparse.ArgumentParser(description="Generate a Solar Plant Performance PDF Report.")
    parser.add_argument("--input", help="Input JSON or CSV file.")
    parser.add_argument("--api-url", help="API endpoint returning plant data JSON.")
    parser.add_argument("--current-project", action="store_true", help="Use current GOODWE project data files.")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR), help="Output folder.")
    parser.add_argument("--logo", help="Optional company logo path.")
    parser.add_argument("--portal-url", help="Optional monitoring portal link for the recommendations page.")
    parser.add_argument("--full", action="store_true", help="Generate the longer detailed report instead of the compact three-page report.")
    parser.add_argument("--plant-reports", action="store_true", help="Also generate one customer-ready PDF per plant.")
    return parser


def main() -> Path:
    """Application entry point."""

    setup_logging()
    parser = build_arg_parser()
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    try:
        df = load_data(input_path=args.input, api_url=args.api_url, current_project=args.current_project)
        save_clean_inputs(df, output_dir)
        logo_path = args.logo or (str(DEFAULT_LOGO) if DEFAULT_LOGO.exists() else None)
        plant_report_links = generate_individual_plant_reports(df, output_dir, logo_path=logo_path) if args.plant_reports else None
        generator = generate_pdf if args.full else generate_compact_pdf
        assets = generator(
            df=df,
            output_path=output_pdf_name(output_dir),
            logo_path=logo_path,
            portal_url=args.portal_url,
            plant_report_links=plant_report_links,
        )
        LOGGER.info("Report completed with %d plants", len(df))
        return assets.pdf_path
    except Exception:
        LOGGER.exception("Failed to generate solar performance report")
        raise


if __name__ == "__main__":
    main()
