#!/usr/bin/env bash
set -e
. .venv/bin/activate
export OPENAI_API_KEY=${OPENAI_API_KEY:-}
echo "Building embeddings (requires OPENAI_API_KEY)."
python build_embeddings.py
echo "Done: noc_embeddings.json + title_embeddings.json (and faiss indices if faiss available) created."
