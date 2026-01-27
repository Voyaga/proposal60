from flask import (
    Flask,
    render_template,
    request,
    make_response,
    send_file,
    redirect,
    abort
)
from werkzeug.exceptions import RequestEntityTooLarge
from proposal_builder import build_proposal_text
from monitoring import init_sentry
import os
import resend
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.colors import HexColor, black
from reportlab.lib.utils import ImageReader
import base64
import io
import time
import secrets
from datetime import timedelta, datetime
from collections import defaultdict, deque
from itsdangerous import URLSafeSerializer, BadSignature
import logging
import stripe
from db import init_db
from db import (
    get_db,
    new_proposal_id,
    compute_proposal_hash,
    utc_now_iso,
    get_free_usage,
    increment_free_usage
)

resend.api_key = os.environ.get("RESEND_API_KEY")
if not resend.api_key:
    raise RuntimeError("RESEND_API_KEY is required")


MAIL_FROM = os.environ.get(
    "MAIL_FROM",
    "Get The Job <onboarding@resend.dev>"
)


_magic_email_hits = defaultdict(deque)
MAGIC_EMAIL_WINDOW = 15 * 60  # 15 minutes
MAGIC_EMAIL_MAX = 3

init_sentry()

init_db()




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

# --------------------
# Request size limits (DoS protection)
# --------------------
# Total request cap (bytes). Default: 2 MiB. Override via env.
MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH", str(2 * 1024 * 1024)))
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

# Field-level caps (characters). Conservative defaults; override via env if needed.
MAX_PROPOSAL_TEXT_CHARS = int(os.environ.get("MAX_PROPOSAL_TEXT_CHARS", "30000"))
MAX_LOGO_B64_CHARS = int(os.environ.get("MAX_LOGO_B64_CHARS", "300000"))  # base64 chars, not bytes

def _reject_if_too_large(name: str, value: str, max_chars: int) -> None:
    if value and len(value) > max_chars:
        abort(413, description=f"{name} is too large")


secret = os.environ.get("SECRET_KEY")
if not secret:
    raise RuntimeError("SECRET_KEY is required")
app.secret_key = secret

_signer = URLSafeSerializer(app.secret_key, salt="gtj-free-limit")

# --------------------
# Magic link verification
# --------------------
MAGIC_LINK_TTL_SECONDS = 15 * 60  # 15 minutes
MAGIC_LINK_SALT = "magic-link"

_magic_signer = URLSafeSerializer(app.secret_key, salt=MAGIC_LINK_SALT)

VERIFIED_EMAIL_COOKIE = "gtj_verified_email"


BASE_URL = os.environ.get("BASE_URL")
if not BASE_URL:
    raise RuntimeError("BASE_URL is required")

BASE_URL = BASE_URL.rstrip("/")


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
TRACK_COOKIE = "gtj_track"


RATE_LIMIT_PER_MINUTE = 10
_ip_hits = defaultdict(deque)

# --------------------
# Helpers
# --------------------
def get_or_set_track_token(resp=None) -> str:
    raw = request.cookies.get(TRACK_COOKIE)
    if raw:
        try:
            return _signer.loads(raw)
        except BadSignature:
            pass

    token = secrets.token_urlsafe(16)

    if resp is not None:
        resp.set_cookie(
            TRACK_COOKIE,
            _signer.dumps(token),
            max_age=60 * 60 * 24 * 30,
            httponly=True,
            samesite="Lax",
            secure=bool(os.environ.get("RENDER")),
        )

    return token

def generate_magic_token(email: str, purpose: str) -> str:
    payload = {
        "email": email,
        "purpose": purpose,
        "exp": time.time() + MAGIC_LINK_TTL_SECONDS,
    }
    return _magic_signer.dumps(payload)

def send_magic_link(email: str, url: str) -> None:
    resend.Emails.send({  # type: ignore[arg-type]
        "from": MAIL_FROM,
        "to": email,
        "subject": "Your secure sign-in link",
        "html": f"""
            <p>Click the link below to continue:</p>
            <p><a href="{url}">Continue securely</a></p>
            <p>This link expires in 15 minutes.</p>
            <p>If you didn’t request this, you can safely ignore this email.</p>
        """,
    })




def is_email_rate_limited(email: str) -> bool:
    now = time.time()
    hits = _magic_email_hits[email]

    while hits and hits[0] < now - MAGIC_EMAIL_WINDOW:
        hits.popleft()

    if len(hits) >= MAGIC_EMAIL_MAX:
        return True

    hits.append(now)
    return False




