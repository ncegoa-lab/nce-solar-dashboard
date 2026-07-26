import json
import os
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


OUTPUT_FILE = Path("fronius_create_report_inspection.json")


def main() -> None:
    options = webdriver.ChromeOptions()
    options.add_experimental_option(
        "debuggerAddress",
        os.getenv("FRONIUS_DEBUGGER_ADDRESS", "127.0.0.1:9223"),
    )
    driver = webdriver.Chrome(options=options)
    wait = WebDriverWait(driver, 30)

    try:
        wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, "//button[contains(normalize-space(.), 'CREATE NEW REPORT')]")
            )
        ).click()
        wait.until(lambda current_driver: "Report" in current_driver.current_url)
        details = driver.execute_script(
            """
            const visible = el => (el.innerText || el.value || el.title || el.ariaLabel || '').trim();
            return {
              url: location.href,
              title: document.title,
              bodyText: document.body.innerText.slice(0, 12000),
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
                type: b.getAttribute('type'),
                href: b.href || '',
                classes: b.className,
              })).filter(x => x.text || x.href || x.classes).slice(0, 300),
            };
            """
        )
        OUTPUT_FILE.write_text(json.dumps(details, indent=2), encoding="utf-8")
        print(f"Saved create report inspection to {OUTPUT_FILE}")
        print(f"Page: {details['title']} | {details['url']}")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
