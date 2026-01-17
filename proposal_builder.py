from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
import textwrap

from ai_engine import generate_proposal_ai


# ------------------------------
# Trade-specific language profiles
# ------------------------------
TRADE_PROFILES = {
    "electrician": """
You are writing a professional proposal for an Australian licensed electrician.
Use clear, compliant electrical trade language suitable for residential or light commercial work.
Reference safe installation practices, testing, and compliance with Australian Standards where appropriate.
Avoid marketing language. Write as a qualified tradesperson quoting real work.
""",

    "plumber": """
You are writing a professional proposal for an Australian licensed plumber.
Use practical plumbing trade language suitable for residential or light commercial work.
Reference installation, replacement, repair, and compliance obligations where relevant.
Keep wording clear, direct, and client-ready. Avoid sales or marketing tone.
""",

    "builder": """
You are writing a professional proposal for an Australian builder or renovation contractor.
Use construction-industry language suitable for residential building or renovation projects.
Describe work in terms of scope, materials, sequencing, and coordination.
Write clearly and professionally, as a builder quoting real on-site work.
""",

    "hvac": """
You are writing a professional proposal for an Australian HVAC contractor.
Use correct heating, cooling, and ventilation trade terminology.
Reference installation, commissioning, and system performance where relevant.
Maintain a professional, technical tone suitable for residential or commercial clients.
""",

    "cleaner": """
You are writing a professional proposal for a commercial or residential cleaning service.
Use clear service-based language focused on tasks, areas, and standards of cleanliness.
Avoid marketing phrases. Write as an established service provider outlining scope of work.
""",

    "general": """
You are writing a professional proposal for an Australian trade or service contractor.
Use practical, industry-appropriate language.
Clearly describe the work, scope, and expectations without marketing or embellishment.
"""
}



# ------------------------------
# Proposal text builders
# ------------------------------
def build_proposal_text(data: dict) -> str:
    """Primary proposal builder: AI first, fallback if unavailable."""
    try:
        return generate_proposal_ai(data)
    except Exception:
        return build_fallback_proposal(data)


def build_fallback_proposal(data: dict) -> str:
    """Deterministic fallback proposal (no AI)."""

    client = data.get("client_name", "Client").strip()
    service = data.get("service_type", "the requested work").strip()
    scope = data.get("scope", "").strip()
    price = data.get("price", "").strip()
    timeframe = data.get("timeframe", "").strip()
    business = data.get("your_business", "").strip()

    lines = []

    # ---- Header ----
    lines.append(f"Proposal for: {client}")
    lines.append("")

    # ---- Overview ----
    lines.append("1. Overview")
    if business:
        lines.append(
            f"This proposal outlines the scope of works for {service} to be carried out by {business}. "
            "The work will be completed in accordance with standard trade practices and applicable requirements."
        )
    else:
        lines.append(
            f"This proposal outlines the scope of works for {service}. "
            "The work will be completed in accordance with standard trade practices and applicable requirements."
        )
    lines.append("")

    # ---- Scope of Work ----
    lines.append("2. Scope of Work")
    if scope:
        for line in scope.splitlines():
            clean = line.lstrip("- ").strip()
            if clean:
                lines.append(f"- {clean}")
    else:
        lines.append("- Details to be confirmed")
    lines.append("")

    # ---- Optional Timeframe ----
    section_index = 3
    if timeframe:
        lines.append(f"{section_index}. Timeframe")
        lines.append(timeframe)
        lines.append("")
        section_index += 1

    # ---- Pricing ----
    lines.append(f"{section_index}. Pricing")
    if price:
        lines.append(price)
    else:
        lines.append("Pricing to be confirmed.")
    lines.append("")
    section_index += 1

    # ---- Acceptance ----
    lines.append(f"{section_index}. Acceptance / Next Steps")
    lines.append(
        "Please review the details above and contact us by phone or email "
        "to confirm acceptance or discuss any questions."
    )
    lines.append("")
    lines.append("Kind regards,")
    if business:
        lines.append(business)

    return "\n".join(lines)



# ------------------------------
# PDF generator (FIXED WRAPPING)
# ------------------------------
def generate_pdf(proposal_text: str, footer_lines: list[str] | None = None):
    """
    Generate a wrapped, professional PDF.
    FIXES text running off the page.
    """
    buffer = bytes()
    c = canvas.Canvas("proposal.pdf", pagesize=A4)

    width, height = A4
    x_margin = 25 * mm
    y_margin = 25 * mm
    max_width_chars = 95  # controls wrapping

    y = height - y_margin
    c.setFont("Helvetica", 10)

    for paragraph in proposal_text.split("\n"):
        wrapped = textwrap.wrap(paragraph, max_width_chars) or [""]

        for line in wrapped:
            if y < y_margin:
                c.showPage()
                c.setFont("Helvetica", 10)
                y = height - y_margin

            c.drawString(x_margin, y, line)
            y -= 14

    # Footer
    if footer_lines:
        c.setFont("Helvetica", 9)
        y = y_margin - 10
        for line in footer_lines:
            c.drawString(x_margin, y, line)
            y -= 12

    c.save()
    return "proposal.pdf"
