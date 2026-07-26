import json
import os
import time
from pathlib import Path

from selenium import webdriver


FIMER_FILE = Path("fimer_generation.json")
OUTPUT_FILE = Path("fimer_plant_inspection.json")


def main() -> None:
    plants = json.loads(FIMER_FILE.read_text(encoding="utf-8"))["plants"]["body"]
    plant = plants[int(os.getenv("FIMER_PLANT_INDEX", "0"))]
    url = os.getenv("FIMER_PLANT_URL") or f"https://www.auroravision.net/dashboard/#{plant['entityID']}"

    options = webdriver.ChromeOptions()
    options.add_experimental_option(
        "debuggerAddress",
        os.getenv("FIMER_DEBUGGER_ADDRESS", "127.0.0.1:9224"),
    )
    driver = webdriver.Chrome(options=options)

    try:
        driver.get(url)
        time.sleep(8)
        details = driver.execute_script(
            """
            const visible = el => (el.innerText || el.value || el.title || el.ariaLabel || '').trim();
            return {
              plant: arguments[0],
              url: location.href,
              title: document.title,
              bodyText: document.body.innerText.slice(0, 14000),
              links: Array.from(document.querySelectorAll('a[href]')).map(a => ({
                text: visible(a),
                href: a.href,
              })).filter(x => x.text || x.href).slice(0, 300),
              resources: performance.getEntriesByType('resource').map(r => r.name)
                .filter(name => /plant|site|inverter|energy|power|api|report|export|production|data|telemetry|agp/i.test(name))
                .slice(-400),
            };
            """,
            plant,
        )
        OUTPUT_FILE.write_text(json.dumps(details, indent=2), encoding="utf-8")
        print(f"Saved FIMER plant inspection to {OUTPUT_FILE}")
        print(f"Page: {details['title']} | {details['url']}")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
