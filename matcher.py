# matcher.py
"""
Full matcher module (flat-folder) for IndCad NOC matching.

Usage:
- prepare_and_build_index(force_rebuild=False)  # call at startup
- match_query(query, top_k=5)                   # duty -> NOC semantic search
- match_by_title(title, top_k=5)                # title -> NOC lookup (cached)
"""

from functools import lru_cache
import re
import difflib
import numpy as np
import traceback

# local modules (flat folder)
from noc_db import load_noc_entries
from embeddings import build_embeddings, load_embeddings, build_faiss_index, load_faiss, save_embeddings, client as embeddings_client
import config

# optional title index loader (created by build_title_index.py)
# this file should exist in the same folder and load title_faiss.index / title_embeddings.json
try:
    from title_index import title_index, title_vectors
except Exception:
    title_index = None
    title_vectors = None

# For duty-based embedding calls we can reuse embeddings_client
# (embeddings_client is an OpenAI client instance created in embeddings.py)
# Defensive: if it doesn't exist, create a local one (rare)
try:
    _ = embeddings_client
except NameError:
    from openai import OpenAI
    embeddings_client = OpenAI(api_key=config.OPENAI_API_KEY)


# -----------------------
# Prepare / Build index
# -----------------------
def prepare_and_build_index(force_rebuild=False):
    """
    Ensure duty embeddings + FAISS index exist. Returns list of entries.
    - If noc_data.jsonl missing or empty -> raise SystemExit
    - If existing embeddings present and not force_rebuild -> load and return
    """
    entries = load_noc_entries()
    if not entries:
        raise SystemExit("No NOC entries found. Place noc_data.jsonl in project folder.")

    # Load existing duty embeddings (for duties -> FAISS matching)
    existing = load_embeddings()
    if existing and not force_rebuild:
        idx = load_faiss()
        if idx is not None:
            return entries

    # Build embeddings for duties and FAISS index
    texts = [e.get("duties", "") for e in entries]
    vectors = build_embeddings(texts, model=config.OPENAI_MODEL, batch_size=config.BATCH_SIZE)
    build_faiss_index(vectors)  # writes faiss index file
    save_embeddings(vectors)    # save JSON backup
    return entries


# -----------------------
# Helpers
# -----------------------
def normalize_text(s: str) -> str:
    """Lowercase, remove punctuation, collapse whitespace."""
    if not s:
        return ""
    s = s.lower().strip()
    s = re.sub(r'[^a-z0-9 ]+', ' ', s)
    s = re.sub(r'\s+', ' ', s)
    return s


def string_similarity(a: str, b: str) -> float:
    """Return a 0..1 similarity score between two strings using difflib."""
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


# -----------------------
# Title-based matching
# -----------------------
def match_by_title(title: str, top_k: int = 5):
    """
    Match a user-provided title to NOC entries.
    Strategy:
      1) numeric NOC shortcut
      2) exact title / related_titles match -> confidence 1.0
      3) fuzzy string match + optional embedding fallback via title_index
      4) keyword boost
    """
    entries = load_noc_entries()
    if not entries:
        return []

    # Trim and quick numeric NOC lookup
    t_raw = (title or "").strip()
    if t_raw.isdigit():
        code = t_raw
        for e in entries:
            if e.get("noc", "") == code:
                return [{
                    "noc": e.get("noc", ""),
                    "title": e.get("title", ""),
                    "teer": e.get("teer", ""),
                    "score": 1.0,
                    "duties_snippet": e.get("duties_short", e.get("duties", "")[:300]),
                    "related_titles": e.get("related_titles", [])
                }]
        # if not found, continue to fuzzy matching

    n_title = normalize_text(title)

    # 1) Exact / related titles
    immediate = []
    for e in entries:
        et = e.get("title", "")
        if not et:
            continue
        et_norm = normalize_text(et)
        related_norms = [normalize_text(x) for x in e.get("related_titles", []) if x]
        if n_title == et_norm or n_title in related_norms:
            immediate.append({
                "noc": e.get("noc", ""),
                "title": e.get("title", ""),
                "teer": e.get("teer", ""),
                "score": 1.0,
                "duties_snippet": e.get("duties_short", e.get("duties", "")[:300]),
                "related_titles": e.get("related_titles", [])
            })
    if immediate:
        # dedupe by noc
        seen = set()
        dedup = []
        for r in immediate:
            if r["noc"] not in seen:
                dedup.append(r); seen.add(r["noc"])
        return dedup[:top_k]

    # 2) Fuzzy + embedding fallback
    scored = []

    # If title_index exists, embed the query once and search it to get emb scores:
    emb_score_map = {}
    if title_index is not None and title_vectors is not None:
        try:
            resp = embeddings_client.embeddings.create(model=config.OPENAI_MODEL, input=[title])
            qvec = np.array(resp.data[0].embedding, dtype='float32')
            # normalize and search
            import faiss as _faiss
            _faiss.normalize_L2(qvec.reshape(1, -1))
            D, I = title_index.search(qvec.reshape(1, -1), min(10, len(title_vectors)))
            # D and I are shape (1, k)
            for dist, idx in zip(D[0], I[0]):
                try:
                    emb_score_map[int(idx)] = float(dist)
                except Exception:
                    pass
        except Exception:
            # If title embedding fails, continue with string-based matching only
            emb_score_map = {}

    # Iterate entries and compute combined score
    for i, e in enumerate(entries):
        title_candidate = e.get("title", "")
        tnorm = normalize_text(title_candidate)
        sscore = string_similarity(n_title, tnorm)

        # related titles fuzzy
        rels = e.get("related_titles", []) or []
        relscore = 0.0
        for r in rels:
            rn = normalize_text(r)
            relscore = max(relscore, string_similarity(n_title, rn))
        string_score = max(sscore, relscore)

        emb_score = emb_score_map.get(i, 0.0)

        # combined weighting
        combined = 0.7 * string_score + 0.3 * emb_score

        # keyword boost (normalize keywords)
        keywords = [normalize_text(k) for k in (e.get("keywords") or []) if k]
        matches = sum(1 for kw in keywords if kw and kw in n_title)
        combined = min(1.0, combined + 0.05 * min(matches, 3))

        scored.append((combined, e))

    # sort and return top_k
    scored.sort(key=lambda x: x[0], reverse=True)
    out = []
    for score, e in scored[:top_k]:
        out.append({
            "noc": e.get("noc", ""),
            "title": e.get("title", ""),
            "teer": e.get("teer", ""),
            "score": float(score),
            "duties_snippet": e.get("duties_short", e.get("duties", "")[:300]),
            "related_titles": e.get("related_titles", [])
        })
    return out


