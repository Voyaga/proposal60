# ai_engine.py

import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# Create OpenAI client (standard API usage)
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

MODEL = "gpt-4.1-nano"


def generate_proposal_ai(data: dict) -> str:
    """
    Generates a professional proposal using GPT-4.1 nano
    Standard API usage only (no fine-tuning).
    """

    if not os.environ.get("OPENAI_API_KEY"):
        return "Server error: OPENAI_API_KEY not set."

    instructions = (
        "You write clear, professional proposals for small service businesses. "
        "Use plain language. Be concise. Do not mention AI. "
        "Write in Australian English."
    )

    prompt = f"""
Business: {data.get('your_business', 'Your Business')}
Client: {data.get('client_name', 'Client')}
Service: {data.get('service_type', 'Service')}

Scope notes:
{data.get('scope', '')}

Price: {data.get('price', 'To be confirmed')}
Tone: {data.get('tone', 'Professional')}

Produce a proposal with these exact sections:
1) Overview
2) Scope of Work (bullet points)
3) Timeline
4) Pricing
5) Payment Terms
6) Acceptance / Next Steps

Do not invent scope items. If information is missing, use reasonable placeholders.
""".strip()

    try:
        response = client.responses.create(
            model=MODEL,
            instructions=instructions,
            input=prompt,
            temperature=0.3,
            max_output_tokens=900
        )

        return response.output_text.strip()

    except Exception as e:
        return f"AI generation failed: {type(e).__name__}"