def get_verified_email(purpose: str) -> str | None:
    raw = request.cookies.get(VERIFIED_EMAIL_COOKIE)
    if not raw:
        return None

    try:
        data = _magic_signer.loads(raw)
    except BadSignature:
        return None

    if data.get("purpose") != purpose:
        return None

    if time.time() > data.get("exp", 0):
        return None

    return data.get("email")


def get_client_ip() -> str:
    # Trust X-Forwarded-For ONLY in production behind proxy
    if os.environ.get("RENDER"):
        xff = request.headers.get("X-Forwarded-For")
        if xff:
            return xff.split(",")[0].strip()

    return request.remote_addr or "unknown"



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

@app.errorhandler(RequestEntityTooLarge)
def handle_request_too_large(_e):
    # Keep it simple and safe for production (no stack traces, no internal details)
    return "Request too large", 413


@app.post("/track")
def client_track():
    payload = request.get_json(silent=True) or {}
    token = payload.get("token")

    if not token:
        abort(403)

    try:
        token = _signer.loads(token)
    except BadSignature:
        abort(403)

    # Token must match cookie
    cookie_token = request.cookies.get(TRACK_COOKIE)
    if not cookie_token:
        abort(403)

    try:
        cookie_token = _signer.loads(cookie_token)
    except BadSignature:
        abort(403)

    if token != cookie_token:
        abort(403)

    event = payload.get("event")
    data = payload.get("data", {})

    if not event:
        abort(400)

    track(event, **data)
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
    if request.cookies.get(PRO_COOKIE) != "1":
        return False

    if not get_device_cookie():
        return False

    if not get_customer_cookie():
        return False

    return True




def set_pro_cookie(resp):
    resp.set_cookie(
        PRO_COOKIE,
        "1",
        max_age=60 * 60 * 24 * 365,
        httponly=True,
        samesite="Lax",
        secure=bool(os.environ.get("RENDER")),
    )

DEVICE_COOKIE = "gtj_device"

def set_device_cookie(resp, device_id: str):
    token = _signer.dumps(device_id)
    resp.set_cookie(
        DEVICE_COOKIE,
        token,
        max_age=60 * 60 * 24 * 365,
        httponly=True,
        samesite="Lax",
        secure=bool(os.environ.get("RENDER")),
    )


def get_device_cookie() -> str | None:
    raw = request.cookies.get(DEVICE_COOKIE)
    if not raw:
        return None
    try:
        return _signer.loads(raw)
    except BadSignature:
        return None

CUSTOMER_COOKIE = "gtj_customer"

def set_customer_cookie(resp, customer_id: str):
    token = _signer.dumps(customer_id)
    resp.set_cookie(
        CUSTOMER_COOKIE,
        token,
        max_age=60 * 60 * 24 * 365,
        httponly=True,
        samesite="Lax",
        secure=bool(os.environ.get("RENDER")),
    )


def get_customer_cookie() -> str | None:
    raw = request.cookies.get(CUSTOMER_COOKIE)
    if not raw:
        return None
    try:
        return _signer.loads(raw)
    except BadSignature:
        return None

def get_free_usage_key() -> str:
    # Prefer device cookie, fallback to IP
    device = get_device_cookie()
    if device:
        return f"device:{device}"

    ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "unknown"
    ip = ip.split(",")[0].strip()
    return f"ip:{ip}"



# --------------------
# Routes
# --------------------
@app.post("/request-magic-link")
def request_magic_link():
    ip = get_client_ip()
    if is_rate_limited(ip):
        abort(429)

    email = request.form.get("email", "").strip().lower()
    purpose = request.form.get("purpose", "").strip()

    if not email or len(email) > 254:
        abort(400)

    if purpose not in ("billing", "restore_pro"):
        abort(400)

    # 🔒 PER-EMAIL RATE LIMIT — THIS LINE GOES HERE
    if is_email_rate_limited(email):
        abort(429)

    token = generate_magic_token(email, purpose)
    magic_url = f"{BASE_URL}/verify?t={token}"

    send_magic_link(email, magic_url)

    track("magic_link_sent")

    return render_template(
        "upgrade.html",
        restore_error=None,
        restored=False,
        is_pro=False,
        message="Check your email for a secure link."
    )



