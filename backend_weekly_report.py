import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path


REPORT_FILE = Path("outputs/numbers_compatible/numbers_compatible_generation_report.xlsx")
CSV_FILE = Path("outputs/numbers_compatible/numbers_compatible_generation_report.csv")
STATUS_FILE = Path("outputs/numbers_compatible/backend_refresh_status.json")

STALE_OPTIONAL_FILES = [
    Path("fronius_systems.json"),
    Path("fronius_weekly_generation.json"),
    Path("fronius_current_generation.json"),
    Path("fimer_generation.json"),
]


def run_step(label, command, required=True, env=None):
    print(f"{label}...")
    started_at = dt.datetime.now().replace(microsecond=0).isoformat()
    result = subprocess.run(command, env=env, text=True, capture_output=True)
    status = {
        "label": label,
        "started_at": started_at,
        "returncode": result.returncode,
        "ok": result.returncode == 0,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }
    if result.returncode != 0 and required:
        raise RuntimeError(f"{label} failed: {result.stderr.strip() or result.stdout.strip()}")
    return status


def file_status(path):
    if not path.exists():
        return {"file": str(path), "exists": False}
    modified = dt.datetime.fromtimestamp(path.stat().st_mtime).replace(microsecond=0)
    age_hours = (dt.datetime.now() - modified).total_seconds() / 3600
    return {
        "file": str(path),
        "exists": True,
        "modified_at": modified.isoformat(),
        "age_hours": round(age_hours, 2),
    }


def main():
    python = sys.executable
    statuses = []

    missing = [
        name
        for name in ("SEMS_USERNAME", "SEMS_PASSWORD")
        if not os.getenv(name)
    ]
    if missing:
        raise RuntimeError(
            "GoodWe backend login needs these values first: " + ", ".join(missing)
        )

    statuses.append(
        run_step("GoodWe backend login and station refresh", [python, "sems_export_json.py"])
    )
    statuses.append(
        run_step("GoodWe backend weekly generation refresh", [python, "sems_weekly_generation.py"])
    )

    optional_sources = [file_status(path) for path in STALE_OPTIONAL_FILES]
    statuses.append(
        run_step("Build combined Numbers-compatible report", [python, "build_numbers_compatible_report.py"])
    )

    payload = {
        "generated_at": dt.datetime.now().replace(microsecond=0).isoformat(),
        "mode": "direct backend for GoodWe; cached latest files for Fronius and FIMER",
        "report_file": str(REPORT_FILE),
        "csv_file": str(CSV_FILE),
        "steps": statuses,
        "optional_sources": optional_sources,
        "notes": [
            "GoodWe/SEMS uses direct username-password backend login.",
            "Fronius Solar.web and FIMER Aurora Vision still need official API access or captured backend login flow before they can refresh without a browser session.",
        ],
    }
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"Saved report: {REPORT_FILE}")
    print(f"Saved CSV: {CSV_FILE}")
    print(f"Saved refresh status: {STATUS_FILE}")


if __name__ == "__main__":
    main()
