# build_title_index.py  (flat-folder version)
import json
import numpy as np
import faiss
from pathlib import Path
from openai import OpenAI
import config
from noc_db import load_noc_entries

client = OpenAI(api_key=config.OPENAI_API_KEY)

OUT_DIR = Path('.')  # current folder
TITLE_EMB_JSON = OUT_DIR / "title_embeddings.json"
TITLE_FAISS = OUT_DIR / "title_faiss.index"

def embed_titles(titles, model, batch_size=32):
    vectors = []
    for i in range(0, len(titles), batch_size):
        chunk = titles[i:i+batch_size]
        resp = client.embeddings.create(model=model, input=chunk)
        for it in resp.data:
            vectors.append(it.embedding)
    return vectors

def build():
    entries = load_noc_entries()
    titles = [e.get("title","") for e in entries]
    if not titles:
        raise SystemExit("No NOC titles found. Check noc_data.jsonl")

    print(f"Embedding {len(titles)} titles...")
    vectors = embed_titles(titles, model=config.OPENAI_MODEL, batch_size=config.BATCH_SIZE)

    arr = np.array(vectors, dtype='float32')
    dim = arr.shape[1]
    index = faiss.IndexFlatIP(dim)
    faiss.normalize_L2(arr)
    index.add(arr)

    # save json vectors and faiss index
    with open(TITLE_EMB_JSON, "w", encoding="utf-8") as f:
        json.dump(vectors, f)
    faiss.write_index(index, str(TITLE_FAISS))
    print("Saved title_embeddings.json and title_faiss.index in current folder")

if __name__ == "__main__":
    build()
