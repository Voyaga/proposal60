from flask import Flask, render_template, request, make_response, send_file
from proposal_builder import build_proposal_text
import os
import io
import time
from collections import defaultdict, deque

from itsdangerous import URLSafeSerializer, BadSignature


app = Flask(__name__)

# IMPORTANT: set this in Render env vars (SECRET_KEY)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")
_signer = URLSafeSerializer(app.secret_key, salt="gtj-free-limit")


# --------------------
# Config
# --------------------
FREE_LIMIT = 3
COOKIE_NAME = "proposal60_free_used"
RATE_LIMIT_PER_MINUTE = 10

# In-memory per-IP request timestamps
_ip_hits = defaultdict(deque)


# --------------------
# Helpers
# --------------------
def is_rate_limited(ip: str) -> bool:
    now = time.time()
    window_start = now - 60
    q = _ip_hits[ip]

    while q and q[0] < window_start:
        q.popleft()

    if len(q) >= RATE_LIMIT_PER_MINUTE:
        return True

    q.append(now)
    return False


def get_used_count() -> int:
    raw = request.cookies.get(COOKIE_NAME, "")
    if not raw:
        return 0
    try:
        return int(_signer.loads(raw))
    except (BadSignature, ValueError, TypeError):
        return 0


def set_used_cookie(resp, used: int) -> None:
    token = _signer.dumps(str(used))
    resp.set_cookie(
        COOKIE_NAME,
        token,
        max_age=60 * 60 * 24 * 365,
        httponly=True,
        samesite="Lax",
        secure=bool(os.environ.get("RENDER")),
    )


# --------------------
# Routes
# --------------------
@app.get("/")
def landing():
    return render_template("landing.html")


@app.get("/app")
def app_home():
    return render_template("home.html")


@app.get("/upgrade")
def upgrade():
    return render_template("upgrade.html")


@app.post("/generate")
def generate():
    # ---- Rate limit ----
    ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "unknown"
    ip = ip.split(",")[0].strip()

    if is_rate_limited(ip):
        return render_template(
            "preview.html",
            proposal_text="",
            blocked=True,
            remaining=0,
            block_reason="rate",
        ), 429

    # ---- Free usage limit ----
    used = get_used_count()
    if used >= FREE_LIMIT:
        return render_template(
            "preview.html",
            proposal_text="",
            blocked=True,
            remaining=0,
            block_reason="free",
        )

    # ---- Collect form data ----
    data = {
        "client_name": request.form.get("client_name", "").strip(),
        "service_type": request.form.get("service_type", "").strip(),
        "scope": request.form.get("scope", "").strip(),
        "price": request.form.get("price", "").strip(),
        "tone": request.form.get("tone", "Professional").strip(),
        "your_business": request.form.get("your_business", "").strip(),
        "trade": request.form.get("trade", "general").strip().lower(),
        "abn": request.form.get("abn", "").strip(),
        "phone": request.form.get("phone", "").strip(),
        "email": request.form.get("email", "").strip(),
    }

    # ---- Generate proposal body ----
    proposal_text = build_proposal_text(data)

    # ---- Append footer (NO AI) ----
    footer_lines = []

    if data["abn"]:
        footer_lines.append(f"ABN: {data['abn']}")
    if data["phone"]:
        footer_lines.append(f"Phone: {data['phone']}")
    if data["email"]:
        footer_lines.append(f"Email: {data['email']}")

    if footer_lines:
        proposal_text += (
            "\n\n—\n"
            "Business Details\n"
            + "\n".join(footer_lines)
        )

    # ---- Increment usage ----
    used += 1
    remaining = max(FREE_LIMIT - used, 0)

    resp = make_response(
        render_template(
            "preview.html",
            proposal_text=proposal_text,
            blocked=False,
            remaining=remaining,
        )
    )
    set_used_cookie(resp, used)

    return resp


@app.post("/pdf")
def pdf():
    proposal_text = request.form.get("proposal_text", "").strip()
    if not proposal_text:
        proposal_text = "No proposal text provided."

    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import LETTER

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=LETTER)

    width, height = LETTER
    x = 50
    y = height - 60
    line_height = 14

    c.setFont("Courier", 11)
    c.drawString(x, y, "Proposal")
    y -= 22

    def wrap_line(s, max_len=95):
        out = []
        while len(s) > max_len:
            cut = s.rfind(" ", 0, max_len)
            if cut == -1:
                cut = max_len
            out.append(s[:cut].rstrip())
            s = s[cut:].lstrip()
        out.append(s)
        return out

    for raw in proposal_text.splitlines():
        for ln in wrap_line(raw):
            if y < 60:
                c.showPage()
                c.setFont("Courier", 11)
                y = height - 60
            c.drawString(x, y, ln)
            y -= line_height

    c.save()
    buffer.seek(0)

    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name="proposal.pdf",
    )


# --------------------
# Local dev entrypoint
# --------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
