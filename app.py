from flask import (
    Flask,
    render_template,
    request,
    make_response,
    send_file,
    redirect
)
from proposal_builder import build_proposal_text
import os
import io
import time
from collections import defaultdict, deque
from itsdangerous import URLSafeSerializer, BadSignature
import logging
import stripe

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


# --------------------
# App init
# --------------------
app = Flask(__name__)

app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")
_signer = URLSafeSerializer(app.secret_key, salt="gtj-free-limit")

# --------------------
# Stripe config
# --------------------
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_PRICE_ID = os.environ.get("STRIPE_PRICE_ID")

# --------------------
# Limits & cookies
# --------------------
FREE_LIMIT = 3
COOKIE_NAME = "proposal60_free_used"
PRO_COOKIE = "gtj_pro"

RATE_LIMIT_PER_MINUTE = 10
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

@app.post("/track")
def client_track():
    try:
        payload = request.get_json(force=True)
        event = payload.get("event")
        data = payload.get("data", {})
        track(event, **data)
    except Exception:
        pass
    return "", 204


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


def is_pro_user() -> bool:
    return request.cookies.get(PRO_COOKIE) == "1"


def set_pro_cookie(resp):
    resp.set_cookie(
        PRO_COOKIE,
        "1",
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
    return render_template(
        "landing.html",
        is_pro=is_pro_user()
    )


@app.get("/app")
def app_home():
    track("page_view", page="app")
    return render_template(
        "home.html",
        is_pro=is_pro_user()
    )


@app.get("/preview")
def preview():
    track("page_view", page="preview")
    return render_template(
        "preview.html",
        proposal_text="",
        blocked=False,
        remaining=0,
        is_pro=is_pro_user()
    )


@app.get("/upgrade")
def upgrade():
    track("page_view", page="upgrade")
    return render_template(
        "upgrade.html",
        restore_error=None,
        restored=False,
        is_pro=is_pro_user()
    )


# --------------------
# Stripe Checkout
# --------------------
@app.post("/checkout")
def checkout():
    session = stripe.checkout.Session.create(
        mode="subscription",
        payment_method_types=["card"],
        line_items=[{
            "price": STRIPE_PRICE_ID,
            "quantity": 1,
        }],
        success_url=request.host_url + "upgrade/success",
        cancel_url=request.host_url + "upgrade",
    )

    return redirect(session.url, code=303)


@app.get("/upgrade/success")
def upgrade_success():
    resp = make_response(
        render_template(
            "upgrade_success.html",
            is_pro=True
        )
    )
    set_pro_cookie(resp)
    return resp


# --------------------
# Restore Pro Access
# --------------------
@app.post("/restore-pro")
def restore_pro():
    email = request.form.get("email", "").strip().lower()

    if not email:
        return render_template(
            "upgrade.html",
            restore_error="Please enter the email you used at checkout.",
            restored=False,
            is_pro=False
        )

    customers = stripe.Customer.search(
        query=f"email:'{email}'",
        limit=1
    )

    if not customers.data:
        return render_template(
            "upgrade.html",
            restore_error="No active Pro subscription found for that email.",
            restored=False,
            is_pro=False
        )

    customer_id = customers.data[0].id

    subs = stripe.Subscription.list(
        customer=customer_id,
        status="active",
        limit=1
    )

    if not subs.data:
        return render_template(
            "upgrade.html",
            restore_error="No active Pro subscription found for that email.",
            restored=False,
            is_pro=False
        )

    resp = make_response(
        render_template(
            "upgrade.html",
            restore_error=None,
            restored=True,
            is_pro=True
        )
    )
    set_pro_cookie(resp)
    return resp


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

        # generation mode (placeholders for now)
        "ai": sum(1 for e in ANALYTICS_EVENTS if e["event"] == "ai_used"),
        "fallback": sum(1 for e in ANALYTICS_EVENTS if e["event"] == "fallback_used"),

        # funnel (safe defaults)
        "funnel": {
            "landing_to_app": 0,
            "app_to_generate": 0,
            "generate_to_pdf": 0,
        },

        # friction signals (NEW, safe)
        "hesitations": sum(
            1 for e in ANALYTICS_EVENTS
            if e["event"] == "first_interaction"
            and e["data"].get("delay_ms", 0) >= 15000
        ),
        "logo_retries": sum(
            1 for e in ANALYTICS_EVENTS
            if e["event"] == "logo_rejected"
        ),
        "preview_abandons": sum(
            1 for e in ANALYTICS_EVENTS
            if e["event"] == "preview_abandon"
        ),
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

# --------------------
# Stripe Billing Portal (Cancel / Manage)
# --------------------
@app.post("/billing-portal")
def billing_portal():
    email = request.form.get("email", "").strip().lower()
    if not email:
        return redirect("/upgrade")

    customers = stripe.Customer.search(
        query=f"email:'{email}'",
        limit=1
    )

    if not customers.data:
        return redirect("/upgrade")

    customer_id = customers.data[0].id

    session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=request.host_url + "app"
    )

    return redirect(session.url, code=303)





