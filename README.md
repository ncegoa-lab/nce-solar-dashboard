# SEMS Daily Generation Logger

This folder contains a small script that fetches yesterday's GoodWe SEMS plant
generation values and appends them to an Excel workbook.

## Setup

Install the Excel dependency:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Install whichever GoodWe SEMS Python wrapper you are using. The script currently
expects that wrapper to be importable as `sems_api` and to provide:

```python
sems_api.SemsAPI(username=..., password=...)
client.get_plant_historical_data(plant_id, date="YYYY-MM-DD")
```

## Run

Set your login and station ID as environment variables:

```bash
export SEMS_USERNAME="your-email"
export SEMS_PASSWORD="your-password"
export SEMS_PLANT_ID="your-station-id-from-url"
python3 sems_daily_log.py
```

The script creates or updates `sems_daily_generation.xlsx`.

## Recommended: Export Station Data Without Browser

This method uses the SEMS backend API directly, so it does not need Chrome,
Safari, Selenium, or manual login.

```bash
export SEMS_USERNAME="your-email"
export SEMS_PASSWORD="your-password"
.venv/bin/python sems_api_to_excel.py
```

The script creates or appends to `sems_station_snapshot.xlsx` with one row per
station. Columns include station name, station ID, capacity, current power,
today generation, month generation, total generation, location, and
organization.

## Direct Backend Weekly Report

GoodWe/SEMS can refresh with username and password in the background. Fronius
Solar.web and FIMER/Aurora Vision currently still need either official API
access or a captured login flow before they can refresh without a browser.

```bash
export SEMS_USERNAME="your-email"
export SEMS_PASSWORD="your-password"
.venv/bin/python backend_weekly_report.py
```

This refreshes GoodWe directly, reuses the latest saved Fronius/FIMER data, and
creates:

- `outputs/numbers_compatible/numbers_compatible_generation_report.xlsx`
- `outputs/numbers_compatible/numbers_compatible_generation_report.csv`
- `outputs/numbers_compatible/backend_refresh_status.json`

## Export Excel From SEMS Portal

There is also a Selenium-based browser script:

```bash
export SEMS_USERNAME="your-email"
export SEMS_PASSWORD="your-password"
SE_CACHE_PATH="$PWD/.selenium-cache" .venv/bin/python sems_export_excel.py
```

If you are already logged in to SEMS Portal in Google Chrome, open Chrome with a
debugging port first, then attach the script to that browser:

```bash
open -na "Google Chrome" --args \
  --remote-debugging-port=9222 \
  --user-data-dir="$PWD/.chrome-manual-profile"
```

Log in to SEMS Portal in that Chrome window. Then run:

```bash
SEMS_DEBUGGER_ADDRESS="127.0.0.1:9222" \
SE_CACHE_PATH="$PWD/.selenium-cache" \
.venv/bin/python sems_export_excel.py
```

Selenium cannot attach to a regular Chrome window that was opened without the
remote debugging port.

## Use Safari Instead

Safari can also be used, but first enable automation:

1. Open Safari.
2. Go to Safari > Settings > Advanced.
3. Enable "Show features for web developers".
4. In the menu bar, go to Develop and enable "Allow Remote Automation".

Then run:

```bash
export SEMS_BROWSER="safari"
export SEMS_USERNAME="your-email"
export SEMS_PASSWORD="your-password"
SE_CACHE_PATH="$PWD/.selenium-cache" .venv/bin/python sems_export_excel.py
```

Safari automation opens a controlled Safari window. It usually cannot attach to
an existing normal Safari tab that is already logged in.

## Fronius Solar.web Login Test

For Fronius systems, use Solar.web instead of SEMS:

```bash
export FRONIUS_USERNAME="your-email"
export FRONIUS_PASSWORD="your-password"
SE_CACHE_PATH="$PWD/.selenium-cache" .venv/bin/python fronius_solarweb_login.py
```

The script logs in only. The report/export page and download button selector
must be added after checking the live Solar.web account page.

If Chrome cannot be started by Selenium, use attach mode:

```bash
open -na "Google Chrome" --args \
  --remote-debugging-port=9223 \
  --user-data-dir="$PWD/.fronius-manual-profile"
```

Log in to Solar.web in that Chrome window, then run:

```bash
FRONIUS_DEBUGGER_ADDRESS="127.0.0.1:9223" \
SE_CACHE_PATH="$PWD/.selenium-cache" \
.venv/bin/python fronius_solarweb_login.py
```

## FIMER / Aurora Vision Login Test

For FIMER systems, the portal is often Aurora Vision:

```bash
export FIMER_URL="https://www.auroravision.net/"
export FIMER_USERNAME="your-email"
export FIMER_PASSWORD="your-password"
SE_CACHE_PATH="$PWD/.selenium-cache" .venv/bin/python fimer_login.py
```

If Chrome cannot be started by Selenium, use attach mode:

```bash
open -na "Google Chrome" --args \
  --remote-debugging-port=9224 \
  --user-data-dir="$PWD/.fimer-manual-profile"
```

Log in to the FIMER/Aurora Vision portal in that Chrome window, then run:

```bash
FIMER_DEBUGGER_ADDRESS="127.0.0.1:9224" \
SE_CACHE_PATH="$PWD/.selenium-cache" \
.venv/bin/python fimer_login.py
```

Do not write your actual email or password inside `os.environ[...]`. The value
inside the brackets must be the environment variable name, for example:

```python
email = os.environ["SEMS_USERNAME"]
password = os.environ["SEMS_PASSWORD"]
```

If SEMS opens a different report page for your account, set:

```bash
export SEMS_REPORT_URL="https://www.semsportal.com/your/report/page"
```

## SolisCloud Login Test

SolisCloud can be added to the same report. First log in or attach to a Chrome
window so the data URLs for the account can be inspected.

```bash
export SOLIS_USERNAME="your-email"
export SOLIS_PASSWORD="your-password"
SE_CACHE_PATH="$PWD/.selenium-cache" .venv/bin/python solis_login.py
```

Attach mode:

```bash
open -na "Google Chrome" --args \
  --remote-debugging-port=9225 \
  --user-data-dir="$PWD/.solis-manual-profile"
```

Log in to SolisCloud in that Chrome window, then run:

```bash
SOLIS_DEBUGGER_ADDRESS="127.0.0.1:9225" \
SE_CACHE_PATH="$PWD/.selenium-cache" \
.venv/bin/python solis_inspect_page.py
```

The combined report automatically includes Solis rows when
`solis_generation.json` exists. Use `solis_generation.example.json` as the
expected format.

### Solis Manual Export Import

If browser automation is difficult, export plant data from SolisCloud as Excel
or CSV and put the file in:

```bash
solis_imports/
```

Then run:

```bash
.venv/bin/python solis_import_export.py
.venv/bin/python build_numbers_compatible_report.py
.venv/bin/python build_pdf_generation_report.py
```

This creates `solis_generation.json` and adds Solis to the same combined
report.

## SolaXCloud Login Capture

SolaXCloud can be added to the same combined report with a real browser login.
The script reads credentials from environment variables, opens Chrome, captures
dashboard API responses, and saves `solax_network_capture.json`.

```bash
export SOLAX_USERNAME="your-email"
export SOLAX_PASSWORD="your-password"
PYTHONPYCACHEPREFIX="$PWD/.pycache" .venv/bin/python ./solax_manual_login_capture.py
```

The combined report automatically includes SolaX rows when
`solax_generation.json` exists. Use `solax_generation.example.json` as the
expected format.

## Notes

The original pasted code used smart quotes around the username and password,
which Python cannot parse. This version also avoids storing credentials directly
in the script.
