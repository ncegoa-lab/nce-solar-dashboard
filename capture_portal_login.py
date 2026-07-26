import argparse
import datetime as dt
import json
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

from selenium import webdriver


OUTPUT_DIR = Path("outputs/login_capture")
PROFILE_ROOT = Path(".login-capture-profiles")

PORTALS = {
    "fronius": {
        "name": "Fronius Solar.web",
        "url": "https://www.solarweb.com/",
        "done_patterns": ["solarweb.com/PvSystems", "solarweb.com/Report", "solarweb.com/Chart"],
        "keep_url_words": ["solarweb", "login", "logon", "account", "auth", "token"],
    },
    "fimer": {
        "name": "FIMER Aurora Vision",
        "url": "https://www.auroravision.net/",
        "done_patterns": ["auroravision.net/home", "auroravision.net/dashboard"],
        "keep_url_words": ["auroravision", "login", "ums", "auth", "token", "session"],
    },
}

SECRET_HEADER_RE = re.compile(r"(cookie|authorization|token|secret|password|csrf)", re.I)
SECRET_FIELD_RE = re.compile(r"(password|passwd|pwd|token|secret|assertion|csrf|email|user|login)", re.I)


def redact_headers(headers):
    clean = {}
    for key, value in (headers or {}).items():
        clean[key] = "<redacted>" if SECRET_HEADER_RE.search(key) else value
    return clean


def redact_post_data(post_data):
    if not post_data:
        return None

    try:
        parsed = json.loads(post_data)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, dict):
        return {
            key: "<redacted>" if SECRET_FIELD_RE.search(str(key)) else value
            for key, value in parsed.items()
        }

    pairs = parse_qsl(post_data, keep_blank_values=True)
    if pairs:
        redacted = [
            (key, "<redacted>" if SECRET_FIELD_RE.search(key) else value)
            for key, value in pairs
        ]
        return urlencode(redacted)

    return "<redacted raw body>"


def should_keep(url, portal_config):
    lowered = (url or "").lower()
    return any(word in lowered for word in portal_config["keep_url_words"])


def event_from_log_entry(entry, portal_config):
    message = json.loads(entry["message"])["message"]
    method = message.get("method")
    params = message.get("params", {})

    if method == "Network.requestWillBeSent":
        request = params.get("request", {})
        url = request.get("url", "")
        if not should_keep(url, portal_config):
            return None
        return {
            "type": "request",
            "request_id": params.get("requestId"),
            "method": request.get("method"),
            "url": url,
            "headers": redact_headers(request.get("headers")),
            "post_data": redact_post_data(request.get("postData")),
        }

    if method == "Network.responseReceived":
        response = params.get("response", {})
        url = response.get("url", "")
        if not should_keep(url, portal_config):
            return None
        return {
            "type": "response",
            "request_id": params.get("requestId"),
            "url": url,
            "status": response.get("status"),
            "mime_type": response.get("mimeType"),
            "headers": redact_headers(response.get("headers")),
        }

    return None


def collect_events(driver, portal_config):
    events = []
    for entry in driver.get_log("performance"):
        event = event_from_log_entry(entry, portal_config)
        if event:
            events.append(event)
    return events


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("portal", choices=sorted(PORTALS))
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()

    portal_config = PORTALS[args.portal]
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_ROOT.mkdir(exist_ok=True)
    profile_dir = tempfile.mkdtemp(prefix=f"{args.portal}-", dir=PROFILE_ROOT)

    options = webdriver.ChromeOptions()
    options.add_argument(f"--user-data-dir={Path(profile_dir).resolve()}")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    driver = webdriver.Chrome(options=options)
    driver.execute_cdp_cmd("Network.enable", {})
    events = []
    started_at = dt.datetime.now().replace(microsecond=0)

    try:
        print(f"Opening {portal_config['name']} login page.")
        print("Please log in in the Chrome window that opened.")
        driver.get(portal_config["url"])

        deadline = time.time() + args.timeout
        while time.time() < deadline:
            events.extend(collect_events(driver, portal_config))
            current_url = driver.current_url
            if any(pattern in current_url for pattern in portal_config["done_patterns"]):
                time.sleep(3)
                events.extend(collect_events(driver, portal_config))
                break
            time.sleep(2)

        events.extend(collect_events(driver, portal_config))
    finally:
        final_url = driver.current_url
        driver.quit()

    seen = set()
    unique_events = []
    for event in events:
        key = json.dumps(event, sort_keys=True)
        if key not in seen:
            seen.add(key)
            unique_events.append(event)

    payload = {
        "portal": args.portal,
        "portal_name": portal_config["name"],
        "started_at": started_at.isoformat(),
        "finished_at": dt.datetime.now().replace(microsecond=0).isoformat(),
        "final_url": final_url,
        "redaction": "passwords, usernames, cookies, tokens, authorization headers, and CSRF-like fields are redacted",
        "events": unique_events,
    }

    output_file = OUTPUT_DIR / f"{args.portal}_login_capture.json"
    output_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Saved redacted capture to {output_file}")
    print(f"Captured {len(unique_events)} relevant request/response events.")


if __name__ == "__main__":
    main()
