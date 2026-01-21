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
        raise RuntimeError("OPENAI_API_KEY not set")

    instructions = (
        "You write clear, professional proposals for Australian trade and service businesses. "
        "Use plain, practical trade language. "
        "You may vary sentence structure naturally, as an experienced tradesperson would. "
        "Sound confident and professional, not generic. "
        "Avoid marketing fluff, hype, or sales language. "
        "Do not mention AI or automation. "
        "Write in Australian English.\n\n"

        "CRITICAL FORMAT RULES:\n"
        "- Output plain text ONLY.\n"
        "- Do NOT use markdown.\n"
        "- Do NOT use headings like ### or **bold**.\n"
        "- Do NOT use bullet symbols other than a dash (-).\n"
        "- Use normal sentence case and simple numbered section titles like '1. Overview'.\n"
    )

    trade_profile = (data.get("trade_profile") or "").strip()
    timeframe = (data.get("timeframe") or "").strip()

    your_business = (data.get("your_business") or "Your Business").strip()
    client_name = (data.get("client_name") or "Client").strip()
    service_type = (data.get("service_type") or "Service").strip()

    scope_notes = (data.get("scope") or "").strip()
    if not scope_notes:
        raise ValueError("Missing required fields: scope")

    price = (data.get("price") or "").strip() or "To be confirmed"
    tone = (data.get("tone") or "Professional").strip()

    TONE_GUIDANCE = {
        "Professional": (
            "Use a formal, businesslike tone. Keep language neutral, clear, and matter-of-fact."
        ),
        "Friendly": (
            "Use a warm, approachable tone while remaining professional. "
            "Allow slightly more conversational phrasing, without slang."
        ),
        "Direct": (
            "Use a concise, straight-to-the-point tone. "
            "Minimise softening language and keep sentences short and clear."
        ),
    }
    tone_instruction = TONE_GUIDANCE.get(tone, TONE_GUIDANCE["Professional"])

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
            temperature=0.0,
            max_output_tokens=250
        )
        locked_scope = (scope_resp.output_text or "").strip()
    except Exception:
        locked_scope = "\n".join(
            f"- {ln.lstrip('- ').strip()}"
            for ln in scope_notes.splitlines()
            if ln.strip()
        ).strip()

    if not locked_scope:
        locked_scope = "- Client to confirm scope details"

    # -------------------------
    # Build dynamic section list
    # -------------------------
    sections = [
        "1. Overview",
        "2. Scope of Work",
    ]

    if timeframe:
        sections.append("3. Timeframe")
        pricing_idx = 4
    else:
        pricing_idx = 3

    sections.append(f"{pricing_idx}. Pricing")
    sections.append(f"{pricing_idx + 1}. Acceptance / Next Steps")

    # -------------------------
    # Pass 2: Full proposal
    # -------------------------
    proposal_prompt = f"""
{trade_profile}

Business: {your_business}
Client: {client_name}
Service: {service_type}

Tone guidance:
{tone_instruction}

Locked Scope of Work bullets (use each bullet as a heading; do not add or remove items):
{locked_scope}

"""

    if timeframe:
        proposal_prompt += f"""
Timeframe (use exactly this wording, do not expand, estimate, or add dates):
{timeframe}
"""

    proposal_prompt += f"""
Price: {price}

This is a trade job involving professional works

Write a professional proposal in this exact structure:

- The first line must be: "Proposal for: {client_name}"
- Leave one blank line after it

Then include these sections in order:

{chr(10).join(sections)}

- The Overview must be 4–5 sentences and describe the work only in general terms based on the service type and scope notes.
- Do NOT name specific components, methods, standards, testing, or safety details unless they are explicitly provided by the user.

- After the final section, leave one blank line.
- End the proposal with:
  "Kind regards,"
  "{your_business}"

Rules:
- Output plain text only.
- Do NOT use markdown or decorative symbols.
- Do NOT invent, infer, or estimate timeframes.
- If a Timeframe section is included, use the supplied wording only.
- Do NOT include Timeline or Payment Terms sections.

- Scope of Work must include all locked bullets as headings.
- Under each bullet, add ONE short sentence that restates the bullet in slightly clearer plain language.
- Do NOT specify items, materials, components, locations, methods, safety steps, or assumptions unless they are explicitly written in the bullet.
- If a bullet is generic, the explanatory sentence must remain equally generic.


- Pricing must reflect the provided price or state that pricing is to be confirmed.
- Acceptance / Next Steps must NOT request a signature.
- Do NOT ask the client to sign or return the proposal.
- Acceptance should be described as confirming via phone or email.
- Write as a real Australian trade business quoting real work.
""".strip()

    try:
        proposal_resp = client.responses.create(
            model=MODEL,
            instructions=instructions,
            input=proposal_prompt,
            temperature=0.3,
            max_output_tokens=900
        )
        text = (proposal_resp.output_text or "").strip()

        if not text:
            raise RuntimeError("Empty AI response")

        return text

    except Exception as e:
        raise RuntimeError(f"AI generation failed: {type(e).__name__}") from e
