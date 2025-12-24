import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
MODEL = "gpt-4.1-nano"


def generate_proposal_ai(data: dict) -> str:
    """
    Two-pass generation:
      1) Generate Scope of Work bullets ONLY (strictly derived from scope notes)
      2) Generate full proposal using the locked scope bullets
    """

    if not os.environ.get("OPENAI_API_KEY"):
        return "Server error: OPENAI_API_KEY not set."

    instructions = (
        "You write clear, professional proposals for Australian trade and service businesses. "
        "Use plain language. Be concise. Do not use marketing fluff. "
        "Do not mention AI or automation. "
        "Write in Australian English."
    )

    trade_profile = (data.get("trade_profile") or "").strip()

    your_business = (data.get("your_business") or "Your Business").strip()
    client_name = (data.get("client_name") or "Client").strip()
    service_type = (data.get("service_type") or "Service").strip()

    scope_notes = (data.get("scope") or "").strip()
    if not scope_notes:
        return "Missing required fields: client name and scope."

    price = (data.get("price") or "").strip() or "To be confirmed"
    tone = (data.get("tone") or "Professional").strip()

    # -------------------------
    # Pass 1: Scope bullets ONLY
    # -------------------------
    scope_prompt = f"""
{trade_profile}

Business: {your_business}
Client: {client_name}
Service: {service_type}

Scope notes:
{scope_notes}

Task:
Convert the Scope notes into a clean "Scope of Work" bullet list.

Hard rules:
- Output ONLY bullet points (each line starts with "- ").
- Use ONLY the information in Scope notes.
- Do NOT add new items, materials, compliance claims, quantities, or steps not mentioned.
- Keep bullets practical and client-ready (Australian English).
""".strip()

    try:
        scope_resp = client.responses.create(
            model=MODEL,
            instructions=instructions,
            input=scope_prompt,
            temperature=0.0,          # make scope deterministic
            max_output_tokens=250
        )
        locked_scope = (scope_resp.output_text or "").strip()
    except Exception:
        # Fallback: minimally sanitize user scope if AI pass fails
        locked_scope = "\n".join(
            f"- {ln.lstrip('- ').strip()}"
            for ln in scope_notes.splitlines()
            if ln.strip()
        ).strip()

    if not locked_scope:
        locked_scope = "- [Client to confirm scope details]"

    # -------------------------
    # Pass 2: Full proposal
    # -------------------------
    proposal_prompt = f"""
{trade_profile}

Business: {your_business}
Client: {client_name}
Service: {service_type}
Tone: {tone}

Locked Scope of Work (MUST use exactly these bullets; do not add/remove scope items):
{locked_scope}

Price: {price}

Write a professional proposal with these exact sections:

1) Overview
2) Scope of Work
3) Timeline
4) Pricing
5) Payment Terms
6) Acceptance / Next Steps

Rules:
- Scope of Work section must reproduce the locked bullets (you may lightly edit wording for grammar only).
- Placeholders are allowed ONLY for Timeline, Pricing wording (if price is “To be confirmed”), Payment Terms, and Next Steps.
- Keep language practical and client-ready. No hype. No exclamation points.
""".strip()

    try:
        proposal_resp = client.responses.create(
            model=MODEL,
            instructions=instructions,
            input=proposal_prompt,
            temperature=0.3,
            max_output_tokens=900
        )
        return (proposal_resp.output_text or "").strip()

    except Exception as e:
        return f"AI generation failed: {type(e).__name__}"