# LRU cache wrapper for fast repeated title lookups
@lru_cache(maxsize=2048)
def match_by_title_cached(normalized_title: str, top_k: int = 5):
    """
    Cache expects normalized title as key. Return structure same as match_by_title.
    """
    # match_by_title expects raw title (we pass normalized to preserve cache key)
    # but to keep behavior identical, pass the original normalized_title string
    return match_by_title(normalized_title, top_k=top_k)


# -----------------------
# Duty-based semantic search
# -----------------------
def match_query(query: str, top_k=None):
    """
    Duty-based semantic search:
    - embed the user query using OpenAI embeddings
    - search FAISS duty index (precomputed) if available
    - fallback to brute-force cosine if FAISS index missing
    """
    top_k = top_k or config.TOP_K
    entries = load_noc_entries()
    if not entries:
        return []

    # embed query once
    resp = embeddings_client.embeddings.create(model=config.OPENAI_MODEL, input=[query])
    qvec = np.array(resp.data[0].embedding, dtype='float32')

    index = load_faiss()
    if index is None:
        # brute force: compare to saved JSON vectors
        vecs = np.array(load_embeddings(), dtype='float32')
        # normalize
        vecs = vecs / np.linalg.norm(vecs, axis=1, keepdims=True)
        qn = qvec / np.linalg.norm(qvec)
        scores = vecs.dot(qn)
        # pick top_k indices
        idxs = np.argsort(-scores)[:top_k]
        results = []
        for i in idxs:
            results.append({
                "noc": entries[int(i)].get("noc", ""),
                "title": entries[int(i)].get("title", ""),
                "teer": entries[int(i)].get("teer", ""),
                "score": float(scores[i]),
                "duties_snippet": entries[int(i)].get("duties", "")[:300]
            })
        return results
    else:
        # use FAISS
        import faiss as _faiss
        _faiss.normalize_L2(qvec.reshape(1, -1))
        D, I = index.search(qvec.reshape(1, -1), top_k)
        res = []
        for score, idx in zip(D[0], I[0]):
            e = entries[int(idx)]
            res.append({
                "noc": e.get("noc", ""),
                "title": e.get("title", ""),
                "teer": e.get("teer", ""),
                "score": float(score),
                "duties_snippet": e.get("duties", "")[:300]
            })
        return res


# -----------------------
# Convenience small test function (optional)
# -----------------------
def _self_test():
    """
    Quick local test to validate the module (does not run server).
    """
    try:
        print("Preparing index (no rebuild)...")
        entries = prepare_and_build_index(force_rebuild=False)
        print("Loaded entries:", len(entries))
        print("Title index present:", title_index is not None)
        print("Testing title match for 'Backend developer'...")
        print(match_by_title("Backend developer", top_k=3))
        print("Testing duty match for sample duty...")
        q = "Design, develop and maintain software applications; write unit tests"
        print(match_query(q, top_k=3))
    except Exception as e:
        print("Self-test failed:", e)
        traceback.print_exc()

# if run directly, run small self-test
if __name__ == "__main__":
    _self_test()
