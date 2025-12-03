# app.py — IndCad backend (cleaned)
import os
import json
import uuid
from io import BytesIO
from flask import Flask, request, jsonify, send_file, abort
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
import stripe
from dotenv import load_dotenv
from flask_cors import CORS

# ---- config ----
load_dotenv()  # optional .env support

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
BASE_URL = os.getenv("BASE_URL", "http://localhost:5001")  # matches your dev port by default

if not STRIPE_SECRET_KEY:
    # Development convenience: fail loudly so you set it. If you want to bypass, set to 'test' explicitly.
    raise RuntimeError("Set STRIPE_SECRET_KEY environment variable (from Stripe dashboard).")

stripe.api_key = STRIPE_SECRET_KEY

# ---- app ----
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})  # permissive for local testing

# In-memory orders store: order_id -> payload dict
ORDERS = {}

# ---- helpers ----
def summarize_payload(payload):
    parts = []
    parts.append(f"Age: {payload.get('age', 'N/A')}")
    parts.append(f"Education: {payload.get('education_level', payload.get('education','N/A'))}")
    fl = payload.get('first_language_clb') or {}
    parts.append("First language CLB: " + ", ".join(f"{k}:{fl.get(k,'-')}" for k in ['listening','reading','writing','speaking']))
    sl = payload.get('second_language_nclc') or {}
    parts.append("Second language NCLC: " + ", ".join(f"{k}:{sl.get(k,'-')}" for k in ['listening','reading','writing','speaking']))
    parts.append(f"Canadian experience (years): {payload.get('canadian_work_years',0)}")
    parts.append(f"Foreign experience (years): {payload.get('foreign_work_years',0)}")
    if payload.get('provincial_nomination'):
        parts.append("Provincial nomination: YES")
    return "\n".join(parts)

def generate_pdf_bytes(order_id, payload, crs_result=None):
    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    margin = 40
    y = height - margin

    # Header
    p.setFont("Helvetica-Bold", 16)
    p.drawString(margin, y, "IndCad — Verified CRS Strategy Report")
    y -= 26
    p.setFont("Helvetica", 10)
    p.drawString(margin, y, f"Order ID: {order_id}")
    y -= 18
    p.drawString(margin, y, f"Generated for: {payload.get('name','(not provided)')} — {payload.get('email','(not provided)')}")
    y -= 22

    # CRS result summary if provided
    if crs_result:
        p.setFont("Helvetica-Bold", 12)
        p.drawString(margin, y, f"Estimated CRS Score: {crs_result.get('total')}")
        y -= 18
        p.setFont("Helvetica", 10)
        p.drawString(margin, y, f"Breakdown — Core: {crs_result['totals']['core']}, Spouse: {crs_result['totals']['spouse']}, Skill: {crs_result['totals']['skill']}, Additional: {crs_result['totals']['additional']}")
        y -= 22

    # Add payload summary
    p.setFont("Helvetica-Bold", 12)
    p.drawString(margin, y, "Profile Summary")
    y -= 16
    p.setFont("Helvetica", 10)
    summary = summarize_payload(payload)
    for line in summary.splitlines():
        if y < margin + 50:
            p.showPage()
            y = height - margin
            p.setFont("Helvetica", 10)
        p.drawString(margin, y, line)
        y -= 14

    y -= 8
    p.setFont("Helvetica-Bold", 12)
    p.drawString(margin, y, "Top recommended actions (from IndCad)")
    y -= 16
    p.setFont("Helvetica", 10)

    # Use suggestions from payload if present
    suggestions = payload.get('_suggestions') or payload.get('suggestions') or []
    if not suggestions and crs_result:
        suggestions = [
            {"title":"Take PNP route","desc":"Check PNP streams you qualify for."},
            {"title":"Improve primary language","desc":"Retake and target higher CLB."},
            {"title":"Consider ECA or Canadian credential","desc":"Validate education."}
        ]

    for s in suggestions:
        title = s.get('title') if isinstance(s, dict) else str(s)
        desc = s.get('desc') if isinstance(s, dict) else ''
        if y < margin + 60:
            p.showPage()
            y = height - margin
            p.setFont("Helvetica", 10)
        p.setFont("Helvetica-Bold", 11)
        p.drawString(margin, y, f"- {title}")
        y -= 14
        p.setFont("Helvetica", 9)
        for line in desc.split('\n'):
            p.drawString(margin + 10, y, line)
            y -= 12
        y -= 6

    # Footer (finalize)
    p.showPage()
    p.save()
    buffer.seek(0)
    return buffer

# ---- routes ----
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200

@app.route("/create_order", methods=["POST"])
def create_order():
    data = request.get_json(force=True)
    if not data:
        return jsonify({"error":"missing payload"}), 400
    order_id = str(uuid.uuid4())
    ORDERS[order_id] = {
        "payload": data,
        "created_at": None
    }
    # return order id so frontend can call create_checkout
    return jsonify({"order_id": order_id}), 200

@app.route("/create_checkout", methods=["POST"])
def create_checkout():
    data = request.get_json(force=True)
    if not data:
        return jsonify({"error": "missing payload"}), 400

    order_id = data.get("order_id")
    if not order_id or order_id not in ORDERS:
        return jsonify({"error":"invalid order id"}), 400

    # amount in paise (₹499 => 49900 paise)
    amount = 49900
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "inr",
                    "product_data": {"name":"IndCad — Verified CRS Strategy (PDF)"},
                    "unit_amount": amount
                },
                "quantity": 1
            }],
            mode="payment",
            success_url=f"{BASE_URL}/download-success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{BASE_URL}/payment-cancelled",
            metadata={"order_id": order_id}
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"checkout_url": session.url, "session_id": session.id}), 200

@app.route("/download", methods=["GET"])
def download():
    session_id = request.args.get("session_id") or request.args.get("order_id")
    if not session_id:
        return jsonify({"error":"missing session_id"}), 400

    # retrieve session from Stripe
    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except Exception as e:
        return jsonify({"error":"invalid session id", "details": str(e)}), 400

    if session.payment_status != "paid":
        return jsonify({"error":"payment not completed"}), 402

    order_id = (session.metadata or {}).get("order_id")
    if not order_id or order_id not in ORDERS:
        return jsonify({"error":"order not found for this session"}), 404

    order = ORDERS[order_id]
    payload = order["payload"]
    crs_result = payload.get('crs_result')

    pdf_io = generate_pdf_bytes(order_id, payload, crs_result=crs_result)
    filename = f"IndCad_CRS_Report_{order_id}.pdf"
    # download_name works on modern Flask/Werkzeug; fallback to attachment_filename for older versions if needed
    return send_file(pdf_io, as_attachment=True, download_name=filename, mimetype="application/pdf")

@app.route("/download-success", methods=["GET"])
def download_success_page():
    # Redirect target from Stripe — frontend can read session_id and call /download
    return """
    <html><body>
    <h3>Payment successful</h3>
    <p>You will be redirected to download your report. If not, click below.</p>
    <script>
      const params = new URLSearchParams(window.location.search);
      const sid = params.get('session_id');
      if (sid) {
        window.location.href = `/download?session_id=${sid}`;
      }
    </script>
    <a href="/">Return</a>
    </body></html>
    """

# ---- main ----
if __name__ == "__main__":
    # use port 5001 (your dev choice). Use debug=True for development only.
    app.run(host="0.0.0.0", port=5001, debug=True)
