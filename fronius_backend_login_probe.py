import datetime as dt
import html
import json
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import requests


STATUS_FILE = Path("outputs/login_capture/fronius_backend_probe_status.json")
SOLARWEB_BASE = "https://www.solarweb.com"
FRONIUS_LOGIN_BASE = "https://login.fronius.com"


def require_env(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def hidden_inputs(page):
    fields = {}
    for match in re.finditer(r"<input\b[^>]*>", page, re.IGNORECASE):
        tag = match.group(0)
        name_match = re.search(r'\bname=["\']([^"\']+)["\']', tag, re.IGNORECASE)
        if not name_match:
            continue
        value_match = re.search(r'\bvalue=["\']([^"\']*)["\']', tag, re.IGNORECASE)
        fields[html.unescape(name_match.group(1))] = (
            html.unescape(value_match.group(1)) if value_match else ""
        )
    return fields


def first_form_action(page, current_url):
    form_match = re.search(r"<form\b[^>]*>", page, re.IGNORECASE)
    if not form_match:
        return None
    action_match = re.search(
        r'\baction=["\']([^"\']+)["\']', form_match.group(0), re.IGNORECASE
    )
    if not action_match:
        return current_url
    return urljoin(current_url, html.unescape(action_match.group(1)))


def status_line(response):
    return {
        "status": response.status_code,
        "url": response.url,
        "content_type": response.headers.get("content-type"),
    }


def main():
    username = require_env("FRONIUS_USERNAME")
    password = require_env("FRONIUS_PASSWORD")

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-GB,en;q=0.9",
        }
    )

    steps = []
    login_start = session.get(f"{SOLARWEB_BASE}/Account/ExternalLogin", timeout=30)
    steps.append({"step": "start external login", **status_line(login_start)})
    login_start.raise_for_status()

    parsed = urlparse(login_start.url)
    query = parse_qs(parsed.query)
    session_data_key = query.get("sessionDataKey", [""])[0]
    relying_party = query.get("relyingParty", ["mf_o9iTAyKemNLQTa6Sp6HYonCIa"])[0]
    tenant_domain = query.get("tenantDomain", ["carbon.super"])[0]

    fields = hidden_inputs(login_start.text)
    all_login_params = fields.get("allLoginParams", "")
    authenticators = fields.get(
        "authenticators",
        "SAMLSSOAuthenticator:Fronius Login;FroniusBasicAuthenticator:LOCAL",
    )

    if not session_data_key:
        raise RuntimeError("Fronius login page did not provide sessionDataKey")

    login_payload = {
        "authenticators": authenticators,
        "tenantDomain": tenant_domain,
        "allLoginParams": all_login_params,
        "usernameUserInput": username,
        "username": username,
        "password": password,
        "chkRemember": "on",
        "sessionDataKey": session_data_key,
    }
    common_auth = session.post(
        f"{FRONIUS_LOGIN_BASE}/commonauth",
        data=login_payload,
        headers={
            "Origin": FRONIUS_LOGIN_BASE,
            "Referer": login_start.url,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=30,
    )
    steps.append({"step": "post credentials to commonauth", **status_line(common_auth)})
    common_auth.raise_for_status()

    callback_posted = False
    action = first_form_action(common_auth.text, common_auth.url)
    callback_fields = hidden_inputs(common_auth.text)
    if action and callback_fields and "solarweb.com" in action:
        callback = session.post(action, data=callback_fields, timeout=30)
        steps.append({"step": "post OpenID callback to Solar.web", **status_line(callback)})
        callback.raise_for_status()
        callback_posted = True

    widgets = session.get(f"{SOLARWEB_BASE}/PvSystems/Widgets", timeout=30)
    steps.append({"step": "open Solar.web widgets", **status_line(widgets)})

    success = widgets.status_code == 200 and "login.fronius.com" not in widgets.url
    payload = {
        "generated_at": dt.datetime.now().replace(microsecond=0).isoformat(),
        "success": success,
        "callback_posted": callback_posted,
        "session_data_key_seen": bool(session_data_key),
        "steps": steps,
        "notes": [
            "Credential values are not saved.",
            "If success is false, Fronius may require captcha, MFA, or an extra OpenID callback adjustment.",
        ],
    }
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Saved Fronius backend probe status to {STATUS_FILE}")
    print(f"Success: {success}")


if __name__ == "__main__":
    main()
