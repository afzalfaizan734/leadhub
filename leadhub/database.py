"""Database infrastructure: connections, schema and forward-only migrations.

Owns *how* data is stored. Nothing above this layer writes SQL DDL.
"""
import os
import sqlite3
import threading
from . import config

write_lock = threading.Lock()      # serialises multi-statement writes

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL,
  role TEXT NOT NULL DEFAULT 'agent', active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS leads (
  id INTEGER PRIMARY KEY,
  usdot TEXT UNIQUE, docket TEXT, mc_number TEXT, number_format TEXT,
  legal_name TEXT, dba_name TEXT, officer TEXT,
  phone TEXT, cell TEXT, email TEXT,
  street TEXT, city TEXT, state TEXT, zip TEXT,
  power_units TEXT, drivers TEXT,
  authority_date TEXT, days_since INTEGER,
  insurance_company TEXT, coverage TEXT, insurance_status TEXT,
  assigned_to INTEGER REFERENCES users(id),
  status TEXT NOT NULL DEFAULT 'New', disposition TEXT DEFAULT '',
  notes TEXT DEFAULT '', last_contact TEXT DEFAULT '', next_followup TEXT DEFAULT '',
  call_attempts INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT, updated_by TEXT
);
CREATE INDEX IF NOT EXISTS ix_assigned ON leads(assigned_to);
CREATE INDEX IF NOT EXISTS ix_status   ON leads(status);
CREATE INDEX IF NOT EXISTS ix_authdate ON leads(authority_date);
CREATE INDEX IF NOT EXISTS ix_state    ON leads(state);
CREATE INDEX IF NOT EXISTS ix_usdot    ON leads(usdot);
CREATE TABLE IF NOT EXISTS activity (
  id INTEGER PRIMARY KEY, lead_id INTEGER, who TEXT, action TEXT,
  detail TEXT, ts TEXT
);
CREATE INDEX IF NOT EXISTS ix_act_ts  ON activity(ts DESC);
CREATE INDEX IF NOT EXISTS ix_act_who ON activity(who);

-- Append-only record of every call. The leads.notes field is untouched and still
-- works exactly as before; this sits alongside it so no history is ever overwritten.
CREATE TABLE IF NOT EXISTS call_log (
  id INTEGER PRIMARY KEY,
  lead_id INTEGER NOT NULL,
  who TEXT NOT NULL,
  outcome TEXT DEFAULT '',
  note TEXT DEFAULT '',
  ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_call_lead ON call_log(lead_id, id DESC);
CREATE INDEX IF NOT EXISTS ix_call_who  ON call_log(who, ts DESC);
CREATE INDEX IF NOT EXISTS ix_followup  ON leads(next_followup);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT
);
"""

_initialised = False


def connect():
    """A short-lived connection. WAL lets readers work while a write is in flight."""
    os.makedirs(config.DATA_DIR, exist_ok=True)   # data dir may be a custom path
    c = sqlite3.connect(config.DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=8000")
    return c


def initialise():
    """Create the schema and apply additive migrations. Safe to run repeatedly."""
    global _initialised
    c = connect()
    c.execute("PRAGMA journal_mode=WAL")      # persistent; set once
    c.executescript(SCHEMA)
    _migrate(c)
    c.commit()
    c.close()
    _initialised = True


def _migrate(c):
    """Additive only - never drops or rewrites existing columns or rows."""
    cols = {r["name"] for r in c.execute("PRAGMA table_info(users)")}
    for col in ("pin_hash", "pin_salt", "created_at", "last_login", "dialer"):
        if col not in cols:
            c.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT")
    lead_cols = {r["name"] for r in c.execute("PRAGMA table_info(leads)")}
    if "created_at" not in lead_cols:
        c.execute("ALTER TABLE leads ADD COLUMN created_at TEXT")
    if "assigned_at" not in lead_cols:
        c.execute("ALTER TABLE leads ADD COLUMN assigned_at TEXT")
    c.execute("CREATE INDEX IF NOT EXISTS ix_assigned_at ON leads(assigned_at)")
    c.commit()
    _backfill_assigned_at(c)


def _backfill_assigned_at(c):
    """Give already-assigned leads an assignment date so the daily view is not
    blank for work done before this column existed.

    Best source first: the activity log, which records every assign with a
    timestamp. Falls back to updated_at. Runs once - rows that already have a
    value are never touched."""
    pending = c.execute(
        "SELECT COUNT(*) FROM leads WHERE assigned_to IS NOT NULL "
        "AND (assigned_at IS NULL OR assigned_at='')").fetchone()[0]
    if not pending:
        return
    c.execute("""
        UPDATE leads SET assigned_at = (
            SELECT MAX(a.ts) FROM activity a
            WHERE a.lead_id = leads.id AND a.action = 'assign')
        WHERE assigned_to IS NOT NULL AND (assigned_at IS NULL OR assigned_at='')
          AND EXISTS (SELECT 1 FROM activity a
                      WHERE a.lead_id = leads.id AND a.action = 'assign')""")
    c.execute("""
        UPDATE leads SET assigned_at = updated_at
        WHERE assigned_to IS NOT NULL AND (assigned_at IS NULL OR assigned_at='')
          AND updated_at IS NOT NULL""")
    c.commit()
    done = c.execute(
        "SELECT COUNT(*) FROM leads WHERE assigned_to IS NOT NULL "
        "AND assigned_at IS NOT NULL AND assigned_at<>''").fetchone()[0]
    print(f"  backfilled assignment dates for {done:,} already-assigned leads")
