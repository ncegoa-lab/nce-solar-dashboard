import os
import tempfile
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


DOWNLOAD_DIR = Path("solis_downloads").resolve()
CHROME_PROFILE_ROOT = Path(".solis-chrome-profiles").resolve()
DEFAULT_URL = "https://www.soliscloud.com/"


def require_env(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def main():
    DOWNLOAD_DIR.mkdir(exist_ok=True)
    CHROME_PROFILE_ROOT.mkdir(exist_ok=True)
    chrome_profile_dir = tempfile.mkdtemp(prefix="profile-", dir=CHROME_PROFILE_ROOT)

    options = webdriver.ChromeOptions()
    debugger_address = os.getenv("SOLIS_DEBUGGER_ADDRESS")
    if debugger_address:
        options.add_experimental_option("debuggerAddress", debugger_address)
    else:
        options.add_argument(f"--user-data-dir={chrome_profile_dir}")
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_experimental_option(
            "prefs",
            {
                "download.default_directory": str(DOWNLOAD_DIR),
                "download.prompt_for_download": False,
            },
        )

    driver = webdriver.Chrome(options=options)
    wait = WebDriverWait(driver, 45)

    try:
        if not debugger_address:
            driver.get(os.getenv("SOLIS_URL", DEFAULT_URL))

            username = require_env("SOLIS_USERNAME")
            password = require_env("SOLIS_PASSWORD")

            wait.until(
                EC.presence_of_element_located(
                    (
                        By.CSS_SELECTOR,
                        "input[type='email'], input[type='text'], input[name*='user' i], input[name*='email' i], input[placeholder*='email' i], input[placeholder*='account' i]",
                    )
                )
            ).send_keys(username)

            wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='password']"))
            ).send_keys(password)

            wait.until(
                EC.element_to_be_clickable(
                    (
                        By.CSS_SELECTOR,
                        "button[type='submit'], input[type='submit'], button",
                    )
                )
            ).click()

            wait.until(
                lambda current_driver: "login" not in current_driver.current_url.lower()
                or "station" in current_driver.current_url.lower()
                or "home" in current_driver.current_url.lower()
            )

        print(f"Connected to SolisCloud. Current page: {driver.current_url}")
        print(f"Downloads will save to: {DOWNLOAD_DIR}")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
