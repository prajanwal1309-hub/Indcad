# app.py — IndCad backend (Postgres-ready, SQLAlchemy Core)
import os
import json
import uuid
import io
import logging
from datetime import datetime
from flask import Flask, request, jsonify, send_file
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
import stripe
from dotenv import load_dotenv
from flask_cors import CORS
from sqlalchemy import create_engine, MetaData, Table, Column, String, Integer, Boolean, Text, select, insert, update
from sqlalchemy.exc import SQLAlchemyError

# ---- config ----
load_dotenv()

# Logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("indcad")

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
BASE_URL = os.getenv("BASE_URL", "http://localhost:5001")
DATABASE_URL = os.getenv("DATABASE_URL")  # required for Postgres
DEFAULT_PRICE_CENTS = int(os.getenv("DEFAULT_PRICE_CENTS", "49900"))
DEFAULT_CURRENCY = os.getenv("DEFAULT_CURRENCY", "inr")
DEBUG_SHOW_ORDERS = os.getenv("DEBUG_SHOW_ORDERS", "0") == "1"
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "local-admin-token")

if not STRIPE_SECRET_KEY:
    raise RuntimeError("Set STRIPE_SECRET_KEY environment variable.")
if not DATABASE_URL:
    raise RuntimeError("Set DATABASE_URL environment variable (Postgres connection string).")

stripe.api_key = STRIPE_SECRET_KEY

# ---- app ----
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# ---- DB setup (SQLAlchemy Core) ----
# Use pool_pre_ping to reduce "stale connection" errors on platforms like Render
engine = create_engine(DATABASE_URL, future=True, pool_pre_ping=True)
metadata = MetaData()

orders = Table(
    "orders",
    metadata,
    Column("id", String, primary_key=True),
    Column("payload", Text, nullable=False),
    Column("created_at", String, nullable=False),
    Column("amount_cents", Integer),
    Column("currency", String(10)),
    Column("stripe_session_id", String, index=True),
    Column("payment_intent", String, index=True),
    Column("paid", Boolean, default=False),
    Column("paid_at", String),
)

def init_db():
    try:
        metadata.create_all(engine)
        log.info("DB initialized / tables ensured")
    except Exception as e:
        log.exception("Failed to initialize DB: %s", e)
        raise

# ---- DB helpers ----
def insert_order_db(order_id, payload, amount_cents=None, currency=None):
    stmt = insert(orders).values(
        id=order_id,
        payload=json.dumps(payload),
        created_at=datetime.utcnow().isoformat(),
        amount_cents=amount_cents,
        currency=currency
    )
    with engine.begin() as conn:
        conn.execute(stmt)

def get_order_db(order_id):
    stmt = select(orders).where(orders.c.id == order_id)
    with engine.connect() as conn:
        row = conn.execute(stmt).fetchone()
    if not row:
        return None
    rec = dict(row)
    try:
        rec['payload'] = json.loads(rec['payload'])
    except Exception:
        rec['payload'] = {"_raw": rec['payload']}
    rec['paid'] = bool(rec.get('paid'))
    return rec

def find_order_by_session_db(session_id):
    stmt = select(orders).where(orders.c.stripe_session_id == session_id)
    with engine.connect() as conn:
        row = conn.execute(stmt).fetchone()
    if not row:
        return None
    rec = dict(row)
    try:
        rec['payload'] = json.loads(rec['payload'])
    except Exception:
        rec['payload'] = {"_raw": rec['payload']}
    rec['paid'] = bool(rec.get('paid'))
    return rec

def update_order_stripe_session_db(order_id, stripe_session_id=None, payment_intent=None, paid=False):
    updates = {}
    if stripe_session_id is not None:
        # coerce to primitive
        if isinstance(stripe_session_id, dict):
            stripe_session_id = stripe_session_id.get("id")
        if hasattr(stripe_session_id, "id"):
            stripe_session_id = getattr(stripe_session_id, "id")
        updates['stripe_session_id'] = str(stripe_session_id) if stripe_session_id is not None else None
    if payment_intent is not None:
        if isinstance(payment_intent, dict):
            payment_intent = payment_intent.get("id")
        if hasattr(payment_intent, "id"):
            payment_intent = getattr(payment_intent, "id")
        updates['payment_intent'] = str(payment_intent) if payment_intent is not None else None
    if paid:
        updates['paid'] = True
        updates['paid_at'] = datetime.utcnow().isoformat()
    if not updates:
        return
    stmt = update(orders).where(orders.c.id == order_id).values(**updates)
    with engine.begin() as conn:
        conn.execute(stmt)

