import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "text-embedding-3-small")
DATA_NOC_PATH = BASE_DIR / "noc_data.jsonl"
EMBEDDINGS_JSON = BASE_DIR / "noc_embeddings.json"
FAISS_INDEX = BASE_DIR / "noc_faiss.index"
BATCH_SIZE = int(os.getenv("BATCH_SIZE", 16))
TOP_K = int(os.getenv("TOP_K", 5))
