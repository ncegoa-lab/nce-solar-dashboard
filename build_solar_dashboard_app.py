#!/usr/bin/env python3
"""Build a self-contained browser dashboard for solar plant reports."""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from solar_performance_report_app import DEFAULT_LOGO, load_data


DEFAULT_OUTPUT_DIR = Path("/Users/sushil/Library/Mobile Documents/com~apple~CloudDocs/Weekly Solar Plant Report")
IST = ZoneInfo("Asia/Kolkata")


def ist_now() -> dt.datetime:
    return dt.datetime.now(IST)


def ist_today() -> dt.date:
    return ist_now().date()


def encode_logo(path: Path | None) -> str:
    if not path or not path.exists():
        return ""
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def data_date(value: Any) -> str:
    text = str(value or "")
    return text[:10] if len(text) >= 10 else ""


def plant_rows(input_path: str | None = None) -> list[dict[str, Any]]:
    df = load_data(input_path=input_path, current_project=not bool(input_path))
    rows: list[dict[str, Any]] = []
    for index, row in df.sort_values(["Brand", "Site Name"]).reset_index(drop=True).iterrows():
        rows.append(
            {
                "id": f"plant_{index}",
                "brand": str(row["Brand"]),
                "site": str(row["Site Name"]),
                "status": str(row["Current Status"]),
                "capacity": number(row["Plant Capacity (kW)"]),
                "daily": number(row["Daily Generation (kWh)"]),
                "weekly": number(row["Weekly Generation (kWh)"]),
                "year": number(row["Year Generation (kWh)"]),
                "total": number(row["Total Generation (MWh)"]),
                "yield2026": number(row["2026 Yield (kWh/kW)"]),
                "avgDay": number(row["Average Daily Yield (kWh/kW/day)"]),
                "specificYield": number(row["Specific Yield (kWh/kWp)"]),
                "cuf": number(row["CUF (%)"]),
                "pr": number(row["PR (%)"]),
                "source": str(row.get("Year Generation Source", "")),
                "timestamp": str(row.get("Timestamp", "")),
                "dataDate": data_date(row.get("Timestamp", "")),
            }
        )
    return rows


def dashboard_html(plants: list[dict[str, Any]], logo_data_uri: str) -> str:
    payload = json.dumps(plants, ensure_ascii=False, separators=(",", ":"))
    generated = ist_now().strftime("%Y-%m-%d %H:%M IST")
    report_date = ist_today().strftime("%Y-%m-%d")
    return HTML_TEMPLATE.replace("__PLANT_DATA__", payload).replace("__LOGO__", logo_data_uri).replace("__GENERATED__", generated).replace("__REPORT_DATE__", report_date)


def build(input_path: str | None, output_dir: Path, logo_path: Path | None) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    html = dashboard_html(plant_rows(input_path), encode_logo(logo_path))
    dated = output_dir / f"Solar_Dashboard_App_{ist_today():%Y%m%d}.html"
    stable = output_dir / "Solar_Dashboard_App.html"
    dated.write_text(html, encoding="utf-8")
    stable.write_text(html, encoding="utf-8")
    print(f"Saved solar dashboard app to {stable}")
    print(f"Saved dated copy to {dated}")
    return stable


def main() -> Path:
    parser = argparse.ArgumentParser(description="Build the interactive browser app for solar plant reports.")
    parser.add_argument("--input", help="Optional JSON/CSV input. Defaults to current GOODWE project data.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Output folder.")
    parser.add_argument("--logo", default=str(DEFAULT_LOGO), help="Logo image path.")
    args = parser.parse_args()
    return build(args.input, Path(args.output_dir), Path(args.logo) if args.logo else None)


HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>NCE Solar Plant Dashboard</title>
  <style>
    :root {
      --blue: #164f9c;
      --cyan: #18b9d6;
      --green: #16845f;
      --red: #c73e3e;
      --orange: #d9822b;
      --yellow: #c99a06;
      --ink: #1e2b3f;
      --muted: #647084;
      --line: #d7e0ec;
      --soft: #f3f7fb;
      --panel: #ffffff;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
      color: var(--ink);
      background: #eef3f8;
      letter-spacing: 0;
    }
    header {
      min-height: 72px;
      background: var(--blue);
      color: #fff;
      display: flex;
      align-items: center;
      gap: 18px;
      padding: 10px 24px;
      position: sticky;
      top: 0;
      z-index: 10;
      box-shadow: 0 2px 12px rgba(18, 40, 75, .16);
    }
    header img { width: 78px; height: 48px; object-fit: contain; background: #fff; padding: 4px; }
    header h1 { margin: 0; font-size: 20px; line-height: 1.15; font-weight: 700; }
    header .meta { margin-left: auto; font-size: 12px; opacity: .92; text-align: right; }
    main { padding: 18px 24px 28px; max-width: 1440px; margin: 0 auto; }
    .toolbar {
      display: grid;
      grid-template-columns: minmax(220px, 1.4fr) minmax(180px, .8fr) minmax(180px, .8fr) minmax(180px, .8fr) auto auto;
      gap: 10px;
      align-items: end;
      margin-bottom: 14px;
    }
    label { display: block; font-size: 11px; color: var(--muted); margin: 0 0 5px; font-weight: 700; }
    select, input {
      width: 100%;
      height: 36px;
      border: 1px solid var(--line);
      background: #fff;
      border-radius: 6px;
      padding: 0 10px;
      color: var(--ink);
      font-size: 13px;
    }
    button {
      height: 36px;
      border: 0;
      border-radius: 6px;
      padding: 0 14px;
      font-weight: 700;
      color: #fff;
      background: var(--blue);
      cursor: pointer;
      white-space: nowrap;
    }
    button.secondary { background: var(--cyan); }
    button.ghost { background: #5b6f8d; }
    .kpis {
      display: grid;
      grid-template-columns: repeat(6, minmax(120px, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }
    .metric, .section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 1px 4px rgba(15, 35, 60, .05);
    }
    .metric { padding: 12px; min-height: 80px; }
    .metric span { color: var(--muted); font-size: 11px; font-weight: 700; display: block; margin-bottom: 10px; }
    .metric strong { font-size: 20px; line-height: 1; }
    .layout {
      display: grid;
      grid-template-columns: minmax(0, 1.7fr) minmax(360px, .9fr);
      gap: 14px;
      align-items: start;
    }
    .section { padding: 14px; }
    .section h2 { margin: 0 0 12px; font-size: 15px; }
    table { width: 100%; border-collapse: collapse; font-size: 12px; }
    th {
      text-align: left;
      color: #fff;
      background: var(--blue);
      padding: 8px 7px;
      position: sticky;
      top: 72px;
      z-index: 2;
    }
    td { padding: 7px; border-bottom: 1px solid var(--line); vertical-align: middle; }
    tbody tr { cursor: pointer; background: #fff; }
    tbody tr:nth-child(even) { background: #f8fafc; }
    tbody tr.active { outline: 2px solid var(--cyan); outline-offset: -2px; background: #ecfbff; }
    .status { font-weight: 700; }
    .status.online { color: var(--green); }
    .status.offline { color: var(--red); }
    .status.warning { color: var(--orange); }
    .status.fault { color: var(--yellow); }
    .plant-name { font-weight: 800; }
    .plant-name.online { color: #064E3B; }
    .plant-name.offline { color: #111827; }
    .plant-today { display: block; margin-top: 3px; font-size: 11px; font-weight: 800; }
    .plant-today.online { color: #16A34A; }
    .plant-today.offline { color: #111827; }
    .plant-card .name { font-size: 22px; font-weight: 800; margin-bottom: 3px; }
    .plant-card .brand { color: var(--muted); font-size: 13px; margin-bottom: 14px; }
    .detail-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; }
    .detail {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px;
      background: #fbfdff;
      min-height: 64px;
    }
    .detail span { display: block; font-size: 11px; color: var(--muted); font-weight: 700; margin-bottom: 8px; }
    .detail strong { font-size: 16px; }
    .charts { display: grid; grid-template-columns: 1fr; gap: 12px; margin-top: 14px; }
    .bar-row { display: grid; grid-template-columns: 150px 1fr 68px; gap: 10px; align-items: center; margin: 8px 0; font-size: 12px; }
    .bar-track { height: 10px; border-radius: 999px; background: #e8eef6; overflow: hidden; }
    .bar { height: 100%; border-radius: 999px; background: linear-gradient(90deg, var(--cyan), var(--green)); }
    .customer-view { display: none; }
    .mode-customer .dashboard-view { display: none; }
    .mode-customer .customer-view { display: block; }
    .customer-sheet {
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 28px;
      max-width: 980px;
      margin: 0 auto;
    }
    .customer-head { display: flex; align-items: center; gap: 18px; border-bottom: 2px solid var(--blue); padding-bottom: 14px; margin-bottom: 20px; }
    .customer-head img { width: 90px; height: 56px; object-fit: contain; }
    .customer-head h2 { margin: 0; font-size: 24px; }
    .customer-actions { display: flex; justify-content: flex-end; gap: 10px; margin-bottom: 12px; }
    .note { color: var(--muted); font-size: 12px; line-height: 1.5; margin-top: 12px; }
    .stale {
      display: inline-block;
      border-radius: 999px;
      padding: 2px 7px;
      font-size: 10px;
      font-weight: 800;
      color: #fff;
      background: var(--red);
      margin-left: 6px;
    }
    .fresh {
      display: inline-block;
      border-radius: 999px;
      padding: 2px 7px;
      font-size: 10px;
      font-weight: 800;
      color: #fff;
      background: var(--green);
      margin-left: 6px;
    }
    .offline-badge {
      display: inline-block;
      border-radius: 999px;
      padding: 2px 7px;
      font-size: 10px;
      font-weight: 800;
      color: #fff;
      background: #111827;
      margin-left: 6px;
    }
    @media (max-width: 980px) {
      .toolbar, .layout, .kpis { grid-template-columns: 1fr; }
      header { position: static; }
      th { position: static; }
    }
    @media print {
      body { background: #fff; }
      header, .toolbar, .dashboard-view, .customer-actions { display: none !important; }
      main { padding: 0; max-width: none; }
      .customer-view { display: block !important; }
      .customer-sheet { border: 0; box-shadow: none; padding: 8mm; max-width: none; }
      @page { size: A4 landscape; margin: 8mm; }
    }
  </style>
</head>
<body>
  <header>
    <img src="__LOGO__" alt="NCE logo">
    <div>
      <h1>Solar Plant Performance Dashboard</h1>
      <div>Interactive plant filtering and customer report view</div>
    </div>
    <div class="meta">Report Date: __REPORT_DATE__<br>Generated: __GENERATED__</div>
  </header>
  <main id="appRoot">
    <section class="dashboard-view">
      <div class="toolbar">
        <div>
          <label for="plantSelect">Plant</label>
          <select id="plantSelect"></select>
        </div>
        <div>
          <label for="brandSelect">Brand</label>
          <select id="brandSelect"></select>
        </div>
        <div>
          <label for="statusSelect">Status</label>
          <select id="statusSelect"></select>
        </div>
        <div>
          <label for="searchBox">Search</label>
          <input id="searchBox" type="search" placeholder="Search plant">
        </div>
        <button class="secondary" id="customerBtn">Customer View</button>
        <button class="ghost" id="printBtn">Print / Save PDF</button>
      </div>
      <div class="kpis" id="kpis"></div>
      <div class="layout">
        <section class="section">
          <h2>Plants</h2>
          <table>
            <thead>
              <tr>
                <th>Brand</th><th>Plant</th><th>Status</th><th>Data Date</th><th>Cap kW</th><th>Daily</th><th>Weekly</th><th>Yearly</th><th>CUF</th>
              </tr>
            </thead>
            <tbody id="plantRows"></tbody>
          </table>
        </section>
        <aside>
          <section class="section plant-card" id="plantCard"></section>
          <section class="section charts">
            <h2>Top CUF Plants</h2>
            <div id="yieldBars"></div>
          </section>
        </aside>
      </div>
    </section>
    <section class="customer-view">
      <div class="customer-actions">
        <button class="ghost" id="backBtn">Back to Dashboard</button>
        <button id="printCustomerBtn">Print / Save Customer PDF</button>
      </div>
      <article class="customer-sheet" id="customerSheet"></article>
    </section>
  </main>
  <script>
    const PLANTS = __PLANT_DATA__;
    const logo = "__LOGO__";
    const appRoot = document.getElementById("appRoot");
    const plantSelect = document.getElementById("plantSelect");
    const brandSelect = document.getElementById("brandSelect");
    const statusSelect = document.getElementById("statusSelect");
    const searchBox = document.getElementById("searchBox");
    const plantRows = document.getElementById("plantRows");
    const kpis = document.getElementById("kpis");
    const plantCard = document.getElementById("plantCard");
    const yieldBars = document.getElementById("yieldBars");
    const customerSheet = document.getElementById("customerSheet");
    let selectedId = "all";

    function fmt(value, digits = 2) {
      const n = Number(value || 0);
      return n.toLocaleString("en-IN", { maximumFractionDigits: digits, minimumFractionDigits: digits });
    }
    function statusClass(status) {
      const s = String(status || "").toLowerCase();
      if (s.includes("online") || s.includes("normal")) return "online";
      if (s.includes("warning")) return "warning";
      if (s.includes("fault")) return "fault";
      return "offline";
    }
    function isFresh(p) { return p.dataDate === "__REPORT_DATE__"; }
    function freshnessBadge(p) {
      if (!p.dataDate) return `<span class="stale">NO DATE</span>`;
      if (statusClass(p.status) === "offline") return `<span class="offline-badge">OFFLINE</span>`;
      return isFresh(p) ? `<span class="fresh">TODAY</span>` : `<span class="stale">STALE</span>`;
    }
    function unique(values) {
      return [...new Set(values)].filter(Boolean).sort((a, b) => String(a).localeCompare(String(b)));
    }
    function populateFilters() {
      plantSelect.innerHTML = `<option value="all">Select All</option>` + PLANTS.map(p => `<option value="${p.id}">${p.site}</option>`).join("");
      brandSelect.innerHTML = `<option value="all">All Brands</option>` + unique(PLANTS.map(p => p.brand)).map(v => `<option>${v}</option>`).join("");
      statusSelect.innerHTML = `<option value="all">All Status</option>` + unique(PLANTS.map(p => p.status)).map(v => `<option>${v}</option>`).join("");
    }
    function filteredPlants() {
      const brand = brandSelect.value;
      const status = statusSelect.value;
      const term = searchBox.value.trim().toLowerCase();
      return PLANTS.filter(p => {
        if (selectedId !== "all" && p.id !== selectedId) return false;
        if (brand !== "all" && p.brand !== brand) return false;
        if (status !== "all" && p.status !== status) return false;
        if (term && !`${p.site} ${p.brand}`.toLowerCase().includes(term)) return false;
        return true;
      });
    }
    function sum(rows, key) { return rows.reduce((total, row) => total + Number(row[key] || 0), 0); }
    function activePlant(rows) {
      if (selectedId !== "all") return PLANTS.find(p => p.id === selectedId) || rows[0] || PLANTS[0];
      return rows[0] || PLANTS[0];
    }
    function renderKpis(rows) {
      const capacity = sum(rows, "capacity");
      const year = sum(rows, "year");
      const online = rows.filter(p => statusClass(p.status) === "online").length;
      const data = [
        ["Plants", rows.length],
        ["Online", online],
        ["Capacity", `${fmt(capacity)} kW`],
        ["Daily", `${fmt(sum(rows, "daily"))} kWh`],
        ["Weekly", `${fmt(sum(rows, "weekly"))} kWh`],
        ["Yearly", `${fmt(year)} kWh`],
        ["CUF", `${fmt(capacity ? rows.reduce((total, p) => total + Number(p.cuf || 0) * Number(p.capacity || 0), 0) / capacity : 0)}%`]
      ];
      kpis.innerHTML = data.map(([label, value]) => `<div class="metric"><span>${label}</span><strong>${value}</strong></div>`).join("");
    }
    function renderTable(rows) {
      plantRows.innerHTML = rows.map(p => `
        <tr class="${p.id === selectedId ? "active" : ""}" data-id="${p.id}">
          <td>${p.brand}</td>
          <td><span class="plant-name ${statusClass(p.status)}">${p.site}</span><span class="plant-today ${statusClass(p.status)}">${fmt(p.daily)} kWh</span></td>
          <td class="status ${statusClass(p.status)}">${p.status}</td>
          <td>${p.dataDate || ""} ${freshnessBadge(p)}</td>
          <td>${fmt(p.capacity)}</td>
          <td>${fmt(p.daily)}</td>
          <td>${fmt(p.weekly)}</td>
          <td>${fmt(p.year)}</td>
          <td>${fmt(p.cuf)}%</td>
        </tr>`).join("");
      plantRows.querySelectorAll("tr").forEach(row => {
        row.addEventListener("click", () => {
          selectedId = row.dataset.id;
          plantSelect.value = selectedId;
          update();
        });
      });
    }
    function renderPlantCard(p) {
      plantCard.innerHTML = `
        <div class="name">${p.site}</div>
        <div class="brand">${p.brand} · <span class="status ${statusClass(p.status)}">${p.status}</span> · Data ${p.dataDate || "unknown"} ${freshnessBadge(p)}</div>
        <div class="detail-grid">
          <div class="detail"><span>Capacity</span><strong>${fmt(p.capacity)} kW</strong></div>
          <div class="detail"><span>Data Date</span><strong>${p.dataDate || "Unknown"}</strong></div>
          <div class="detail"><span>Daily</span><strong>${fmt(p.daily)} kWh</strong></div>
          <div class="detail"><span>Weekly</span><strong>${fmt(p.weekly)} kWh</strong></div>
          <div class="detail"><span>Yearly</span><strong>${fmt(p.year)} kWh</strong></div>
          <div class="detail"><span>CUF</span><strong>${fmt(p.cuf)}%</strong></div>
          <div class="detail"><span>Total</span><strong>${fmt(p.total)} MWh</strong></div>
          <div class="detail"><span>Avg/day</span><strong>${fmt(p.avgDay)}</strong></div>
        </div>
        <div class="note">2026 source: ${p.source || "Not available"}</div>`;
    }
    function renderBars(rows) {
      const top = [...rows].sort((a, b) => b.cuf - a.cuf).slice(0, 8);
      const max = Math.max(1, ...top.map(p => p.cuf));
      yieldBars.innerHTML = top.map(p => `
        <div class="bar-row">
          <div>${p.site}</div>
          <div class="bar-track"><div class="bar" style="width:${Math.max(2, p.cuf / max * 100)}%"></div></div>
          <strong>${fmt(p.cuf, 1)}%</strong>
        </div>`).join("");
    }
    function renderCustomer(p) {
      customerSheet.innerHTML = `
        <div class="customer-head">
          <img src="${logo}" alt="NCE logo">
          <div>
            <h2>${p.site}</h2>
            <div>${p.brand} Solar Plant Performance · Report Date __REPORT_DATE__</div>
          </div>
        </div>
        <div class="detail-grid">
          <div class="detail"><span>Status</span><strong class="status ${statusClass(p.status)}">${p.status}</strong></div>
          <div class="detail"><span>Data Date</span><strong>${p.dataDate || "Unknown"} ${freshnessBadge(p)}</strong></div>
          <div class="detail"><span>Capacity</span><strong>${fmt(p.capacity)} kW</strong></div>
          <div class="detail"><span>Daily Generation</span><strong>${fmt(p.daily)} kWh</strong></div>
          <div class="detail"><span>Weekly Generation</span><strong>${fmt(p.weekly)} kWh</strong></div>
          <div class="detail"><span>2026 Generation</span><strong>${fmt(p.year)} kWh</strong></div>
          <div class="detail"><span>CUF</span><strong>${fmt(p.cuf)}%</strong></div>
          <div class="detail"><span>Total Generation</span><strong>${fmt(p.total)} MWh</strong></div>
          <div class="detail"><span>Average per Day</span><strong>${fmt(p.avgDay)} kWh/kW/day</strong></div>
        </div>
        <p class="note">Year basis: 2026-01-01 to report date. Week basis: Monday-Sunday. 2026 values use portal YTD where available; otherwise estimated from the current week.</p>`;
    }
    function update() {
      const rows = filteredPlants();
      const p = activePlant(rows);
      renderKpis(rows);
      renderTable(rows);
      renderPlantCard(p);
      renderBars(rows);
      renderCustomer(p);
    }
    plantSelect.addEventListener("change", () => { selectedId = plantSelect.value; update(); });
    brandSelect.addEventListener("change", update);
    statusSelect.addEventListener("change", update);
    searchBox.addEventListener("input", update);
    document.getElementById("customerBtn").addEventListener("click", () => appRoot.classList.add("mode-customer"));
    document.getElementById("backBtn").addEventListener("click", () => appRoot.classList.remove("mode-customer"));
    document.getElementById("printBtn").addEventListener("click", () => window.print());
    document.getElementById("printCustomerBtn").addEventListener("click", () => window.print());
    populateFilters();
    update();
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
