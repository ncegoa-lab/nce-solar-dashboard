import json
import os
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait


OUTPUT_FILE = Path("solis_page_inspection.json")


def main():
    options = webdriver.ChromeOptions()
    options.add_experimental_option(
        "debuggerAddress",
        os.getenv("SOLIS_DEBUGGER_ADDRESS", "127.0.0.1:9225"),
    )
    driver = webdriver.Chrome(options=options)

    try:
        driver.get(os.getenv("SOLIS_PAGE_URL", "https://www.soliscloud.com/"))
        wait = WebDriverWait(driver, 30)
        wait.until(lambda current_driver: current_driver.execute_script("return document.readyState") == "complete")
        time.sleep(8)

        possible_routes = [
            "https://www.soliscloud.com/#/station",
            "https://www.soliscloud.com/#/plant",
            "https://www.soliscloud.com/#/home",
            "https://www.soliscloud.com/#/index",
        ]
        route_results = []
        for route in possible_routes:
            driver.get(route)
            time.sleep(5)
            route_results.append(
                {
                    "route": route,
                    "url": driver.current_url,
                    "title": driver.title,
                    "bodyText": driver.execute_script("return document.body.innerText.slice(0, 1200)"),
                    "resourceCount": driver.execute_script(
                        "return performance.getEntriesByType('resource').length"
                    ),
                }
            )

        storage_keys = driver.execute_script(
            """
            const output = {};
            for (const areaName of ['localStorage', 'sessionStorage']) {
              const area = window[areaName];
              output[areaName] = {};
              for (let index = 0; index < area.length; index++) {
                const key = area.key(index);
                const value = area.getItem(key);
                if (/pass|psw|pwd|secret/i.test(key)) {
                  output[areaName][key] = '<hidden>';
                } else {
                  output[areaName][key] = value && value.length > 500
                    ? value.slice(0, 500) + '...'
                    : value;
                }
              }
            }
            return output;
            """
        )
        payload = driver.execute_script(
            """
            return {
              title: document.title,
              url: location.href,
              bodyText: document.body.innerText.slice(0, 4000),
              links: Array.from(document.querySelectorAll('a[href]')).map(a => ({
                text: (a.innerText || '').trim().slice(0, 120),
                href: a.href,
              })).filter(x => x.text || x.href),
              resources: performance.getEntriesByType('resource')
                .map(entry => entry.name)
                .filter(name => /solis|station|plant|inverter|energy|power|api|epc|atRead|login/i.test(name))
                .slice(-500),
            };
            """
        )
        payload["routeResults"] = route_results
        payload["storage"] = storage_keys
        OUTPUT_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Saved SolisCloud page inspection to {OUTPUT_FILE}")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
