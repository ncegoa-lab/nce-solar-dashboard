import os
from pathlib import Path

from selenium import webdriver


DOWNLOAD_DIR = Path("fronius_downloads").resolve()


def main() -> None:
    DOWNLOAD_DIR.mkdir(exist_ok=True)

    options = webdriver.ChromeOptions()
    options.add_experimental_option(
        "debuggerAddress",
        os.getenv("FRONIUS_DEBUGGER_ADDRESS", "127.0.0.1:9223"),
    )
    driver = webdriver.Chrome(options=options)

    try:
        driver.execute_cdp_cmd(
            "Page.setDownloadBehavior",
            {"behavior": "allow", "downloadPath": str(DOWNLOAD_DIR)},
        )
        urls = driver.execute_script(
            """
            return Array.from(document.querySelectorAll('a[href*="/Report/DownloadAuto?reportId="]'))
              .map(a => a.href)
              .filter(Boolean);
            """
        )
        limit = int(os.getenv("FRONIUS_DOWNLOAD_LIMIT", "1"))
        for url in urls[:limit]:
            driver.get(url)
        print(f"Triggered {min(len(urls), limit)} report download(s) into {DOWNLOAD_DIR}")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
