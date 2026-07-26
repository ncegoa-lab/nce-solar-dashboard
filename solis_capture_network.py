import datetime as dt
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from selenium import webdriver


OUTPUT_FILE = Path("solis_network_capture.json")
STATION_URL = "https://www.soliscloud.com/station?glyun_vue2=%2F%23%2Fstation"


CAPTURE_SCRIPT = r"""
(() => {
  window.__solisCapturedFetches = [];
  window.__solisCapturedXhrs = [];
  const maxBody = 20000;

  function short(value) {
    if (value === undefined || value === null) return null;
    const text = typeof value === 'string' ? value : JSON.stringify(value);
    return text.length > maxBody ? text.slice(0, maxBody) + '...' : text;
  }

  const originalFetch = window.fetch;
  window.fetch = async function(input, init = {}) {
    const startedAt = new Date().toISOString();
    const url = typeof input === 'string' ? input : input && input.url;
    const method = (init && init.method) || (input && input.method) || 'GET';
    const requestBody = init && init.body ? short(init.body) : null;
    const entry = { type: 'fetch', startedAt, url, method, requestBody };
    window.__solisCapturedFetches.push(entry);
    try {
      const response = await originalFetch.apply(this, arguments);
      entry.status = response.status;
      entry.ok = response.ok;
      entry.responseUrl = response.url;
      try {
        const text = await response.clone().text();
        entry.responseText = short(text);
      } catch (error) {
        entry.responseReadError = String(error);
      }
      return response;
    } catch (error) {
      entry.error = String(error);
      throw error;
    }
  };

  const OriginalXHR = window.XMLHttpRequest;
  const originalOpen = OriginalXHR.prototype.open;
  const originalSend = OriginalXHR.prototype.send;
  OriginalXHR.prototype.open = function(method, url) {
    this.__solisMethod = method;
    this.__solisUrl = url;
    return originalOpen.apply(this, arguments);
  };
  OriginalXHR.prototype.send = function(body) {
    const entry = {
      type: 'xhr',
      startedAt: new Date().toISOString(),
      url: this.__solisUrl,
      method: this.__solisMethod || 'GET',
      requestBody: short(body),
    };
    window.__solisCapturedXhrs.push(entry);
    this.addEventListener('loadend', () => {
      entry.status = this.status;
      entry.responseUrl = this.responseURL;
      entry.responseText = short(this.responseText);
    });
    return originalSend.apply(this, arguments);
  };
})();
"""


def main():
    debugger_address = os.getenv("SOLIS_DEBUGGER_ADDRESS", "127.0.0.1:9225")
    try:
        with urllib.request.urlopen(f"http://{debugger_address}/json/version", timeout=3) as response:
            print(response.read().decode("utf-8")[:500])
    except (OSError, urllib.error.URLError) as error:
        raise RuntimeError(
            "Chrome debug port is not reachable. Close the Solis Chrome window, "
            "start Chrome again with --remote-debugging-port=9225, then confirm "
            f"http://{debugger_address}/json/version opens before rerunning. "
            f"Original error: {error}"
        ) from error

    options = webdriver.ChromeOptions()
    options.add_experimental_option("debuggerAddress", debugger_address)
    driver = webdriver.Chrome(options=options)

    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": CAPTURE_SCRIPT},
        )
        driver.get(STATION_URL)
        time.sleep(int(os.getenv("SOLIS_CAPTURE_SECONDS", "25")))

        payload = driver.execute_script(
            """
            return {
              generated_at: new Date().toISOString(),
              title: document.title,
              url: location.href,
              bodyText: document.body.innerText.slice(0, 5000),
              fetches: window.__solisCapturedFetches || [],
              xhrs: window.__solisCapturedXhrs || [],
              resources: performance.getEntriesByType('resource').map(entry => entry.name),
              localStorage: Object.fromEntries(Object.entries(localStorage).filter(([key]) => !/pass|psw|pwd|secret/i.test(key))),
              sessionStorageKeys: Object.keys(sessionStorage),
            };
            """
        )
        payload["captured_at"] = dt.datetime.now().replace(microsecond=0).isoformat()
        OUTPUT_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Saved Solis network capture to {OUTPUT_FILE.resolve()}")
        print(f"Captured {len(payload.get('fetches', []))} fetches and {len(payload.get('xhrs', []))} XHRs")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
