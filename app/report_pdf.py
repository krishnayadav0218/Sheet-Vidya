"""
Builds a short PDF summary report (quality score, issues, fix history) —
NOT a dump of the raw data (a PDF is a bad format for a big table; xlsx/csv
cover that). Used by GET /api/download/{id}?format=pdf.

Uses fpdf2's core "helvetica" font (latin-1 only) to avoid bundling extra
font files for a demo project — text is defensively transliterated/
stripped of anything outside latin-1 so this never crashes on stray
characters coming from an uploaded sheet or an AI explanation.
"""

from datetime import datetime, timezone
from typing import List, Optional

from fpdf import FPDF
from fpdf.enums import XPos, YPos


def _safe(text: str) -> str:
    if text is None:
        return ""
    return str(text).encode("latin-1", errors="replace").decode("latin-1")


class _ReportPDF(FPDF):
    def header(self):
        self.set_font("helvetica", "B", 16)
        self.set_text_color(30, 30, 30)
        self.cell(0, 10, "SheetVaidya - Data Quality Report", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_draw_color(200, 200, 200)
        self.line(10, 20, 200, 20)
        self.ln(6)

    def footer(self):
        self.set_y(-15)
        self.set_font("helvetica", "I", 8)
        self.set_text_color(140, 140, 140)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")

    def section_title(self, text: str):
        self.set_font("helvetica", "B", 12)
        self.set_text_color(20, 20, 20)
        self.ln(2)
        self.cell(0, 8, _safe(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_text_color(60, 60, 60)
        self.set_font("helvetica", "", 10)

    def full_line(self, height: float, text: str):
        """cell(width=0, ...) that reliably resets the cursor to the left margin after."""
        self.cell(0, height, _safe(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def wrapped_line(self, height: float, text: str):
        self.multi_cell(0, height, _safe(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def build_quality_report_pdf(
    filename: str,
    quality: dict,
    fix_history: Optional[List[dict]] = None,
) -> bytes:
    pdf = _ReportPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    pdf.set_font("helvetica", "", 10)
    pdf.set_text_color(90, 90, 90)
    pdf.full_line(6, f"File: {filename}")
    pdf.full_line(6, f"Generated: {generated_at}")

    # Score box
    pdf.ln(4)
    score = quality.get("score", 0)
    grade = quality.get("grade", "-")
    pdf.set_font("helvetica", "B", 28)
    color = (124, 154, 110) if score >= 75 else (212, 162, 76) if score >= 50 else (193, 85, 74)
    pdf.set_text_color(*color)
    pdf.full_line(16, f"Score: {score}/100  (Grade {grade})")
    pdf.set_text_color(60, 60, 60)
    pdf.set_font("helvetica", "", 10)
    pdf.full_line(6, f"{quality.get('total_rows', 0)} rows x {quality.get('total_columns', 0)} columns")

    # Issues
    pdf.section_title("Issues found")
    for issue in quality.get("issues", []):
        bullet = {"high": "[HIGH]", "medium": "[MED]", "low": "[LOW]", "info": "[OK]"}.get(
            issue.get("severity", "info"), "-"
        )
        pdf.wrapped_line(6, f"{bullet} {issue.get('message', '')}")
    pdf.ln(2)

    # Missing values by column (top 10)
    missing_cols = [c for c in quality.get("missing_by_column", []) if c.get("missing_count", 0) > 0][:10]
    if missing_cols:
        pdf.section_title("Missing values by column (top 10)")
        pdf.set_font("helvetica", "B", 9)
        pdf.cell(90, 7, "Column", border="B")
        pdf.cell(40, 7, "Missing count", border="B")
        pdf.cell(40, 7, "Missing %", border="B", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("helvetica", "", 9)
        for c in missing_cols:
            pdf.cell(90, 6, _safe(c["column"])[:45])
            pdf.cell(40, 6, str(c["missing_count"]))
            pdf.cell(40, 6, f"{c['missing_pct']}%", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(2)

    # Fix history
    if fix_history:
        pdf.section_title("Fixes applied this session")
        pdf.set_font("helvetica", "", 9)
        for i, item in enumerate(fix_history, 1):
            instruction = item.get("instruction", "")
            explanation = item.get("explanation", "")
            line = f"{i}. {instruction}"
            if explanation:
                line += f" -> {explanation}"
            pdf.wrapped_line(6, line)

    return bytes(pdf.output())
