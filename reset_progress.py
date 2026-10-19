"""Clear all assignments and call progress, keeping the leads themselves.

DANGEROUS: wipes every status, disposition, note, follow-up, call count and
assignment, and clears the activity log, for EVERY lead. It now takes a
timestamped backup first and requires an explicit typed confirmation, so a stray
run cannot silently destroy work. Restore from the printed backup if needed.

Run:  python reset_progress.py
"""
import datetime
import os
import sqlite3
import sys

from leadhub import config

DB = config.DB_PATH
if not os.path.exists(DB):
    print("No database found at", DB)
    sys.exit(1)

print("This will RESET all progress (assignments, statuses, dispositions, notes,")
print("follow-ups, call counts) and DELETE the activity log for EVERY lead in:")
print("   ", DB)
try:
    ans = input('Type "RESET" to continue, anything else to cancel: ').strip()
except EOFError:
    ans = ""
if ans != "RESET":
    print("Cancelled - nothing was changed.")
    sys.exit(0)

# Consistent backup BEFORE touching anything (safe even with the server running).
ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
bak = os.path.join(config.DATA_DIR, f"leadhub.db.backup-{ts}-pre-reset")
src = sqlite3.connect(DB)
dst = sqlite3.connect(bak)
with dst:
    src.backup(dst)
src.close()
dst.close()
print("Backup written:", bak)

c = sqlite3.connect(DB, timeout=30)
c.execute("PRAGMA journal_mode=WAL")
c.execute("""UPDATE leads SET assigned_to=NULL, assigned_at=NULL, status='New',
             disposition='', notes='', last_contact='', next_followup='',
             call_attempts=0, updated_at=NULL, updated_by=NULL""")
c.execute("DELETE FROM activity")
c.commit()
total = c.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
free = c.execute("SELECT COUNT(*) FROM leads WHERE assigned_to IS NULL").fetchone()[0]
c.close()
print(f"reset complete: {free:,} unassigned of {total:,} leads")
print(f"(a backup of the pre-reset state is at {bak})")
