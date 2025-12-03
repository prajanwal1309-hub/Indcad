# app.py — IndCad backend (SQLite-backed, production-ready)
import os
import json
import uuid
import sqlite3
import io
from datetime import datetime
from flask import Flask, request, jsonify, send_file, redirect, g
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
import stripe
from dotenv import load_dotenv
from flask_cors import CORS

# ---- config ----
load_dotenv()

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")  # set on Render
BASE_URL = os.getenv("BASE_URL", "http://localhost:5001")
DB_PATH = os.getenv("SQLITE_PATH", "indcad_orders.db")
DEFAULT_PRICE_CENTS = int(os.getenv("DEFAULT_PRICE_CENTS", "49900"))
DEFAULT_CURRENCY = os.getenv("DEFAULT_CURRENCY", "inr")
DEBUG_SHOW_ORDERS = os.getenv("DEBUG_SHOW_ORDERS", "0") == "1"

if not STRIPE_SECRET_KEY:
    raise RuntimeError("Set STRIPE_SECRET_KEY environment variable.")

stripe.api_key = STRIPE_SECRET_KEY

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# ---- DB helpers ----
def get_db_conn():
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id TEXT PRIMARY KEY,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL,
        amount_cents INTEGER,
        currency TEXT,
        stripe_session_id TEXT,
        payment_intent TEXT,
        paid INTEGER DEFAULT 0,
        paid_at TEXT
    );
    """)
    conn.commit()
    conn.close()

def insert_order(order_id, payload, amount_cents=None, currency=None):
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO orders (id, payload, created_at, amount_cents, currency, paid)
        VALUES (?, ?, ?, ?, ?, 0)
    """, (order_id, json.dumps(payload), datetime.utcnow().isoformat(), amount_cents, currency))
    conn.commit()
    conn.close()

def get_order(order_id):
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    record = dict(row)
    record["payload"] = json.loads(record["payload"])
    record["paid"] = bool(record["paid"])
    return record

def update_order_stripe_session(order_id, stripe_session_id=None, payment_intent=None, paid=False):
    """
    Safely update order row. Coerce Stripe objects to primitive types before DB binding.
    """
    # Normalize values that could be Stripe objects/dicts into primitives
    if isinstance(payment_intent, dict):
        payment_intent = payment_intent.get("id")
    # sometimes stripe returns a StripeObject; coerce to str if needed
    if hasattr(payment_intent, "id"):
        payment_intent = getattr(payment_intent, "id")

    if isinstance(stripe_session_id, dict):
        stripe_session_id = stripe_session_id.get("id")
    if hasattr(stripe_session_id, "id"):
        stripe_session_id = getattr(stripe_session_id, "id")

    # Ensure we pass only supported types to sqlite3 (None or str/int)
    if payment_intent is not None:
        payment_intent = str(payment_intent)
    if stripe_session_id is not None:
        stripe_session_id = str(stripe_session_id)

    conn = get_db_conn()
    c = conn.cursor()
    try:
        if stripe_session_id:
            c.execute("UPDATE orders SET stripe_session_id = ? WHERE id = ?", (stripe_session_id, order_id))
        if payment_intent:
            c.execute("UPDATE orders SET payment_intent = ? WHERE id = ?", (payment_intent, order_id))
        if paid:
            c.execute("UPDATE orders SET paid = 1, paid_at = ? WHERE id = ?", (datetime.utcnow().isoformat(), order_id))
        conn.commit()
    finally:
        conn.close()


def find_order_by_session(session_id):
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM orders WHERE stripe_session_id = ?", (session_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    rec = dict(row)
    rec["payload"] = json.loads(rec["payload"])
    rec["paid"] = bool(rec["paid"])
    return rec

# ---- PDF helpers (kept from your version) ----
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
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    margin = 40
    y = height - margin

    p.setFont("Helvetica-Bold", 16)
    p.drawString(margin, y, "IndCad — Verified CRS Strategy Report")
    y -= 26
    p.setFont("Helvetica", 10)
    p.drawString(margin, y, f"Order ID: {order_id}")
    y -= 18
    p.drawString(margin, y, f"Generated for: {payload.get('name','(not provided)')} — {payload.get('email','(not provided)')}")
    y -= 22

    if crs_result:
        p.setFont("Helvetica-Bold", 12)
        p.drawString(margin, y, f"Estimated CRS Score: {crs_result.get('total')}")
        y -= 18
        p.setFont("Helvetica", 10)
        p.drawString(margin, y, f"Breakdown — Core: {crs_result['totals']['core']}, Spouse: {crs_result['totals']['spouse']}, Skill: {crs_result['totals']['skill']}, Additional: {crs_result['totals']['additional']}")
        y -= 22

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

    p.showPage()
    p.save()
    buffer.seek(0)
    return buffer

# ---- routes ----
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status":"ok"}), 200

@app.route("/create_order", methods=["POST"])
def create_order():
    data = request.get_json(force=True)
    if not data:
        return jsonify({"error":"missing payload"}), 400
    order_id = str(uuid.uuid4())
    insert_order(order_id, data, amount_cents=None, currency=None)
    return jsonify({"order_id": order_id}), 200

