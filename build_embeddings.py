import sys
import os
import json
from openai import OpenAI

# ──────────────────────────────────────────────
# Add project root so "modules" can be imported
# ──────────────────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(ROOT)

from modules.matcher import prepare_and_build_index  # now it works

client = OpenAI()

DATA_FILE = "data/noc/noc_dataset.jsonl"
OUTPUT_FILE = "data/noc/noc_embeddings.json"


def load_dataset():
    if not os.path.exists(DATA_FILE):
        raise FileNotFoundError(f"{DATA_FILE} not found")

    with open(DATA_FILE, "r") as f:
        lines = f.readlines()
        return [json.loads(l) for l in lines]


def build_embeddings():
    data = load_dataset()
    output = []

    for i, item in enumerate(data):
        text = item.get("description") or ""
        if not text.strip():
            continue

        embedding = client.embeddings.create(
            model="text-embedding-3-large",
            input=text
        ).data[0].embedding

        output.append({
            "id": item.get("id", i),
            "text": text,
            "embedding": embedding
        })

        print(f"Embedded {i+1}/{len(data)}")

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f)

    print(f"Saved embeddings to {OUTPUT_FILE}")


if __name__ == "__main__":
    build_embeddings()
