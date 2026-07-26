#!/bin/zsh
set -euo pipefail

PROJECT_DIR="/Users/sushil/Documents/GOODWE"
OUTPUT_DIR="/Users/sushil/Library/Mobile Documents/com~apple~CloudDocs/Weekly Solar Plant Report"
BUNDLED_PYTHON="/Users/sushil/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"

cd "$PROJECT_DIR"

if [ -x "$BUNDLED_PYTHON" ]; then
  PYTHON_BIN="$BUNDLED_PYTHON"
elif [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
  PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
else
  PYTHON_BIN="python3"
fi

if [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
  REFRESH_PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
else
  REFRESH_PYTHON_BIN="$PYTHON_BIN"
fi

mkdir -p "$OUTPUT_DIR"

if [ -n "${SEMS_USERNAME:-}" ] && [ -n "${SEMS_PASSWORD:-}" ]; then
  PYTHONPYCACHEPREFIX="$PROJECT_DIR/.pycache" "$REFRESH_PYTHON_BIN" ./sems_export_json.py || true
  PYTHONPYCACHEPREFIX="$PROJECT_DIR/.pycache" "$REFRESH_PYTHON_BIN" ./sems_weekly_generation.py || true
fi

if [ -n "${FRONIUS_USERNAME:-}" ] && [ -n "${FRONIUS_PASSWORD:-}" ]; then
  PYTHONPYCACHEPREFIX="$PROJECT_DIR/.pycache" "$REFRESH_PYTHON_BIN" ./fronius_backend_current_generation.py || true
  PYTHONPYCACHEPREFIX="$PROJECT_DIR/.pycache" "$REFRESH_PYTHON_BIN" ./fronius_backend_weekly_generation.py || true
fi

if [ -n "${FIMER_USERNAME:-}" ] && [ -n "${FIMER_PASSWORD:-}" ]; then
  PYTHONPYCACHEPREFIX="$PROJECT_DIR/.pycache" "$REFRESH_PYTHON_BIN" ./fimer_backend_export_generation.py || true
fi

if [ -f solis_network_capture.json ]; then
  PYTHONPYCACHEPREFIX="$PROJECT_DIR/.pycache" "$REFRESH_PYTHON_BIN" ./solis_capture_to_generation.py || true
fi

if [ -f solax_network_capture.json ]; then
  PYTHONPYCACHEPREFIX="$PROJECT_DIR/.pycache" "$REFRESH_PYTHON_BIN" ./solax_capture_to_generation.py || true
fi

PYTHONPYCACHEPREFIX="$PROJECT_DIR/.pycache" "$PYTHON_BIN" ./solar_performance_report_app.py \
  --current-project \
  --output-dir "$OUTPUT_DIR" \
  --plant-reports

PYTHONPYCACHEPREFIX="$PROJECT_DIR/.pycache" "$PYTHON_BIN" ./build_solar_dashboard_app.py \
  --output-dir "$OUTPUT_DIR"
