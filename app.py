# app.py

from flask import Flask, render_template, request, make_response
import json
from proposal_builder import build_proposal_text
import os

app = Flask(__name__)
FREE_LIMIT = 3
COOKIE_NAME = "proposal60_free_used"

@app.get("/")
def home():
    return render_template("home.html")

@app.post("/generate")
def generate():
    # Read how many free proposals this browser has used
    used_raw = request.cookies.get(COOKIE_NAME, "0")
    try:
        used = int(used_raw)
    except ValueError:
        used = 0

    # If over limit, show a clean block message
    if used >= FREE_LIMIT:
        return render_template(
            "preview.html",
            proposal_text=(
                "Free limit reached.\n\n"
                "You’ve used your 3 free proposals.\n"
                "To generate more, please upgrade."
            ),
            data={},
            blocked=True,
            remaining=0
        )

    data = {
        "client_name": request.form.get("client_name", "").strip(),
        "service_type": request.form.get("service_type", "").strip(),
        "scope": request.form.get("scope", "").strip(),
        "price": request.form.get("price", "").strip(),
        "tone": request.form.get("tone", "Professional").strip(),
        "your_business": request.form.get("your_business", "").strip(),
    }

    proposal_text = build_proposal_text(data)

    # Increment usage count only when we attempted a generation
    used += 1
    remaining = max(FREE_LIMIT - used, 0)

    resp = make_response(render_template(
        "preview.html",
        proposal_text=proposal_text,
        data=data,
        blocked=False,
        remaining=remaining
    ))
    resp.set_cookie(COOKIE_NAME, str(used), max_age=60 * 60 * 24 * 365)  # 1 year
    return resp


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

