# embeddings.py
import os
import json
from pathlib import Path
from openai import OpenAI
import numpy as np
import config

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY") or getattr(config, "OPENAI_API_KEY", None))

EMB_JSON = Path(getattr(config, "EMBEDDINGS_JSON", "noc_embeddings.json"))
TITLE_EMB_JSON = Path(getattr(config, "TITLE_EMBEDDINGS_JSON", "title_embeddings.json"))
FAISS_INDEX = EMB_JSON.parent / "noc_faiss.index"
TITLE_FAISS_INDEX = TITLE_EMB_JSON.parent / "title_faiss.index"

try:
    import faiss
    FAISS_AVAILABLE = True
except Exception:
    faiss = None
    FAISS_AVAILABLE = False

def build_embeddings(texts, model: str = "text-embedding-3-small", batch_size: int = 16):
    vectors = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i+batch_size]
        resp = client.embeddings.create(model=model, input=batch)
        for it in resp.data:
            vectors.append(it.embedding)
    return vectors

def save_embeddings(vectors, path=EMB_JSON):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(vectors, f)
    return str(path)

def load_embeddings(path=EMB_JSON):
    if not Path(path).exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def build_faiss_index(vectors, out_path=FAISS_INDEX):
    arr = np.array(vectors, dtype='float32')
    if FAISS_AVAILABLE:
        dim = arr.shape[1]
        index = faiss.IndexFlatIP(dim)
        faiss.normalize_L2(arr)
        index.add(arr)
        faiss.write_index(index, str(out_path))
        return str(out_path)
    else:
        save_embeddings(vectors, out_path.with_suffix(".json"))
        return None

def load_faiss(path=FAISS_INDEX):
    if not FAISS_AVAILABLE:
        return None
    if not Path(path).exists():
        return None
    try:
        idx = faiss.read_index(str(path))
        return idx
    except Exception:
        return None
