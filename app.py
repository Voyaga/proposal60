# app.py

from flask import Flask, render_template, request
from proposal_builder import build_proposal_text

app = Flask(__name__)

@app.get("/")
def home():
    return render_template("home.html")

@app.post("/generate")
def generate():
    data = {
        "client_name": request.form.get("client_name", "").strip(),
        "service_type": request.form.get("service_type", "").strip(),
        "scope": request.form.get("scope", "").strip(),
        "price": request.form.get("price", "").strip(),
        "tone": request.form.get("tone", "Professional").strip(),
        "your_business": request.form.get("your_business", "").strip(),
    }

    proposal_text = build_proposal_text(data)
    return render_template("preview.html", proposal_text=proposal_text, data=data)

if __name__ == "__main__":
    app.run(debug=True)