@app.get("/verify")
def verify_magic_link():
    token = request.args.get("t")
    if not token:
        abort(400)

    try:
        data = _magic_signer.loads(token)
    except BadSignature:
        abort(403)

    if time.time() > data.get("exp", 0):
        abort(403)

    email = data.get("email")
    purpose = data.get("purpose")

    if not email or not purpose:
        abort(400)

    resp = redirect("/upgrade")

    resp.set_cookie(
        VERIFIED_EMAIL_COOKIE,
        _magic_signer.dumps(data),
        max_age=MAGIC_LINK_TTL_SECONDS,
        httponly=True,
        samesite="Lax",
        secure=bool(os.environ.get("RENDER")),
    )

    return resp


@app.get("/")
def landing():
    track("page_view", page="landing")

    resp = make_response(
        render_template(
            "landing.html",
            is_pro=is_pro_user()
        )
    )

    get_or_set_track_token(resp)
    return resp



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
    verified_email = get_verified_email("restore_pro") is not None

    return render_template(
        "upgrade.html",
        restore_error=None,
        restored=False,
        is_pro=is_pro_user(),
        verified_email=verified_email,
    )

@app.get("/privacy")
def privacy():
    return render_template("privacy.html")

@app.get("/terms")
def terms():
    return render_template("terms.html")


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
        success_url = f"{BASE_URL}/upgrade-success",
        cancel_url = f"{BASE_URL}/upgrade",
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

    # Device + customer will be attached on first restore
    return resp


# --------------------
# Restore Pro Access
# --------------------
@app.post("/restore-pro")
def restore_pro():
    # 🔐 Require magic-link verification
    email = get_verified_email("restore_pro")
    if not email:
        return render_template(
            "upgrade.html",
            restore_error="Verification expired. Please check your email again.",
            restored=False,
            is_pro=False
        )

    device_id = request.form.get("device_id", "").strip()
    if not device_id:
        return render_template(
            "upgrade.html",
            restore_error="Could not verify this device. Please refresh and try again.",
            restored=False,
            is_pro=False
        )

    # --------------------
    # Stripe lookup
    # --------------------
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

    # --------------------
    # DEVICE LIMIT (Stripe metadata)
    # --------------------
    MAX_DEVICES = 2

    cust = stripe.Customer.retrieve(customer_id)
    devices_raw = (cust.metadata or {}).get("pro_devices", "")
    devices = [d for d in devices_raw.split(",") if d]

    if device_id not in devices:
        if len(devices) >= MAX_DEVICES:
            return render_template(
                "upgrade.html",
                restore_error="Pro access is already active on two devices.",
                restored=False,
                is_pro=False
            )
        devices.append(device_id)

    stripe.Customer.modify(
        customer_id,
        metadata={"pro_devices": ",".join(devices)}
    )

    # --------------------
    # Success
    # --------------------
    resp = make_response(
        render_template(
            "upgrade.html",
            restore_error=None,
            restored=True,
            is_pro=True
        )
    )

    set_pro_cookie(resp)
    set_device_cookie(resp, device_id)
    set_customer_cookie(resp, customer_id)
    resp.delete_cookie(VERIFIED_EMAIL_COOKIE)

    return resp



