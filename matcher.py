# matcher.py
import re
import difflib
import numpy as np
from noc_db import load_noc_entries
from embeddings import load_embeddings, load_faiss, client as embeddings_client
import config

# -----------------------
# Helper functions
# -----------------------

def make_official_noc_link(entry):
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
    return difflib.SequenceMatcher(None, a, b).ratio()


def extract_employment_requirements(entry):
    """
    Try common keys. If not present, return empty string.
    """
    return (
        entry.get("employment_requirements")
        or entry.get("requirements")
        or entry.get("employment")
        or ""
    )

# -----------------------
# Index build wrapper
# -----------------------

def prepare_and_build_index(force_rebuild=False):
    entries = load_noc_entries()
    texts = [e.get("duties", "") for e in entries]

    if not texts:
        raise SystemExit("No NOC entries found.")

    existing = load_embeddings()
    if existing and not force_rebuild:
        idx = load_faiss()
        if idx is not None:
            return entries

    raise SystemExit(
        "Embeddings missing. Run rebuild_index.sh to create embeddings and FAISS index."
    )

# -----------------------
# Match by duties (embedding search)
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
                "job_name": e.get("title", ""),
                "noc_code": e.get("noc", ""),
                "teer": e.get("teer", ""),
                "duties": e.get("duties", ""),
                "employment_requirements": extract_employment_requirements(e),
                "official_noc_link": make_official_noc_link(e)
            })

        return results

    # Fallback (no FAISS)
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
            "job_name": e.get("title", ""),
            "noc_code": e.get("noc", ""),
            "teer": e.get("teer", ""),
            "duties": e.get("duties", ""),
            "employment_requirements": extract_employment_requirements(e),
            "official_noc_link": make_official_noc_link(e)
        })

    return results

# -----------------------
# Match by title (string + embedding)
# -----------------------

def match_by_title(title: str, top_k: int = 5):
    entries = load_noc_entries()
    if not entries:
        return []

    n_title = normalize_text(title)
    results = []

    # Exact / related match first
    for e in entries:
        title_norm = normalize_text(e.get("title", ""))
        related_norms = [normalize_text(r) for r in e.get("related_titles", [])]

        if n_title == title_norm or n_title in related_norms:
            results.append({
                "job_name": e.get("title", ""),
                "noc_code": e.get("noc", ""),
                "teer": e.get("teer", ""),
                "duties": e.get("duties", ""),
                "employment_requirements": extract_employment_requirements(e),
                "official_noc_link": make_official_noc_link(e)
            })

    if results:
        seen = set()
        deduped = []
        for r in results:
            if r["noc_code"] not in seen:
                deduped.append(r)
                seen.add(r["noc_code"])
        return deduped[:top_k]

    # Fuzzy + embedding fallback
    scored = []

    for e in entries:
        tnorm = normalize_text(e.get("title", ""))
        sscore = string_similarity(n_title, tnorm)

        rels = e.get("related_titles", [])
        relscore = max(
            [string_similarity(n_title, normalize_text(r)) for r in rels] + [0]
        )

        string_score = max(sscore, relscore)
        scored.append((string_score, e))

    scored.sort(key=lambda x: x[0], reverse=True)

    for _, e in scored[:top_k]:
        results.append({
            "job_name": e.get("title", ""),
            "noc_code": e.get("noc", ""),
            "teer": e.get("teer", ""),
            "duties": e.get("duties", ""),
            "employment_requirements": extract_employment_requirements(e),
            "official_noc_link": make_official_noc_link(e)
        })

    return results
