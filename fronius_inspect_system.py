import json
import os
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait


SYSTEMS_FILE = Path("fronius_systems.json")
OUTPUT_FILE = Path("fronius_system_detail_inspection.json")


def main() -> None:
    systems = json.loads(SYSTEMS_FILE.read_text(encoding="utf-8"))["systems"]
    target_url = os.getenv("FRONIUS_SYSTEM_URL") or systems[0]["url"]

    options = webdriver.ChromeOptions()
    options.add_experimental_option(
        "debuggerAddress",
        os.getenv("FRONIUS_DEBUGGER_ADDRESS", "127.0.0.1:9223"),
    )
    driver = webdriver.Chrome(options=options)
    wait = WebDriverWait(driver, 30)

    try:
        driver.get(target_url)
        wait.until(lambda current_driver: current_driver.execute_script("return document.readyState") == "complete")
        detail = driver.execute_script(
            """
            const visible = el => (el.innerText || el.value || el.title || el.ariaLabel || '').trim();
            return {
              url: location.href,
              title: document.title,
              bodyText: document.body.innerText.slice(0, 12000),
              links: Array.from(document.querySelectorAll('a[href]')).map(a => ({
                text: visible(a),
                href: a.href,
              })).filter(x => x.text || x.href).slice(0, 250),
              buttons: Array.from(document.querySelectorAll('button, [role="button"], input[type="button"], input[type="submit"]')).map(b => ({
                text: visible(b),
                tag: b.tagName,
                type: b.getAttribute('type'),
                aria: b.getAttribute('aria-label'),
                title: b.getAttribute('title'),
                classes: b.className,
              })).filter(x => x.text || x.aria || x.title || x.classes).slice(0, 250),
            };
            """
        )
        OUTPUT_FILE.write_text(json.dumps(detail, indent=2), encoding="utf-8")
        print(f"Saved system detail inspection to {OUTPUT_FILE}")
        print(f"Page: {detail['title']} | {detail['url']}")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