# --------------------
# Admin analytics (PRIVATE)
# --------------------
@app.get("/admin/analytics")
def admin_analytics():
    key = request.args.get("key")
    if key != os.environ.get("ADMIN_KEY"):
        return "Forbidden", 403

    # ---- trade counts ----
    trade_counts = {}
    for e in ANALYTICS_EVENTS:
        if e["event"] == "trade_selected":
            trade = e["data"].get("trade")
            if trade:
                trade_counts[trade] = trade_counts.get(trade, 0) + 1

    # ---- base counts ----

    upgrade_checkout_clicks = sum(
        1 for e in ANALYTICS_EVENTS
        if e["event"] == "upgrade_checkout_click"
    )

    upgrade_page_views = sum(
        1 for e in ANALYTICS_EVENTS
        if e["event"] == "page_view"
        and e["data"].get("page") == "upgrade"
    )

    upgrade_cta_clicks = sum(
        1 for e in ANALYTICS_EVENTS
        if e["event"] == "upgrade_cta_click"
    )

    landing_views = sum(
        1 for e in ANALYTICS_EVENTS
        if e["event"] == "page_view" and e["data"].get("page") == "landing"
    )

    app_views = sum(
        1 for e in ANALYTICS_EVENTS
        if e["event"] == "page_view" and e["data"].get("page") == "app"
    )

    generates = sum(
        1 for e in ANALYTICS_EVENTS
        if e["event"] == "generate_attempt"
    )

    pdfs = sum(
        1 for e in ANALYTICS_EVENTS
        if e["event"] == "pdf_download"
    )

    def pct(a, b):
        return round((a / b) * 100) if b else 0

    # ---- stats payload ----
    stats = {
        "page_views": sum(1 for e in ANALYTICS_EVENTS if e["event"] == "page_view"),
        "generates": generates,
        "pdfs": pdfs,

        # generation mode
        "ai": sum(1 for e in ANALYTICS_EVENTS if e["event"] == "ai_used"),
        "fallback": sum(1 for e in ANALYTICS_EVENTS if e["event"] == "fallback_used"),

        # trades
        "trades": trade_counts,

        # funnel (REAL, calculated)
        "funnel": {
            "landing_to_app": pct(app_views, landing_views),
            "app_to_generate": pct(generates, app_views),
            "generate_to_pdf": pct(pdfs, generates),
        },

        "upgrade_page_views": upgrade_page_views,
        "upgrade_cta_clicks": upgrade_cta_clicks,

        "upgrade_checkout_clicks": upgrade_checkout_clicks,

        # friction signals
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

    # ---- recent activity ----
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
    # 🔐 Require magic-link verification
    email = get_verified_email("billing")
    if not email:
        abort(403)

    customers = stripe.Customer.search(
        query=f"email:'{email}'",
        limit=1
    )

    if not customers.data:
        return redirect("/upgrade")

    customer_id = customers.data[0].id

    session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=f"{BASE_URL}/app"
    )

    # 🔒 Single-use magic link: clear after use
    resp = redirect(session.url, code=303)
    resp.delete_cookie(VERIFIED_EMAIL_COOKIE)
    return resp



