# embeddings.py
"""
Embeddings helper — tolerant to missing faiss.
Provides:
- client (OpenAI client)
- build_embeddings(texts, model, batch_size)
- save_embeddings(vectors)
- load_embeddings()
- build_faiss_index(vectors)
- load_faiss()
"""

import os
import json
from pathlib import Path
from openai import OpenAI
import numpy as np
import config

# OpenAI client
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY") or config.OPENAI_API_KEY)

# Files (use config if present, otherwise fallback)
EMB_JSON_PATH = Path(getattr(config, "EMBEDDINGS_JSON", Path("noc_embeddings.json")))
FAISS_INDEX_PATH = EMB_JSON_PATH.parent / "noc_faiss.index"

# Try import faiss
try:
    import faiss
    FAISS_AVAILABLE = True
except Exception:
    faiss = None
    FAISS_AVAILABLE = False

# -----------------------------
# Embeddings build / save / load
# -----------------------------
def build_embeddings(texts, model: str = "text-embedding-3-small", batch_size: int = 16):
    """
    Returns list[list[float]] embeddings in same order as texts.
    Uses OpenAI client in batches.
    """
    vectors = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i+batch_size]
        resp = client.embeddings.create(model=model, input=batch)
        for it in resp.data:
            vectors.append(it.embedding)
    return vectors

def save_embeddings(vectors):
    EMB_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(EMB_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(vectors, f)
    return str(EMB_JSON_PATH)

def load_embeddings():
    if not EMB_JSON_PATH.exists():
        return None
    with open(EMB_JSON_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

# -----------------------------
# FAISS index helpers (optional)
# -----------------------------
def build_faiss_index(vectors):
    """
    Build and save a FAISS index if faiss available.
    If faiss not available, simply save JSON for brute-force fallback.
    """
    arr = np.array(vectors, dtype="float32")
    if FAISS_AVAILABLE:
        dim = arr.shape[1]
        index = faiss.IndexFlatIP(dim)
        faiss.normalize_L2(arr)
        index.add(arr)
        faiss.write_index(index, str(FAISS_INDEX_PATH))
        return str(FAISS_INDEX_PATH)
    else:
        # no faiss: save embeddings JSON (already handled in save_embeddings)
        save_embeddings(vectors)
        return None

def load_faiss():
    """
    Return faiss index instance if available and file exists.
    Otherwise return None.
    """
    if not FAISS_AVAILABLE:
        return None
    if not FAISS_INDEX_PATH.exists():
        return None
    try:
        idx = faiss.read_index(str(FAISS_INDEX_PATH))
        return idx
    except Exception:
        return None

# -----------------------------
# Brute-force helper (used when FAISS not available)
# -----------------------------
def brute_force_search(query_vector, top_k=5):
    """
    query_vector: numpy array (float32)
    returns list of tuples (score, index)
    """
    vectors = load_embeddings()
    if vectors is None:
        return []
    arr = np.array(vectors, dtype="float32")
    # normalize
    arr = arr / np.linalg.norm(arr, axis=1, keepdims=True)
    qn = query_vector / np.linalg.norm(query_vector)
    scores = arr.dot(qn)
    idxs = np.argsort(-scores)[:top_k]
    return [(float(scores[i]), int(i)) for i in idxs]
