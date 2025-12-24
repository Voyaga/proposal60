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
    # Minimal validation (keep MVP fast)
    if not data.get("client_name") or not data.get("scope"):
        return "Missing required fields: client name and scope."

    # Resolve trade profile
    trade_key = data.get("trade", "general").lower()
    trade_profile = TRADE_PROFILES.get(trade_key, TRADE_PROFILES["general"])

    # Inject trade profile into data for the AI engine
    data_with_profile = {
        **data,
        "trade_profile": trade_profile
    }

    # Always use AI for MVP
    return generate_proposal_ai(data_with_profile)