# --------------------
# Generate proposal
# --------------------
@app.post("/generate")
def generate():
    track("generate_attempt")

    # ---- rate limiting (unchanged) ----
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

    # ---- free-tier enforcement (SERVER-SIDE) ----
    if not is_pro_user():
        key = get_free_usage_key()
        conn = get_db()
        used = get_free_usage(conn, key)

        if used >= FREE_LIMIT:
            conn.close()
            track("free_limit_reached")
            return render_template(
                "preview.html",
                proposal_text="",
                blocked=True,
                remaining=0,
                block_reason="free",
                is_pro=is_pro_user()
            )

        conn.close()
    else:
        used = 0

    # ---- collect input ----
    data = {
        "client_name": request.form.get("client_name", "").strip(),
        "service_type": request.form.get("service_type", "").strip(),
        "scope": request.form.get("scope", "").strip(),
        "price": request.form.get("price", "").strip(),
        "timeframe": request.form.get("timeframe", "").strip(),
        "tone": request.form.get("tone", "Professional").strip(),
        "your_business": request.form.get("your_business", "").strip(),
        "trade": request.form.get("trade", "general").strip().lower(),
        "abn": request.form.get("abn", "").strip(),
        "phone": request.form.get("phone", "").strip(),
        "email": request.form.get("email", "").strip(),
    }

    # ---- input size validation ----
    _reject_if_too_large("client_name", data.get("client_name", ""), 200)
    _reject_if_too_large("service_type", data.get("service_type", ""), 200)
    _reject_if_too_large("scope", data.get("scope", ""), 8000)
    _reject_if_too_large("price", data.get("price", ""), 200)
    _reject_if_too_large("timeframe", data.get("timeframe", ""), 200)
    _reject_if_too_large("tone", data.get("tone", ""), 50)
    _reject_if_too_large("your_business", data.get("your_business", ""), 200)
    _reject_if_too_large("trade", data.get("trade", ""), 50)
    _reject_if_too_large("abn", data.get("abn", ""), 50)
    _reject_if_too_large("phone", data.get("phone", ""), 50)
    _reject_if_too_large("email", data.get("email", ""), 254)


    track("trade_selected", trade=data["trade"])

    # ---- generate proposal ----
    proposal_text = build_proposal_text(data)

    # ---- increment free usage AFTER successful generation ----
    if not is_pro_user():
        conn = get_db()
        increment_free_usage(conn, get_free_usage_key())
        conn.commit()
        conn.close()

    # ---- append business footer ----
    footer_lines = []
    if data["abn"]:
        footer_lines.append(f"ABN: {data['abn']}")
    if data["phone"]:
        footer_lines.append(f"Phone: {data['phone']}")
    if data["email"]:
        footer_lines.append(f"Email: {data['email']}")

    if footer_lines:
        proposal_text += "\n\n—\nBusiness Details\n" + "\n".join(footer_lines)

    # ---- remaining count (UX only) ----
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

    # ---- cookie now NON-AUTHORITATIVE (UX only) ----
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
    # Read + clean proposal body
    # --------------------
    proposal_text = request.form.get("proposal_text", "").strip()

    # Strip preview-injected business details from body
    clean_lines = []
    for line in proposal_text.splitlines():
        if line.strip() == "—":
            break
        clean_lines.append(line)
    proposal_text = "\n".join(clean_lines).rstrip()

    # --------------------
    # Read other inputs
    # --------------------
    logo_data = request.form.get("logo_data", "").strip()
    business_name = request.form.get("business_name", "").strip()
    footer_text = (
        request.form.get("biz_footer", "Generated with Get The Job").strip()
        or "Generated with Get The Job"
    )

    # --------------------
    # Payload caps (DoS protection)
    # --------------------
    _reject_if_too_large("proposal_text", proposal_text, MAX_PROPOSAL_TEXT_CHARS)
    _reject_if_too_large("logo_data", logo_data, MAX_LOGO_B64_CHARS)
    _reject_if_too_large("business_name", business_name, 200)
    _reject_if_too_large("biz_footer", footer_text, 200)


    # ==================================================
    # CREATE PROPOSAL RECORD (BEFORE PDF RENDER)
    # ==================================================

    proposal_id = new_proposal_id()

    # --- acceptance token ---
    accept_token = secrets.token_urlsafe(32)
    accept_expires_at = (
            datetime.utcnow() + timedelta(days=14)
    ).isoformat()

    proposal_hash = compute_proposal_hash(
        proposal_text,
        business_name,
    )

    accept_url = (
        f"{BASE_URL.rstrip('/')}"
        f"/accept/{proposal_id}?t={accept_token}"
    )

    conn = get_db()
    conn.execute("""
        INSERT INTO proposals (
            id,
            created_at,
            business_name,
            client_email,
            proposal_text,
            proposal_hash,
            status,
            accept_token,
            accept_expires_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
    """, (
        proposal_id,
        utc_now_iso(),
        business_name,
        request.form.get("client_email", "").strip(),
        proposal_text,
        proposal_hash,
        accept_token,
        accept_expires_at
    ))

    conn.commit()
    conn.close()

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

    FOOTER_TEXT = footer_text


    # print("FOOTER:", request.form.get("biz_footer"))


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

    # --------------------
    # ACCEPT PROPOSAL LINK
    # --------------------
    if y < footer_height + 60:
        draw_footer()
        c.showPage()
        c.setFont("Helvetica", 11)
        c.setFillColor(black)
        y = height - header_height - 30

    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(
        width / 2,
        y,
        "Accept this proposal"
    )
    y -= 18

    link_text = "Click here to accept this proposal"
    text_width = c.stringWidth(link_text, "Helvetica", 11)
    x = (width - text_width) / 2

    c.setFont("Helvetica", 11)
    c.drawString(x, y, link_text)

    c.linkURL(
        accept_url,
        (x, y - 2, x + text_width, y + 12),
        relative=0
    )

    y -= 30

    draw_footer()

    c.save()
    buffer.seek(0)

    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name="proposal.pdf",
    )

STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")

if not STRIPE_WEBHOOK_SECRET:
    raise RuntimeError("STRIPE_WEBHOOK_SECRET is not set")