# ---- PDF helpers ----
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
        try:
            totals = crs_result.get('totals', {})
            p.setFont("Helvetica-Bold", 12)
            p.drawString(margin, y, f"Estimated CRS Score: {crs_result.get('total')}")
            y -= 18
            p.setFont("Helvetica", 10)
            p.drawString(margin, y, f"Breakdown — Core: {totals.get('core')}, Spouse: {totals.get('spouse')}, Skill: {totals.get('skill')}, Additional: {totals.get('additional')}")
            y -= 22
        except Exception:
            y -= 0

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
    try:
        insert_order_db(order_id, data, amount_cents=None, currency=None)
        log.info("Created order %s", order_id)
    except SQLAlchemyError as e:
        log.exception("Failed to insert order")
        return jsonify({"error":"db_error","details": str(e)}), 500
    return jsonify({"order_id": order_id}), 200

@app.route("/create_checkout", methods=["POST"])
def create_checkout():
    data = request.get_json(force=True)
    if not data:
        return jsonify({"error": "missing payload"}), 400
    order_id = data.get("order_id")
    if not order_id:
        return jsonify({"error":"order_id required"}), 400
    order = get_order_db(order_id)
    if not order:
        return jsonify({"error":"order not found"}), 404

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
        log.exception("Stripe session creation failed")
        return jsonify({"error": str(e)}), 500

    try:
        update_order_stripe_session_db(order_id, stripe_session_id=session.id, payment_intent=session.get("payment_intent"))
        log.info("Checkout created for order %s -> %s", order_id, session.id)
    except SQLAlchemyError:
        log.exception("Failed to update order with stripe session")
        # continue returning session so user can still complete payment
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
        // ensure redirect uses same origin
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

    try:
        session = stripe.checkout.Session.retrieve(session_id, expand=["payment_intent"])
    except stripe.error.InvalidRequestError as e:
        log.exception("Invalid session id on download")
        return jsonify({"error":"invalid session id", "details": str(e)}), 400
    except Exception as e:
        log.exception("Stripe error on retrieve")
        return jsonify({"error":"stripe error", "details": str(e)}), 500

    if session.payment_status != "paid":
        return jsonify({"error":"payment not completed", "payment_status": session.payment_status}), 402

    order_id = (session.metadata or {}).get("order_id")
    if not order_id:
        rec = find_order_by_session_db(session.id)
        if rec:
            order_id = rec["id"]

    if not order_id:
        log.error("Order id not found for session %s", session.id)
        return jsonify({"error":"order not found for this session", "session_id": session.id}), 404

    order = get_order_db(order_id)
    if not order:
        log.error("Order lookup failed for id %s", order_id)
        return jsonify({"error":"order not found by id", "order_id": order_id}), 404

    try:
        update_order_stripe_session_db(order_id, stripe_session_id=session.id, payment_intent=session.get("payment_intent"), paid=True)
    except SQLAlchemyError:
        log.exception("Failed to mark order paid")

    payload = order["payload"]
    crs_result = payload.get("crs_result")
    pdf_io = generate_pdf_bytes(order_id, payload, crs_result=crs_result)
    pdf_io.seek(0)
    filename = f"IndCad_CRS_Report_{order_id}.pdf"
    return send_file(pdf_io, as_attachment=True, download_name=filename, mimetype="application/pdf")

