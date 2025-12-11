from openai import OpenAI
import numpy as np
import faiss
import json
from pathlib import Path
from typing import List
import config

client = OpenAI(api_key=config.OPENAI_API_KEY)

def chunkify(iterable, size):
    chunk = []
    for item in iterable:
        chunk.append(item)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk

def build_embeddings(texts: List[str], model=None, batch_size=None):
    model = model or config.OPENAI_MODEL
    batch_size = batch_size or config.BATCH_SIZE
    vectors = []
    for batch in chunkify(texts, batch_size):
        resp = client.embeddings.create(model=model, input=batch)
        for item in resp.data:
            vectors.append(item.embedding)
    return vectors

def save_embeddings(vectors: List[List[float]], path=None):
    path = Path(path or config.EMBEDDINGS_JSON)
    with path.open('w', encoding='utf-8') as f:
        json.dump(vectors, f)

def load_embeddings(path=None):
    path = Path(path or config.EMBEDDINGS_JSON)
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding='utf-8'))

def build_faiss_index(vectors, path=None):
    path = Path(path or config.FAISS_INDEX)
    mat = np.array(vectors, dtype='float32')
    dim = mat.shape[1]
    index = faiss.IndexFlatIP(dim)
    faiss.normalize_L2(mat)
    index.add(mat)
    faiss.write_index(index, str(path))
    return index

def load_faiss(path=None):
    path = Path(path or config.FAISS_INDEX)
    if not path.exists():
        return None
    return faiss.read_index(str(path))
