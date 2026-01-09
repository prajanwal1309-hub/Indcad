# app.py
from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import config
from matcher import match_query, match_by_title, prepare_and_build_index
import sqlite3
from pathlib import Path

app = Flask(__name__)
CORS(app, origins=os.getenv("CORS_ORIGINS", "*").split(",") if os.getenv("CORS_ORIGINS") else "*")

# Ensure DB (SQLite) for feedback
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

# Try to prepare index (non-blocking: only if no heavy build required)
try:
    prepare_and_build_index(force_rebuild=False)
except SystemExit as e:
    # print warning and continue; build will be available using rebuild script
    print("WARNING during prepare:", e)

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"})

@app.route('/lookup-by-title', methods=['POST'])
def lookup_by_title():
    data = request.json or {}
    title = (data.get('title') or "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    k = int(data.get('k', config.TOP_K))
    results = match_by_title(title, top_k=k)
    return jsonify({"results": results})

@app.route('/match-noc', methods=['POST'])
def match_noc():
    data = request.json or {}
    q = data.get('query') or data.get('job_title') or data.get('duties') or ""
    if not q:
        return jsonify({"error": "Provide 'query' or 'job_title'/'duties'"}), 400
    k = int(data.get('k', config.TOP_K))
    results = match_query(q, top_k=k)
    return jsonify({"results": results})

@app.route('/feedback', methods=['POST'])
def feedback():
    data = request.json or {}
    # store in sqlite
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO feedback (user_input, flow, suggested_noc, suggested_title, user_selected_noc, user_selected_title, is_correct, notes, source)
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

if __name__ == '__main__':
    port = int(os.getenv("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=True)
