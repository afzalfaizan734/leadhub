# LeadHub

A small shared lead-management server for a truck-dispatch team. One admin assigns
leads, agents work them, everyone sees the same data, and new carriers are pulled
straight from FMCSA with a button.

Runs on one PC on the office network. No installer, no internet dependency for
day-to-day use, no monthly bill.

---

## Running it

1. Double-click **`Start-LeadHub.bat`** and leave the window open.
2. On this PC open `http://localhost`
3. Everyone else opens **`http://leadmanager`**

First run creates `leadhub.db` and writes everyone's PIN to `INITIAL-PINS.txt`.
Hand out the PINs, then delete that file.

| Command | What it does |
| --- | --- |
| `python app.py` | Start the server |
| `python app.py --resetpins` | Issue brand-new PINs for everyone |
| `python app.py --reseed` | Import the seed CSV again (only when the DB is empty) |

### The `leadmanager` name

Run **`Setup-Name.bat` as administrator, once, on the server PC only**. It renames
this PC to `LEADMANAGER`, opens the firewall and restarts. After that everyone uses
`http://leadmanager`.

Until then, `http://<computer-name>` works — the server prints it on startup.

The name follows the PC, so the router handing out a different IP changes nothing.
Nothing to edit on the agents' machines, ever.

**Why a rename and not an alias.** An SMB alias
(`LanmanServer\Parameters\OptionalNames`) registers only the NetBIOS `<20>`
file-server record. Browsers resolve hostnames from the `<00>` workstation record,
which an alias never creates — so `http://leadmanager` fails with
`ERR_NAME_NOT_RESOLVED` while `\\leadmanager\share` would have worked. Renaming the
machine registers both.

**If a VPN is running** (Hotspot Shield and friends), it can capture DNS and break
local name lookups. If the name stops resolving on an agent's PC, disconnect the VPN
or fall back to the IP address shown in the server window.

---

## Moving it to another PC

Copy the whole `LeadHub` folder. That's it.

* Everything lives in the folder, including the database (`leadhub.db`).
* Pure Python standard library — nothing to `pip install`.
* Needs Python 3.9+.
* On the new PC run `Setup-Name.bat` as admin if you want the `leadmanager` name.

**Back up `leadhub.db`.** It holds every lead, assignment, note and call outcome.

---

## How it is put together

```
LeadHub/
├─ app.py                  entry point only: wire up, print URLs, serve
├─ leadhub/
│  ├─ config.py            settings + domain vocabulary (single source of truth)
│  ├─ database.py          connection, schema, additive migrations
│  ├─ security.py          PIN hashing, sessions, lockout
│  ├─ repositories.py      data access, one class per table
│  ├─ services.py          the use cases
│  ├─ fmcsa.py             gateway to FMCSA's open-data API
│  ├─ api.py               HTTP routing + auth guards + composition root
│  └─ web/index.html       the whole UI, one file, no build step
├─ leadhub.db              your data
├─ Start-LeadHub.bat
└─ Setup-Name.bat          run as admin once
```

Dependencies point one way only: `api → services → repositories → database`.

**SOLID.** Each module has one reason to change — `security.py` for auth rules,
`fmcsa.py` for the external API, `repositories.py` for queries. Services depend on
repository objects handed to them by the composition root in `api.py`, not on
globals, so any of them can be swapped or faked in a test. Adding a status or an
age band means editing `config.py` and nothing else.

**GRASP.** Repositories are the Information Expert for their table. Services are
Controllers for a use case. `fmcsa.py` is Pure Fabrication shielding everything
else from an API we do not own (Protected Variations). HTTP knows no SQL and SQL
knows no HTTP (Low Coupling, High Cohesion).

---

## Lead age groups

Leads are grouped by how long ago their operating authority went active:
**0-7, 8-30, 31-60, 61-90, 91+ days**, plus **No date**.

These are **computed at read time from `authority_date`, never stored**. A lead
sitting in *0-7 days* today lands in *8-30 days* tomorrow on its own. There is no
nightly job and nothing to re-run. Change the bands in `config.BUCKETS` and the
tables, filters and assignment all follow.

Admin → **Lead Groups** shows each band, how many are free to assign, and who holds
the rest. Assign a chosen number from a chosen band straight to an agent.

---

## Roles

**Admin** — dashboard, lead groups, all leads, team management, FMCSA sync,
assignment, per-agent reports.

**Agent** — only the leads assigned to them. Enforced server-side: asking for
another scope is ignored, and editing someone else's lead is refused.

PINs are stored as PBKDF2-SHA256 with a per-user random salt, so **nobody can read
them back** — not an admin, not whoever copies the database file. If someone forgets
theirs, an admin sets a new one from the Team page. Five wrong attempts locks that
one account for two minutes.

---

## Where the data comes from

All free, public, no API key, from `data.transportation.gov`:

| Dataset | ID | Used for |
| --- | --- | --- |
| Motus AuthHist | `yu5v-wbh6` | Authority grants, with dates |
| Motus Carrier | `inys-ebih` | Name, address, phone, authority type/status |
| Motus Insur | `c5y8-a4uz` | Insurer, policy number, coverage |
| Company Census | `az4n-8mr2` | Cell phone, company officer, fleet size (daily) |
| SMS Census | `kjg3-diqy` | Email address (monthly) |

Dashboard → **Get new leads from FMCSA** pulls authorities that went active in the
chosen window, keeps active US trucking carriers only, **drops any USDOT already in
the database**, enriches the rest, and inserts. Runs in the background; typically
under a minute. Safe to run repeatedly — it never creates a duplicate.

Email coverage is thinner on the freshest carriers because FMCSA only republishes
the email file monthly. They fill in on later syncs.

---

## Click-to-dial

Phone numbers open RingCentral via `rcapp://r/call?number=+1XXXXXXXXXX`.

`tel:` is deliberately **not** used — on this machine Windows maps `tel:` to
Microsoft Lync, so those links would open the wrong app. If Excel or the browser
prompts on first click, `Enable-RingCentral-Dialing.reg` (in the parent folder)
trusts the `rcapp:` protocol.

---

## Known limits

* **Plain HTTP on the local network.** Fine behind your own router; do not expose
  this to the internet without TLS and real passwords first.
* **Sessions live in memory**, so restarting the server signs everyone out. No data
  is lost — edits save immediately.
* **Four-digit PINs** are only 10,000 combinations; the lockout is what makes them
  safe. Adequate for a trusted office, not for public exposure.
* **One server PC.** If it is off or asleep, nobody can work.
* Search runs `LIKE` across seven columns, so it is a scan. Fine at tens of
  thousands of rows; it would need a full-text index well beyond that.
