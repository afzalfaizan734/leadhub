"""Single source of truth for settings and domain vocabulary.

Every other module imports from here, so changing a bucket boundary, a status
name or a port happens in exactly one place (Open/Closed Principle).

Operational settings now read from the environment (12-factor) with safe
defaults that reproduce the original single-PC behaviour when nothing is set.
Copy .env.example to .env to override per deployment. Secrets and the database
live under DATA_DIR, which should sit OUTSIDE any web/deploy root in production.
"""
import os


# ----------------------------------------------------------------- env helpers
def _env(key, default=""):
    v = os.environ.get(key)
    return v if v is not None and v != "" else default


def _env_int(key, default):
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return int(default)


def _env_bool(key, default=False):
    return str(os.environ.get(key, "1" if default else "0")).strip().lower() \
        in ("1", "true", "yes", "on")


def _env_list(key):
    return [x.strip() for x in str(os.environ.get(key, "")).split(",") if x.strip()]


# ----------------------------------------------------------------- paths
PKG_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(PKG_DIR)                  # the LeadHub folder
WEB_DIR = os.path.join(PKG_DIR, "web")

# Data directory holds the database and any first-run files. Keep it OUTSIDE the
# web root in production. Defaults to the app folder for the existing install.
DATA_DIR = os.path.abspath(_env("LEADHUB_DATA_DIR", BASE_DIR))
DB_PATH = os.path.join(DATA_DIR, "leadhub.db")
PIN_FILE = os.path.join(DATA_DIR, "INITIAL-PINS.txt")

# Optional seed file. Absent on a fresh copy - the app simply starts empty and
# you populate it from the Dashboard, so the folder stays portable.
SEED_CSV = _env("LEADHUB_SEED_CSV", os.path.join(DATA_DIR, "seed_leads.csv"))
LEGACY_SEED_CSV = os.path.abspath(
    os.path.join(BASE_DIR, "..", "latest_motus_carriers_2026-09-12.csv"))
SEED_YEAR = "2026"

# ----------------------------------------------------------------- server
BIND = _env("LEADHUB_BIND", "0.0.0.0")               # use 127.0.0.1 behind a TLS proxy
# Cloud hosts (Render, Railway, Heroku, …) inject the port to bind as $PORT.
PORT = _env_int("PORT", _env_int("LEADHUB_PORT", 80))
FALLBACK_PORT = _env_int("LEADHUB_FALLBACK_PORT", 8000)
SITE_NAME = _env("LEADHUB_SITE_NAME", "leadmanager")

# ----------------------------------------------------------------- sessions & auth
SESSION_HOURS = _env_int("LEADHUB_SESSION_HOURS", 12)         # sliding idle timeout
SESSION_MAX_HOURS = _env_int("LEADHUB_SESSION_MAX_HOURS", 24)  # absolute max age
MAX_PIN_FAILS = _env_int("LEADHUB_MAX_PIN_FAILS", 5)
PIN_LOCK_SECONDS = _env_int("LEADHUB_PIN_LOCK_SECONDS", 120)
# NOTE: existing stored hashes were computed at 120_000 rounds. Do not raise this
# without an upgrade-on-login rehash (see security.py) or every login breaks.
PBKDF2_ROUNDS = _env_int("LEADHUB_PBKDF2_ROUNDS", 120_000)
PIN_MIN = _env_int("LEADHUB_PIN_MIN", 4)                      # agents
ADMIN_PIN_MIN = _env_int("LEADHUB_ADMIN_PIN_MIN", 6)         # admins (public-facing)

