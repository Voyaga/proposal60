from ai_engine import generate_proposal_ai

# Trade-specific language profiles
TRADE_PROFILES = {
    "electrician": """
You are writing a professional proposal for an Australian licensed electrician.

Use practical, compliant language such as:
- supply and install
- test and commission
- licensed electrician
- in accordance with relevant Australian Standards

Assume:
- Access to the work area is provided
- Existing wiring is in serviceable condition unless stated otherwise
- Site will be left clean and safe on completion

Avoid:
- Marketing or sales language
- Overly technical explanations
""",

    "plumber": """
You are writing a professional proposal for an Australian licensed plumber.

Use language such as:
- isolate water supply
- replace fittings
- pressure test
- compliant installation

Assume:
- Standard access is provided
- Existing pipework is serviceable unless stated otherwise
- Site will be left clean on completion

Avoid:
- Sales language
- Technical jargon the client does not need
""",

    "builder": """
You are writing a professional proposal for a builder or renovation contractor.

Use language around:
- scope of works
- site inspection
- exclusions and variations
- staged works if applicable

Assume:
- Works are subject to site conditions
- Variations are excluded unless agreed in writing

Keep the tone clear, practical, and professional.
""",

    "hvac": """
You are writing a professional proposal for an HVAC contractor.

Use language such as:
- supply and install
- system commissioning
- compliant installation
- manufacturer specifications

Assume:
- Standard access is available
- Existing systems are in serviceable condition unless noted
- Site will be left clean on completion
""",

    "cleaner": """
You are writing a professional proposal for a cleaning service.

Use clear, simple language describing:
- inclusions
- frequency (if applicable)
- exclusions

Assume:
- Reasonable access is provided
- Heavy staining or specialised cleaning is excluded unless stated
""",

    "general": """
Write a clear, professional proposal suitable for a general trade contractor.

Use practical, client-ready language.
Avoid marketing or sales phrasing.
"""
}


def build_proposal_text(data: dict) -> str:
    """
    Primary proposal builder.
    Uses AI if available, otherwise falls back to a clean template.
    """

    # Try AI first
    try:
        from ai_engine import generate_proposal_with_ai
        return generate_proposal_with_ai(data)

    except Exception:
        # AI unavailable → fallback template
        return build_fallback_proposal(data)


def build_fallback_proposal(data: dict) -> str:
    """
    Deterministic, non-AI proposal template.
    This guarantees output even if AI fails.
    """

    client = data.get("client_name", "Client")
    service = data.get("service_type", "the requested work")
    scope = data.get("scope", "").strip()
    price = data.get("price", "").strip()
    business = data.get("your_business", "").strip()

    lines = []

    # Header
    lines.append(f"Proposal for {client}")
    lines.append("")
    lines.append(f"Service: {service}")
    lines.append("")

    # Intro
    if business:
        lines.append(
            f"Thank you for the opportunity to provide this proposal on behalf of {business}."
        )
    else:
        lines.append(
            "Thank you for the opportunity to provide this proposal."
        )

    lines.append("")

    # Scope
    lines.append("Scope of Works:")
    if scope:
        for line in scope.splitlines():
            lines.append(f"- {line.lstrip('- ').strip()}")
    else:
        lines.append("- Details to be confirmed")

    lines.append("")

    # Price
    if price:
        lines.append(f"Price: {price}")
        lines.append("")

    # Closing
    lines.append(
        "Please review the details above and let us know if you have any questions "
        "or would like to proceed."
    )
    lines.append("")
    lines.append("Kind regards,")

    if business:
        lines.append(business)

    return "\n".join(lines)

