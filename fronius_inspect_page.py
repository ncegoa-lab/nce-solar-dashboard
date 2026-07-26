import json
import os
from pathlib import Path

from selenium import webdriver


OUTPUT_FILE = Path("fronius_page_inspection.json")


def main() -> None:
    debugger_address = os.getenv("FRONIUS_DEBUGGER_ADDRESS", "127.0.0.1:9223")
    options = webdriver.ChromeOptions()
    options.add_experimental_option("debuggerAddress", debugger_address)
    driver = webdriver.Chrome(options=options)

    try:
        details = driver.execute_script(
            """
            const visibleText = el => (el.innerText || el.value || el.title || el.ariaLabel || '').trim();
            const linkItems = Array.from(document.querySelectorAll('a[href]')).map(a => ({
              text: visibleText(a),
              href: a.href,
            })).filter(x => x.text || x.href);
            const buttonItems = Array.from(document.querySelectorAll('button, [role="button"], input[type="button"], input[type="submit"]')).map(b => ({
              text: visibleText(b),
              tag: b.tagName,
              type: b.getAttribute('type'),
              aria: b.getAttribute('aria-label'),
              title: b.getAttribute('title'),
              classes: b.className,
            })).filter(x => x.text || x.aria || x.title || x.classes);
            return {
              url: location.href,
              title: document.title,
              links: linkItems.slice(0, 250),
              buttons: buttonItems.slice(0, 250),
              bodyText: document.body.innerText.slice(0, 5000),
            };
            """
        )
        OUTPUT_FILE.write_text(json.dumps(details, indent=2), encoding="utf-8")
        print(f"Saved page inspection to {OUTPUT_FILE}")
        print(f"Page: {details['title']} | {details['url']}")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