# ----------------------------------------------------------------- transport / headers
ENFORCE_HTTPS = _env_bool("LEADHUB_ENFORCE_HTTPS", False)    # send HSTS when True
PUBLIC_LOGIN = _env_bool("LEADHUB_PUBLIC_LOGIN", False)      # hide the user roster pre-login
MAX_BODY_BYTES = _env_int("LEADHUB_MAX_BODY_BYTES", 1_000_000)
ALLOWED_ORIGINS = _env_list("LEADHUB_ALLOWED_ORIGINS")      # extra same-site origins
# Interim CSP allows inline because the current page uses inline handlers; it is
# tightened to remove 'unsafe-inline' once the redesigned frontend ships its JS
# as a served file. Override entirely with LEADHUB_CSP.
CSP = _env(
    "LEADHUB_CSP",
    "default-src 'self'; "
    "script-src 'self'; "                          # app ships JS as a served file; no inline scripts
    "style-src 'self' 'unsafe-inline'; "          # inline style attributes are used; low XSS risk
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

# ----------------------------------------------------------------- branding (per client)
BRAND_NAME = _env("LEADHUB_BRAND_NAME", "LeadHub")
BRAND_ACCENT = _env("LEADHUB_BRAND_ACCENT", "#4f4ff0")

# ----------------------------------------------------------------- demo
DEMO = _env_bool("LEADHUB_DEMO", False)          # public demo: auto-seed sample data
DEMO_PIN = _env("LEADHUB_DEMO_PIN", "135790")    # shown on the demo login screen

# ----------------------------------------------------------------- domain
STATUSES = ["New", "Attempted", "Contacted", "Interested",
            "Not Interested", "DNC", "Closed"]
DISPOSITIONS = ["", "Left Voicemail", "No Answer", "Wrong Number", "Busy - Call Back",
                "Gatekeeper", "Spoke to Decision Maker", "Quoted",
                "Follow-Up Scheduled", "Not Interested", "Do Not Call", "Signed"]
ROLES = ("admin", "agent")

EDITABLE_LEAD_FIELDS = {"status", "disposition", "notes",
                        "last_contact", "next_followup", "call_attempts"}

# ----------------------------------------------------------------- buckets
# Age bands, newest first. Age is derived from authority_date at query time,
# never stored, so a lead moves band on its own as the days pass.
BUCKETS = [
    {"key": "0-7",   "label": "0-7 days",   "min": 0,    "max": 7},
    {"key": "8-30",  "label": "8-30 days",  "min": 8,    "max": 30},
    {"key": "31-60", "label": "31-60 days", "min": 31,   "max": 60},
    {"key": "61-90", "label": "61-90 days", "min": 61,   "max": 90},
    {"key": "91+",   "label": "91+ days",   "min": 91,   "max": None},
    {"key": "none",  "label": "No date",    "min": None, "max": None},
]
BUCKET_KEYS = [b["key"] for b in BUCKETS]

# Days between the authority date and today. NULL when the date is unusable.
# Future-dated authorities (FMCSA post-dates some) are clamped to 0.
AGE_SQL = """
CASE
  WHEN l.authority_date IS NULL OR length(l.authority_date) <> 8 THEN NULL
  ELSE MAX(0, CAST(julianday('now','localtime') - julianday(
        substr(l.authority_date,1,4)||'-'||substr(l.authority_date,5,2)||'-'||
        substr(l.authority_date,7,2)) AS INTEGER))
END
"""


def age_sql(now_lit: str) -> str:
    """AGE expression that uses an explicit 'now' literal (the accurate
    business-timezone time) instead of the server's local clock."""
    return f"""
CASE
  WHEN l.authority_date IS NULL OR length(l.authority_date) <> 8 THEN NULL
  ELSE MAX(0, CAST(julianday('{now_lit}') - julianday(
        substr(l.authority_date,1,4)||'-'||substr(l.authority_date,5,2)||'-'||
        substr(l.authority_date,7,2)) AS INTEGER))
END"""


def bucket_case_sql(age_expr: str = AGE_SQL) -> str:
    """Build the CASE that maps a live age onto a bucket key."""
    parts = ["CASE", f"  WHEN ({age_expr}) IS NULL THEN 'none'"]
    for b in BUCKETS:
        if b["key"] == "none":
            continue
        if b["max"] is None:
            parts.append(f"  WHEN ({age_expr}) >= {b['min']} THEN '{b['key']}'")
        else:
            parts.append(f"  WHEN ({age_expr}) <= {b['max']} THEN '{b['key']}'")
    parts.append("  ELSE 'none'\nEND")
    return "\n".join(parts)


def bucket_where_sql(key: str, age_expr: str = AGE_SQL):
    """WHERE fragment restricting rows to one bucket. Returns None if unknown."""
    b = next((x for x in BUCKETS if x["key"] == key), None)
    if b is None:
        return None
    if b["key"] == "none":
        return f"({age_expr}) IS NULL"
    if b["max"] is None:
        return f"({age_expr}) >= {b['min']}"
    return f"({age_expr}) BETWEEN {b['min']} AND {b['max']}"
