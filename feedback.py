# feedback.py
"""
Feedback storage helper for IndCad.
Stores user feedback into the same SQLite DB (indcad.db) used by users.py.
"""

import sqlite3
from pathlib import Path
import json
from datetime import datetime

DB_PATH = Path("indcad.db")  # same DB as users.py

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    user_input TEXT,
    flow TEXT,                   -- 'title' or 'duty'
    suggested_noc TEXT,
    suggested_title TEXT,
    suggested_teer TEXT,
    user_selected_noc TEXT,      -- what user chose (may be same as suggested)
    user_selected_title TEXT,
    is_correct INTEGER,          -- 1 = yes, 0 = no, NULL = unknown
    notes TEXT,
    source TEXT                  -- e.g., 'widget_v1' or session id
);
"""

def init_feedback_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(CREATE_TABLE_SQL)
    con.commit()
    con.close()

def save_feedback(payload: dict) -> int:
    """
    payload keys:
      - user_input (str)
      - flow ('title'|'duty')
      - suggested_noc, suggested_title, suggested_teer
      - user_selected_noc, user_selected_title
      - is_correct (True/False or 1/0)
      - notes (optional)
      - source (optional)
    Returns inserted row id.
    """
    init_feedback_db()
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    created_at = datetime.utcnow().isoformat() + "Z"
    user_input = payload.get("user_input")
    flow = payload.get("flow")
    suggested_noc = payload.get("suggested_noc")
    suggested_title = payload.get("suggested_title")
    suggested_teer = payload.get("suggested_teer")
    user_selected_noc = payload.get("user_selected_noc")
    user_selected_title = payload.get("user_selected_title")
    is_correct = payload.get("is_correct")
    if isinstance(is_correct, bool):
        is_correct = 1 if is_correct else 0
    elif is_correct is None:
        is_correct = None
    notes = payload.get("notes")
    source = payload.get("source")

    cur.execute(
        """
        INSERT INTO feedback(
            created_at, user_input, flow,
            suggested_noc, suggested_title, suggested_teer,
            user_selected_noc, user_selected_title, is_correct,
            notes, source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            created_at, user_input, flow,
            suggested_noc, suggested_title, suggested_teer,
            user_selected_noc, user_selected_title, is_correct,
            notes, source
        )
    )
    con.commit()
    rowid = cur.lastrowid
    con.close()
    return rowid

def get_feedback(limit: int = 200):
    """Return latest feedback rows as list of dicts (most recent first)."""
    init_feedback_db()
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT id, created_at, user_input, flow, suggested_noc, suggested_title, suggested_teer, user_selected_noc, user_selected_title, is_correct, notes, source FROM feedback ORDER BY id DESC LIMIT ?", (limit,))
    rows = cur.fetchall()
    con.close()
    cols = ["id","created_at","user_input","flow","suggested_noc","suggested_title","suggested_teer","user_selected_noc","user_selected_title","is_correct","notes","source"]
    out = []
    for r in rows:
        d = dict(zip(cols, r))
        out.append(d)
    return out

def export_feedback_csv(limit: int = 10000):
    """Return CSV string (header + rows)."""
    import csv
    import io
    rows = get_feedback(limit=limit)
    output = io.StringIO()
    writer = csv.writer(output)
    header = ["id","created_at","user_input","flow","suggested_noc","suggested_title","suggested_teer","user_selected_noc","user_selected_title","is_correct","notes","source"]
    writer.writerow(header)
    for r in rows:
        writer.writerow([r.get(h) for h in header])
    return output.getvalue()