# --------------------
# Generate proposal
# --------------------
@app.post("/generate")
def generate():
    track("generate_attempt")

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
            is_pro=is_pro_user()
        ), 429

    if not is_pro_user():
        used = get_used_count()
        if used >= FREE_LIMIT:
            track("free_limit_reached")
            return render_template(
                "preview.html",
                proposal_text="",
                blocked=True,
                remaining=0,
                block_reason="free",
                is_pro=is_pro_user()
            )
    else:
        used = 0

    data = {
        "client_name": request.form.get("client_name", "").strip(),
        "service_type": request.form.get("service_type", "").strip(),
        "scope": request.form.get("scope", "").strip(),
        "price": request.form.get("price", "").strip(),
        "timeframe": request.form.get("timeframe", "").strip(),  # ← ADD THIS
        "tone": request.form.get("tone", "Professional").strip(),
        "your_business": request.form.get("your_business", "").strip(),
        "trade": request.form.get("trade", "general").strip().lower(),
        "abn": request.form.get("abn", "").strip(),
        "phone": request.form.get("phone", "").strip(),
        "email": request.form.get("email", "").strip(),
    }

    proposal_text = build_proposal_text(data)

    footer_lines = []
    if data["abn"]:
        footer_lines.append(f"ABN: {data['abn']}")
    if data["phone"]:
        footer_lines.append(f"Phone: {data['phone']}")
    if data["email"]:
        footer_lines.append(f"Email: {data['email']}")

    if footer_lines:
        proposal_text += "\n\n—\nBusiness Details\n" + "\n".join(footer_lines)

    if not is_pro_user():
        used += 1
        remaining = max(FREE_LIMIT - used, 0)
    else:
        remaining = "∞"

    resp = make_response(
        render_template(
            "preview.html",
            proposal_text=proposal_text,
            blocked=False,
            remaining=remaining,
            is_pro=is_pro_user()
        )
    )

    if not is_pro_user():
        set_used_cookie(resp, used)

    return resp


