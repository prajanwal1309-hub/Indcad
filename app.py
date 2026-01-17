# app.py
import os
import uuid
import sqlite3
from pathlib import Path

from flask import Flask, request, jsonify, abort
from flask_cors import CORS
from dotenv import load_dotenv

import config
from matcher import match_query, match_by_title, prepare_and_build_index
from decision_engine import run_decision_engine
from pdf_generator import generate_indcad_pdf

# ------------------------------------------------------------------
# ENV + SECURITY
# ------------------------------------------------------------------

load_dotenv()

INDCAD_INTERNAL_KEY = os.getenv("INDCAD_INTERNAL_KEY")
if not INDCAD_INTERNAL_KEY:
    raise RuntimeError("INDCAD_INTERNAL_KEY is not set")

def verify_internal_key():
    received = request.headers.get("X-INTERNAL-KEY")
    print("EXPECTED KEY:", INDCAD_INTERNAL_KEY)
    print("RECEIVED KEY:", received)

    if received != INDCAD_INTERNAL_KEY:
        abort(403)


# ------------------------------------------------------------------
# FLASK APP INIT
# ------------------------------------------------------------------

app = Flask(__name__)

CORS(
    app,
    origins=os.getenv("CORS_ORIGINS", "*").split(",")
    if os.getenv("CORS_ORIGINS")
    else "*"
)

# ------------------------------------------------------------------
# DATABASE SETUP
# ------------------------------------------------------------------

DB_PATH = Path(os.getenv("DATABASE_PATH", "./indcad.db"))

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts DATETIME DEFAULT CURRENT_TIMESTAMP,
        user_input TEXT,
        flow TEXT,
        suggested_noc TEXT,
        suggested_title TEXT,
        user_selected_noc TEXT,
        user_selected_title TEXT,
        is_correct INTEGER DEFAULT 0,
        notes TEXT,
        source TEXT
    )
    """)
    conn.commit()
    conn.close()

init_db()

# ------------------------------------------------------------------
# SEARCH INDEX
# ------------------------------------------------------------------

try:
    prepare_and_build_index(force_rebuild=False)
except SystemExit as e:
    print("WARNING during prepare:", e)

# ------------------------------------------------------------------
# HEALTH
# ------------------------------------------------------------------

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"})

# ------------------------------------------------------------------
# NOC LOOKUP
# ------------------------------------------------------------------

@app.route('/lookup-by-title', methods=['POST'])
def lookup_by_title():
    data = request.json or {}
    title = (data.get('title') or "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400

    k = int(data.get('k', config.TOP_K))
    return jsonify({"results": match_by_title(title, top_k=k)})

@app.route('/match-noc', methods=['POST'])
def match_noc():
    data = request.json or {}
    q = data.get('query') or data.get('job_title') or data.get('duties') or ""
    if not q:
        return jsonify({"error": "Provide query"}), 400

    k = int(data.get('k', config.TOP_K))
    return jsonify({"results": match_query(q, top_k=k)})

# ------------------------------------------------------------------
# FEEDBACK
# ------------------------------------------------------------------

@app.route('/feedback', methods=['POST'])
def feedback():
    data = request.json or {}
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO feedback (
            user_input, flow, suggested_noc, suggested_title,
            user_selected_noc, user_selected_title,
            is_correct, notes, source
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data.get('user_input'),
        data.get('flow'),
        data.get('suggested_noc'),
        data.get('suggested_title'),
        data.get('user_selected_noc'),
        data.get('user_selected_title'),
        1 if data.get('is_correct') else 0,
        data.get('notes'),
        data.get('source')
    ))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})

# ------------------------------------------------------------------
# DECISION ENGINE (INTERNAL)
# ------------------------------------------------------------------

@app.route('/decision-engine', methods=['POST'])
def decision_engine():
    verify_internal_key()

    payload = request.get_json(silent=True)
    if not payload or "snapshot" not in payload or "context" not in payload:
        return jsonify({"status": "error", "message": "Invalid payload"}), 400

    try:
        result = run_decision_engine(payload)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

    return jsonify({
        "status": "success",
        "engine_version": "v1",
        "result": result
    })

# ------------------------------------------------------------------
# PDF GENERATION (INTERNAL)
# ------------------------------------------------------------------

@app.route('/generate-pdf', methods=['POST'])
def generate_pdf():
    verify_internal_key()

    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"status": "error", "message": "Invalid JSON"}), 400

    engine_result = payload.get("engine_result")
    snapshot = payload.get("snapshot")
    context = payload.get("context")

    if not engine_result or not snapshot or not context:
        return jsonify({"status": "error", "message": "Missing data"}), 400

    os.makedirs("output_pdfs", exist_ok=True)

    filename = f"indcad_report_{uuid.uuid4().hex}.pdf"
    output_path = os.path.join("output_pdfs", filename)

    try:
        generate_indcad_pdf(output_path, engine_result, snapshot, context)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

    return jsonify({
        "status": "success",
        "download_url": f"/output_pdfs/{filename}"
    })

# ------------------------------------------------------------------
# ENTRY
# ------------------------------------------------------------------

if __name__ == '__main__':
    port = int(os.getenv("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=True)
