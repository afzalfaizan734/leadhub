"""Browser E2E + security + a11y test. Serves the app on a throwaway dev DB and
drives real Chromium through the admin and agent flows, asserts no JS errors,
verifies stored-XSS is neutralised, runs an axe accessibility scan, and captures
responsive light/dark screenshots.

    pip install -r requirements-dev.txt && python -m playwright install chromium
    python tests/test_e2e.py
"""
import os
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
DEV = tempfile.mkdtemp(prefix="leadhub-e2e-")
SHOTS = tempfile.mkdtemp(prefix="leadhub-shots-")
os.environ.update({
    "LEADHUB_DATA_DIR": DEV, "LEADHUB_BIND": "127.0.0.1",
    "LEADHUB_PORT": "8153", "LEADHUB_FALLBACK_PORT": "8154",
    # allow inline script so axe-core can be injected for the a11y scan
    "LEADHUB_CSP": "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                   "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
})
sys.path.insert(0, APP)

from leadhub import api, database                     # noqa: E402
from leadhub.repositories import INSERT_COLS, now     # noqa: E402

database.initialise()
app = api.Container()
admin_id = app.accounts.create_user("Boss", "admin", "135790")
agent_id = app.accounts.create_user("Dev", "agent", "2468")

XSS_NAME = '<img src=x onerror="window.__xss=1">Pwn Freight'
def mk(usdot, name, extra=None):
    row = {c: "" for c in INSERT_COLS}
    row.update(usdot=usdot, docket="MC" + usdot, mc_number=usdot, number_format="MOTUS",
               legal_name=name, officer="Sample Officer", phone="5550000000", cell="5551111111",
               email="sample@example.com", city="Dallas", state="TX", power_units="10", drivers="12",
               authority_date="20261001", insurance_company="Acme Ins", insurance_status="ON_FILE",
               created_at=now())
    if extra:
        row.update(extra)
    return tuple(row[c] for c in INSERT_COLS)

app.leads.bulk_insert([
    mk("90000001", "Alpha Freight Sample"), mk("90000002", "Beta Carriers Sample"),
    mk("90000003", XSS_NAME, {"notes": "<script>window.__xss=1</script>note"}),
    mk("90000004", "Delta Logistics Sample"),
])
with database.connect() as c:
    ids = [r[0] for r in c.execute("SELECT id FROM leads ORDER BY id")]
app.leads.assign(ids[:3], agent_id, "Dev", "Boss", app.activity)

server, port = api.create_server(app)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{port}"

results = []
def check(name, ok, extra=""):
    results.append((name, bool(ok), extra))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {extra}" if extra else ""))

from playwright.sync_api import sync_playwright        # noqa: E402

