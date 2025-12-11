# app.py — production-minded Flask app (flat-folder)
import os
import traceback
from functools import wraps
from datetime import timedelta
from flask import Flask, request, jsonify, Response
from dotenv import load_dotenv
import logging
# --- DB fallback: if DATABASE_URL not provided, use local sqlite file ---
import os
if not os.getenv("DATABASE_URL"):
    # Use a relative sqlite file in the project root (single-file DB, OK for light traffic)
    # SQLALCHEMY style URI: sqlite:///./indcad.db  (3 slashes or 4 depends on library; this is common)
    os.environ["DATABASE_URL"] = "sqlite:///./indcad.db"
    # optional log
    import logging
    logging.getLogger("indcad").warning("DATABASE_URL not set — falling back to sqlite: ./indcad.db")

# load .env from project folder
load_dotenv()

# Configuration from environment
ADMIN_USER = os.getenv("ADMIN_USER", "")
ADMIN_PASS = os.getenv("ADMIN_PASS", "")
PUBLIC_API_KEY = os.getenv("PUBLIC_API_KEY", "")  # optional - if set, clients must send X-API-KEY
RATE_LIMIT_DEFAULT = os.getenv("RATE_LIMIT_DEFAULT", "200/hour")  # default global limit

# imports for core functionality
import config
from matcher import normalize_text, match_by_title_cached, match_query, prepare_and_build_index

# flask + extensions
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

app = Flask(__name__)

# Logging
LOG_FILE = os.getenv("LOG_FILE", "indcad.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("indcad")

# CORS — allow WordPress domain(s). Default allows all origins; change ORIGINS in .env for production.
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*")  # set to comma-separated host list for production
if CORS_ORIGINS == "*" or CORS_ORIGINS.strip() == "":
    CORS(app, supports_credentials=True)
else:
    origins = [h.strip() for h in CORS_ORIGINS.split(",")]
    CORS(app, origins=origins, supports_credentials=True)

# Rate limiter (IP-based). Can be tuned via RATE_LIMIT_DEFAULT env var, e.g. "100/hour"
# Rate limiter (new Flask-Limiter >=3.x syntax)
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[RATE_LIMIT_DEFAULT],
    storage_uri="memory://"
)
limiter.init_app(app)


# Helper: basic auth decorator for admin endpoints
def check_basic_auth(auth_header):
    if not ADMIN_USER or not ADMIN_PASS:
        return False
    if not auth_header:
        return False
    # auth_header should be 'Basic base64(...)' - use Flask helper below to check
    return True

def require_basic_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        # If admin credentials are not configured, disallow access
        if not ADMIN_USER or not ADMIN_PASS:
            return jsonify({"error": "admin credentials not configured"}), 403
        auth = request.authorization
        if not auth or auth.username != ADMIN_USER or auth.password != ADMIN_PASS:
            return Response('Authentication required', 401, {'WWW-Authenticate': 'Basic realm="Login Required"'})
        return f(*args, **kwargs)
    return decorated

# Optional API key decorator for write endpoints (if PUBLIC_API_KEY set)
def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not PUBLIC_API_KEY:
            return f(*args, **kwargs)  # no API key configured => allow
        header = request.headers.get("X-API-KEY") or request.headers.get("x-api-key")
        if not header or header != PUBLIC_API_KEY:
            return jsonify({"error": "invalid or missing API key"}), 401
        return f(*args, **kwargs)
    return decorated

# Startup: build/load indexes (safe handling)
logger.info("Starting IndCad app.")
try:
    entries = prepare_and_build_index(force_rebuild=False)
    logger.info("Loaded %d NOC entries.", len(entries))
except SystemExit as e:
    logger.warning("prepare_and_build_index warning: %s", str(e))
    entries = []
except Exception as ex:
    logger.exception("Error during prepare_and_build_index: %s", ex)
    entries = []

# Health endpoint — no rate limit for health
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})

# Duty-based search (embedding) — rate-limited by default limiter
@app.route("/match-noc", methods=["POST"])
@require_api_key
@limiter.limit("60/minute")  # stricter for embeddings
def match_noc():
    try:
        data = request.json or {}
        q = data.get("query") or data.get("job_title") or data.get("duties")
        if not q:
            return jsonify({"error": "Provide 'query' or 'job_title'/'duties'"}), 400
        k = int(data.get("k", config.TOP_K))
        results = match_query(q, top_k=k)
        return jsonify({"results": results})
    except Exception as e:
        logger.exception("match-noc error: %s", e)
        return jsonify({"error": "internal error"}), 500

# Title lookup — lightweight, cached
@app.route("/lookup-by-title", methods=["POST"])
@require_api_key
@limiter.limit("300/minute")  # allow more short title lookups
def lookup_by_title():
    try:
        data = request.json or {}
        title = (data.get("title") or "").strip()
        if not title:
            return jsonify({"error": "title required"}), 400
        k = int(data.get("k", 5))
        # normalize BEFORE caching so cache keys are consistent
        n = normalize_text(title)
        results = match_by_title_cached(n, top_k=k)
        return jsonify({"results": results})
    except Exception as e:
        logger.exception("lookup-by-title error: %s", e)
        return jsonify({"error": "internal error"}), 500

# Feedback (write) endpoint
from feedback import save_feedback  # imported here to avoid early DB init if not used
@app.route("/feedback", methods=["POST"])
@require_api_key
@limiter.limit("30/minute")  # reduce spam risk
def feedback():
    try:
        payload = request.json or {}
        if not payload.get("user_input"):
            return jsonify({"error": "user_input required"}), 400
        rowid = save_feedback(payload)
        return jsonify({"ok": True, "id": rowid})
    except Exception as e:
        logger.exception("feedback error: %s", e)
        return jsonify({"error": "internal error"}), 500

# Admin endpoints — protected with basic auth; also rate-limited lightly
from feedback import get_feedback, export_feedback_csv, init_feedback_db

@app.route("/admin/feedback", methods=["GET"])
@require_basic_auth
@limiter.limit("100/hour")
def admin_feedback():
    try:
        limit = int(request.args.get("limit", 200))
        rows = get_feedback(limit=limit)
        return jsonify({"count": len(rows), "rows": rows})
    except Exception as e:
        logger.exception("admin_feedback error: %s", e)
        return jsonify({"error": "internal error"}), 500

@app.route("/admin/feedback.csv", methods=["GET"])
@require_basic_auth
@limiter.limit("50/hour")
def admin_feedback_csv():
    try:
        csv_text = export_feedback_csv(limit=10000)
        return app.response_class(csv_text, mimetype="text/csv", headers={"Content-Disposition":"attachment; filename=feedback.csv"})
    except Exception as e:
        logger.exception("admin_feedback_csv error: %s", e)
        return jsonify({"error": "internal error"}), 500

# Optional small admin endpoint to re-build indexes (protected)
@app.route("/admin/rebuild-indexes", methods=["POST"])
@require_basic_auth
@limiter.limit("10/hour")
def admin_rebuild_indexes():
    try:
        # optional JSON param: {"force": true}
        data = request.json or {}
        force = bool(data.get("force", False))
        entries = prepare_and_build_index(force_rebuild=force)
        return jsonify({"ok": True, "entries_loaded": len(entries)})
    except Exception as e:
        logger.exception("admin_rebuild_indexes error: %s", e)
        return jsonify({"error": "internal error"}), 500

# Run with gunicorn in production. For dev you can still use python app.py but debug must be False.
if __name__ == "__main__":
    # Never run Flask debugger in production. Use only for local dev debugging.
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5001)), debug=False)
