import json
import re
from pathlib import Path

from fronius_backend_session import SOLARWEB_BASE, solarweb_session


OUTPUT_FILE = Path("fronius_systems.json")


def system_rows(page):
    rows = []
    seen = set()
    links = re.finditer(
        r'<a\b[^>]*href=["\']([^"\']*pvSystemId=([^"\'&]+)[^"\']*)["\'][^>]*>(.*?)</a>',
        page,
        re.IGNORECASE | re.DOTALL,
    )
    for match in links:
        system_id = match.group(2)
        if system_id in seen:
            continue
        seen.add(system_id)
        text = re.sub(r"<[^>]+>", "\n", match.group(3))
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            continue
        href = match.group(1)
        rows.append(
            {
                "system_id": system_id,
                "name": lines[0],
                "status": " ".join(lines[1:]),
                "url": href if href.startswith("http") else f"{SOLARWEB_BASE}{href}",
            }
        )
    return rows


def main():
    session = solarweb_session()
    response = session.get(f"{SOLARWEB_BASE}/PvSystems/Widgets", timeout=30)
    response.raise_for_status()
    systems = system_rows(response.text)
    if not systems:
        raise RuntimeError("No Fronius systems found on Solar.web widgets page")

    payload = {
        "source": "Fronius Solar.web",
        "page": response.url,
        "systems": systems,
    }
    OUTPUT_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Saved {len(systems)} Fronius systems to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
