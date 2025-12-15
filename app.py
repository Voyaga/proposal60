from flask import Flask, render_template, request, make_response, send_file
from proposal_builder import build_proposal_text
import os
import io

app = Flask(__name__)

FREE_LIMIT = 999
COOKIE_NAME = "proposal60_free_used"


@app.get("/")
def home():
    return render_template("home.html")


@app.get("/upgrade")
def upgrade():
    return render_template("upgrade.html")


@app.post("/generate")
def generate():
    used_raw = request.cookies.get(COOKIE_NAME, "0")
    try:
        used = int(used_raw)
    except ValueError:
        used = 0

    if used >= FREE_LIMIT:
        return render_template(
            "preview.html",
            proposal_text="",
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

    used += 1
    remaining = max(FREE_LIMIT - used, 0)

    resp = make_response(render_template(
        "preview.html",
        proposal_text=proposal_text,
        blocked=False,
        remaining=remaining
    ))
    resp.set_cookie(COOKIE_NAME, str(used), max_age=60 * 60 * 24 * 365)
    return resp


@app.post("/pdf")
def pdf():
    # Generates a PDF from the proposal text sent from preview.html
    proposal_text = request.form.get("proposal_text", "").strip()
    if not proposal_text:
        # fallback
        proposal_text = "No proposal text provided."

    # Lazy import so local dev doesn't break if reportlab missing
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

    # simple line wrap
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
        lines = wrap_line(raw)
        for ln in lines:
            if y < 60:
                c.showPage()
                c.setFont("Courier", 11)
                y = height - 60
            c.drawString(x, y, ln)
            y -= line_height

    c.showPage()
    c.save()

    buffer.seek(0)
    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name="proposal.pdf"
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
