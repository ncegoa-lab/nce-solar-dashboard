import json
import os
import time
from pathlib import Path

from selenium import webdriver


SYSTEMS_FILE = Path("fronius_systems.json")
OUTPUT_FILE = Path("fronius_energy_balance_inspection.json")


def main() -> None:
    systems = json.loads(SYSTEMS_FILE.read_text(encoding="utf-8"))["systems"]
    system = systems[int(os.getenv("FRONIUS_SYSTEM_INDEX", "0"))]
    url = (
        os.getenv("FRONIUS_ENERGY_URL")
        or f"https://www.solarweb.com/Chart/Chart?pvSystemId={system['system_id']}"
    )

    options = webdriver.ChromeOptions()
    options.add_experimental_option(
        "debuggerAddress",
        os.getenv("FRONIUS_DEBUGGER_ADDRESS", "127.0.0.1:9223"),
    )
    driver = webdriver.Chrome(options=options)

    try:
        driver.get(url)
        time.sleep(8)
        details = driver.execute_script(
            """
            const visible = el => (el.innerText || el.value || el.title || el.ariaLabel || '').trim();
            const resources = performance.getEntriesByType('resource').map(r => r.name)
              .filter(name => /Chart|Energy|Balance|PvSystem|Report|Data|Production|api|Get/i.test(name));
            return {
              system: arguments[0],
              url: location.href,
              title: document.title,
              bodyText: document.body.innerText.slice(0, 16000),
              resources: resources.slice(-300),
              inputs: Array.from(document.querySelectorAll('input, select, textarea')).map(el => ({
                tag: el.tagName,
                type: el.getAttribute('type'),
                name: el.getAttribute('name'),
                id: el.id,
                value: el.value,
                placeholder: el.getAttribute('placeholder'),
                label: el.labels && el.labels[0] ? el.labels[0].innerText.trim() : '',
              })).slice(0, 300),
              buttons: Array.from(document.querySelectorAll('button, [role="button"], input[type="button"], input[type="submit"], a.btn')).map(b => ({
                text: visible(b),
                tag: b.tagName,
                href: b.href || '',
                classes: b.className,
                aria: b.getAttribute('aria-label'),
                title: b.getAttribute('title'),
              })).filter(x => x.text || x.href || x.classes || x.aria || x.title).slice(0, 300),
            };
            """,
            system,
        )
        OUTPUT_FILE.write_text(json.dumps(details, indent=2), encoding="utf-8")
        print(f"Saved energy balance inspection to {OUTPUT_FILE}")
        print(f"Page: {details['title']} | {details['url']}")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
