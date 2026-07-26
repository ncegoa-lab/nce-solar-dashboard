import json
import os
from pathlib import Path

from selenium import webdriver


OUTPUT_FILE = Path("fimer_page_inspection.json")


def main() -> None:
    options = webdriver.ChromeOptions()
    options.add_experimental_option(
        "debuggerAddress",
        os.getenv("FIMER_DEBUGGER_ADDRESS", "127.0.0.1:9224"),
    )
    driver = webdriver.Chrome(options=options)

    try:
        details = driver.execute_script(
            """
            const visible = el => (el.innerText || el.value || el.title || el.ariaLabel || '').trim();
            return {
              url: location.href,
              title: document.title,
              bodyText: document.body.innerText.slice(0, 12000),
              links: Array.from(document.querySelectorAll('a[href]')).map(a => ({
                text: visible(a),
                href: a.href,
              })).filter(x => x.text || x.href).slice(0, 300),
              buttons: Array.from(document.querySelectorAll('button, [role="button"], input[type="button"], input[type="submit"], a.btn')).map(b => ({
                text: visible(b),
                tag: b.tagName,
                type: b.getAttribute('type'),
                href: b.href || '',
                aria: b.getAttribute('aria-label'),
                title: b.getAttribute('title'),
                classes: b.className,
              })).filter(x => x.text || x.href || x.aria || x.title || x.classes).slice(0, 300),
              inputs: Array.from(document.querySelectorAll('input, select, textarea')).map(el => ({
                tag: el.tagName,
                type: el.getAttribute('type'),
                name: el.getAttribute('name'),
                id: el.id,
                value: el.value,
                placeholder: el.getAttribute('placeholder'),
              })).slice(0, 300),
              resources: performance.getEntriesByType('resource').map(r => r.name)
                .filter(name => /plant|site|inverter|energy|power|api|report|export|production|data/i.test(name))
                .slice(-300),
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
