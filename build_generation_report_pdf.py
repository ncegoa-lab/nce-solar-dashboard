import datetime as dt
import json
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


GOODWE_FILE = Path("sems_station_data.json")
GOODWE_WEEKLY_FILE = Path("sems_weekly_generation.json")
FRONIUS_WEEKLY_FILE = Path("fronius_weekly_generation.json")
FRONIUS_CURRENT_FILE = Path("fronius_current_generation.json")
FIMER_FILE = Path("fimer_generation.json")
OUTPUT_DIR = Path("output/pdf")
OUTPUT_FILE = OUTPUT_DIR / "weekly_generation_report.pdf"

INK = colors.HexColor("#17324D")
MUTED = colors.HexColor("#5F6F80")
LINE = colors.HexColor("#D8E1EA")
GOODWE = colors.HexColor("#2C7FB8")
FRONIUS = colors.HexColor("#E1A900")
FIMER = colors.HexColor("#7357B8")
SOFT_BLUE = colors.HexColor("#EAF3F8")
SOFT_YELLOW = colors.HexColor("#FFF4D6")
SOFT_PURPLE = colors.HexColor("#EFEAF8")
SOFT_GREEN = colors.HexColor("#EEF6F1")


def capacity_from_name(name):
    match = re.search(r"(\d+(?:\.\d+)?)\s*k\s*w", name or "", re.IGNORECASE)
    return float(match.group(1)) if match else None


def fmt(value, decimals=2):
    if value is None:
        return "-"
    return f"{float(value):,.{decimals}f}"


def energy_value(item, key):
    value = item.get("values", {}).get(key, {})
    body = value.get("body", [])
    if value.get("status") == 200 and body:
        return body[0].get("value")
    return None


