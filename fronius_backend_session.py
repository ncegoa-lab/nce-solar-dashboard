import html
import os
import re
from urllib.parse import parse_qs, urljoin, urlparse

import requests


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


def first_form(page):
    match = re.search(r"<form\b[^>]*>", page, re.IGNORECASE)
    return match.group(0) if match else ""


def first_form_action(page, current_url):
    form = first_form(page)
    if not form:
        return None
    action_match = re.search(r'\baction=["\']([^"\']+)["\']', form, re.IGNORECASE)
    if not action_match:
        return current_url
    return urljoin(current_url, html.unescape(action_match.group(1)))


def solarweb_session(username=None, password=None):
    username = username or require_env("FRONIUS_USERNAME")
    password = password or require_env("FRONIUS_PASSWORD")

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-GB,en;q=0.9",
        }
    )

    login_start = session.get(f"{SOLARWEB_BASE}/Account/ExternalLogin", timeout=30)
    login_start.raise_for_status()

    query = parse_qs(urlparse(login_start.url).query)
    session_data_key = query.get("sessionDataKey", [""])[0]
    relying_party = query.get("relyingParty", ["mf_o9iTAyKemNLQTa6Sp6HYonCIa"])[0]
    tenant_domain = query.get("tenantDomain", ["carbon.super"])[0]
    if not session_data_key:
        raise RuntimeError("Fronius login page did not provide sessionDataKey")

    fields = hidden_inputs(login_start.text)
    common_auth = session.post(
        f"{FRONIUS_LOGIN_BASE}/commonauth",
        data={
            "authenticators": fields.get(
                "authenticators",
                "SAMLSSOAuthenticator:Fronius Login;FroniusBasicAuthenticator:LOCAL",
            ),
            "tenantDomain": tenant_domain,
            "allLoginParams": fields.get("allLoginParams", ""),
            "usernameUserInput": username,
            "username": username,
            "password": password,
            "chkRemember": "on",
            "sessionDataKey": session_data_key,
        },
        headers={
            "Origin": FRONIUS_LOGIN_BASE,
            "Referer": login_start.url,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=30,
    )
    common_auth.raise_for_status()

    action = first_form_action(common_auth.text, common_auth.url)
    callback_fields = hidden_inputs(common_auth.text)
    if action and callback_fields:
        callback = session.post(action, data=callback_fields, timeout=30)
        callback.raise_for_status()

    widgets = session.get(f"{SOLARWEB_BASE}/PvSystems/Widgets", timeout=30)
    widgets.raise_for_status()
    if "login.fronius.com" in widgets.url:
        raise RuntimeError("Fronius login did not create a Solar.web session")

    return session
