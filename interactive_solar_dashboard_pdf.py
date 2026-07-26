#!/usr/bin/env python3
"""Create a self-contained Acrobat JavaScript solar dashboard PDF.

The PDF uses ReportLab AcroForm widgets for the UI and pypdf to inject
document-level Acrobat JavaScript. The interactivity is supported in Adobe
Acrobat/Reader. macOS Preview and most browser PDF viewers ignore Acrobat JS.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, BooleanObject, DictionaryObject, FloatObject, NameObject, NumberObject, TextStringObject
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from solar_performance_report_app import DEFAULT_LOGO, load_data, output_pdf_name


BLUE = colors.HexColor("#1F63B5")
TEAL = colors.HexColor("#18B9D6")
LIGHT_BLUE = colors.HexColor("#EEF6FF")
GRID = colors.HexColor("#C8D5E6")
TEXT = colors.HexColor("#253247")
MUTED = colors.HexColor("#6D7480")
GREEN = colors.HexColor("#1D9A6C")
RED = colors.HexColor("#D64545")
ORANGE = colors.HexColor("#F2994A")


def fmt(value: Any, digits: int = 2) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if abs(number) >= 1000:
        return f"{number:,.{digits}f}"
    return f"{number:.{digits}f}"


def status_color(status: str) -> colors.Color:
    status_text = (status or "").lower()
    if "online" in status_text or "normal" in status_text:
        return GREEN
    if "warning" in status_text:
        return ORANGE
    if "fault" in status_text or "offline" in status_text:
        return RED
    return MUTED


def sanitize_field_name(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value)[:80]


def prepare_plant_data(current_project: bool = True, input_path: str | None = None) -> list[dict[str, Any]]:
    df = load_data(input_path=input_path, current_project=current_project)
    rows: list[dict[str, Any]] = []
    for index, row in df.sort_values(["Brand", "Site Name"]).reset_index(drop=True).iterrows():
        capacity = float(row["Plant Capacity (kW)"] or 0)
        daily = float(row["Daily Generation (kWh)"] or 0)
        weekly = float(row["Weekly Generation (kWh)"] or 0)
        year = float(row["Year Generation (kWh)"] or 0)
        total = float(row["Total Generation (MWh)"] or 0)
        rows.append(
            {
                "id": f"p{index}",
                "brand": str(row["Brand"]),
                "site": str(row["Site Name"]),
                "status": str(row["Current Status"]),
                "capacity": capacity,
                "daily": daily,
                "weekly": weekly,
                "year": year,
                "total": total,
                "yield2026": float(row["2026 Yield (kWh/kW)"] or 0),
                "avgDay": float(row["Average Daily Yield (kWh/kW/day)"] or 0),
                "source": str(row.get("Year Generation Source", "")),
            }
        )
    return rows


class InteractiveDashboardPDF:
    """ReportLab + Acrobat JS dashboard builder."""

    def __init__(self, plants: list[dict[str, Any]], output_path: Path, logo_path: Path | None = None):
        self.plants = plants
        self.output_path = output_path
        self.logo_path = logo_path if logo_path and logo_path.exists() else None
        self.width, self.height = landscape(A4)
        self.row_count = len(plants)
        self.initial_metrics = self._calculate_metrics(plants)

    def _calculate_metrics(self, plants: list[dict[str, Any]]) -> dict[str, str]:
        capacity = sum(float(plant.get("capacity") or 0) for plant in plants)
        daily = sum(float(plant.get("daily") or 0) for plant in plants)
        weekly = sum(float(plant.get("weekly") or 0) for plant in plants)
        year = sum(float(plant.get("year") or 0) for plant in plants)
        online = sum(1 for plant in plants if "online" in str(plant.get("status", "")).lower() or "normal" in str(plant.get("status", "")).lower())
        alerts = len(plants) - online
        return {
            "calc_count": str(len(plants)),
            "calc_capacity": fmt(capacity, 2),
            "calc_daily": fmt(daily, 2),
            "calc_weekly": fmt(weekly, 2),
            "calc_year": fmt(year, 2),
            "calc_efficiency": fmt(year / capacity if capacity > 0 else 0, 2),
            "calc_online": str(online),
            "calc_alerts": str(alerts),
            "detail_site": "All selected plants",
            "detail_brand": "Multiple",
            "detail_status": "Mixed",
            "detail_source": "Mixed",
        }

    def build(self) -> Path:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_pdf = self.output_path.with_suffix(".base.pdf")
        c = canvas.Canvas(str(temporary_pdf), pagesize=landscape(A4))
        c.setTitle("Interactive Solar Plant Dashboard")

        self._draw_page_1(c)
        c.showPage()
        self._draw_page_2(c)
        c.showPage()
        self._draw_page_3(c)
        c.save()

        self._inject_javascript(temporary_pdf, self.output_path)
        temporary_pdf.unlink(missing_ok=True)
        return self.output_path

    def _header(self, c: canvas.Canvas, title: str, page: int) -> None:
        c.setFillColor(BLUE)
        c.rect(0, self.height - 13 * mm, self.width, 13 * mm, stroke=0, fill=1)
        if self.logo_path:
            c.drawImage(
                str(self.logo_path),
                10 * mm,
                self.height - 11 * mm,
                width=28 * mm,
                height=10 * mm,
                preserveAspectRatio=True,
                mask="auto",
            )
            title_x = 42 * mm
        else:
            title_x = 11 * mm
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(title_x, self.height - 8 * mm, title)
        c.setFont("Helvetica", 7)
        c.drawRightString(self.width - 11 * mm, self.height - 8 * mm, f"Page {page} of 3")
        c.setFillColor(MUTED)
        c.drawString(11 * mm, 7 * mm, f"Generated {dt.datetime.now():%Y-%m-%d %H:%M}")

    def _label(self, c: canvas.Canvas, text: str, x: float, y: float, size: int = 7, bold: bool = False) -> None:
        c.setFillColor(TEXT)
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        c.drawString(x, y, text)

    def _box(self, c: canvas.Canvas, x: float, y: float, w: float, h: float, fill=LIGHT_BLUE) -> None:
        c.setFillColor(fill)
        c.setStrokeColor(GRID)
        c.roundRect(x, y, w, h, 2 * mm, stroke=1, fill=1)

    def _readonly_field(self, c: canvas.Canvas, name: str, x: float, y: float, w: float, h: float, value: str = "", size: int = 7) -> None:
        c.acroForm.textfield(
            name=name,
            value=value,
            x=x,
            y=y,
            width=w,
            height=h,
            borderWidth=0,
            fillColor=None,
            textColor=TEXT,
            fontSize=size,
            fieldFlags="readOnly",
        )

    def _metric(self, c: canvas.Canvas, title: str, field: str, x: float, y: float, w: float, h: float) -> None:
        self._box(c, x, y, w, h)
        self._label(c, title, x + 3 * mm, y + h - 6 * mm, size=6, bold=False)
        self._readonly_field(c, field, x + 3 * mm, y + 3 * mm, w - 6 * mm, 8 * mm, self.initial_metrics.get(field, ""), size=9)

    def _draw_page_1(self, c: canvas.Canvas) -> None:
        self._header(c, "Interactive Solar Plant Dashboard", 1)
        self._label(c, "Inverter Selector", 12 * mm, self.height - 24 * mm, 8, True)
        options = ["Select All"] + [plant["site"] for plant in self.plants]
        c.acroForm.choice(
            name="inverter_selector",
            tooltip="Select an inverter/plant to filter the dashboard",
            options=options,
            value="Select All",
            x=50 * mm,
            y=self.height - 29 * mm,
            width=92 * mm,
            height=8 * mm,
            fontSize=7,
            borderColor=BLUE,
            fillColor=colors.white,
        )
        button_x = 148 * mm
        button_y = self.height - 29 * mm
        c.setFillColor(TEAL)
        c.roundRect(button_x, button_y, 34 * mm, 8 * mm, 1.5 * mm, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 7)
        c.drawCentredString(button_x + 17 * mm, button_y + 2.4 * mm, "Apply Filter")
        self._label(c, "Use Adobe Acrobat/Reader.", 188 * mm, self.height - 26 * mm, 7)

        y = self.height - 55 * mm
        x = 12 * mm
        metrics = [
            ("Visible Plants", "calc_count"),
            ("Total Daily kWh", "calc_daily"),
            ("Total Weekly kWh", "calc_weekly"),
            ("2026 Generation kWh", "calc_year"),
            ("Installed Capacity kW", "calc_capacity"),
            ("Efficiency kWh/kW", "calc_efficiency"),
            ("Online Plants", "calc_online"),
            ("Alerts", "calc_alerts"),
        ]
        for idx, (title, field) in enumerate(metrics):
            self._metric(c, title, field, x + (idx % 4) * 64 * mm, y - (idx // 4) * 26 * mm, 58 * mm, 20 * mm)

        self._box(c, 12 * mm, 67 * mm, 124 * mm, 40 * mm, fill=colors.HexColor("#F8FBFF"))
        self._label(c, "Selected Plant Detail", 16 * mm, 98 * mm, 8, True)
        for i, (label, field) in enumerate(
            [
                ("Plant", "detail_site"),
                ("Brand", "detail_brand"),
                ("Status", "detail_status"),
                ("2026 Source", "detail_source"),
            ]
        ):
            yy = 88 * mm - i * 8 * mm
            self._label(c, label, 16 * mm, yy, 6)
            self._readonly_field(c, field, 42 * mm, yy - 2 * mm, 88 * mm, 6 * mm, self.initial_metrics.get(field, ""), 7)

        self._box(c, 145 * mm, 67 * mm, 126 * mm, 40 * mm, fill=colors.HexColor("#F8FBFF"))
        self._label(c, "How this works", 149 * mm, 98 * mm, 8, True)
        c.setFont("Helvetica", 7)
        c.setFillColor(TEXT)
        c.drawString(149 * mm, 88 * mm, "The inverter data is embedded inside this PDF.")
        c.drawString(149 * mm, 80 * mm, "Selecting a plant updates the metrics, table rows, and detail fields.")
        c.drawString(149 * mm, 72 * mm, "This uses Acrobat JavaScript and PDF form fields.")

    def _draw_page_2(self, c: canvas.Canvas) -> None:
        self._header(c, "Filtered Plant Table", 2)
        headers = ["Brand", "Plant", "Status", "Cap", "Daily", "Weekly", "2026/kW"]
        widths = [24 * mm, 76 * mm, 23 * mm, 22 * mm, 27 * mm, 30 * mm, 27 * mm]
        x0 = 12 * mm
        y = self.height - 27 * mm
        c.setFillColor(BLUE)
        c.rect(x0, y, sum(widths), 7 * mm, stroke=0, fill=1)
        x = x0
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 6)
        for header, width in zip(headers, widths):
            c.drawString(x + 1.5 * mm, y + 2 * mm, header)
            x += width

        row_h = 6.6 * mm
        for row_idx in range(self.row_count):
            plant = self.plants[row_idx]
            row_y = y - (row_idx + 1) * row_h
            c.setFillColor(colors.white if row_idx % 2 == 0 else colors.HexColor("#F6F8FA"))
            c.rect(x0, row_y, sum(widths), row_h, stroke=0, fill=1)
            x = x0
            initial_values = [
                plant["brand"],
                plant["site"],
                plant["status"],
                fmt(plant["capacity"], 2),
                fmt(plant["daily"], 2),
                fmt(plant["weekly"], 2),
                fmt(plant["yield2026"], 2),
            ]
            for col_idx, width in enumerate(widths):
                field_name = f"r{row_idx}_{headers[col_idx].lower().replace('/', '_')}"
                self._readonly_field(c, field_name, x + 1 * mm, row_y + 0.8 * mm, width - 2 * mm, 4.8 * mm, str(initial_values[col_idx]), 5.2)
                x += width
        c.setStrokeColor(GRID)
        c.rect(x0, y - self.row_count * row_h, sum(widths), (self.row_count + 1) * row_h, stroke=1, fill=0)

    def _draw_page_3(self, c: canvas.Canvas) -> None:
        self._header(c, "Interactive Calculation Fields", 3)
        self._label(c, "Dynamic Totals", 12 * mm, self.height - 28 * mm, 9, True)
        metrics = [
            ("Filtered Daily Generation", "calc_daily"),
            ("Filtered Weekly Generation", "calc_weekly"),
            ("Filtered 2026 Generation", "calc_year"),
            ("Filtered Efficiency", "calc_efficiency"),
        ]
        for idx, (title, field) in enumerate(metrics):
            self._metric(c, title, field, 12 * mm + idx * 65 * mm, self.height - 55 * mm, 58 * mm, 20 * mm)

        self._box(c, 12 * mm, 64 * mm, 260 * mm, 70 * mm, fill=colors.HexColor("#F8FBFF"))
        self._label(c, "Acrobat JavaScript notes", 17 * mm, 123 * mm, 9, True)
        c.setFont("Helvetica", 7)
        c.setFillColor(TEXT)
        c.drawString(17 * mm, 112 * mm, "1. Use Adobe Acrobat or Adobe Reader for dropdown filtering and recalculation.")
        c.drawString(17 * mm, 103 * mm, "2. The PDF is self-contained: no internet connection is needed after generation.")
        c.drawString(17 * mm, 94 * mm, "3. The table on page 2 reuses the same visual row area and hides unused rows.")
        c.drawString(17 * mm, 85 * mm, "4. macOS Preview may show the static opening state only because it does not run Acrobat JS.")

    def _javascript(self) -> str:
        plant_json = json.dumps(self.plants, separators=(",", ":"))
        return f"""
