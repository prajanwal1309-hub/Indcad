# matcher.py
import re
import difflib
import numpy as np

from noc_db import load_noc_entries
from embeddings import load_embeddings, load_faiss, client as embeddings_client
import config


# =========================================================
# Helper functions
# =========================================================

def make_official_noc_link(entry):
    """
    Return official NOC 2021 URL.
    """
    if entry.get("source_file_url"):
        return entry["source_file_url"]

    noc = str(entry.get("noc", "")).strip()
    noc_digits = "".join(ch for ch in noc if ch.isdigit())
    if not noc_digits:
        return ""

    return f"https://noc.esdc.gc.ca/Structure/NOCProfile?code={noc_digits}&version=2021.0"


def normalize_text(s):
    s = (s or "").lower().strip()
    s = re.sub(r'[^a-z0-9 ]+', ' ', s)
    s = re.sub(r'\s+', ' ', s)
    return s


def string_similarity(a, b):
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


# =========================================================
# Index build wrapper
# =========================================================

def prepare_and_build_index(force_rebuild=False):
    """
    Ensures NOC entries + embeddings + FAISS index exist.
    This module does NOT auto-build embeddings to avoid startup cost.
    """
    entries = load_noc_entries()
    if not entries:
        raise SystemExit("No NOC entries found.")

    existing = load_embeddings()
    if existing and not force_rebuild:
        idx = load_faiss()
        if idx is not None:
            return entries

    raise SystemExit(
        "Embeddings missing. Run rebuild_index.sh to create embeddings and FAISS index."
    )


# =========================================================
# Match by duties (semantic embedding search)
# =========================================================

def match_query(query: str, top_k=None):
    top_k = top_k or config.TOP_K
    entries = load_noc_entries()
    if not entries:
        return []

    # Create query embedding
    resp = embeddings_client.embeddings.create(
        model=config.OPENAI_MODEL,
        input=[query]
    )
    qvec = np.array(resp.data[0].embedding, dtype="float32")

    index = load_faiss()

    # ---- FAISS path ----
    if index is not None:
        import faiss
        faiss.normalize_L2(qvec.reshape(1, -1))
        D, I = index.search(qvec.reshape(1, -1), top_k)

        results = []
        for score, idx in zip(D[0], I[0]):
            e = entries[int(idx)]
            results.append({
                "noc": e.get("noc", ""),
                "title": e.get("title", ""),
                "teer": e.get("teer", ""),
                "score": float(score),
                # 🔥 FULL DUTIES — NO TRUNCATION
                "duties_snippet": e.get("duties", ""),
                "source_file_url": make_official_noc_link(e)
            })
        return results

    # ---- Fallback brute-force cosine ----
    vecs = load_embeddings()
    if not vecs:
        return []

    arr = np.array(vecs, dtype="float32")
    arr = arr / np.linalg.norm(arr, axis=1, keepdims=True)
    qn = qvec / np.linalg.norm(qvec)

    scores = arr.dot(qn)
    idxs = np.argsort(-scores)[:top_k]

    out = []
    for i in idxs:
        e = entries[int(i)]
        out.append({
            "noc": e.get("noc", ""),
            "title": e.get("title", ""),
            "teer": e.get("teer", ""),
            "score": float(scores[i]),
            # 🔥 FULL DUTIES — NO TRUNCATION
            "duties_snippet": e.get("duties", ""),
            "source_file_url": make_official_noc_link(e)
        })
    return out


# =========================================================
# Match by title (exact + fuzzy + embedding boost)
# =========================================================

def match_by_title(title: str, top_k: int = 5):
    entries = load_noc_entries()
    if not entries:
        return []

    n_title = normalize_text(title)

    # -----------------------------------------------------
    # 1. Exact title / related titles
    # -----------------------------------------------------
    direct_hits = []
    for e in entries:
        title_norm = normalize_text(e.get("title", ""))
        related_norms = [
            normalize_text(r) for r in e.get("related_titles", [])
        ]

        if n_title == title_norm or n_title in related_norms:
            direct_hits.append({
                "noc": e.get("noc", ""),
                "title": e.get("title", ""),
                "teer": e.get("teer", ""),
                "score": 1.0,
                # 🔥 FULL DUTIES — NO TRUNCATION
                "duties_snippet": e.get("duties", ""),
                "related_titles": e.get("related_titles", []),
                "source_file_url": make_official_noc_link(e)
            })

    if direct_hits:
        seen = set()
        dedup = []
        for r in direct_hits:
            if r["noc"] not in seen:
                dedup.append(r)
                seen.add(r["noc"])
        return dedup[:top_k]

    # -----------------------------------------------------
    # 2. Fuzzy + embedding scoring
    # -----------------------------------------------------
    scored = []

    for e in entries:
        tnorm = normalize_text(e.get("title", ""))
        sscore = string_similarity(n_title, tnorm)

        rels = e.get("related_titles", [])
        relscore = max(
            [string_similarity(n_title, normalize_text(r)) for r in rels] + [0]
        )

        string_score = max(sscore, relscore)

        emb_score = 0.0
        try:
            fa = load_faiss()
            if fa is not None:
                resp = embeddings_client.embeddings.create(
                    model=config.OPENAI_MODEL,
                    input=[title]
                )
                qvec = np.array(resp.data[0].embedding, dtype="float32")
                import faiss
                faiss.normalize_L2(qvec.reshape(1, -1))
                D, I = fa.search(qvec.reshape(1, -1), 1)
                emb_score = float(D[0][0])
        except Exception:
            emb_score = 0.0

        combined = 0.7 * string_score + 0.3 * emb_score

        keywords = e.get("keywords", [])
        matches = sum(1 for kw in keywords if kw and kw in n_title)
        combined = min(1.0, combined + 0.05 * min(matches, 3))

        scored.append((combined, e))

    scored.sort(key=lambda x: x[0], reverse=True)

    out = []
    for score, e in scored[:top_k]:
        out.append({
            "noc": e.get("noc", ""),
            "title": e.get("title", ""),
            "teer": e.get("teer", ""),
            "score": float(score),
            # 🔥 FULL DUTIES — NO TRUNCATION
            "duties_snippet": e.get("duties", ""),
            "related_titles": e.get("related_titles", []),
            "source_file_url": make_official_noc_link(e)
        })

    return out
if __name__ == "__main__":
    print("=== TITLE TEST: PLUMBER ===")
    r1 = match_by_title("plumber", top_k=1)
    print(r1[0]["title"])
    print(r1[0]["noc"])
    print(r1[0]["teer"])
    print("DUTIES:\n", r1[0]["duties_snippet"])
    print("-" * 60)

    print("=== TITLE TEST: SOFTWARE ENGINEER ===")
    r2 = match_by_title("software engineer", top_k=1)
    print(r2[0]["title"])
    print(r2[0]["noc"])
    print(r2[0]["teer"])
    print("DUTIES:\n", r2[0]["duties_snippet"])
    print("-" * 60)

    print("=== DUTIES SEARCH TEST ===")
    r3 = match_query("design, develop and maintain software applications", top_k=1)
    print(r3[0]["title"])
    print(r3[0]["noc"])
    print("DUTIES:\n", r3[0]["duties_snippet"])
