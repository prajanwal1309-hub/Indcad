# noc_db.py
import json
from pathlib import Path

DATA_FILE = Path("noc_data.jsonl")

def load_noc_entries():
    entries = []
    if not DATA_FILE.exists():
        return entries
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
                # ensure keys exist and preserve everything (including source_file_url)
                entries.append(e)
            except Exception:
                continue
    return entries
