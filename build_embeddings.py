# build_embeddings.py
"""
Run to build both duty embeddings and title embeddings + optional FAISS indices.
Usage: export OPENAI_API_KEY=sk-...; python build_embeddings.py
"""
import os
from noc_db import load_noc_entries
from embeddings import build_embeddings, save_embeddings, build_faiss_index, save_embeddings
from pathlib import Path
import json
import sys

MODEL = os.getenv("OPENAI_MODEL", "text-embedding-3-small")
BATCH = int(os.getenv("BATCH_SIZE", 16))
print("Using model:", MODEL)

entries = load_noc_entries()
if not entries:
    print("No NOC entries found in noc_data.jsonl")
    sys.exit(1)

# Build duty embeddings
texts = [e.get("duties","") for e in entries]
print("Building duty embeddings for", len(texts), "entries ...")
vectors = build_embeddings(texts, model=MODEL, batch_size=BATCH)
save_embeddings(vectors)  # defaults to noc_embeddings.json
try:
    build_faiss_index(vectors)
    print("Saved duty embeddings + faiss index (if faiss available).")
except Exception as ex:
    print("Warning building faiss:", ex)

# Build title embeddings (for title-only quick search)
titles = [e.get("title","") for e in entries]
print("Building title embeddings ...")
title_vecs = build_embeddings(titles, model=MODEL, batch_size=BATCH)
 # write title embeddings json
with open("title_embeddings.json", "w", encoding="utf-8") as f:
    json.dump(title_vecs, f)
try:
    # try building title faiss index
    from embeddings import build_faiss_index as _build_faiss
    _build_faiss(title_vecs, out_path=Path("title_faiss.index"))
    print("Saved title embeddings + faiss index (if faiss available).")
except Exception as ex:
    print("Warning building title faiss:", ex)

print("Done.")
