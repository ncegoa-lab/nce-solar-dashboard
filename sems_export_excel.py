import os
import tempfile
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.safari.service import Service as SafariService
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


DOWNLOAD_DIR = Path("sems_downloads").resolve()
CHROME_PROFILE_ROOT = Path(".chrome-profiles").resolve()


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def main() -> None:
    DOWNLOAD_DIR.mkdir(exist_ok=True)
    CHROME_PROFILE_ROOT.mkdir(exist_ok=True)
    chrome_profile_dir = tempfile.mkdtemp(prefix="profile-", dir=CHROME_PROFILE_ROOT)

    browser = os.getenv("SEMS_BROWSER", "chrome").lower()
    if browser == "safari":
        service = SafariService()
        driver = webdriver.Safari(service=service)
    else:
        options = webdriver.ChromeOptions()
        debugger_address = os.getenv("SEMS_DEBUGGER_ADDRESS")
        if debugger_address:
            options.add_experimental_option("debuggerAddress", debugger_address)
        else:
            options.add_argument(f"--user-data-dir={chrome_profile_dir}")
            options.add_argument("--no-first-run")
            options.add_argument("--no-default-browser-check")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            if os.getenv("SEMS_HEADLESS", "1") != "0":
                options.add_argument("--headless=new")
                options.add_argument("--disable-gpu")
        options.add_experimental_option(
            "prefs",
            {
                "download.default_directory": str(DOWNLOAD_DIR),
                "download.prompt_for_download": False,
            },
        )

        service = Service(log_output="chromedriver.log")
        driver = webdriver.Chrome(options=options, service=service)
    wait = WebDriverWait(driver, 30)

    try:
        if browser == "safari" or not debugger_address:
            driver.get("https://www.semsportal.com/home/login")

            email = require_env("SEMS_USERNAME")
            password = require_env("SEMS_PASSWORD")

            wait.until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "input[type='text'], input[type='email']")
                )
            ).send_keys(email)
            driver.find_element(By.CSS_SELECTOR, "input[type='password']").send_keys(
                password
            )

            driver.find_element(
                By.CSS_SELECTOR,
                "button, input[type='button'], input[type='submit']",
            ).click()

            wait.until(
                lambda current_driver: "login" not in current_driver.current_url.lower()
            )

        report_url = os.getenv(
            "SEMS_REPORT_URL",
            "https://www.semsportal.com/Report/HistoricalData",
        )
        driver.get(report_url)

        wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, "//*[contains(., 'Export') or contains(., 'Excel')]")
            )
        ).click()

        print(f"Export clicked. Check downloads in: {DOWNLOAD_DIR}")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
