# title_index.py
import faiss, json, numpy as np
from pathlib import Path
import config

TITLE_FAISS_PATH = Path('.') / "title_faiss.index"
TITLE_EMB_JSON = Path('.') / "title_embeddings.json"

title_index = None
title_vectors = None

def load_title_index():
    global title_index, title_vectors
    if TITLE_FAISS_PATH.exists():
        try:
            title_index = faiss.read_index(str(TITLE_FAISS_PATH))
        except Exception as e:
            print("Failed to load title_faiss.index:", e)
            title_index = None
    if TITLE_EMB_JSON.exists():
        try:
            title_vectors = np.array(json.loads(TITLE_EMB_JSON.read_text()), dtype='float32')
        except Exception as e:
            print("Failed to load title_embeddings.json:", e)
            title_vectors = None

# load at import
load_title_index()
