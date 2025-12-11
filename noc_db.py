import json
from pathlib import Path
import config

def load_noc_entries(path: Path = None):
    path = path or config.DATA_NOC_PATH
    path = Path(path)
    if not path.exists():
        return []
    entries = []
    with path.open('r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            obj.setdefault("noc", "")
            obj.setdefault("related_titles", [])
            obj.setdefault("keywords", [])
            obj.setdefault("employment_requirements", "")
            obj.setdefault("duties_short", obj.get("duties","")[:300])
            # ensure TEER key exists (string)
            obj.setdefault("teer", "")   # empty if unknown
            entries.append(obj)
    return entries
