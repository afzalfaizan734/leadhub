# LeadHub tests

Automated tests for the hardened/redesigned LeadHub. They run the **real** server
against a throwaway dev database on `127.0.0.1` — they never touch any live data.

## Setup

```
pip install -r ../requirements-dev.txt
python -m playwright install chromium
```

## Run

```
python tests/test_security.py   # backend: auth, authorization, validation, headers, lockout
python tests/test_e2e.py        # browser: full admin + agent flows, stored-XSS, axe a11y, responsive
```

Both print a PASS/FAIL summary and exit non-zero on failure (CI-friendly).

## What they cover

**test_security.py** — security headers present; neutral server banner; unauthenticated
and tampered-token requests rejected (401); agent scope forced server-side; agents blocked
from admin routes (403); IDOR blocked; lead field *values* validated (bad status / negative
counts rejected); `create_user` name-collision rejected (409); admin PIN-length policy;
sessions invalidated on PIN reset; generic login errors; brute-force lockout (429).

**test_e2e.py** — admin and agent flows in real Chromium with **zero JS errors**; stored-XSS
payload rendered inert (escaped, no injected `<img>`, no script execution); axe accessibility
scan with **no serious/critical** violations; responsive screenshots at 375 / 768 / 1440 px;
light and dark mode; login/logout/session handling.
