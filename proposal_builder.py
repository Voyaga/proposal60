# proposal_builder.py

from ai_engine import generate_proposal_ai

def build_proposal_text(data: dict) -> str:
    # Minimal validation (keep MVP fast)
    if not data.get("client_name") or not data.get("scope"):
        return "Missing required fields: client name and scope."

    # For MVP: always use AI. Later we can add “template-only” fallback.
    return generate_proposal_ai(data)
