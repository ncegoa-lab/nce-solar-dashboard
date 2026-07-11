import json
import os
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


SOLIS_URL = "https://www.soliscloud.com/"
STATION_URL = "https://www.soliscloud.com/station?glyun_vue2=%2F%23%2Fstation"
OUTPUT_FILE = Path("solis_network_capture.json")


def require_env(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def first_visible(driver, selectors):
    for by, selector in selectors:
        try:
            element = driver.find_element(by, selector)
            if element.is_displayed():
                return element
        except Exception:
            pass
    return None


def visible_enabled_inputs(driver, input_type=None):
    script = """
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
    """
    return driver.execute_script(script, input_type)


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


def maybe_fill_login(driver, wait):
    username = require_env("SOLIS_USERNAME")
    password = require_env("SOLIS_PASSWORD")

    wait.until(lambda current_driver: visible_enabled_inputs(current_driver))
    password_fields = visible_enabled_inputs(driver, "password")
    if not password_fields:
        raise RuntimeError("Could not find a visible SolisCloud password field")
    password_field = password_fields[0]

    text_fields = [
        element
        for element in visible_enabled_inputs(driver)
        if (element.get_attribute("type") or "text").lower()
        in ("text", "email", "tel", "")
    ]
    if not text_fields:
        raise RuntimeError("Could not find a visible SolisCloud username field")
    username_field = text_fields[0]

    set_input_value(driver, username_field, username)
    set_input_value(driver, password_field, password)

    checkbox = first_visible(driver, [(By.XPATH, "//input[@type='checkbox']")])
    if checkbox and not checkbox.is_selected():
        driver.execute_script("arguments[0].click();", checkbox)

    login_button = first_visible(
        driver,
        [
            (By.XPATH, "//button[contains(., 'Login') or contains(., 'Log In') or contains(., 'Sign in')]"),
            (By.XPATH, "//button[@type='submit']"),
        ],
    )
    if login_button:
        login_button.click()


def capture_interesting_responses(driver):
    interesting = []
    logs = driver.get_log("performance")
    seen = set()
    requests = {}
    for entry in logs:
        try:
            message = json.loads(entry["message"])["message"]
        except Exception:
            continue
        if message.get("method") == "Network.requestWillBeSent":
            params = message.get("params", {})
            request = params.get("request", {})
            request_id = params.get("requestId")
            if request_id:
                requests[request_id] = {
                    "method": request.get("method"),
                    "url": request.get("url"),
                    "headers": request.get("headers") or {},
                    "postData": request.get("postData"),
                }
            continue
        if message.get("method") != "Network.responseReceived":
            continue
        params = message.get("params", {})
        request_id = params.get("requestId")
        response = params.get("response", {})
        url = response.get("url", "")
        if request_id in seen:
            continue
        if not any(
            token in url.lower()
            for token in ("/api/", "station", "plant", "inverter", "energy", "power")
        ):
            continue
        seen.add(request_id)
        item = {
            "requestId": request_id,
            "url": url,
            "status": response.get("status"),
            "mimeType": response.get("mimeType"),
            "request": requests.get(request_id, {}),
        }
        try:
            body = driver.execute_cdp_cmd("Network.getResponseBody", {"requestId": request_id})
            item["body"] = body.get("body")
            item["base64Encoded"] = body.get("base64Encoded")
        except Exception as error:
            item["bodyError"] = str(error)
        interesting.append(item)
    return interesting


def direct_refetch_station_list(driver, responses):
    station_calls = [
        item for item in responses
        if str(item.get("url", "")).endswith("/api/station/list")
    ]
    if not station_calls:
        return None

    request = station_calls[-1].get("request") or {}
    url = request.get("url") or station_calls[-1].get("url")
    method = request.get("method") or "POST"
    post_data = request.get("postData")
    result = driver.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const url = arguments[0];
        const method = arguments[1] || "POST";
        const postData = arguments[2];
        const options = {
          method,
          credentials: "include",
          cache: "no-store",
          headers: {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "Pragma": "no-cache",
            "Cache-Control": "no-cache"
          }
        };
        if (postData && method.toUpperCase() !== "GET") options.body = postData;
        fetch(url, options)
          .then(async response => done({
            ok: response.ok,
            status: response.status,
            url: response.url,
            body: await response.text()
          }))
          .catch(error => done({ ok: false, error: String(error), url }));
        """,
        url,
        method,
        post_data,
    )
    if not result or not result.get("body"):
        return None
    return {
        "requestId": "direct-station-list-refetch",
        "url": "https://v3.soliscloud.com/api/station/list",
        "status": result.get("status"),
        "mimeType": "application/json",
        "directFetch": True,
        "request": {
            "method": method,
            "url": url,
            "postData": post_data,
        },
        "body": result.get("body"),
    }


def main():
    options = webdriver.ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument(f"--user-data-dir={Path('.solis-selenium-profile').resolve()}")
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    driver = webdriver.Chrome(options=options)
    wait = WebDriverWait(driver, 120)

    try:
        driver.execute_cdp_cmd("Network.enable", {})
        driver.get(SOLIS_URL)
        time.sleep(4)

        if "login" in driver.current_url.lower() or first_visible(
            driver, [(By.XPATH, "//input[@type='password']")]
        ):
            maybe_fill_login(driver, wait)
            print("If SolisCloud shows a slider CAPTCHA, solve it in the browser.")

        wait.until(
            lambda current_driver: "login" not in current_driver.current_url.lower()
            and ("soliscloud.com" in current_driver.current_url.lower())
        )
        driver.get(STATION_URL)
        print("Waiting for SolisCloud station data to load...")
        time.sleep(int(os.getenv("SOLIS_CAPTURE_SECONDS", "45")))

        responses = capture_interesting_responses(driver)
        direct_station = direct_refetch_station_list(driver, responses)
        if direct_station:
            responses.append(direct_station)
            print("Direct Solis station list fetch completed.")

        payload = {
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "url": driver.current_url,
            "title": driver.title,
            "bodyText": driver.execute_script("return document.body.innerText.slice(0, 5000)"),
            "responses": responses,
        }
        OUTPUT_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Saved Solis capture to {OUTPUT_FILE.resolve()}")
        print(f"Captured {len(payload['responses'])} interesting responses")
    finally:
        if os.getenv("SOLIS_KEEP_BROWSER_OPEN", "0") == "1":
            print("Leaving browser open for review.")
        else:
            driver.quit()


if __name__ == "__main__":
    main()
