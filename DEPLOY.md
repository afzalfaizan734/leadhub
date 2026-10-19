# LeadHub — Deployment & Handover

This is the hardened, redesigned LeadHub. It keeps every original feature; it adds
security, a new UI, and the ability to run on the public internet over HTTPS.

> **Important:** this app is a long-running server with a local database. It does
> **not** run on Vercel or Netlify (those are for static sites / short serverless
> functions). Deploy it on an **always-on host**. Render is the simplest; the same
> Docker image runs on Railway, Fly.io, or any VPS.

---

## 1. One-time prerequisites

1. Install **Git for Windows** (https://git-scm.com/download/win) and create a free
   **GitHub** account.
2. Put this `LeadHub-v2` folder in a new GitHub repository:
   ```
   cd "LeadHub-v2"
   git init
   git add -A
   git commit -m "LeadHub v2"
   git branch -M main
   git remote add origin https://github.com/<you>/leadhub.git
   git push -u origin main
   ```
   The `.gitignore` already keeps the database, PIN files and backups out of Git.

## 2. Deploy on Render (recommended)

1. Go to https://render.com → **New** → **Blueprint**, and pick your GitHub repo.
   Render reads `render.yaml` and creates the service with a persistent `/data`
   disk for the database.
2. Choose the **Starter** plan ($7/mo) so it stays awake. (The Free plan works for
   a demo but sleeps after 15 minutes and the first visit then takes ~1 minute.)
3. First deploy: open the service **Logs**. On first run LeadHub prints each
   account's PIN **once** (it is never saved to disk). Copy them, then:
   - Sign in as **Admin**, click **PIN**, and set a new 6-digit admin PIN.
   - Add your real team under **Team**; give each person their own PIN.

## 3. Connect the subdomain + HTTPS

1. In Render → your service → **Settings → Custom Domains** → add
   `app.championlogisticworldwideinc.online`.
2. Render shows a target hostname. At your DNS provider (where
   `championlogisticworldwideinc.online` is managed) add:

   | Type  | Name  | Value                         |
   |-------|-------|-------------------------------|
   | CNAME | `app` | `<your-service>.onrender.com` |

   (Your main website on the root domain is untouched.)
3. HTTPS is automatic (Render issues a Let's Encrypt certificate). With
   `LEADHUB_ENFORCE_HTTPS=1` the app also sends HSTS.

## 4. Environment variables (all optional; safe defaults shown)

| Variable | Default | Purpose |
|---|---|---|
| `LEADHUB_DATA_DIR` | `/data` | where the SQLite DB + first-run files live (keep off the web root) |
| `LEADHUB_ENFORCE_HTTPS` | `0` | set `1` in production → sends HSTS |
| `LEADHUB_ADMIN_PIN_MIN` | `6` | minimum admin PIN length |
| `LEADHUB_BRAND_NAME` / `LEADHUB_BRAND_ACCENT` | `LeadHub` / `#4f4ff0` | per-client branding (shown on login + app) |
| `PORT` | (host-injected) | the port to bind; Render sets this automatically |

See `.env.example` for the full list.

## 5. Backups

- The database is a single file under `/data`. On Render, the persistent disk can
  be snapshotted; also schedule a copy off-box.
- A safe, consistent copy (even while running) is made by SQLite's backup API —
  the same method `reset_progress.py` uses before it wipes anything.
- Keep backups **off** any public/web folder (they contain contact data).

## 6. Per-client deployments (multi-company)

Until shared multi-tenancy is built, give **each dispatch company its own Render
service + its own `/data` disk** (one database per company = complete isolation).
Set that client's `LEADHUB_BRAND_NAME` / `LEADHUB_BRAND_ACCENT` and point their own
subdomain at their service.

## 7. Running the tests

```
pip install -r requirements-dev.txt
python -m playwright install chromium
python tests/test_security.py     # backend auth/authz/validation (23 checks)
python tests/test_e2e.py          # real-browser flows + XSS + a11y + responsive
```

## 8. Local / office-LAN mode (unchanged)

Double-click `Start-LeadHub.bat` and open `http://localhost`. All the original LAN
behaviour still works; the hardening and new UI apply there too.
