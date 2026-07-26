import os
import tempfile
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


DOWNLOAD_DIR = Path("fronius_downloads").resolve()
CHROME_PROFILE_ROOT = Path(".fronius-chrome-profiles").resolve()


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def main() -> None:
    DOWNLOAD_DIR.mkdir(exist_ok=True)
    CHROME_PROFILE_ROOT.mkdir(exist_ok=True)
    chrome_profile_dir = tempfile.mkdtemp(prefix="profile-", dir=CHROME_PROFILE_ROOT)

    options = webdriver.ChromeOptions()
    debugger_address = os.getenv("FRONIUS_DEBUGGER_ADDRESS")
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
    wait = WebDriverWait(driver, 30)

    try:
        if not debugger_address:
            driver.get("https://www.solarweb.com/")

            email = require_env("FRONIUS_USERNAME")
            password = require_env("FRONIUS_PASSWORD")

            wait.until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "input[type='email'], input[name*='email' i]")
                )
            ).send_keys(email)

            wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='password']"))
            ).send_keys(password)

            wait.until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, "button[type='submit'], input[type='submit']")
                )
            ).click()

            wait.until(
                lambda current_driver: "login" not in current_driver.current_url.lower()
            )

        print(f"Connected. Current page: {driver.current_url}")
        print(f"Downloads will save to: {DOWNLOAD_DIR}")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