# --------------------
# PDF (Pro-only)
# --------------------
@app.post("/pdf")
def pdf():
    if not is_pro_user():
        return redirect("/upgrade")

    track("pdf_download")

    # --------------------
    # Clean proposal body
    # --------------------
    proposal_text = request.form.get("proposal_text", "").strip()

    # Strip preview-injected business details from body
    lines = proposal_text.splitlines()
    clean_lines = []
    for line in lines:
        if line.strip() == "—":
            break
        clean_lines.append(line)
    proposal_text = "\n".join(clean_lines).rstrip()

    logo_data = request.form.get("logo_data", "").strip()
    business_name = request.form.get("business_name", "").strip()
    footer_text = request.form.get(
        "biz_footer",
        "Generated with Get The Job"
    ).strip() or "Generated with Get The Job"

    # --------------------
    # Imports
    # --------------------
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.colors import HexColor, black
    from reportlab.lib.utils import ImageReader
    import base64
    import io

    # --------------------
    # Canvas setup
    # --------------------
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=LETTER)

    width, height = LETTER
    margin_x = 50
    y = height

    header_height = 70
    footer_height = 40
    line_height = 14

    # --------------------
    # Gradient header
    # --------------------
    GRADIENT_START = HexColor("#1e5eff")  # brand blue
    GRADIENT_END   = HexColor("#22c55e")  # brand green

    def draw_gradient_header(c, x, y, width, height, steps=100):
        for i in range(steps):
            ratio = i / steps
            r = GRADIENT_START.red   + (GRADIENT_END.red   - GRADIENT_START.red)   * ratio
            g = GRADIENT_START.green + (GRADIENT_END.green - GRADIENT_START.green) * ratio
            b = GRADIENT_START.blue  + (GRADIENT_END.blue  - GRADIENT_START.blue)  * ratio

            c.setFillColorRGB(r, g, b)
            c.rect(
                x + (width / steps) * i,
                y,
                width / steps + 1,
                height,
                stroke=0,
                fill=1
            )

    draw_gradient_header(
        c,
        x=0,
        y=height - header_height,
        width=width,
        height=header_height
    )
    # Logo — aligned with body text margin
    if logo_data.startswith("data:image"):
        try:
            _, encoded = logo_data.split(",", 1)
            image_bytes = base64.b64decode(encoded)
            image = ImageReader(io.BytesIO(image_bytes))

            logo_h = 50
            logo_w = 120

            c.drawImage(
                image,
                margin_x,
                height - header_height + (header_height - logo_h) / 2,
                width=logo_w,
                height=logo_h,
                preserveAspectRatio=True,
                mask="auto",
            )
        except Exception:
            pass

    # Business name — centered
    if business_name:
        c.setFillColor("white")
        c.setFont("Helvetica-Bold", 20)
        c.drawCentredString(
            width / 2,
            height - header_height / 2 + 10,
            business_name
        )

    # Proposal title — centered below business name
    c.setFont("Helvetica-Bold", 13)
    c.drawCentredString(
        width / 2,
        height - header_height / 2 - 10,
        "PROPOSAL"
    )

    # Reset cursor
    y = height - header_height - 30
    c.setFillColor(black)
    c.setFont("Helvetica", 11)

    biz_footer_parts = []

    if business_name:
        biz_footer_parts.append(business_name)

    if proposal_text:
        pass  # leave body alone

    # Pull business details from proposal_text OR better: pass separately
    # If you already have them separately, use those variables instead

    # Example using request.form (recommended)
    abn = request.form.get("abn", "").strip()
    phone = request.form.get("phone", "").strip()
    email = request.form.get("email", "").strip()

    if abn:
        biz_footer_parts.append(f"ABN: {abn}")
    if phone:
        biz_footer_parts.append(f"Phone: {phone}")
    if email:
        biz_footer_parts.append(f"Email: {email}")

    FOOTER_TEXT = request.form.get(
        "biz_footer",
        "Generated with Get The Job"
    ).strip() or "Generated with Get The Job"

    print("FOOTER:", request.form.get("biz_footer"))


    # --- BODY TEXT ---
    def draw_footer():
        c.setStrokeColorRGB(0.85, 0.85, 0.85)
        c.line(margin_x, footer_height + 10, width - margin_x, footer_height + 10)

        c.setFont("Helvetica", 9)
        c.setFillColorRGB(0.4, 0.4, 0.4)

        c.drawCentredString(
            width / 2,
            footer_height - 2,
            FOOTER_TEXT
        )

    from reportlab.lib.utils import simpleSplit

    max_text_width = width - (margin_x * 2)

    for paragraph in proposal_text.splitlines():
        wrapped_lines = simpleSplit(
            paragraph,
            "Helvetica",
            11,
            max_text_width
        ) or [""]

        for line in wrapped_lines:
            if y < footer_height + 30:
                draw_footer()
                c.showPage()

                c.setFont("Helvetica", 11)
                c.setFillColor(black)
                y = height - header_height - 30

            c.drawString(margin_x, y, line)
            y -= line_height

    draw_footer()

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
