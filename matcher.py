# matcher.py
import re
import difflib
import numpy as np
from functools import lru_cache

from noc_db import load_noc_entries
from embeddings import load_embeddings, load_faiss, client as embeddings_client
import config


# -----------------------
# Helpers
# -----------------------

def normalize_text(s: str) -> str:
    s = (s or "").lower().strip()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s


def string_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def build_full_description(entry: dict) -> str:
    """
    Build FULL readable text:
    - Duties
    - Employment requirements
    """
    duties = entry.get("duties", "").strip()
    employment = entry.get("employment_requirements", "").strip()

    out = []
    if duties:
        out.append("Duties:\n" + duties)
    if employment:
        out.append("\nEmployment requirements:\n" + employment)

    return "\n\n".join(out).strip()


# -----------------------
# Index check
# -----------------------

def prepare_and_build_index(force_rebuild=False):
    entries = load_noc_entries()
    if not entries:
        raise SystemExit("No NOC entries found.")

    if load_embeddings() and load_faiss():
        return entries

    raise SystemExit(
        "Embeddings missing. Run rebuild_index.sh before starting server."
    )


# -----------------------
# Match by duties (semantic)
# -----------------------

def match_query(query: str, top_k=None):
    top_k = top_k or config.TOP_K
    entries = load_noc_entries()
    if not entries:
        return []

    resp = embeddings_client.embeddings.create(
        model=config.OPENAI_MODEL,
        input=[query]
    )
    qvec = np.array(resp.data[0].embedding, dtype="float32")

    index = load_faiss()

    results = []

    if index is not None:
        import faiss
        faiss.normalize_L2(qvec.reshape(1, -1))
        D, I = index.search(qvec.reshape(1, -1), top_k)

        for score, idx in zip(D[0], I[0]):
            e = entries[int(idx)]
            results.append({
                "title": e.get("title", ""),
                "noc": e.get("noc", ""),
                "teer": e.get("teer", ""),
                "score": float(score),
                "duties_snippet": build_full_description(e)
            })
        return results

    # fallback (no FAISS)
    vecs = load_embeddings()
    if not vecs:
        return []

    arr = np.array(vecs, dtype="float32")
    arr = arr / np.linalg.norm(arr, axis=1, keepdims=True)
    qn = qvec / np.linalg.norm(qvec)

    scores = arr.dot(qn)
    idxs = np.argsort(-scores)[:top_k]

    for i in idxs:
        e = entries[int(i)]
        results.append({
            "title": e.get("title", ""),
            "noc": e.get("noc", ""),
            "teer": e.get("teer", ""),
            "score": float(scores[i]),
            "duties_snippet": build_full_description(e)
        })

    return results


# -----------------------
# Match by title (exact + fuzzy)
# -----------------------

def match_by_title(title: str, top_k: int = 5):
    entries = load_noc_entries()
    if not entries:
        return []

    n_title = normalize_text(title)

    # Exact / related title match
    exact = []
    for e in entries:
        tnorm = normalize_text(e.get("title", ""))
        related = [normalize_text(r) for r in e.get("related_titles", [])]

        if n_title == tnorm or n_title in related:
            exact.append({
                "title": e.get("title", ""),
                "noc": e.get("noc", ""),
                "teer": e.get("teer", ""),
                "score": 1.0,
                "duties_snippet": build_full_description(e)
            })

    if exact:
        seen = set()
        out = []
        for r in exact:
            if r["noc"] not in seen:
                out.append(r)
                seen.add(r["noc"])
        return out[:top_k]

    # Fuzzy scoring
    scored = []

    for e in entries:
        tnorm = normalize_text(e.get("title", ""))
        sscore = string_similarity(n_title, tnorm)

        rels = e.get("related_titles", [])
        relscore = max(
            [string_similarity(n_title, normalize_text(r)) for r in rels] + [0]
        )

        combined = max(sscore, relscore)
        scored.append((combined, e))

    scored.sort(key=lambda x: x[0], reverse=True)

    out = []
    for score, e in scored[:top_k]:
        out.append({
            "title": e.get("title", ""),
            "noc": e.get("noc", ""),
            "teer": e.get("teer", ""),
            "score": float(score),
            "duties_snippet": build_full_description(e)
        })

    return out


# -----------------------
# Cached wrapper
# -----------------------

@lru_cache(maxsize=2048)
def match_by_title_cached(title: str, top_k: int = 5):
    return match_by_title(title, top_k)
