"""Backend smoke + security test: runs the real server on a throwaway dev DB and
exercises auth, authorization, validation and headers over HTTP.

    python tests/test_security.py
"""
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
DEV = tempfile.mkdtemp(prefix="leadhub-sec-")
os.environ.update({
    "LEADHUB_DATA_DIR": DEV, "LEADHUB_BIND": "127.0.0.1",
    "LEADHUB_PORT": "8151", "LEADHUB_FALLBACK_PORT": "8152",
    "LEADHUB_MAX_PIN_FAILS": "5", "LEADHUB_PIN_LOCK_SECONDS": "120",
})
sys.path.insert(0, APP)

from leadhub import api, database                     # noqa: E402
from leadhub.repositories import INSERT_COLS, now     # noqa: E402

results = []
def check(name, ok, extra=""):
    results.append((name, bool(ok), extra))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {extra}" if extra else ""))

database.initialise()
app = api.Container()
admin_id = app.accounts.create_user("Boss", "admin", "135790")
agent_id = app.accounts.create_user("Dev", "agent", "2468")

def mk(usdot, name):
    row = {c: "" for c in INSERT_COLS}
    row.update(usdot=usdot, docket="MC" + usdot, mc_number=usdot, number_format="MOTUS",
               legal_name=name, phone="5550000000", state="TX",
               authority_date="20261001", insurance_status="ON_FILE", created_at=now())
    return tuple(row[c] for c in INSERT_COLS)

app.leads.bulk_insert([mk("90000001", "Alpha Sample"), mk("90000002", "Beta Sample")])
with database.connect() as c:
    lead_a = c.execute("SELECT id FROM leads WHERE usdot='90000001'").fetchone()[0]
    lead_b = c.execute("SELECT id FROM leads WHERE usdot='90000002'").fetchone()[0]
app.leads.assign([lead_a], agent_id, "Dev", "Boss", app.activity)

server, port = api.create_server(app)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{port}"

def req(method, path, token=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("X-Token", token)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, dict(resp.headers), resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode()

def jb(s):
    try:
        return json.loads(s)
    except Exception:
        return {}

try:
    st, hd, bd = req("GET", "/healthz")
    check("healthz 200", st == 200 and jb(bd).get("ok") is True)
    check("CSP header present", "Content-Security-Policy" in hd)
    check("X-Frame-Options=DENY", hd.get("X-Frame-Options") == "DENY")
    check("nosniff", hd.get("X-Content-Type-Options") == "nosniff")
    check("no Python version in Server banner", "Python" not in hd.get("Server", ""))

    st, hd, bd = req("GET", "/api/bootstrap")
    b = jb(bd)
    check("bootstrap has brand", b.get("brand", {}).get("name") == "LeadHub")

    check("unauth /api/leads -> 401", req("GET", "/api/leads")[0] == 401)
    check("bad token /api/stats -> 401", req("GET", "/api/stats", token="x")[0] == 401)

    atok = jb(req("POST", "/api/login", body={"name": "Boss", "pin": "135790"})[2]).get("token")
    check("admin login OK", bool(atok))
    gtok = jb(req("POST", "/api/login", body={"name": "Dev", "pin": "2468"})[2]).get("token")
    check("agent login OK", bool(gtok))

    rows = jb(req("GET", "/api/leads?scope=all", token=gtok)[2]).get("rows", [])
    check("agent scope forced to own", len(rows) == 1 and rows[0]["id"] == lead_a)
    check("agent blocked from /api/stats -> 403", req("GET", "/api/stats", token=gtok)[0] == 403)
    check("agent IDOR on other lead -> 403",
          req("POST", f"/api/lead/{lead_b}", token=gtok, body={"status": "Interested"})[0] == 403)

    check("bogus status rejected -> 400",
          req("POST", f"/api/lead/{lead_a}", token=atok, body={"status": "<img onerror=x>"})[0] == 400)
    check("valid status accepted -> 200",
          req("POST", f"/api/lead/{lead_a}", token=atok, body={"status": "Interested"})[0] == 200)
    check("negative call_attempts rejected -> 400",
          req("POST", f"/api/lead/{lead_a}", token=atok, body={"call_attempts": -5})[0] == 400)

    check("duplicate user -> 409",
          req("POST", "/api/user", token=atok, body={"name": "Boss", "role": "agent", "pin": "9999"})[0] == 409)
    check("short admin PIN -> 400",
          req("POST", "/api/user", token=atok, body={"name": "New", "role": "admin", "pin": "1234"})[0] == 400)

    check("reset agent PIN -> 200",
          req("POST", "/api/resetpin", token=atok, body={"user_id": agent_id, "pin": "7777"})[0] == 200)
    check("agent old token invalidated -> 401", req("GET", "/api/leads", token=gtok)[0] == 401)

    msg = jb(req("POST", "/api/login", body={"name": "Boss", "pin": "000000"})[2]).get("error", "")
    check("generic login error (no attempts leak)", "attempt" not in msg.lower())

    codes = [req("POST", "/api/login", body={"name": "Ghost", "pin": f"00000{i}"})[0] for i in range(7)]
    check("lockout 429 seen", 429 in codes, f"codes={codes}")
finally:
    server.shutdown()

fails = [r for r in results if not r[1]]
print(f"\n==== {len(results)-len(fails)}/{len(results)} passed, {len(fails)} failed ====")
sys.exit(1 if fails else 0)
