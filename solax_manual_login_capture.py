import json
import os
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


SOLAX_URL = "https://www.solaxcloud.com/"
OUTPUT_FILE = Path("solax_network_capture.json")


def require_env(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def visible_enabled_inputs(driver, input_type=None):
    return driver.execute_script(
        """
        const inputType = arguments[0];
        return Array.from(document.querySelectorAll('input')).filter(input => {
          const rect = input.getBoundingClientRect();
          const style = window.getComputedStyle(input);
          const type = (input.getAttribute('type') || 'text').toLowerCase();
          return rect.width > 0
            && rect.height > 0
            && style.visibility !== 'hidden'
            && style.display !== 'none'
            && !input.disabled
            && !input.readOnly
            && (!inputType || type === inputType);
        });
        """,
        input_type,
    )


def set_input_value(driver, element, value):
    try:
        element.click()
        element.clear()
        element.send_keys(value)
        return
    except Exception:
        pass
    driver.execute_script(
        """
        const input = arguments[0];
        const value = arguments[1];
        input.focus();
        input.value = value;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
        """,
        element,
        value,
    )


def first_clickable(driver, xpaths):
    for xpath in xpaths:
        try:
            element = driver.find_element(By.XPATH, xpath)
            if element.is_displayed() and element.is_enabled():
                return element
        except Exception:
            pass
    return None


def visible_button_report(driver):
    return driver.execute_script(
        """
        return Array.from(document.querySelectorAll('button, input[type=button], input[type=submit], .el-button, [role=button], a'))
          .map((element, index) => {
            const rect = element.getBoundingClientRect();
            const style = window.getComputedStyle(element);
            return {
              index,
              tag: element.tagName,
              type: element.getAttribute('type'),
              text: (element.innerText || element.value || element.getAttribute('aria-label') || '').trim(),
              className: element.className,
              visible: rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden',
              disabled: element.disabled || element.getAttribute('aria-disabled') === 'true'
            };
          })
          .filter(item => item.visible);
        """
    )


def click_login_control(driver):
    login_button = first_clickable(
        driver,
        [
            "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'login')]",
            "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'log in')]",
            "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'sign in')]",
            "//button[@type='submit']",
            "//*[@role='button' and contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'login')]",
            "//*[contains(@class, 'login') and (self::button or self::div or self::span)]",
        ],
    )
    if login_button:
        driver.execute_script("arguments[0].click();", login_button)
        return

    clicked = driver.execute_script(
        """
        const controls = Array.from(document.querySelectorAll('button, input[type=button], input[type=submit], .el-button, [role=button], div, span'));
        const candidates = controls.filter(element => {
          const rect = element.getBoundingClientRect();
          const style = window.getComputedStyle(element);
          const text = (element.innerText || element.value || '').trim().toLowerCase();
          const cls = String(element.className || '').toLowerCase();
          return rect.width > 0
            && rect.height > 0
            && style.display !== 'none'
            && style.visibility !== 'hidden'
            && !element.disabled
            && (
              text === 'login'
              || text === 'log in'
              || text.includes('login')
              || text.includes('sign in')
              || cls.includes('login')
            );
        });
        if (!candidates.length) return false;
        candidates[0].click();
        return true;
        """
    )
    if not clicked:
        raise RuntimeError(
            "Could not find SolaX login button. Visible controls: "
            + json.dumps(visible_button_report(driver), indent=2)
        )


def fill_login(driver, wait):
    username = require_env("SOLAX_USERNAME")
    password = require_env("SOLAX_PASSWORD")

    wait.until(lambda current_driver: visible_enabled_inputs(current_driver))
    password_fields = visible_enabled_inputs(driver, "password")
    if not password_fields:
        raise RuntimeError("Could not find a visible SolaX password field")
    password_field = password_fields[0]

    text_fields = [
        element
        for element in visible_enabled_inputs(driver)
        if (element.get_attribute("type") or "text").lower()
        in ("text", "email", "tel", "")
    ]
    if not text_fields:
        raise RuntimeError("Could not find a visible SolaX username field")
    username_field = text_fields[0]

    set_input_value(driver, username_field, username)
    set_input_value(driver, password_field, password)

    checkbox = first_clickable(driver, ["//input[@type='checkbox']"])
    if checkbox and not checkbox.is_selected():
        driver.execute_script("arguments[0].click();", checkbox)

    click_login_control(driver)


def capture_interesting_responses(driver):
    interesting = []
    seen = set()
    for entry in driver.get_log("performance"):
        try:
            message = json.loads(entry["message"])["message"]
        except Exception:
            continue
        if message.get("method") != "Network.responseReceived":
            continue
        params = message.get("params", {})
        request_id = params.get("requestId")
        response = params.get("response", {})
        url = response.get("url", "")
        if request_id in seen:
            continue
        lower_url = url.lower()
        is_solax_app_asset = (
            "solaxcloud.com/green/" in lower_url
            and (
                lower_url.endswith(".js")
                or "/assets/" in lower_url
                or response.get("mimeType") == "application/javascript"
            )
        )
        is_data_response = any(
            token in lower_url
            for token in (
                "api",
                "station",
                "plant",
                "inverter",
                "device",
                "energy",
                "power",
                "yield",
                "zeus",
                "statistic",
                "site",
            )
        )
        if not (is_data_response or is_solax_app_asset):
            continue
        seen.add(request_id)
        item = {
            "requestId": request_id,
            "url": url,
            "status": response.get("status"),
            "mimeType": response.get("mimeType"),
        }
        try:
            body = driver.execute_cdp_cmd("Network.getResponseBody", {"requestId": request_id})
            item["body"] = body.get("body")
            item["base64Encoded"] = body.get("base64Encoded")
        except Exception as error:
            item["bodyError"] = str(error)
        interesting.append(item)
    return interesting


def click_plants_if_available(driver):
    clicked = driver.execute_script(
        """
        const candidates = Array.from(document.querySelectorAll('a, button, div, span, li')).filter(element => {
          const rect = element.getBoundingClientRect();
          const style = window.getComputedStyle(element);
          const text = (element.innerText || '').trim().toLowerCase();
          return rect.width > 0
            && rect.height > 0
            && style.display !== 'none'
            && style.visibility !== 'hidden'
            && (text === 'plants' || text.split(/\\s+/).includes('plants'));
        });
        if (!candidates.length) return false;
        candidates.sort((a, b) => {
          const aText = (a.innerText || '').trim().toLowerCase();
          const bText = (b.innerText || '').trim().toLowerCase();
          return (aText === 'plants' ? 0 : 1) - (bText === 'plants' ? 0 : 1);
        });
        candidates[0].click();
        return true;
        """
    )
    if clicked:
        time.sleep(8)
    return clicked


def open_plants_page(driver):
    for url in (
        "https://global.solaxcloud.com/green/#/plants",
        "https://global.solaxcloud.com/green/#/plant",
        "https://global.solaxcloud.com/green/#/plants/list",
    ):
        driver.get(url)
        time.sleep(8)
        body_text = driver.execute_script("return document.body.innerText || ''")
        if any(token in body_text.lower() for token in ("plant name", "plant status", "pv capacity")):
            return True
    return click_plants_if_available(driver)


def capture_visible_tables(driver):
    return driver.execute_script(
        """
        const selectors = [
          '.el-table__body tr',
          '.ant-table-row',
          'tbody tr',
          '[role=row]',
          '.plant-card',
          '.card',
          '.list-item'
        ];
        const rows = [];
        for (const selector of selectors) {
          for (const element of document.querySelectorAll(selector)) {
            const rect = element.getBoundingClientRect();
            const style = window.getComputedStyle(element);
            const text = (element.innerText || '').trim();
            if (rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden' && text) {
              rows.push({ selector, text });
            }
          }
        }
        return rows;
        """
    )


def capture_browser_storage(driver):
    return driver.execute_script(
        """
        const dump = {};
        for (const storageName of ['localStorage', 'sessionStorage']) {
          const storage = window[storageName];
          dump[storageName] = {};
          for (let index = 0; index < storage.length; index += 1) {
            const key = storage.key(index);
            const value = storage.getItem(key);
            if (/(plant|station|site|power|energy|user|token|account)/i.test(key + ' ' + value)) {
              dump[storageName][key] = value && value.length > 5000 ? value.slice(0, 5000) : value;
            }
          }
        }
        return dump;
        """
    )


def capture_script_urls(driver):
    return driver.execute_script(
        """
        return Array.from(document.scripts)
          .map(script => script.src)
          .filter(Boolean);
        """
    )


def capture_plant_detail_pages(driver):
    details = []
    rows = capture_visible_tables(driver)
    plant_names = []
    for row in rows:
        lines = [line.strip() for line in row.get("text", "").splitlines() if line.strip()]
        if len(lines) >= 4 and lines[1].lower() == "residential":
            plant_names.append(lines[0])

    for plant_name in plant_names:
        open_plants_page(driver)
        time.sleep(2)
        clicked = driver.execute_script(
            """
            const plantName = arguments[0];
            const rows = Array.from(document.querySelectorAll('.el-table__body tr, tbody tr, [role=row]'));
            const row = rows.find(element => (element.innerText || '').includes(plantName));
            if (!row) return false;
            row.scrollIntoView({ block: 'center' });

            const candidates = [];
            const cells = Array.from(row.querySelectorAll('td, [role=cell]'));
            const lastCells = cells.slice(Math.max(cells.length - 3, 0));
            for (const cell of lastCells) {
              candidates.push(...Array.from(cell.querySelectorAll('button, a, span, div, i, svg, use')));
              candidates.push(cell);
            }
            candidates.push(...Array.from(row.querySelectorAll('button, a, span, div, i, svg, use')));
            candidates.push(row);

            const visible = candidates.filter(element => {
              const rect = element.getBoundingClientRect();
              const style = window.getComputedStyle(element);
              return rect.width > 0
                && rect.height > 0
                && style.display !== 'none'
                && style.visibility !== 'hidden'
                && !element.disabled;
            });

            const preferred = visible.find(element => {
              const text = (element.innerText || element.getAttribute('title') || element.getAttribute('aria-label') || '').trim().toLowerCase();
              const cls = String(element.className || '').toLowerCase();
              return /detail|view|monitor|overview|operation|chart|data/.test(text + ' ' + cls);
            });

            const before = location.href + '|' + (document.body.innerText || '').slice(0, 500);
            const target = preferred || visible[0];
            if (!target) return false;
            target.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, cancelable: true, view: window }));
            target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
            target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
            target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
            row.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true, view: window }));
            return before !== location.href + '|' + (document.body.innerText || '').slice(0, 500) || true;
            """,
            plant_name,
        )
        time.sleep(10)
        details.append(
            {
                "name": plant_name,
                "clicked": clicked,
                "url": driver.current_url,
                "title": driver.title,
                "bodyText": driver.execute_script("return document.body.innerText.slice(0, 30000)"),
                "visibleTables": capture_visible_tables(driver),
            }
        )
    open_plants_page(driver)
    return details


def main():
    options = webdriver.ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument(f"--user-data-dir={Path('.solax-selenium-profile').resolve()}")
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    driver = webdriver.Chrome(options=options)
    wait = WebDriverWait(driver, 120)

    try:
        driver.execute_cdp_cmd("Network.enable", {})
        driver.get(SOLAX_URL)
        time.sleep(5)
        if "login" in driver.current_url.lower() or visible_enabled_inputs(driver, "password"):
            fill_login(driver, wait)
            print("Login submitted. Complete any SolaX security prompt in the browser if shown.")

        time.sleep(int(os.getenv("SOLAX_CAPTURE_SECONDS", "45")))
        dashboard_text = driver.execute_script("return document.body.innerText.slice(0, 20000)")
        clicked_plants = open_plants_page(driver)
        plants_text = driver.execute_script("return document.body.innerText.slice(0, 20000)")
        plant_details = capture_plant_detail_pages(driver) if clicked_plants else []
        payload = {
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "url": driver.current_url,
            "title": driver.title,
            "bodyText": dashboard_text,
            "clickedPlants": clicked_plants,
            "plantsBodyText": plants_text if clicked_plants else "",
            "plantDetails": plant_details,
            "visibleTables": capture_visible_tables(driver),
            "browserStorage": capture_browser_storage(driver),
            "scriptUrls": capture_script_urls(driver),
            "responses": capture_interesting_responses(driver),
        }
        OUTPUT_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Saved SolaX capture to {OUTPUT_FILE.resolve()}")
        print(f"Captured {len(payload['responses'])} interesting responses")
    finally:
        if os.getenv("SOLAX_KEEP_BROWSER_OPEN", "0") == "1":
            print("Leaving browser open for review.")
        else:
            driver.quit()


if __name__ == "__main__":
    main()