var PLANTS = {plant_json};
function n(v) {{ return Number(v || 0); }}
function f(v, d) {{ return util.printf('%.' + d + 'f', n(v)); }}
function field(name) {{ return this.getField(name); }}
function setv(name, value) {{ var x = field(name); if (x) x.value = value; }}
function show(name, visible) {{ var x = field(name); if (x) x.display = visible ? display.visible : display.hidden; }}
function selectedPlants(selected) {{
  if (!selected || selected === 'Select All') return PLANTS;
  var out = [];
  for (var i = 0; i < PLANTS.length; i++) if (PLANTS[i].site === selected) out.push(PLANTS[i]);
  return out;
}}
function updateDashboard(selected) {{
  var selector = field('inverter_selector');
  var value = selected || (selector ? selector.value : 'Select All');
  var visiblePlants = selectedPlants(value);
  var capacity = 0, daily = 0, weekly = 0, year = 0, online = 0, alerts = 0;
  for (var i = 0; i < visiblePlants.length; i++) {{
    var p = visiblePlants[i];
    capacity += n(p.capacity); daily += n(p.daily); weekly += n(p.weekly); year += n(p.year);
    if (String(p.status).toLowerCase().indexOf('online') >= 0 || String(p.status).toLowerCase().indexOf('normal') >= 0) online++;
    else alerts++;
  }}
  setv('calc_count', String(visiblePlants.length));
  setv('calc_capacity', f(capacity, 2));
  setv('calc_daily', f(daily, 2));
  setv('calc_weekly', f(weekly, 2));
  setv('calc_year', f(year, 2));
  setv('calc_efficiency', capacity > 0 ? f(year / capacity, 2) : '0.00');
  setv('calc_online', String(online));
  setv('calc_alerts', String(alerts));

  var detail = visiblePlants.length === 1 ? visiblePlants[0] : {{site:'All selected plants', brand:'Multiple', status:'Mixed', source:'Mixed'}};
  setv('detail_site', detail.site); setv('detail_brand', detail.brand);
  setv('detail_status', detail.status); setv('detail_source', detail.source || '');

  for (var r = 0; r < PLANTS.length; r++) {{
    var p2 = visiblePlants[r];
    var visible = !!p2;
    var names = ['brand','plant','status','cap','daily','weekly','2026_kw'];
    for (var c = 0; c < names.length; c++) show('r' + r + '_' + names[c], visible);
    if (visible) {{
      setv('r' + r + '_brand', p2.brand);
      setv('r' + r + '_plant', p2.site);
      setv('r' + r + '_status', p2.status);
      setv('r' + r + '_cap', f(p2.capacity, 2));
      setv('r' + r + '_daily', f(p2.daily, 2));
      setv('r' + r + '_weekly', f(p2.weekly, 2));
      setv('r' + r + '_2026_kw', f(p2.yield2026, 2));
    }}
  }}
}}
function setupDashboard() {{
  var selector = field('inverter_selector');
  if (selector) selector.setAction('Validate', 'updateDashboard(event.value);');
  updateDashboard('Select All');
}}
setupDashboard();
"""

    def _inject_javascript(self, source_pdf: Path, output_pdf: Path) -> None:
        reader = PdfReader(str(source_pdf))
        writer = PdfWriter()
        writer.clone_document_from_reader(reader)
        writer.add_js(self._javascript())
        writer._root_object.update(
            {
                NameObject("/OpenAction"): DictionaryObject(
                    {
                        NameObject("/S"): NameObject("/JavaScript"),
                        NameObject("/JS"): TextStringObject("setupDashboard();"),
                    }
                )
            }
        )
        if "/AcroForm" in writer._root_object:
            writer._root_object["/AcroForm"].update(
                {
                    NameObject("/NeedAppearances"): BooleanObject(True),
                    NameObject("/DA"): TextStringObject("/Helv 0 Tf 0 g"),
                }
            )
        self._attach_dropdown_action(writer)
        self._attach_apply_button_action(writer)
        with output_pdf.open("wb") as handle:
            writer.write(handle)

    def _attach_dropdown_action(self, writer: PdfWriter) -> None:
        root = writer._root_object
        acroform = root.get("/AcroForm")
        if not acroform:
            return
        fields = acroform.get("/Fields") or []
        for field_ref in fields:
            field = field_ref.get_object()
            if field.get("/T") == "inverter_selector":
                field.update(
                    {
                        NameObject("/AA"): DictionaryObject(
                            {
                                NameObject("/V"): DictionaryObject(
                                    {
                                        NameObject("/S"): NameObject("/JavaScript"),
                                        NameObject("/JS"): TextStringObject("updateDashboard(event.value || this.getField('inverter_selector').value);"),
                                    }
                                )
                            }
                        )
                    }
                )

    def _attach_apply_button_action(self, writer: PdfWriter) -> None:
        rect = ArrayObject(
            [
                FloatObject(148 * mm),
                FloatObject(self.height - 29 * mm),
                FloatObject(182 * mm),
                FloatObject(self.height - 21 * mm),
            ]
        )
        writer.add_annotation(
            page_number=0,
            annotation=DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Annot"),
                    NameObject("/Subtype"): NameObject("/Link"),
                    NameObject("/Rect"): rect,
                    NameObject("/Border"): ArrayObject([NumberObject(0), NumberObject(0), NumberObject(0)]),
                    NameObject("/A"): DictionaryObject(
                        {
                            NameObject("/S"): NameObject("/JavaScript"),
                            NameObject("/JS"): TextStringObject("updateDashboard(this.getField('inverter_selector').value);"),
                        }
                    ),
                }
            ),
        )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate an interactive Acrobat JavaScript solar dashboard PDF.")
    parser.add_argument("--input", help="Optional JSON/CSV input. Defaults to current GOODWE project data.")
    parser.add_argument("--output-dir", default="/Users/sushil/Library/Mobile Documents/com~apple~CloudDocs/Weekly Solar Plant Report")
    parser.add_argument("--logo", default=str(DEFAULT_LOGO))
    return parser


def main() -> Path:
    args = build_arg_parser().parse_args()
    output_dir = Path(args.output_dir)
    output_path = output_dir / f"Solar_Interactive_Dashboard_{dt.date.today():%Y%m%d}.pdf"
    plants = prepare_plant_data(current_project=not bool(args.input), input_path=args.input)
    logo_path = Path(args.logo) if args.logo else None
    pdf = InteractiveDashboardPDF(plants, output_path, logo_path=logo_path)
    result = pdf.build()
    print(f"Saved interactive Acrobat dashboard to {result}")
    return result


if __name__ == "__main__":
    main()
