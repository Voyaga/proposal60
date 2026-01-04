from flask import Flask, render_template, request, make_response, send_file
from proposal_builder import build_proposal_text
import os
import io
import time
from collections import defaultdict, deque
from itsdangerous import URLSafeSerializer, BadSignature
import logging

# --------------------
# Analytics (admin-only)
# --------------------
logging.basicConfig(
    level=logging.INFO,
    format="ANALYTICS | %(message)s"
)

ANALYTICS_EVENTS = deque(maxlen=300)

def track(event, **data):
    entry = {
        "event": event,
        "data": data,
        "ts": time.time()
    }
    logging.info(f"{event} | {data}")
    ANALYTICS_EVENTS.appendleft(entry)


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
    track("page_view", page="landing")
    return render_template("landing.html")


@app.get("/app")
def app_home():
    track("page_view", page="app")
    return render_template("home.html")


@app.get("/preview")
def preview():
    track("page_view", page="preview")
    return render_template(
        "preview.html",
        proposal_text="",
        blocked=False,
        remaining=0,
    )


@app.get("/upgrade")
def upgrade():
    track("page_view", page="upgrade")
    return render_template("upgrade.html")


# --------------------
# Admin analytics (PRIVATE)
# --------------------
@app.get("/admin/analytics")
def admin_analytics():
    key = request.args.get("key")
    if key != os.environ.get("ADMIN_KEY"):
        return "Forbidden", 403

    stats = {
        "page_views": sum(1 for e in ANALYTICS_EVENTS if e["event"] == "page_view"),
        "generates": sum(1 for e in ANALYTICS_EVENTS if e["event"] == "generate_attempt"),
        "pdfs": sum(1 for e in ANALYTICS_EVENTS if e["event"] == "pdf_download"),
        "ai": sum(
            1 for e in ANALYTICS_EVENTS
            if e["event"] == "generate_success" and e["data"].get("mode") == "ai"
        ),
        "fallback": sum(
            1 for e in ANALYTICS_EVENTS
            if e["event"] == "generate_success" and e["data"].get("mode") == "fallback"
        ),
        "funnel": {
            "landing_to_app": 52,
            "app_to_generate": 31,
            "generate_to_pdf": 55,
        }
    }

    recent = [
        f"{time.strftime('%H:%M:%S', time.localtime(e['ts']))} — "
        f"{e['event']} {e['data']}"
        for e in list(ANALYTICS_EVENTS)[:12]
    ]

    return render_template(
        "admin_analytics.html",
        stats=stats,
        recent=recent
    )


@app.post("/generate")
def generate():
    track("generate_attempt")

    # ---- Rate limit ----
    ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "unknown"
    ip = ip.split(",")[0].strip()

    if is_rate_limited(ip):
        track("rate_limited", ip=ip)
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
        track("free_limit_reached")
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
    try:
        proposal_text = build_proposal_text(data)
        track("generate_success", mode="ai_or_fallback")
    except Exception as e:
        track("generate_error", error=str(e))
        proposal_text = "Proposal generation failed."

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
            "\n\n—\nBusiness Details\n" +
            "\n".join(footer_lines)
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
    track("pdf_download")

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
