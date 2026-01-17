# pdf_generator.py
# IndCad PDF Generator v1
# Uses ReportLab to generate Canada PR Action Plan PDFs

from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    PageBreak
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
import os
from datetime import datetime


def generate_indcad_pdf(output_path: str, engine_result: dict, snapshot: dict, context: dict):
    """
    Generates a Canada PR Action Plan PDF based on decision engine output
    """

    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        rightMargin=2 * cm,
        leftMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm
    )

    styles = getSampleStyleSheet()
    story = []

    # ------------------------------------------------------------------
    # CUSTOM STYLES
    # ------------------------------------------------------------------

    title_style = ParagraphStyle(
        name="TitleStyle",
        fontSize=20,
        spaceAfter=20,
        alignment=1,
        textColor=colors.HexColor("#0B6E4F")
    )

    section_style = ParagraphStyle(
        name="SectionStyle",
        fontSize=14,
        spaceBefore=16,
        spaceAfter=8,
        textColor=colors.HexColor("#0B6E4F")
    )

    normal_style = ParagraphStyle(
        name="NormalStyle",
        fontSize=10,
        spaceAfter=6
    )

    warning_style = ParagraphStyle(
        name="WarningStyle",
        fontSize=10,
        spaceAfter=6,
        textColor=colors.red
    )

    # ------------------------------------------------------------------
    # PAGE 1 — COVER
    # ------------------------------------------------------------------

    story.append(Paragraph("Canada PR Reality & Action Plan", title_style))
    story.append(Spacer(1, 20))

    story.append(Paragraph(
        f"Generated on: {datetime.utcnow().strftime('%Y-%m-%d')}",
        normal_style
    ))

    story.append(Paragraph(
        "Prepared by IndCad — Strategic Guidance Tool (Not a Consultant)",
        normal_style
    ))

    story.append(PageBreak())

    # ------------------------------------------------------------------
    # PAGE 2 — REALITY CHECK
    # ------------------------------------------------------------------

    story.append(Paragraph("Your Current PR Reality", section_style))

    verdict_map = {
        "REALISTIC": "🟢 PR is realistically achievable with correct execution.",
        "IMPROVABLE": "🟡 PR is not realistic immediately, but achievable with improvements.",
        "NOT_REALISTIC": "🔴 PR is not realistic with the current profile."
    }

    story.append(Paragraph(
        verdict_map.get(engine_result["ee_status"], "Reality unclear."),
        normal_style
    ))

    story.append(Paragraph(
        f"Your CRS score: {snapshot.get('crs_score')}",
        normal_style
    ))

    story.append(Paragraph(
        f"Recent cutoff: {snapshot.get('recent_cutoff')}",
        normal_style
    ))

    story.append(Spacer(1, 10))

    story.append(Paragraph(
        "This assessment is based on recent draw trends and the information you provided.",
        normal_style
    ))

    story.append(PageBreak())

    # ------------------------------------------------------------------
    # PAGE 3 — CONSTRAINTS
    # ------------------------------------------------------------------

    story.append(Paragraph("Your Current Constraints", section_style))

    constraints_table = [
        ["Current Status", context.get("status", "Not provided")],
        ["Time Remaining in Canada", context.get("time_remaining", "Not provided")],
        ["Language CLB", str(snapshot.get("clb", "N/A"))],
        ["Stay Priority", engine_result.get("stay_priority")]
    ]

    table = Table(constraints_table, colWidths=[7 * cm, 7 * cm])
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke)
    ]))

    story.append(table)
    story.append(PageBreak())

    # ------------------------------------------------------------------
    # PAGE 4 — PRIMARY PATH
    # ------------------------------------------------------------------

    story.append(Paragraph("Recommended Primary Path", section_style))

    story.append(Paragraph(
        f"Primary Strategy: {engine_result.get('primary_path')}",
        normal_style
    ))

    story.append(Paragraph(
        f"Risk Level: {engine_result.get('risk_level')}",
        normal_style
    ))

    story.append(Spacer(1, 10))

    if engine_result.get("primary_path") == "STUDY_PLUS_ALIGNMENT":
        story.append(Paragraph(
            "This path focuses on extending your legal stay while aligning your profile with in-demand roles.",
            normal_style
        ))
        story.append(Paragraph(
            "Typical actions include enrolling in a short, aligned course and gaining Canadian experience.",
            normal_style
        ))

    elif engine_result.get("primary_path") == "HEALTHCARE_ALIGNMENT":
        story.append(Paragraph(
            "This path focuses on transitioning into healthcare-aligned roles that may be eligible for category-based draws.",
            normal_style
        ))

    elif engine_result.get("primary_path") == "EXPRESS_ENTRY_FOCUS":
        story.append(Paragraph(
            "Your profile is close to recent Express Entry cutoffs. Correct timing and execution are critical.",
            normal_style
        ))

    else:
        story.append(Paragraph(
            "This strategy focuses on general improvements such as language scores and eligibility alignment.",
            normal_style
        ))

    story.append(PageBreak())

    # ------------------------------------------------------------------
    # PAGE 5 — WHAT NOT TO DO
    # ------------------------------------------------------------------

    story.append(Paragraph("What You Should Avoid", section_style))

    for item in engine_result.get("do_not_list", []):
        story.append(Paragraph(f"• {item}", normal_style))

    story.append(PageBreak())

    # ------------------------------------------------------------------
    # PAGE 6 — DISCLAIMER
    # ------------------------------------------------------------------

    story.append(Paragraph("Important Disclaimer", section_style))

    story.append(Paragraph(
        "This report is not legal advice and does not guarantee permanent residence.",
        warning_style
    ))

    story.append(Paragraph(
        "It is based on current public programs, recent draw patterns, and the information you provided.",
        warning_style
    ))

    story.append(Paragraph(
        "Immigration policies and outcomes may change without notice.",
        warning_style
    ))

    # ------------------------------------------------------------------
    # BUILD PDF
    # ------------------------------------------------------------------

    doc.build(story)
