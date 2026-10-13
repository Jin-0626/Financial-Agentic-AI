"""
Institutional Financial Report & PDF Generator
Converts structured financial payloads into executive PDF tear-sheets and HTML briefs.
Supports WeasyPrint and native ReportLab engines with automated chart rendering.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime
import io
import json
import logging
import os
from typing import Any, Dict, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

logger = logging.getLogger(__name__)

# Backend checks
try:
    from weasyprint import HTML
    WEASYPRINT_AVAILABLE = True
except (ImportError, OSError):
    WEASYPRINT_AVAILABLE = False

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage, KeepTogether
    )
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib import colors as rl_colors
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False


class FinancialReportGenerator:
    """Renders executive-grade financial reports and charts."""

    def __init__(self, templates_dir: Optional[str] = None):
        self.templates_dir = templates_dir or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "report_templates"
        )
        os.makedirs(self.templates_dir, exist_ok=True)

        self.env = Environment(
            loader=FileSystemLoader(self.templates_dir),
            autoescape=select_autoescape(["html", "xml"])
        )

        # Register template filters
        self.env.filters["format_number"] = self.format_number
        self.env.filters["format_currency"] = self.format_currency
        self.env.filters["format_percent"] = self.format_percent
        self.env.filters["format_date"] = self.format_date

        self._ensure_default_template()

    # -------------------------------------------------------------------------
    # Formatters & Filters
    # -------------------------------------------------------------------------

    @staticmethod
    def format_number(val: Any, decimals: int = 2) -> str:
        try:
            return f"{float(val):,.{decimals}f}"
        except (ValueError, TypeError):
            return str(val or "—")

    @staticmethod
    def format_currency(val: Any, symbol: str = "$", decimals: int = 2) -> str:
        try:
            return f"{symbol}{float(val):,.{decimals}f}"
        except (ValueError, TypeError):
            return f"{symbol}{val or '—'}"

    @staticmethod
    def format_percent(val: Any, decimals: int = 2) -> str:
        try:
            return f"{float(val):.{decimals}f}%"
        except (ValueError, TypeError):
            return f"{val or '—'}%"

    @staticmethod
    def format_date(val: Any, fmt: str = "%Y-%m-%d") -> str:
        try:
            if isinstance(val, str):
                dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            else:
                dt = val
            return dt.strftime(fmt)
        except Exception:
            return str(val or "")

    # -------------------------------------------------------------------------
    # Charting Subsystem
    # -------------------------------------------------------------------------

    def render_chart_base64(self, chart_config: Dict[str, Any]) -> Optional[str]:
        """Generate Matplotlib chart and encode as base64 data URI."""
        if not MATPLOTLIB_AVAILABLE:
            logger.warning("Matplotlib is not installed. Skipping chart generation.")
            return None

        chart_type = chart_config.get("type", "line")
        data = chart_config.get("data", {})
        title = chart_config.get("title", "")
        figsize = chart_config.get("figsize", (9, 4.5))

        fig, ax = plt.subplots(figsize=figsize, facecolor="#ffffff")
        ax.set_facecolor("#fafafa")

        # Palette
        colors = ["#0052cc", "#00875a", "#ffab00", "#de350b", "#6554c0"]

        if chart_type == "line":
            for i, (series_name, series_data) in enumerate(data.items()):
                x = series_data.get("x", [])
                y = series_data.get("y", [])
                color = colors[i % len(colors)]
                ax.plot(x, y, label=series_name, color=color, linewidth=2)
            ax.legend(frameon=True, facecolor="#ffffff", edgecolor="#e0e0e0")

        elif chart_type == "bar":
            categories = list(data.keys())
            values = [data[k] for k in categories]
            bars = ax.bar(categories, values, color="#0052cc", width=0.55)
            ax.bar_label(bars, padding=3, fmt="%.2f", fontsize=8)

        elif chart_type == "pie":
            labels = list(data.keys())
            sizes = [data[k] for k in labels]
            ax.pie(sizes, labels=labels, autopct="%1.1f%%", colors=colors, startangle=140)

        ax.set_title(title, fontsize=12, fontweight="bold", pad=12, color="#172b4d")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color("#cccccc")
        ax.spines["bottom"].set_color("#cccccc")
        ax.grid(True, linestyle="--", alpha=0.5, color="#e0e0e0")

        buf = io.BytesIO()
        plt.tight_layout()
        plt.savefig(buf, format="png", dpi=200, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        encoded = base64.b64encode(buf.read()).decode("utf-8")
        return f"data:image/png;base64,{encoded}"

    # -------------------------------------------------------------------------
    # HTML Rendering
    # -------------------------------------------------------------------------

    def _ensure_default_template(self) -> str:
        tpl_path = os.path.join(self.templates_dir, "default.html")
        if os.path.exists(tpl_path):
            return tpl_path

        template_src = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>{{ metadata.title }}</title>
    <style>
        @page {
            size: {{ styles.pageSize | default('A4') }} {{ styles.orientation | default('portrait') }};
            margin: {{ styles.margins | default('20mm 15mm 20mm 15mm') }};
            @top-right {
                content: "{{ metadata.company | default('Financial Investment Research') }}";
                font-family: 'Helvetica Neue', Arial, sans-serif;
                font-size: 8pt;
                color: #7a869a;
            }
            @bottom-right {
                content: "Page " counter(page) " of " counter(pages);
                font-family: 'Helvetica Neue', Arial, sans-serif;
                font-size: 8pt;
                color: #7a869a;
            }
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            color: #172b4d;
            line-height: 1.5;
            font-size: 10pt;
            margin: 0;
            padding: 0;
        }
        .header-bar {
            border-bottom: 2px solid #0052cc;
            padding-bottom: 12px;
            margin-bottom: 20px;
        }
        h1 {
            color: #091e42;
            font-size: 20pt;
            margin: 0 0 4px 0;
            font-weight: 700;
        }
        .company-name {
            font-size: 11pt;
            font-weight: 600;
            color: #0052cc;
            margin: 0 0 8px 0;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .metadata-strip {
            font-size: 8.5pt;
            color: #5e6c84;
            display: flex;
            gap: 20px;
        }
        h2 {
            color: #091e42;
            font-size: 13pt;
            border-left: 3.5px solid #0052cc;
            padding-left: 8px;
            margin: 20px 0 10px 0;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            margin: 12px 0 20px 0;
            font-size: 9pt;
        }
        th, td {
            padding: 7px 10px;
            text-align: left;
            border-bottom: 1px solid #ebecf0;
        }
        th {
            background-color: #f4f5f7;
            color: #42526e;
            font-weight: 600;
            border-bottom: 2px solid #dfe1e6;
        }
        tr:nth-child(even) td {
            background-color: #fafbfc;
        }
        .chart-container {
            text-align: center;
            margin: 15px 0;
        }
        .chart-container img {
            max-width: 100%;
            height: auto;
            border-radius: 4px;
            border: 1px solid #ebecf0;
        }
        .callout {
            background-color: #f4f5f7;
            border-left: 4px solid #4c9aff;
            padding: 10px 14px;
            margin: 12px 0;
            font-size: 9pt;
        }
    </style>
</head>
<body>
    <div class="header-bar">
        {% if metadata.company %}
        <div class="company-name">{{ metadata.company }}</div>
        {% endif %}
        <h1>{{ metadata.title }}</h1>
        <div class="metadata-strip">
            {% if metadata.date %}<span><strong>Date:</strong> {{ metadata.date | format_date('%B %d, %Y') }}</span>{% endif %}
            {% if metadata.author %}<span><strong>Author:</strong> {{ metadata.author }}</span>{% endif %}
            {% if metadata.confidentiality %}<span><strong>Classification:</strong> {{ metadata.confidentiality }}</span>{% endif %}
        </div>
    </div>

    {% for comp in components %}
        {% if comp.type == 'heading' %}
            <h2>{{ comp.content }}</h2>

        {% elif comp.type == 'text' %}
            <p>{{ comp.content }}</p>

        {% elif comp.type == 'callout' %}
            <div class="callout">{{ comp.content }}</div>

        {% elif comp.type == 'table' %}
            <table>
                <thead>
                    <tr>
                        {% for col in comp.config.columns %}
                        <th>{{ col }}</th>
                        {% endfor %}
                    </tr>
                </thead>
                <tbody>
                    {% for row in comp.content.rows %}
                    <tr>
                        {% for cell in row %}
                        <td>{{ cell }}</td>
                        {% endfor %}
                    </tr>
                    {% endfor %}
                </tbody>
            </table>

        {% elif comp.type == 'chart' %}
            {% if comp.content.image %}
            <div class="chart-container">
                <img src="{{ comp.content.image }}" alt="Report Chart" />
            </div>
            {% endif %}
        {% endif %}
    {% endfor %}
</body>
</html>"""
        with open(tpl_path, "w", encoding="utf-8") as f:
            f.write(template_src)
        return tpl_path

    def generate_html(self, payload: Dict[str, Any], template_name: str = "default.html") -> str:
        """Render populated HTML from report payload."""
        # Process and generate charts if required
        for comp in payload.get("components", []):
            if comp.get("type") == "chart" and comp.get("content", {}).get("data"):
                img_uri = self.render_chart_base64(comp["content"])
                if img_uri:
                    comp["content"]["image"] = img_uri

        tpl = self.env.get_template(template_name)
        return tpl.render(**payload)

    # -------------------------------------------------------------------------
    # PDF Generators (WeasyPrint & Native ReportLab)
    # -------------------------------------------------------------------------

    def _generate_reportlab(self, payload: Dict[str, Any], output_path: str) -> None:
        """Generate executive PDF using pure ReportLab."""
        styles = getSampleStyleSheet()

        title_style = ParagraphStyle(
            "DocTitle",
            parent=styles["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=20,
            leading=24,
            textColor=rl_colors.HexColor("#091e42"),
            spaceAfter=4,
        )
        company_style = ParagraphStyle(
            "CompanyHeader",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=12,
            textColor=rl_colors.HexColor("#0052cc"),
            spaceAfter=6,
        )
        meta_style = ParagraphStyle(
            "MetaLine",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=8,
            textColor=rl_colors.HexColor("#5e6c84"),
            spaceAfter=15,
        )
        heading_style = ParagraphStyle(
            "SectionHeading",
            parent=styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=16,
            textColor=rl_colors.HexColor("#091e42"),
            spaceBefore=14,
            spaceAfter=6,
        )
        body_style = ParagraphStyle(
            "Body",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=9,
            leading=13,
            textColor=rl_colors.HexColor("#172b4d"),
            spaceAfter=6,
        )

        doc = SimpleDocTemplate(
            output_path,
            pagesize=A4,
            leftMargin=36,
            rightMargin=36,
            topMargin=36,
            bottomMargin=36
        )
        story = []

        # Header
        meta = payload.get("metadata", {})
        if meta.get("company"):
            story.append(Paragraph(meta["company"].upper(), company_style))
        if meta.get("title"):
            story.append(Paragraph(meta["title"], title_style))

        meta_line = []
        if meta.get("date"):
            meta_line.append(f"<b>Date:</b> {meta['date']}")
        if meta.get("author"):
            meta_line.append(f"<b>Author:</b> {meta['author']}")
        if meta.get("confidentiality"):
            meta_line.append(f"<b>Class:</b> {meta['confidentiality']}")
        if meta_line:
            story.append(Paragraph(" &nbsp;|&nbsp; ".join(meta_line), meta_style))

        story.append(Spacer(1, 0.15 * inch))

        # Render Components
        for comp in payload.get("components", []):
            ctype = comp.get("type")
            content = comp.get("content")

            if ctype == "heading":
                story.append(Paragraph(str(content), heading_style))

            elif ctype in ("text", "callout"):
                story.append(Paragraph(str(content), body_style))

            elif ctype == "table":
                config = comp.get("config", {})
                cols = config.get("columns", [])
                rows = content.get("rows", [])
                if cols and rows:
                    t_data = [[Paragraph(f"<b>{c}</b>", body_style) for c in cols]]
                    for row in rows:
                        t_data.append([Paragraph(str(cell), body_style) for cell in row])

                    table = Table(t_data, repeatRows=1)
                    table.setStyle(TableStyle([
                        ("BACKGROUND", (0, 0), (-1, 0), rl_colors.HexColor("#f4f5f7")),
                        ("LINEBELOW", (0, 0), (-1, 0), 1.5, rl_colors.HexColor("#dfe1e6")),
                        ("LINEBELOW", (0, 1), (-1, -1), 0.5, rl_colors.HexColor("#ebecf0")),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                        ("TOPPADDING", (0, 0), (-1, -1), 5),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ]))
                    story.append(table)
                    story.append(Spacer(1, 0.15 * inch))

            elif ctype == "chart":
                img_data = content.get("image")
                if img_data and img_data.startswith("data:image"):
                    # Decode base64 chart into in-memory Image flowable
                    raw_b64 = img_data.split(",", 1)[1]
                    img_bytes = io.BytesIO(base64.b64decode(raw_b64))
                    rl_img = RLImage(img_bytes, width=6.5 * inch, height=3.2 * inch)
                    story.append(KeepTogether([rl_img, Spacer(1, 0.1 * inch)]))

        doc.build(story)

    def generate_pdf(self, payload: Dict[str, Any], output_path: str) -> str:
        """
        Produce a PDF deliverable.
        Prefers WeasyPrint for pixel-accurate CSS rendering; falls back to ReportLab.
        """
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

        if WEASYPRINT_AVAILABLE:
            try:
                html_str = self.generate_html(payload)
                HTML(string=html_str).write_pdf(output_path)
                return output_path
            except Exception as e:
                logger.warning(f"WeasyPrint failed ({e}). Defaulting to ReportLab.")

        if REPORTLAB_AVAILABLE:
            self._generate_reportlab(payload, output_path)
            return output_path

        raise RuntimeError("No PDF backend available. Install WeasyPrint or ReportLab.")


# -----------------------------------------------------------------------------
# CLI Entry Point
# -----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Financial Report & PDF Generator")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # html
    p_html = subparsers.add_parser("html", help="Render report to HTML")
    p_html.add_argument("input_json", help="Path to template data JSON")
    p_html.add_argument("-o", "--output", required=True, help="Output HTML file path")

    # pdf
    p_pdf = subparsers.add_parser("pdf", help="Render report to PDF")
    p_pdf.add_argument("input_json", help="Path to template data JSON")
    p_pdf.add_argument("-o", "--output", required=True, help="Output PDF file path")

    args = parser.parse_args()
    generator = FinancialReportGenerator()

    with open(args.input_json, "r", encoding="utf-8") as f:
        payload = json.load(f)

    if args.command == "html":
        rendered = generator.generate_html(payload)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(rendered)
        print(f"HTML saved to {args.output}")

    elif args.command == "pdf":
        out = generator.generate_pdf(payload, args.output)
        print(f"PDF successfully rendered to {out}")


if __name__ == "__main__":
    main()