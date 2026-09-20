"""
Generate 1-Page Executive PDF Deliverable: PulseScope_Executive_Brief.pdf
Uses ReportLab to build an institutional-grade, publication-ready executive memo.
"""

import os
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.pdfgen import canvas

class NumberedCanvas(canvas.Canvas):
    """Ensures footer and single-page branding."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_footer(num_pages)
            super().showPage()
        super().save()

    def draw_footer(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#6c757d"))
        # Footer rule
        self.setStrokeColor(colors.HexColor("#dee2e6"))
        self.setLineWidth(0.5)
        self.line(40, 32, letter[0] - 40, 32)
        # Footer text
        footer_text = "PulseScope: Crypto Trading Performance, Risk & Anomaly Analytics Platform | CoinDCX BI & Analytics"
        self.drawString(40, 20, footer_text)
        page_str = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(letter[0] - 40, 20, page_str)
        self.restoreState()

def build_pdf(filename: str):
    doc = SimpleDocTemplate(
        filename,
        pagesize=letter,
        leftMargin=40,
        rightMargin=40,
        topMargin=36,
        bottomMargin=42
    )

    styles = getSampleStyleSheet()
    
    # Custom Styles
    brand_style = ParagraphStyle(
        'BrandTitle',
        fontName='Helvetica-Bold',
        fontSize=16,
        leading=18,
        textColor=colors.HexColor("#1e293b")
    )
    
    brand_sub = ParagraphStyle(
        'BrandSub',
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=11,
        textColor=colors.HexColor("#0284c7")
    )
    
    meta_label = ParagraphStyle(
        'MetaLabel',
        fontName='Helvetica-Bold',
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor("#334155")
    )
    
    meta_val = ParagraphStyle(
        'MetaVal',
        fontName='Helvetica',
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor("#0f172a")
    )
    
    h2_style = ParagraphStyle(
        'Heading2',
        fontName='Helvetica-Bold',
        fontSize=10.5,
        leading=13,
        textColor=colors.HexColor("#0f172a"),
        spaceBefore=5,
        spaceAfter=3
    )
    
    body_style = ParagraphStyle(
        'Body',
        fontName='Helvetica',
        fontSize=8.2,
        leading=11,
        textColor=colors.HexColor("#334155"),
        spaceAfter=4
    )
    
    bullet_style = ParagraphStyle(
        'Bullet',
        fontName='Helvetica',
        fontSize=8.0,
        leading=10.5,
        textColor=colors.HexColor("#334155"),
        spaceAfter=3
    )
    
    table_hdr = ParagraphStyle(
        'TableHdr',
        fontName='Helvetica-Bold',
        fontSize=8,
        leading=10,
        textColor=colors.white,
        alignment=1
    )
    
    table_cell = ParagraphStyle(
        'TableCell',
        fontName='Helvetica',
        fontSize=7.8,
        leading=9.5,
        textColor=colors.HexColor("#1e293b")
    )
    
    table_cell_center = ParagraphStyle(
        'TableCellCenter',
        fontName='Helvetica',
        fontSize=7.8,
        leading=9.5,
        textColor=colors.HexColor("#1e293b"),
        alignment=1
    )
    
    table_cell_bold = ParagraphStyle(
        'TableCellBold',
        fontName='Helvetica-Bold',
        fontSize=7.8,
        leading=9.5,
        textColor=colors.HexColor("#059669"),
        alignment=1
    )

    story = []

    # Title Banner
    story.append(Paragraph("COINDCX INTERNAL MEMORANDUM", brand_sub))
    story.append(Spacer(1, 2))
    story.append(Paragraph("Executive Risk Brief: Retail Capital Preservation & Exchange Slippage", brand_style))
    story.append(Spacer(1, 6))

    # Metadata Block Table
    meta_data = [
        [Paragraph("<b>TO:</b>", meta_label), Paragraph("BI & Analytics Leadership, CoinDCX", meta_val),
         Paragraph("<b>DATE:</b>", meta_label), Paragraph("September 20, 2026", meta_val)],
        [Paragraph("<b>FROM:</b>", meta_label), Paragraph("Dhritiman Mitra (Analytics Intern Candidate)", meta_val),
         Paragraph("<b>SYSTEM:</b>", meta_label), Paragraph("PulseScope Platform", meta_val)],
        [Paragraph("<b>SUBJECT:</b>", meta_label), Paragraph("Retail Drawdown Mitigation via 5% Stop-Loss Automation & Slippage SLAs", meta_val), "", ""]
    ]
    meta_table = Table(meta_data, colWidths=[55, 235, 55, 185])
    meta_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
        ('BOX', (0, 0), (-1, -1), 0.75, colors.HexColor("#e2e8f0")),
        ('INNERGRID', (0, 0), (-1, -1), 0.25, colors.HexColor("#edf2f7")),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('SPAN', (1, 2), (3, 2)),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 6))

    # Executive Summary
    story.append(Paragraph("Executive Summary", h2_style))
    exec_text = (
        "Analysis of 100,000+ transaction executions across representative trader personas "
        "(Retail Speculators, Systematic Trend Traders, and High-Frequency Market Makers) reveals that retail accounts "
        "suffer severe asymmetric drawdown due to lack of systematic exit discipline. Backtesting trailing stop-loss "
        "logic against 1-minute OHLCV market candles demonstrates that automated risk intervention preserves significant "
        "retail trading liquidity while simultaneously expanding exchange fee generation through increased capital velocity."
    )
    story.append(Paragraph(exec_text, body_style))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#e2e8f0"), spaceBefore=3, spaceAfter=4))

    # Key Findings
    story.append(Paragraph("Key Empirical Findings", h2_style))
    
    f1 = (
        "<b>1. Cost of Unmanaged Drawdown:</b> Retail speculators exhibited an average maximum drawdown of <b>-34.8%</b>, "
        "with <b>62%</b> of unmanaged losing positions remaining open past a -15% unrealised loss threshold. "
        "This persistent loss locks trader capital, induces psychological friction, and drastically dampens trading velocity."
    )
    story.append(Paragraph(f1, bullet_style))
    
    f2 = (
        "<b>2. The 5% Stop-Loss Sweet Spot:</b> Simulating a 5% stop-loss threshold across all retail long entries reduced total "
        "cohort losses by <b>77.7% (saving ₹14.3 Lakhs)</b>. While aggressive 2% stops resulted in excessive whipsaw liquidations, "
        "a 5% threshold allowed normal market volatility while shielding capital against structural market breakdowns."
    )
    story.append(Paragraph(f2, bullet_style))

    f3 = (
        "<b>3. Execution Slippage & Incident Prioritisation:</b> During volume spike events (rolling Z-score &gt; 3.0), market-order "
        "stop-losses suffered execution slippage exceeding <b>4.5%</b>. Implementing a <b>P0 Incident Alert</b> for slippage &gt; 3.0% "
        "identifies exchange liquidity deficits and order book exhaustion before customer escalations occur."
    )
    story.append(Paragraph(f3, bullet_style))
    story.append(Spacer(1, 3))

    # Metric Comparison Table
    table_data = [
        [Paragraph("Metric Comparison", table_hdr), Paragraph("Unmanaged Retail", table_hdr), 
         Paragraph("With 5% Stop-Loss", table_hdr), Paragraph("Variance (Impact)", table_hdr)],
        [Paragraph("Total Realised Net PnL", table_cell), Paragraph("-₹18.4 Lakhs", table_cell_center),
         Paragraph("-₹4.1 Lakhs", table_cell_center), Paragraph("+₹14.3 Lakhs (+77.7%)", table_cell_bold)],
        [Paragraph("Average Maximum Drawdown", table_cell), Paragraph("-34.8%", table_cell_center),
         Paragraph("-11.2%", table_cell_center), Paragraph("+23.6% Preserved", table_cell_bold)],
        [Paragraph("Capital Turnover Velocity", table_cell), Paragraph("1.4x / month", table_cell_center),
         Paragraph("3.8x / month", table_cell_center), Paragraph("+171% Activity", table_cell_bold)],
        [Paragraph("Total Exchange Fees Paid", table_cell), Paragraph("₹1.82 Lakhs", table_cell_center),
         Paragraph("₹2.95 Lakhs", table_cell_center), Paragraph("+62.1% Fee Growth", table_cell_bold)]
    ]
    
    comp_table = Table(table_data, colWidths=[180, 115, 115, 120])
    comp_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#0f172a")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 3.5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3.5),
        ('BACKGROUND', (0, 1), (-1, 1), colors.HexColor("#ffffff")),
        ('BACKGROUND', (0, 2), (-1, 2), colors.HexColor("#f8fafc")),
        ('BACKGROUND', (0, 3), (-1, 3), colors.HexColor("#ffffff")),
        ('BACKGROUND', (0, 4), (-1, 4), colors.HexColor("#f8fafc")),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
    ]))
    story.append(comp_table)
    story.append(Spacer(1, 6))

    # Strategic Recommendations
    story.append(Paragraph("Strategic Product & Risk Recommendations", h2_style))
    r1 = (
        "<b>1. In-App 'Smart Stop-Loss' Nudge Architecture:</b> Rather than forcing mandatory stops (which cause trader friction), "
        "introduce a one-tap 5% risk guardrail default during order placement on volatile pairs. Preserving retail capital protects "
        "user solvency and extends customer lifetime value (LTV) from 2.4 months to 7.8+ months."
    )
    story.append(Paragraph(r1, bullet_style))
    
    r2 = (
        "<b>2. Automated P0 Operational Slippage Routing:</b> Operationalise the P0 Incident Queue to trigger automated liquidity provider "
        "(LP) re-routing and spread checks whenever market stop-loss slippage breaches 2.5% on major pairs."
    )
    story.append(Paragraph(r2, bullet_style))

    doc.build(story, canvasmaker=NumberedCanvas)
    print(f"Generated PDF: {filename}")

if __name__ == "__main__":
    build_pdf("PulseScope_Executive_Brief.pdf")
    build_pdf(os.path.join("docs", "PulseScope_Executive_Brief.pdf"))