@app.route("/webhook", methods=["POST"])
def stripe_webhook():
    payload = request.data
    sig_header = request.headers.get('Stripe-Signature', None)
    if STRIPE_WEBHOOK_SECRET and sig_header:
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        except Exception as e:
            log.exception("Webhook signature verification failed")
            return jsonify({"error":"signature verification failed", "details": str(e)}), 400
    else:
        try:
            event = json.loads(payload)
        except Exception as e:
            log.exception("Invalid webhook payload")
            return jsonify({"error":"invalid payload", "details": str(e)}), 400

    typ = event.get("type")
    obj = event.get("data", {}).get("object", {})

    # checkout.session.completed
    if typ == "checkout.session.completed":
        session = obj
        sess_id = session.get("id")
        order_id = (session.get("metadata") or {}).get("order_id")
        payment_intent = session.get("payment_intent")
        if order_id:
            if not get_order_db(order_id):
                # best-effort create a stub if missing (rare)
                try:
                    insert_order_db(order_id, {"note":"created-from-webhook"}, amount_cents=session.get("amount_total"), currency=session.get("currency"))
                except SQLAlchemyError:
                    log.exception("Failed to create stub order from webhook")
            try:
                update_order_stripe_session_db(order_id, stripe_session_id=sess_id, payment_intent=payment_intent, paid=True)
            except SQLAlchemyError:
                log.exception("Failed to update order from webhook for order %s", order_id)
        else:
            # fallback: find a non-attached order by amount
            try:
                amount = session.get("amount_total")
                if amount:
                    with engine.begin() as conn:
                        res = conn.execute(select(orders.c.id).where(orders.c.amount_cents == amount).where(orders.c.stripe_session_id == None).limit(1))
                        row = res.fetchone()
                        if row:
                            oid = row[0]
                            update_order_stripe_session_db(oid, stripe_session_id=sess_id, payment_intent=payment_intent, paid=True)
            except Exception:
                log.exception("Failed to attach webhook session to an order by amount")

    # payment_intent.succeeded
    if typ == "payment_intent.succeeded":
        pi = obj
        pi_id = pi.get("id")
        try:
            with engine.begin() as conn:
                res = conn.execute(select(orders.c.id).where((orders.c.payment_intent == pi_id) | (orders.c.stripe_session_id == pi_id)).limit(1))
                row = res.fetchone()
                if row:
                    update_order_stripe_session_db(row[0], paid=True)
        except Exception:
            log.exception("Failed to mark order paid on payment_intent.succeeded")

    return jsonify({"received": True}), 200

# debug/admin endpoint (opt-in)
if DEBUG_SHOW_ORDERS:
    @app.route("/_debug_orders", methods=["GET"])
    def debug_orders():
        with engine.connect() as conn:
            res = conn.execute(select(orders).order_by(orders.c.created_at.desc()).limit(200))
            rows = [dict(r) for r in res.fetchall()]
        for r in rows:
            try:
                r['payload'] = json.loads(r['payload'])
            except Exception:
                r['payload'] = {"_raw": r['payload']}
            r['paid'] = bool(r.get('paid'))
        return jsonify(rows), 200

    @app.route("/admin/orders", methods=["GET"])
    def admin_orders():
        # restrict to localhost for extra safety
        if request.remote_addr not in ("127.0.0.1", "localhost", "::1"):
            return "Forbidden", 403
        token = request.args.get("admin_token", "")
        if token != ADMIN_TOKEN:
            return "Unauthorized - provide ?admin_token=...", 401
        with engine.connect() as conn:
            res = conn.execute(select(orders).order_by(orders.c.created_at.desc()).limit(200))
            rows = [dict(r) for r in res.fetchall()]
        rows_html = ""
        for r in rows:
            oid = r["id"]
            sess = r.get("stripe_session_id") or ""
            pi = r.get("payment_intent") or ""
            paid = "YES" if r.get("paid") else "NO"
            paid_at = r.get("paid_at") or ""
            amt = str(r.get("amount_cents") or "")
            download_link = f"/download?session_id={sess}" if sess else ""
            rows_html += f"<tr><td>{oid}</td><td>{amt}</td><td>{sess}</td><td>{pi}</td><td>{paid}</td><td>{paid_at}</td><td><a href='{download_link}'>download</a></td></tr>"
        html_page = f"""
        <html><head><meta charset='utf-8'><title>IndCad — Admin Orders</title>
        <style>table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ddd;padding:8px}}th{{background:#0B6E4F;color:white}}</style>
        </head><body>
        <h2>Orders (dev view)</h2>
        <p>Access restricted to localhost & token.</p>
        <table>
          <thead><tr><th>Order ID</th><th>Amount (cents)</th><th>Stripe Session</th><th>Payment Intent</th><th>Paid</th><th>Paid At</th><th>Actions</th></tr></thead>
          <tbody>{rows_html}</tbody>
        </table>
        </body></html>
        """
        return html_page

if __name__ == "__main__":
    init_db()
    port = int(os.getenv("PORT", "5001"))
    log.info("Starting IndCad on port %s", port)
    app.run(host="0.0.0.0", port=port, debug=True)
