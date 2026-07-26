import json
import os
from pathlib import Path

from selenium import webdriver


OUTPUT_FILE = Path("fronius_systems.json")


def main() -> None:
    debugger_address = os.getenv("FRONIUS_DEBUGGER_ADDRESS", "127.0.0.1:9223")
    options = webdriver.ChromeOptions()
    options.add_experimental_option("debuggerAddress", debugger_address)
    driver = webdriver.Chrome(options=options)

    try:
        driver.get("https://www.solarweb.com/PvSystems/Widgets")
        systems = driver.execute_script(
            """
            return Array.from(document.querySelectorAll('a[href*="pvSystemId="]')).map(a => {
              const url = new URL(a.href);
              const text = (a.innerText || '').trim();
              const lines = text.split('\\n').map(x => x.trim()).filter(Boolean);
              return {
                system_id: url.searchParams.get('pvSystemId'),
                name: lines[0] || '',
                status: lines.slice(1).join(' ') || '',
                url: a.href,
              };
            }).filter(x => x.system_id && x.name);
            """
        )

        payload = {
            "source": "Fronius Solar.web",
            "page": driver.current_url,
            "systems": systems,
        }
        OUTPUT_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Saved {len(systems)} Fronius systems to {OUTPUT_FILE}")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