def load_rows():
    goodwe = json.loads(GOODWE_FILE.read_text(encoding="utf-8"))
    goodwe_weekly = json.loads(GOODWE_WEEKLY_FILE.read_text(encoding="utf-8"))
    fronius_weekly = json.loads(FRONIUS_WEEKLY_FILE.read_text(encoding="utf-8"))
    fronius_current = json.loads(FRONIUS_CURRENT_FILE.read_text(encoding="utf-8"))
    fimer = json.loads(FIMER_FILE.read_text(encoding="utf-8")) if FIMER_FILE.exists() else None

    goodwe_weekly_by_id = {
        station["station_id"]: station for station in goodwe_weekly["stations"]
    }
    fronius_current_by_id = {
        system["system_id"]: system for system in fronius_current["systems"]
    }

    rows = []
    for station in goodwe["stations"]:
        weekly = goodwe_weekly_by_id.get(station.get("powerstation_id"), {})
        rows.append(
            {
                "brand": "GoodWe",
                "name": station.get("stationname"),
                "status": station.get("status"),
                "capacity": station.get("capacity"),
                "today": station.get("eday"),
                "weekly": weekly.get("weekly_generation_kwh"),
                "total": station.get("etotal"),
            }
        )

    for system in fronius_weekly["systems"]:
        current = fronius_current_by_id.get(system.get("system_id"), {})
        rows.append(
            {
                "brand": "Fronius",
                "name": system.get("name"),
                "status": system.get("status"),
                "capacity": capacity_from_name(system.get("name")),
                "today": current.get("today_generation_kwh"),
                "weekly": system.get("weekly_generation_kwh"),
                "total": current.get("total_generation_kwh"),
            }
        )

    if fimer:
        for item in fimer.get("plantEnergy", []):
            plant = item["plant"]
            rows.append(
                {
                    "brand": "FIMER",
                    "name": plant.get("name"),
                    "status": plant.get("state"),
                    "capacity": plant.get("configuration", {}).get("panelsNominalPower"),
                    "today": energy_value(item, "today"),
                    "weekly": energy_value(item, "week"),
                    "total": energy_value(item, "total"),
                }
            )

    meta = {
        "goodwe_week": f"{goodwe_weekly['start_date']} to {goodwe_weekly['end_date']}",
        "fronius_week": f"{fronius_weekly['start_date']} to {fronius_weekly['end_date']}",
        "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    return rows, meta


def brand_summary(rows):
    brands = ["GoodWe", "Fronius", "FIMER"]
    return [
        {
            "brand": brand,
            "systems": sum(1 for row in rows if row["brand"] == brand),
            "today": sum(row["today"] or 0 for row in rows if row["brand"] == brand),
            "weekly": sum(row["weekly"] or 0 for row in rows if row["brand"] == brand),
        }
        for brand in brands
    ]


def add_header(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(INK)
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(18 * mm, 12 * mm, "Weekly Generation Report")
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 8)
    canvas.drawRightString(279 * mm, 12 * mm, f"Page {doc.page}")
    canvas.restoreState()


def summary_cards(summary):
    data = []
    for item in summary:
        data.append(
            [
                Paragraph(f"<b>{item['brand']}</b>", ParagraphStyle("cardTitle", fontSize=12, textColor=INK, leading=14)),
                Paragraph(f"{fmt(item['today'])} kWh<br/><font color='#5F6F80'>Today</font>", ParagraphStyle("cardMetric", fontSize=11, leading=14)),
                Paragraph(f"{fmt(item['weekly'])} kWh<br/><font color='#5F6F80'>Week</font>", ParagraphStyle("cardMetric2", fontSize=11, leading=14)),
                Paragraph(f"{item['systems']}<br/><font color='#5F6F80'>Systems</font>", ParagraphStyle("cardMetric3", fontSize=11, leading=14)),
            ]
        )
    table = Table(data, colWidths=[34 * mm, 42 * mm, 42 * mm, 26 * mm], rowHeights=18 * mm)
    table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.6, LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, LINE),
                ("BACKGROUND", (0, 0), (-1, 0), SOFT_BLUE),
                ("BACKGROUND", (0, 1), (-1, 1), SOFT_YELLOW),
                ("BACKGROUND", (0, 2), (-1, 2), SOFT_PURPLE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return table


def brand_bar_chart(summary):
    max_value = max(item["weekly"] for item in summary) or 1
    colors_by_brand = {"GoodWe": GOODWE, "Fronius": FRONIUS, "FIMER": FIMER}
    drawing = Drawing(96 * mm, 55 * mm)
    drawing.add(Rect(0, 0, 96 * mm, 55 * mm, strokeColor=LINE, fillColor=colors.white, strokeWidth=0.6))
    drawing.add(Rect(0, 45 * mm, 96 * mm, 10 * mm, strokeColor=None, fillColor=SOFT_GREEN))
    drawing.add(String(6 * mm, 48 * mm, "Weekly Generation by Brand", fontName="Helvetica-Bold", fontSize=10, fillColor=INK))

    for index, item in enumerate(summary):
        y = (34 - index * 12) * mm
        bar_width = max(4 * mm, (item["weekly"] / max_value) * 48 * mm)
        drawing.add(String(6 * mm, y + 2 * mm, item["brand"], fontName="Helvetica-Bold", fontSize=8, fillColor=INK))
        drawing.add(Rect(36 * mm, y, 50 * mm, 5 * mm, strokeColor=None, fillColor=colors.HexColor("#EEF2F6")))
        drawing.add(Rect(36 * mm, y, bar_width, 5 * mm, strokeColor=None, fillColor=colors_by_brand[item["brand"]]))
        drawing.add(String(36 * mm, y - 4 * mm, f"{fmt(item['weekly'])} kWh", fontName="Helvetica", fontSize=7, fillColor=MUTED))
    return drawing


def detail_table(rows):
    styles = getSampleStyleSheet()
    name_style = ParagraphStyle("name", parent=styles["Normal"], fontSize=7.4, leading=8.5)
    table_rows = [["Brand", "System", "Status", "Capacity kW", "Today kWh", "Weekly kWh", "Total kWh"]]
    for row in sorted(rows, key=lambda item: (item["brand"], item["name"] or "")):
        table_rows.append(
            [
                row["brand"],
                Paragraph(row["name"] or "-", name_style),
                row["status"] or "-",
                fmt(row["capacity"]),
                fmt(row["today"]),
                fmt(row["weekly"]),
                fmt(row["total"]),
            ]
        )
    table = Table(
        table_rows,
        colWidths=[20 * mm, 62 * mm, 30 * mm, 24 * mm, 26 * mm, 28 * mm, 34 * mm],
        repeatRows=1,
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), INK),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 8),
                ("FONTSIZE", (0, 1), (-1, -1), 7.2),
                ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7FAFC")]),
                ("GRID", (0, 0), (-1, -1), 0.25, LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def build_pdf():
    rows, meta = load_rows()
    summary = brand_summary(rows)
    total_today = sum(item["today"] for item in summary)
    total_weekly = sum(item["weekly"] for item in summary)
    total_systems = sum(item["systems"] for item in summary)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUTPUT_FILE),
        pagesize=landscape(A4),
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
    )
    styles = getSampleStyleSheet()
    title = ParagraphStyle("title", parent=styles["Title"], fontSize=23, leading=27, textColor=INK, alignment=0)
    subtitle = ParagraphStyle("subtitle", parent=styles["Normal"], fontSize=9, leading=12, textColor=MUTED)
    big = ParagraphStyle("big", parent=styles["Normal"], fontSize=16, leading=20, textColor=INK)

    story = [
        Paragraph("Weekly Generation Report", title),
        Paragraph(
            f"Generated {meta['generated']} | GoodWe week: {meta['goodwe_week']} | Fronius week: {meta['fronius_week']}",
            subtitle,
        ),
        Spacer(1, 8 * mm),
        Table(
            [
                [
                    Paragraph(f"<b>{fmt(total_today)}</b><br/><font color='#5F6F80'>Total today kWh</font>", big),
                    Paragraph(f"<b>{fmt(total_weekly)}</b><br/><font color='#5F6F80'>Total weekly kWh</font>", big),
                    Paragraph(f"<b>{total_systems}</b><br/><font color='#5F6F80'>Systems and plants</font>", big),
                ]
            ],
            colWidths=[62 * mm, 62 * mm, 62 * mm],
            rowHeights=22 * mm,
            style=TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), SOFT_GREEN),
                    ("BOX", (0, 0), (-1, -1), 0.6, LINE),
                    ("INNERGRID", (0, 0), (-1, -1), 0.4, LINE),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ]
            ),
        ),
        Spacer(1, 9 * mm),
        Table([[summary_cards(summary), brand_bar_chart(summary)]], colWidths=[150 * mm, 100 * mm]),
        PageBreak(),
        Paragraph("System Detail", ParagraphStyle("section", fontSize=17, leading=20, textColor=INK)),
        Paragraph("Generation values are shown without the current power column.", subtitle),
        Spacer(1, 5 * mm),
        detail_table(rows),
    ]

    doc.build(story, onFirstPage=add_header, onLaterPages=add_header)
    print(f"Saved PDF report to {OUTPUT_FILE}")


if __name__ == "__main__":
    build_pdf()
