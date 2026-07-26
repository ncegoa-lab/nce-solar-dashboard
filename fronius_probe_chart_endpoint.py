import json
import os
from pathlib import Path

from selenium import webdriver


SYSTEMS_FILE = Path("fronius_systems.json")
OUTPUT_FILE = Path("fronius_chart_probe.json")


def main() -> None:
    system = json.loads(SYSTEMS_FILE.read_text(encoding="utf-8"))["systems"][0]
    url = (
        "https://www.solarweb.com/Chart/GetChartNew"
        f"?pvSystemId={system['system_id']}"
        "&year=2026&month=7&day=1&interval=day&view=production"
    )

    options = webdriver.ChromeOptions()
    options.add_experimental_option(
        "debuggerAddress",
        os.getenv("FRONIUS_DEBUGGER_ADDRESS", "127.0.0.1:9223"),
    )
    driver = webdriver.Chrome(options=options)

    try:
        result = driver.execute_async_script(
            """
            const url = arguments[0];
            const done = arguments[1];
            fetch(url, { credentials: 'include' })
              .then(r => r.text().then(t => ({ status: r.status, text: t })))
              .then(done)
              .catch(err => done({ error: String(err) }));
            """,
            url,
        )
        OUTPUT_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"Saved chart probe to {OUTPUT_FILE}")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