@app.route("/create_checkout", methods=["POST"])
def create_checkout():
    data = request.get_json(force=True)
    if not data:
        return jsonify({"error":"missing payload"}), 400
    order_id = data.get("order_id")
    if not order_id:
        return jsonify({"error":"order_id required"}), 400
    order = get_order(order_id)
    if not order:
        return jsonify({"error":"order not found"}), 404

    # use default amount unless order specifies one
    amount = order.get("amount_cents") or DEFAULT_PRICE_CENTS
    currency = order.get("currency") or DEFAULT_CURRENCY

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": currency,
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

    # persist session and payment_intent
    update_order_stripe_session(order_id, stripe_session_id=session.id, payment_intent=session.get("payment_intent"))
    return jsonify({"checkout_url": session.url, "session_id": session.id}), 200

@app.route("/download-success", methods=["GET"])
def download_success_page():
    return """
    <html><body>
    <h3>Payment successful</h3>
    <p>You will be redirected to download your report. If not, click below.</p>
    <script>
      const params = new URLSearchParams(window.location.search);
      const sid = params.get('session_id');
      if (sid) {
        window.location.href = window.location.origin + '/download?session_id=' + sid;
      }
    </script>
    <a href="/">Return</a>
    </body></html>
    """

@app.route("/download", methods=["GET"])
def download():
    session_id = request.args.get("session_id")
    if not session_id:
        return jsonify({"error":"missing session_id"}), 400

    # retrieve session from Stripe
    try:
        session = stripe.checkout.Session.retrieve(session_id, expand=["payment_intent"])
    except stripe.error.InvalidRequestError as e:
        return jsonify({"error":"invalid session id", "details": str(e)}), 400
    except Exception as e:
        return jsonify({"error":"stripe error", "details": str(e)}), 500

    if session.payment_status != "paid":
        return jsonify({"error":"payment not completed", "payment_status": session.payment_status}), 402

    order_id = (session.metadata or {}).get("order_id")
    if not order_id:
        # fallback: find by stripe_session_id in DB
        rec = find_order_by_session(session.id)
        if rec:
            order_id = rec["id"]

    if not order_id:
        return jsonify({"error":"order not found for this session", "session_id": session.id}), 404

    order = get_order(order_id)
    if not order:
        return jsonify({"error":"order not found by id", "order_id": order_id}), 404

    # mark paid (idempotent)
    update_order_stripe_session(order_id, stripe_session_id=session.id, payment_intent=session.get("payment_intent"), paid=True)

    payload = order["payload"]
    crs_result = payload.get("crs_result")
    pdf_io = generate_pdf_bytes(order_id, payload, crs_result=crs_result)
    pdf_io.seek(0)
    filename = f"IndCad_CRS_Report_{order_id}.pdf"
    return send_file(pdf_io, as_attachment=True, download_name=filename, mimetype="application/pdf")

# ---- webhook ----
@app.route("/webhook", methods=["POST"])
def stripe_webhook():
    payload = request.data
    sig_header = request.headers.get('Stripe-Signature', None)
    if STRIPE_WEBHOOK_SECRET and sig_header:
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        except Exception as e:
            return jsonify({"error":"signature verification failed", "details": str(e)}), 400
    else:
        try:
            event = json.loads(payload)
        except Exception as e:
            return jsonify({"error":"invalid payload", "details": str(e)}), 400

    typ = event.get("type")
    obj = event.get("data", {}).get("object", {})

    if typ == "checkout.session.completed":
        session = obj
        sess_id = session.get("id")
        order_id = (session.get("metadata") or {}).get("order_id")
        payment_intent = session.get("payment_intent")
        if order_id:
            # ensure order exists; if not, create best-effort (not ideal)
            if not get_order(order_id):
                # create a stub order with the metadata we have
                insert_order(order_id, {"note":"created-from-webhook"}, amount_cents=session.get("amount_total"), currency=session.get("currency"))
            update_order_stripe_session(order_id, stripe_session_id=sess_id, payment_intent=payment_intent, paid=True)
        else:
            # fallback: try to find by amount and attach if possible
            try:
                amount = session.get("amount_total")
                if amount:
                    conn = get_db_conn()
                    c = conn.cursor()
                    c.execute("SELECT id FROM orders WHERE amount_cents = ? AND stripe_session_id IS NULL LIMIT 1", (amount,))
                    row = c.fetchone()
                    if row:
                        oid = row[0]
                        update_order_stripe_session(oid, stripe_session_id=sess_id, payment_intent=payment_intent, paid=True)
            except Exception:
                pass

    if typ == "payment_intent.succeeded":
        pi = obj
        pi_id = pi.get("id")
        # find order by payment_intent or stripe_session
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("SELECT id FROM orders WHERE payment_intent = ? OR stripe_session_id = ?", (pi_id, pi_id))
        row = c.fetchone()
        if row:
            update_order_stripe_session(row[0], paid=True)
        conn.close()

    return jsonify({"received": True}), 200

# debug endpoint (opt-in by env variable)
if DEBUG_SHOW_ORDERS:
    @app.route("/_debug_orders", methods=["GET"])
    def debug_orders():
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("SELECT id, stripe_session_id, payment_intent, paid, created_at, amount_cents FROM orders ORDER BY created_at DESC LIMIT 200")
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
        return jsonify(rows), 200

# ---- startup ----
if __name__ == "__main__":
    init_db()
    port = int(os.getenv("PORT", "5001"))
    app.run(host="0.0.0.0", port=port, debug=True)