@app.route("/stripe/webhook", methods=["POST"])
@app.route("/stripe/webhook/", methods=["POST"])
def stripe_webhook():
    payload = request.data
    sig = request.headers.get("Stripe-Signature")

    if not sig:
        logging.warning("Stripe webhook missing signature")
        return "", 400

    try:
        event = stripe.Webhook.construct_event(
            payload,
            sig,
            STRIPE_WEBHOOK_SECRET
        )
    except stripe.error.SignatureVerificationError:
        logging.warning("Stripe webhook signature verification failed")
        return "", 400
    except Exception:
        logging.exception("Stripe webhook error")
        return "", 400

    # --------------------
    # EVENT HANDLING LOGIC
    # --------------------
    event_type = event.get("type")
    data = event.get("data", {}).get("object", {})

    if not event_type:
        logging.warning("Stripe webhook missing event type")
        return "", 400

    if event_type in (
        "customer.subscription.deleted",
        "customer.subscription.updated",
    ):
        status = data.get("status")
        customer_id = data.get("customer")

        if not customer_id:
            logging.warning("Stripe webhook missing customer ID")
            return "", 200  # acknowledge but do nothing

        if status in ("canceled", "unpaid", "incomplete_expired"):
            try:
                stripe.Customer.modify(
                    customer_id,
                    metadata={"pro_devices": ""}
                )
                logging.info(
                    f"Cleared pro_devices for customer {customer_id}"
                )
            except Exception:
                logging.exception(
                    f"Failed to clear pro_devices for {customer_id}"
                )

    # --------------------
    # ALWAYS ACK STRIPE
    # --------------------
    return "", 200


@app.get("/accept/<proposal_id>")
def accept_proposal(proposal_id):
    conn = get_db()
    proposal = conn.execute(
        "SELECT * FROM proposals WHERE id = ?",
        (proposal_id,)
    ).fetchone()
    conn.close()

    if not proposal:
        abort(404)

    # If already responded, allow status view WITHOUT token
    if proposal["status"] != "pending":
        return render_template(
            "proposal_status.html",
            proposal=proposal
        )

    # Pending → token required
    token = request.args.get("t")
    if not token:
        abort(403)

    if (
        token != proposal["accept_token"] or
        not proposal["accept_expires_at"] or
        datetime.utcnow() > datetime.fromisoformat(proposal["accept_expires_at"])
    ):
        abort(403)

    return render_template(
        "proposal_accept.html",
        proposal=proposal
    )



@app.post("/accept/<proposal_id>")
def accept_proposal_post(proposal_id):
    decision = request.form.get("decision")
    name = request.form.get("name", "").strip()
    decline_reason = request.form.get("decline_reason", "").strip()
    token = request.form.get("t")

    if decision == "accept" and not name:
        abort(400)

    if decision == "decline" and len(decline_reason) > 1000:
        abort(400)

    if not token:
        abort(403)

    conn = get_db()
    proposal = conn.execute(
        "SELECT * FROM proposals WHERE id = ?",
        (proposal_id,)
    ).fetchone()

    if not proposal:
        conn.close()
        abort(404)

    # Validate token + expiry + pending state
    if (
        proposal["status"] != "pending" or
        token != proposal["accept_token"] or
        not proposal["accept_expires_at"] or
        datetime.utcnow() > datetime.fromisoformat(proposal["accept_expires_at"])
    ):
        conn.close()
        abort(403)

    new_status = "accepted" if decision == "accept" else "declined"

    conn.execute("""
        UPDATE proposals
        SET
            status = ?,
            responded_at = ?,
            responded_name = ?,
            responded_ip = ?,
            decline_reason = ?,
            accept_token = NULL,
            accept_expires_at = NULL
        WHERE id = ?
    """, (
        new_status,
        utc_now_iso(),
        name if decision == "accept" else None,
        request.remote_addr,
        decline_reason if decision == "decline" else None,
        proposal_id
    ))

    conn.commit()
    conn.close()

    return redirect(f"/accept/{proposal_id}")

@app.get("/dashboard/proposals/<proposal_id>")
def dashboard_proposal_view(proposal_id):
    if not is_pro_user():
        return redirect("/upgrade")

    conn = get_db()
    proposal = conn.execute(
        "SELECT * FROM proposals WHERE id = ?",
        (proposal_id,)
    ).fetchone()
    conn.close()

    if not proposal:
        abort(404)

    return render_template(
        "proposal_readonly.html",
        proposal=proposal
    )


@app.get("/dashboard/proposals")
def proposals_dashboard():
    if not is_pro_user():
        return redirect("/upgrade")

    conn = get_db()
    proposals = conn.execute("""
        SELECT
            id,
            business_name,
            status,
            responded_name,
            responded_at,
            created_at
        FROM proposals
        ORDER BY created_at DESC
    """).fetchall()
    conn.close()

    return render_template(
        "proposals_dashboard.html",
        proposals=proposals
    )



# --------------------
# Local dev entrypoint
# --------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
