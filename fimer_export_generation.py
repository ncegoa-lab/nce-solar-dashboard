import datetime as dt
import json
import os
from pathlib import Path

from selenium import webdriver


OUTPUT_FILE = Path("fimer_generation.json")
PORTFOLIO_ID = os.getenv("FIMER_PORTFOLIO_ID", "31841756")


def main() -> None:
    options = webdriver.ChromeOptions()
    options.add_experimental_option(
        "debuggerAddress",
        os.getenv("FIMER_DEBUGGER_ADDRESS", "127.0.0.1:9224"),
    )
    driver = webdriver.Chrome(options=options)

    try:
        result = driver.execute_async_script(
            """
            const portfolioId = arguments[0];
            const done = arguments[1];
            const today = new Date();
            const startOfLocalDay = new Date(today.getFullYear(), today.getMonth(), today.getDate());
            const startOfWeek = new Date(startOfLocalDay);
            startOfWeek.setDate(startOfLocalDay.getDate() - 6);
            const startOfMonth = new Date(today.getFullYear(), today.getMonth(), 1);
            const startOfYear = new Date(today.getFullYear(), 0, 1);

            async function fetchJson(url) {
              const response = await fetch(url, { credentials: 'include' });
              return { status: response.status, body: await response.json() };
            }

            const plantsUrl = `/asset/v1/portfolios/${portfolioId}/plants?includePerformanceProfiles=true`;
            const energyBase = `/telemetry/v1/plantGroups/${portfolioId}/energy/GenerationEnergy`;
            const ranges = {
              today: startOfLocalDay,
              week: startOfWeek,
              month: startOfMonth,
              year: startOfYear,
            };
            Promise.all([
              fetchJson(plantsUrl),
              ...Object.entries(ranges).map(([key, start]) =>
                fetchJson(`${energyBase}?sdt=${encodeURIComponent(start.toISOString())}&edt=${encodeURIComponent(today.toISOString())}`)
                  .then(value => ({ key, value }))
              )
            ]).then(([plants, ...energy]) => {
              done({ plants, energy, now: today.toISOString(), rangeStarts: Object.fromEntries(Object.entries(ranges).map(([k, v]) => [k, v.toISOString()])) });
            }).catch(err => done({ error: String(err) }));
            """,
            PORTFOLIO_ID,
        )

        plant_result = driver.execute_async_script(
            """
            const plants = arguments[0];
            const done = arguments[1];
            const today = new Date();
            const startOfLocalDay = new Date(today.getFullYear(), today.getMonth(), today.getDate());
            const startOfWeek = new Date(startOfLocalDay);
            startOfWeek.setDate(startOfLocalDay.getDate() - 6);
            const startOfMonth = new Date(today.getFullYear(), today.getMonth(), 1);
            const ranges = {
              today: startOfLocalDay,
              week: startOfWeek,
              month: startOfMonth,
            };

            async function fetchJson(url) {
              const response = await fetch(url, { credentials: 'include' });
              return { status: response.status, body: await response.json() };
            }

            Promise.all(plants.map(async plant => {
              const installDate = plant.configuration && plant.configuration.installDate
                ? new Date(plant.configuration.installDate)
                : startOfWeek;
              const energyBase = `/telemetry/v1/plants/${plant.entityID}/energy/GenerationEnergy`;
              const values = {};
              for (const [key, start] of Object.entries({ ...ranges, total: installDate })) {
                values[key] = await fetchJson(
                  `${energyBase}?agp=All&afx=Delta&sdt=${encodeURIComponent(start.toISOString())}&edt=${encodeURIComponent(today.toISOString())}`
                );
              }
              return { plant, values };
            })).then(done).catch(err => done({ error: String(err) }));
            """,
            result.get("plants", {}).get("body", []),
        )
        result["plantEnergy"] = plant_result

        OUTPUT_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"Saved FIMER generation data to {OUTPUT_FILE}")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
