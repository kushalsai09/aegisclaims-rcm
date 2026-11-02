from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.models.claim import Claim


NAVY = colors.HexColor("#0A233C")
TEAL = colors.HexColor("#0D8D85")
SLATE = colors.HexColor("#475569")
LIGHT_SLATE = colors.HexColor("#F1F5F9")
BORDER = colors.HexColor("#D8E1EA")
ROSE = colors.HexColor("#B42318")


def generate_appeal_pdf(claim: Claim) -> bytes:
    """Build a polished, single-page payer appeal summary packet."""

    if not claim.suggested_fix or not claim.policy_citation:
        raise ValueError("Claim must be analyzed before generating an appeal packet")

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=0.55 * inch,
        leftMargin=0.55 * inch,
        topMargin=0.45 * inch,
        bottomMargin=0.45 * inch,
        title=f"Appeal Summary - {claim.id}",
        author="MainStreet Family Care",
    )
    styles = _styles()

    header = Table(
        [
            [
                Paragraph("<b>MAINSTREET FAMILY CARE</b>", styles["brand"]),
                Paragraph("PAYER APPEAL SUMMARY", styles["header_right"]),
            ],
            [
                Paragraph("Revenue Cycle Management", styles["header_subtitle"]),
                Paragraph(f"Claim {_pdf_escape(claim.id)}", styles["header_meta"]),
            ],
        ],
        colWidths=[4.3 * inch, 2.5 * inch],
    )
    header.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), NAVY),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.white),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (0, -1), 16),
                ("RIGHTPADDING", (-1, 0), (-1, -1), 16),
                ("TOPPADDING", (0, 0), (-1, 0), 11),
                ("BOTTOMPADDING", (0, 1), (-1, 1), 11),
            ]
        )
    )

    summary_data = [
        [
            _summary_cell("Patient", claim.patient_name, styles),
            _summary_cell("Insurer", claim.insurer, styles),
            _summary_cell("Date of service", claim.date_of_service.strftime("%B %d, %Y"), styles),
        ],
        [
            _summary_cell("Claim ID", claim.id, styles),
            _summary_cell("CPT code", claim.cpt_code, styles),
            _summary_cell("Billed amount", f"${claim.amount:,.2f}", styles),
        ],
    ]
    summary = Table(summary_data, colWidths=[2.28 * inch] * 3)
    summary.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), LIGHT_SLATE),
                ("BOX", (0, 0), (-1, -1), 0.6, BORDER),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )

    story = [
        header,
        Spacer(1, 13),
        Paragraph("CLAIM SUMMARY", styles["section_label"]),
        Spacer(1, 5),
        summary,
        Spacer(1, 12),
        _section(
            "REJECTION EXPLANATION",
            f"<b>{_pdf_escape(claim.denial_code)}</b> - {_pdf_escape(claim.denial_reason)}",
            styles,
            accent=ROSE,
        ),
        Spacer(1, 10),
        _section(
            "CORRECTIVE ACTION",
            _pdf_escape(claim.suggested_fix),
            styles,
            accent=TEAL,
        ),
        Spacer(1, 10),
        _section(
            "PAYER POLICY CITATION",
            _pdf_escape(claim.policy_citation),
            styles,
            accent=colors.HexColor("#2563EB"),
            citation=True,
        ),
        Spacer(1, 11),
        Paragraph(
            "<b>Requested disposition:</b> Reprocess the corrected claim or reconsider the denial "
            "using the enclosed claim facts, documented corrective action, and cited payer policy.",
            styles["request"],
        ),
    ]
    document.build(story, onFirstPage=_draw_footer)
    return buffer.getvalue()


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "brand": ParagraphStyle(
            "Brand", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=15,
            leading=18, textColor=colors.white,
        ),
        "header_right": ParagraphStyle(
            "HeaderRight", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=10,
            leading=12, textColor=colors.white, alignment=TA_RIGHT, spaceAfter=0,
        ),
        "header_subtitle": ParagraphStyle(
            "HeaderSubtitle", parent=base["Normal"], fontSize=8.5, leading=10,
            textColor=colors.HexColor("#A9CBD1"),
        ),
        "header_meta": ParagraphStyle(
            "HeaderMeta", parent=base["Normal"], fontSize=8.5, leading=10,
            textColor=colors.HexColor("#A9CBD1"), alignment=TA_RIGHT,
        ),
        "section_label": ParagraphStyle(
            "SectionLabel", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=8,
            leading=10, textColor=SLATE, tracking=1.1,
        ),
        "summary_label": ParagraphStyle(
            "SummaryLabel", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=7,
            leading=8, textColor=SLATE, spaceAfter=2,
        ),
        "summary_value": ParagraphStyle(
            "SummaryValue", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=9,
            leading=11, textColor=NAVY,
        ),
        "body": ParagraphStyle(
            "Body", parent=base["Normal"], fontSize=8.4, leading=12, textColor=colors.HexColor("#25364A"),
        ),
        "citation": ParagraphStyle(
            "Citation", parent=base["Normal"], fontName="Helvetica-Oblique", fontSize=7.8,
            leading=11, textColor=SLATE,
        ),
        "request": ParagraphStyle(
            "Request", parent=base["Normal"], fontSize=8.2, leading=11.5, textColor=NAVY,
            borderColor=BORDER, borderWidth=0.6, borderPadding=8, backColor=LIGHT_SLATE,
        ),
    }


def _summary_cell(label: str, value: str, styles: dict[str, ParagraphStyle]) -> list[Paragraph]:
    return [
        Paragraph(_pdf_escape(label).upper(), styles["summary_label"]),
        Paragraph(_pdf_escape(value), styles["summary_value"]),
    ]


def _section(
    heading: str,
    content: str,
    styles: dict[str, ParagraphStyle],
    *,
    accent: colors.Color,
    citation: bool = False,
) -> KeepTogether:
    body = Paragraph(content, styles["citation"] if citation else styles["body"])
    block = Table(
        [[Paragraph(heading, styles["section_label"]), body]],
        colWidths=[1.55 * inch, 5.3 * inch],
    )
    block.setStyle(
        TableStyle(
            [
                ("LINEBEFORE", (0, 0), (0, 0), 3, accent),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (0, 0), 9),
                ("RIGHTPADDING", (0, 0), (0, 0), 10),
                ("LEFTPADDING", (1, 0), (1, 0), 8),
                ("RIGHTPADDING", (1, 0), (1, 0), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return KeepTogether([block])


def _draw_footer(canvas, document) -> None:
    canvas.saveState()
    width, _ = letter
    canvas.setStrokeColor(BORDER)
    canvas.line(document.leftMargin, 0.34 * inch, width - document.rightMargin, 0.34 * inch)
    canvas.setFillColor(SLATE)
    canvas.setFont("Helvetica", 6.8)
    canvas.drawString(document.leftMargin, 0.2 * inch, "CONFIDENTIAL - MINIMUM NECESSARY BILLING INFORMATION")
    canvas.drawRightString(width - document.rightMargin, 0.2 * inch, "Page 1 of 1")
    canvas.restoreState()


def _pdf_escape(value: str) -> str:
    normalized = (
        value.replace("\u2011", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2026", "...")
    )
    return escape(normalized)