errors = []
with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_context(viewport={"width": 1440, "height": 900}).new_page()
    page.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))
    page.on("console", lambda m: errors.append("console.error: " + m.text) if m.type == "error" else None)

    def login(name, pin):
        page.goto(BASE, wait_until="domcontentloaded")
        page.wait_for_selector(f"button.ubtn:has-text('{name}')", timeout=10000)
        page.click(f"button.ubtn:has-text('{name}')")
        page.fill("#pinBox", pin)
        page.click("#btnSignin")

    login("Boss", "135790")
    page.wait_for_selector("#vDash:not(.hide)", timeout=8000)
    check("admin dashboard loads", page.locator("#stats .stat").count() >= 4)
    check("pipeline matrix renders", page.locator("#matrixTbl tr").count() >= 1)
    page.screenshot(path=os.path.join(SHOTS, "01-admin-dashboard-light.png"))
    page.click("#btnTheme"); page.wait_for_timeout(250)
    check("dark mode applies", page.evaluate("document.documentElement.getAttribute('data-theme')") == "dark")
    page.screenshot(path=os.path.join(SHOTS, "02-admin-dashboard-dark.png"))
    page.click("#btnTheme"); page.wait_for_timeout(150)

    page.click("#navWrap a[data-view='leads']")
    page.wait_for_selector("#tbl tbody tr", timeout=5000)
    check("leads table renders", page.locator("#tbl tbody tr").count() >= 1)
    check("stored XSS did NOT execute", not page.evaluate("window.__xss"))
    check("malicious markup escaped", page.locator("#tbl img").count() == 0 and "&lt;img" in page.locator("#tbl").inner_html())
    page.screenshot(path=os.path.join(SHOTS, "03-admin-leads-light.png"))

    page.click("#tbl tbody tr:first-child")
    page.wait_for_selector("#saveBtn", timeout=5000)
    page.select_option("#dStatus", "Interested")
    page.fill("#dNotes", "E2E note")
    page.click("#saveBtn")
    page.wait_for_selector("#toast.on", timeout=5000)
    check("lead save shows toast", page.locator("#toast").inner_text().strip() != "")
    page.wait_for_timeout(400)
    page.click("#tbl tbody tr:first-child")
    page.wait_for_selector("#logBtn", timeout=5000)
    page.fill("#lgNote", "Spoke to owner")
    page.click("#logBtn")
    page.wait_for_timeout(600)
    check("call log entry appears", page.locator("#logList .logitem").count() >= 1)

    page.click("#navWrap a[data-view='team']")
    page.wait_for_selector("#vTeam:not(.hide)", timeout=5000)
    page.fill("#nuName", "E2EHire"); page.select_option("#nuRole", "agent"); page.fill("#nuPin", "4455")
    page.click("#btnAddUser"); page.wait_for_timeout(700)
    check("create user succeeds", "created" in page.locator("#nuMsg").inner_text().lower())

    page.click("#navWrap a[data-view='buckets']")
    page.wait_for_selector("#bkTbl tr", timeout=5000)
    check("lead groups render", page.locator("#bkTbl tr").count() >= 1)

    try:
        from axe_playwright_python.sync_playwright import Axe
        page.click("#navWrap a[data-view='dash']")
        page.wait_for_selector("#vDash:not(.hide)", timeout=5000)
        res = Axe().run(page)
        viol = res.response.get("violations", []) if hasattr(res, "response") else res.get("violations", [])
        serious = [v for v in viol if v.get("impact") in ("serious", "critical")]
        for v in serious:
            print(f"      a11y[{v.get('impact')}]: {v.get('id')} ({len(v.get('nodes', []))} nodes)")
        check("no serious/critical a11y violations", len(serious) == 0, f"{len(serious)} serious")
    except Exception as e:
        check("axe a11y scan ran", False, f"axe error: {e}")

    page.click("#navWrap a[data-view='leads']")
    page.wait_for_selector("#vLeads:not(.hide)", timeout=5000)
    for w, h, tag in [(1440, 900, "desktop"), (768, 1024, "tablet"), (375, 800, "mobile")]:
        page.set_viewport_size({"width": w, "height": h}); page.wait_for_timeout(300)
        page.screenshot(path=os.path.join(SHOTS, f"04-leads-{tag}-{w}px.png"))
    check("responsive screenshots captured", True)
    page.set_viewport_size({"width": 1440, "height": 900})

    page.click("#btnLogout"); page.wait_for_load_state("load"); page.wait_for_timeout(500)
    check("logout clears session token", page.evaluate("localStorage.getItem('leadhub_token')") is None)
    login("Dev", "2468")
    page.wait_for_selector("#vLeads:not(.hide)", timeout=8000)
    check("agent cannot see admin nav",
          page.locator("#navWrap a[data-view='dash']").is_hidden() and page.locator("#navWrap a[data-view='team']").is_hidden())
    page.wait_for_selector("#tbl tbody tr", timeout=5000)
    check("agent sees own leads", page.locator("#tbl tbody tr").count() >= 1)
    page.screenshot(path=os.path.join(SHOTS, "05-agent-myleads.png"))
    check("no JS/page errors during flows", len(errors) == 0, "; ".join(errors[:4]))
    browser.close()

server.shutdown()
fails = [r for r in results if not r[1]]
print(f"\n==== E2E: {len(results)-len(fails)}/{len(results)} passed, {len(fails)} failed ====")
print(f"screenshots in {SHOTS}")
for e in errors[:8]:
    print("  " + e)
sys.exit(1 if fails else 0)
