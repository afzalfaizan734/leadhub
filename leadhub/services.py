"""Application services - the use cases.

Each service coordinates repositories to fulfil one area of work (GRASP
Controller). HTTP knows nothing about SQL; SQL knows nothing about HTTP.
"""
import csv
import datetime
import os
import re
import sys
import threading

from . import clock, config, database, fmcsa, security
from .repositories import (ActivityRepository, CallLogRepository, INSERT_COLS,
                           LeadRepository, UserRepository, now)


class AuthError(Exception):
    def __init__(self, message, code=403):
        super().__init__(message)
        self.code = code


def _log_security(msg):
    """Append-only-ish security/audit trail to stderr (captured by the host's
    logs). Never logs PINs, hashes or tokens - only who/what/when."""
    try:
        print(f"[security] {now()} {msg}", file=sys.stderr, flush=True)
    except Exception:
        pass


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_changes(changes):
    """Whitelist editable fields AND validate their values before they are written
    or later rendered. This blocks data corruption and is the backend half of the
    stored-XSS defence (a status can only ever be one of the known values)."""
    if not isinstance(changes, dict):
        raise AuthError("Invalid update.", 400)
    out = {}
    for k, v in changes.items():
        if k not in config.EDITABLE_LEAD_FIELDS:
            continue
        if k == "status":
            if v not in config.STATUSES:
                raise AuthError("Unknown status.", 400)
        elif k == "disposition":
            if (v or "") not in config.DISPOSITIONS:
                raise AuthError("Unknown disposition.", 400)
        elif k == "call_attempts":
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise AuthError("Call attempts must be a number.", 400)
            if v < 0 or v > 100000:
                raise AuthError("Call attempts out of range.", 400)
        elif k in ("last_contact", "next_followup"):
            v = str(v or "")
            if v and not _DATE_RE.match(v):
                raise AuthError("Dates must look like YYYY-MM-DD.", 400)
        elif k == "notes":
            v = str(v or "")
            if len(v) > 10000:
                raise AuthError("That note is too long.", 400)
        out[k] = v
    return out


# --------------------------------------------------------------- accounts
class AccountService:
    def __init__(self, users: UserRepository, sessions: security.SessionStore,
                 lockout: security.LockoutPolicy):
        self.users, self.sessions, self.lockout = users, sessions, lockout

    def bootstrap_defaults(self):
        # Fresh install starts with a single Admin account; the admin then adds
        # their own team from the Team page.
        for name, role in (("Admin", "admin"),):
            self.users.ensure(name, role)

    def issue_missing_pins(self):
        """Give a random PIN to anyone who has none and reveal it ONCE on the
        console. Nothing is written to disk by default (set LEADHUB_WRITE_PIN_FILE=1
        only if you accept a plaintext file). Admin PINs are longer than agent PINs."""
        pending = self.users.without_pin()
        if not pending:
            return {}
        issued = {}
        for u in pending:
            digits = config.ADMIN_PIN_MIN if u["role"] == "admin" else config.PIN_MIN
            pin = security.random_pin(digits)
            self.users.set_pin(u["id"], pin)
            issued[u["name"]] = (pin, u["role"])
        lines = ["", "=" * 54,
                 "  LeadHub first-run PINs - shown ONCE, copy them now",
                 "  (not saved to disk; each person can change theirs after login)",
                 "=" * 54]
        for nm, (pin, role) in issued.items():
            lines.append(f"   {nm:<14} ({role:<5})  PIN: {pin}")
        lines.append("=" * 54 + "\n")
        print("\n".join(lines))
        if config._env_bool("LEADHUB_WRITE_PIN_FILE", False):
            with open(config.PIN_FILE, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            print(f"  (also written to {config.PIN_FILE} - delete it after handing out PINs)")
        return issued

    def login(self, name, pin, source=None):
        wait = self.lockout.locked_for(name, source)
        if wait:
            raise AuthError("Too many attempts. Please wait and try again.", 429)
        row = self.users.by_name(name)
        if not row or not security.verify_pin(row, pin):
            if not row:
                security.dummy_verify(pin)          # equalise timing
            self.lockout.record_failure(name, source)
            _log_security(f"login FAILED name={name!r} src={source}")
            raise AuthError("Wrong name or PIN.", 401)
        self.lockout.clear(name)
        self.users.touch_login(row["id"])
        token = self.sessions.create(row)
        _log_security(f"login OK user={row['name']!r} role={row['role']} src={source}")
        return token, {"id": row["id"], "name": row["name"], "role": row["role"]}

    def change_pin(self, user, old, new, token=None):
        row = self.users.by_id(user["id"])
        min_len = config.ADMIN_PIN_MIN if (row and row["role"] == "admin") else config.PIN_MIN
        if not security.is_valid_pin(new, min_len):
            raise AuthError(f"New PIN must be {min_len}-12 digits.", 400)
        key = f"changepin:{user['id']}"
        if self.lockout.locked_for(key):
            raise AuthError("Too many attempts. Please wait and try again.", 429)
        if not row or not security.verify_pin(row, old):
            self.lockout.record_failure(key)
            raise AuthError("Current PIN is wrong.", 401)
        if security.verify_pin(row, new):
            raise AuthError("New PIN must be different from the current one.", 400)
        self.lockout.clear(key)
        self.users.set_pin(user["id"], new)
        # Revoke the user's OTHER sessions; keep the one they are using right now.
        self.sessions.drop_user(user["id"], except_token=token)
        _log_security(f"changepin user={user.get('name')!r}")

    def reset_pin(self, target_id, new):
        row = self.users.by_id(target_id)
        if not row:
            raise AuthError("No such user.", 404)
        min_len = config.ADMIN_PIN_MIN if row["role"] == "admin" else config.PIN_MIN
        if not security.is_valid_pin(new, min_len):
            raise AuthError(f"PIN must be {min_len}-12 digits.", 400)
        self.users.set_pin(target_id, new)
        self.lockout.clear(row["name"])
        # A reset must actually lock out anyone holding an old token for this user.
        self.sessions.drop_user(target_id)
        _log_security(f"resetpin target={row['name']!r}")
        return row["name"]

    def create_user(self, name, role, pin):
        if not name:
            raise AuthError("Name is required.", 400)
        if role not in config.ROLES:
            raise AuthError("Unknown role.", 400)
        if self.users.by_name(name):
            # Never route creation through INSERT OR IGNORE onto an existing name -
            # that would silently overwrite that account's PIN (takeover).
            raise AuthError("That name is already taken.", 409)
        min_len = config.ADMIN_PIN_MIN if role == "admin" else config.PIN_MIN
        if not security.is_valid_pin(pin, min_len):
            raise AuthError(f"PIN must be {min_len}-12 digits.", 400)
        uid = self.users.ensure(name, role)
        self.users.set_pin(uid, pin)
        _log_security(f"create_user name={name!r} role={role}")
        return uid

    def delete_user(self, actor, target_id):
        """Remove a user. An admin cannot delete their own account (that would
        risk locking everyone out). The caller returns the user's leads to the
        pool first (see AssignmentService.withdraw_all)."""
        if actor["id"] == target_id:
            raise AuthError("You cannot delete your own account.", 400)
        row = self.users.by_id(target_id)
        if not row:
            raise AuthError("No such user.", 404)
        self.sessions.drop_user(target_id)      # end any of their live sessions
        self.users.delete(target_id)
        _log_security(f"delete_user name={row['name']!r} by={actor.get('name')!r}")
        return row["name"]


# --------------------------------------------------------------- leads
class LeadService:
    def __init__(self, leads: LeadRepository, activity: ActivityRepository,
                 calls: CallLogRepository = None):
        self.leads, self.activity = leads, activity
        self.calls = calls or CallLogRepository()

    def search_for(self, user, params):
        scope = params.get("scope") or "mine"
        if user["role"] != "admin":
            scope = "mine"                       # agents never see anyone else's work
        text = params.get("q") or None
        day = params.get("day") or None
        # A search is a hunt for one carrier, so it must not be boxed in by the
        # day the lead happened to be assigned.
        if text:
            day = None
        return self.leads.search(
            scope=scope, user_id=user["id"],
            status=params.get("status") or None,
            state=params.get("state") or None,
            bucket=params.get("bucket") or None,
            text=text,
            day=day,
            month=params.get("month") or None,
            number_format=params.get("format") or None,
            followup=params.get("followup") or None,
            fresh=params.get("fresh") == "1",
            limit=min(int(params.get("limit") or 300), 1000),
            offset=int(params.get("offset") or 0))

    def followup_counts(self, user):
        return self.leads.followup_counts(
            None if user["role"] == "admin" else user["id"])

    def call_log(self, user, lead_id):
        lead = self.leads.by_id(lead_id)
        if not lead:
            raise AuthError("Lead not found.", 404)
        if user["role"] != "admin" and lead["assigned_to"] != user["id"]:
            raise AuthError("That lead is not assigned to you.")
        return {"rows": self.calls.for_lead(lead_id)}

    def log_call(self, user, lead_id, outcome, note, bump=True):
        lead = self.leads.by_id(lead_id)
        if not lead:
            raise AuthError("Lead not found.", 404)
        if user["role"] != "admin" and lead["assigned_to"] != user["id"]:
            raise AuthError("That lead is not assigned to you.")
        outcome = outcome or ""
        if outcome and outcome not in config.DISPOSITIONS:
            raise AuthError("Unknown call outcome.", 400)
        if len(str(note or "")) > 10000:
            raise AuthError("That note is too long.", 400)
        if not outcome.strip() and not str(note or "").strip():
            raise AuthError("Write something before logging the call.", 400)
        entry = self.calls.add(lead_id, user["name"], outcome, note)
        updated = None
        if bump:
            attempts = (lead["call_attempts"] or 0) + 1
            updated = self.leads.update_fields(
                lead_id, {"call_attempts": attempts}, user["name"], self.activity)
        return {"entry": entry,
                "lead": dict(updated) if updated else dict(lead),
                "count": self.calls.count_for(lead_id)}

    def update(self, user, lead_id, changes):
        current = self.leads.by_id(lead_id)
        if not current:
            raise AuthError("Lead not found.", 404)
        if user["role"] != "admin" and current["assigned_to"] != user["id"]:
            raise AuthError("That lead is not assigned to you.")
        clean = _validate_changes(changes)
        updated = self.leads.update_fields(lead_id, clean, user["name"], self.activity)
        if updated is None:
            raise AuthError("Nothing to update.", 400)
        return dict(updated)


# --------------------------------------------------------------- assignment
class AssignmentService:
    def __init__(self, leads: LeadRepository, users: UserRepository,
                 activity: ActivityRepository):
        self.leads, self.users, self.activity = leads, users, activity

    def assign(self, actor, agent_id, *, count=0, ids=None, bucket=None,
               state=None, fresh=False, number_format=None):
        agent = self.users.by_id(agent_id)
        if not agent:
            raise AuthError("No such agent.", 404)
        if ids:
            picked = [int(i) for i in ids]
        else:
            if count <= 0:
                raise AuthError("Choose how many leads to assign.", 400)
            picked = self.leads.pick_unassigned(count, bucket=bucket, state=state,
                                                fresh=fresh,
                                                number_format=number_format)
        n = self.leads.assign(picked, agent_id, agent["name"], actor["name"], self.activity)
        return {"assigned": n, "agent": agent["name"]}

    def unassign(self, actor, ids):
        return {"unassigned": self.leads.unassign([int(i) for i in ids],
                                                  actor["name"], self.activity)}

    def withdraw_all(self, actor, agent_id):
        """Take every lead back from one agent and return them to the pool."""
        agent = self.users.by_id(agent_id)
        if not agent:
            raise AuthError("No such agent.", 404)
        ids = self.leads.ids_for_agent(agent_id)
        n = self.leads.unassign(ids, actor["name"], self.activity) if ids else 0
        return {"withdrawn": n, "agent": agent["name"]}


# --------------------------------------------------------------- reporting
class ReportService:
    def __init__(self, leads: LeadRepository, users: UserRepository,
                 activity: ActivityRepository):
        self.leads, self.users, self.activity = leads, users, activity

    def dashboard(self):
        return {"total": self.leads.count(),
                "unassigned": self.leads.unassigned_count(),
                "by_status": self.leads.status_totals(),
                "by_format": self.leads.format_totals(),
                "agents": self.leads.agent_summary()}

    def status_matrix(self):
        grouped = {}
        for r in self.leads.status_matrix():
            grouped.setdefault(r["owner"], {s: 0 for s in config.STATUSES})[r["st"]] = r["n"]
        rows = []
        for owner, counts in grouped.items():
            row = dict(counts)
            row["name"] = owner
            row["total"] = sum(counts.values())
            rows.append(row)
        rows.sort(key=lambda r: (r["name"] == "(unassigned)", -r["total"]))
        totals = {s: sum(r.get(s, 0) for r in rows) for s in config.STATUSES}
        totals.update(name="TOTAL", total=sum(totals.values()))
        return {"rows": rows, "totals": totals, "statuses": config.STATUSES}

    def buckets(self):
        """Live age bands: totals, what is free to assign, and who holds the rest."""
        raw = self.leads.bucket_matrix()
        owners = sorted({r["owner"] for r in raw if r["owner"] != "(unassigned)"})
        out = []
        for b in config.BUCKETS:
            rows = [r for r in raw if r["bucket"] == b["key"]]
            entry = {"key": b["key"], "label": b["label"],
                     "total": sum(r["n"] for r in rows),
                     "unassigned": sum(r["n"] for r in rows
                                       if r["owner"] == "(unassigned)"),
                     "by_agent": {o: 0 for o in owners}}
            for r in rows:
                if r["owner"] != "(unassigned)":
                    entry["by_agent"][r["owner"]] = r["n"]
            out.append(entry)
        return {"buckets": out, "agents": owners,
                "total": sum(b["total"] for b in out),
                "unassigned": sum(b["unassigned"] for b in out)}

    def bucket_detail(self, key):
        return self.leads.bucket_detail(key)

    def agent(self, uid):
        u = self.users.by_id(uid)
        if not u:
            raise AuthError("No such user.", 404)
        rep = self.leads.agent_report(uid, u["name"])
        rep["user"] = {"id": u["id"], "name": u["name"], "role": u["role"],
                       "last_login": u["last_login"] if "last_login" in u.keys() else None}
        rep["recent"] = self.activity.recent_for(u["name"])
        rep["buckets"] = config.BUCKETS
        return rep


# --------------------------------------------------------------- calendar
class CalendarService:
    """Month / day folders built from when leads were assigned.

    Folders are derived, never stored: a month or a day appears because leads
    were assigned then. Today's folder shows up on its own the moment the first
    lead is assigned, and is listed (empty) from the moment the server starts, so
    there is nothing to create or schedule.
    """

    MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    def __init__(self, leads: LeadRepository, users: UserRepository):
        self.leads, self.users = leads, users

    @classmethod
    def month_label(cls, ym):                      # "2026-09" -> "Sep 2026"
        try:
            y, m = ym.split("-")
            return f"{cls.MONTH_NAMES[int(m) - 1]} {y}"
        except Exception:
            return ym

    @staticmethod
    def day_label(ymd):                            # "2026-09-16" -> "16-09-26"
        try:
            y, m, d = ymd.split("-")
            return f"{d}-{m}-{y[2:]}"
        except Exception:
            return ymd

    def _scope_agent(self, user, agent_id):
        """Agents are pinned to themselves; admins may look at anyone."""
        if user["role"] != "admin":
            return user["id"]
        return int(agent_id) if agent_id else None

    def months(self, user, agent_id=None):
        who = self._scope_agent(user, agent_id)
        rows = self.leads.months(who)
        today = clock.today().strftime("%Y-%m")
        if not any(r["month"] == today for r in rows):
            rows.insert(0, {"month": today, "leads": 0, "days": 0, "agents": 0,
                            "worked": 0, "calls": 0})
        for r in rows:
            r["label"] = self.month_label(r["month"])
        return {"months": rows, "today": today,
                "agents": [a for a in self.users.all_active() if a["role"] == "agent"],
                "scoped_to": who}

    def days(self, user, month, agent_id=None):
        who = self._scope_agent(user, agent_id)
        raw = self.leads.days(month, who)
        grouped = {}
        for r in raw:
            g = grouped.setdefault(r["day"], {"day": r["day"],
                                              "label": self.day_label(r["day"]),
                                              "leads": 0, "worked": 0, "interested": 0,
                                              "closed": 0, "dnc": 0, "calls": 0,
                                              "agents": []})
            for k in ("leads", "worked", "interested", "closed", "dnc", "calls"):
                g[k] += r[k] or 0
            g["agents"].append({"id": r["agent_id"], "name": r["agent"],
                                "leads": r["leads"], "worked": r["worked"],
                                "interested": r["interested"], "calls": r["calls"]})
        days = sorted(grouped.values(), key=lambda d: d["day"], reverse=True)
        today = clock.today().strftime("%Y-%m-%d")
        if month == today[:7] and not any(d["day"] == today for d in days):
            days.insert(0, {"day": today, "label": self.day_label(today), "leads": 0,
                            "worked": 0, "interested": 0, "closed": 0, "dnc": 0,
                            "calls": 0, "agents": []})
        return {"month": month, "label": self.month_label(month),
                "days": days, "today": today}

    def day(self, user, day, agent_id=None):
        who = self._scope_agent(user, agent_id)
        return {"day": day, "label": self.day_label(day),
                "by_status": self.leads.day_status_split(day, who),
                "agents": self.leads.days(day[:7], who)}


# --------------------------------------------------------------- sync
class SyncService:
    """Pulls newly-active FMCSA authorities and inserts only unseen USDOTs."""

    def __init__(self, leads: LeadRepository, activity: ActivityRepository,
                 client: fmcsa.FmcsaClient = None):
        self.leads, self.activity = leads, activity
        self.client = client or fmcsa.FmcsaClient()
        self.state = {"running": False, "msg": "Not run yet", "added": 0,
                      "scanned": 0, "error": None, "at": None, "pct": 0}
        self._thread = None

    def status(self):
        return dict(self.state)

    @staticmethod
    def format_of(docket):
        """MOTUS numbers are the randomised 8-digit series introduced in 2026."""
        mc = docket[2:] if docket[:2].isalpha() else docket
        return "MOTUS" if len(mc) >= 8 else "LEGACY"

    def start(self, days=14, number_format=None):
        if self.state["running"]:
            raise AuthError("A sync is already running.", 409)
        days = max(1, min(int(days), 120))
        fmt = (number_format or "").strip().upper() or None
        if fmt and fmt not in ("MOTUS", "LEGACY"):
            raise AuthError("Unknown MC number type.", 400)
        self._thread = threading.Thread(target=self._run, args=(days, fmt), daemon=True)
        self._thread.start()

    def _set(self, **kw):
        self.state.update(kw)

    def _run(self, days, number_format=None):
        label = f"{number_format} " if number_format else ""
        try:
            self._set(running=True, error=None, added=0, scanned=0, pct=5,
                      msg="Asking FMCSA for recent authority grants...")
            active = self.client.recent_active_authorities(days)
            self._set(scanned=len(active), pct=20,
                      msg=f"{len(active):,} authorities went active in the last {days} days.")
            if not active:
                self._set(running=False, pct=100, at=now(), msg="Nothing new at FMCSA.")
                return

            have = self.leads.existing_usdots()
            self._set(pct=35, msg="Fetching carrier records...")
            carriers = self.client.carriers(active.keys())

            fresh, skipped_fmt = {}, 0
            for docket, rec in carriers.items():
                if not self.client.is_us_trucking_carrier(rec):
                    continue
                # Filter on MC number type here, before the enrichment lookups,
                # so we never pay for records we are going to throw away.
                if number_format and self.format_of(docket) != number_format:
                    skipped_fmt += 1
                    continue
                dot = (rec.get("usdot_number") or "").strip()
                if not dot or dot in have or dot in fresh:
                    continue
                rec["_active_on"] = active.get(docket, "")   # carried into the row
                fresh[dot] = (docket, rec)

            extra = f" ({skipped_fmt:,} skipped as not {number_format})" if number_format else ""
            self._set(pct=55, msg=f"{len(fresh):,} new {label}carriers{extra}. Enriching...")
            if not fresh:
                self._set(running=False, pct=100, at=now(),
                          msg=f"Checked FMCSA - no new {label}carriers you do not "
                              f"already have.")
                return

            dots = list(fresh)
            census = self.client.census(dots)
            self._set(pct=75, msg="Looking up email addresses...")
            emails = self.client.emails(dots)
            self._set(pct=88, msg="Checking insurance filings...")
            insurance = self.client.insurance([v[0] for v in fresh.values()])

            rows = [r for r in (self._to_row(d, dk, rec, census, emails, insurance)
                                for d, (dk, rec) in fresh.items()) if r]
            added = self.leads.bulk_insert(rows)
            total = self.leads.count()
            self._set(running=False, added=added, pct=100, at=now(),
                      msg=f"Added {added:,} new {label}leads. "
                          f"Database now holds {total:,}.")
        except Exception as exc:
            self._set(running=False, pct=100, at=now(), error=str(exc),
                      msg=f"Sync failed: {exc}")

    @staticmethod
    def _to_row(dot, docket, rec, census, emails, insurance):
        ce = census.get(dot, {})
        email = (emails.get(dot) or {}).get("email_address", "")
        phone = (rec.get("bus_telno") or "").strip() or (ce.get("phone") or "").strip()
        cell = (ce.get("cell_phone") or "").strip()
        if not phone and not cell and not email:
            return None
        ins = insurance.get(docket, {})
        mc = docket[2:] if docket[:2].isalpha() else docket
        active_on = rec.get("_active_on", "")
        return (dot, docket, mc, SyncService.format_of(docket),
                (rec.get("legal_name") or "").strip(),
                (rec.get("dba_name") or "").strip(),
                (ce.get("company_officer_1") or "").strip(),
                phone, cell, email,
                (rec.get("bus_street_po") or "").strip(),
                (rec.get("bus_city") or "").strip(),
                (rec.get("bus_state_code") or "").strip(),
                (rec.get("bus_zip_code") or "").strip(),
                (ce.get("power_units") or "").strip(),
                (ce.get("total_drivers") or "").strip(),
                active_on, None,
                (ins.get("insurance_company_name") or "").strip(),
                (ins.get("max_cov_amount") or "").strip(),
                "ON_FILE" if ins else "NONE_FOUND", now())


# --------------------------------------------------------------- seeding
class SeedService:
    """One-off CSV import, used only when the database is empty."""

    def __init__(self, leads: LeadRepository):
        self.leads = leads

    def seed_if_empty(self):
        if self.leads.count():
            return 0
        path = next((p for p in (config.SEED_CSV, config.LEGACY_SEED_CSV)
                     if os.path.exists(p)), None)
        if not path:
            print("  no seed CSV found - starting empty "
                  "(use 'Get new leads from FMCSA' on the Dashboard)")
            return 0
        print(f"  seeding from {os.path.basename(path)} ...")
        rows, seen = [], set()
        with open(path, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                when = (r.get("authority_active_date") or "").strip()
                if config.SEED_YEAR and when[:4] != config.SEED_YEAR:
                    continue
                dot = (r.get("usdot_number") or "").strip()
                if not dot or dot in seen:
                    continue
                phone = ((r.get("phone_motus") or "").strip()
                         or (r.get("phone_census") or "").strip())
                cell = (r.get("cell_phone") or "").strip()
                email = (r.get("email_address") or "").strip()
                if not phone and not cell and not email:
                    continue
                seen.add(dot)
                mc = (r.get("mc_number") or "").strip()
                rows.append((
                    dot, (r.get("docket_number") or "").strip(), mc,
                    "MOTUS" if len(mc) >= 8 else "LEGACY",
                    (r.get("legal_name") or "").strip(),
                    (r.get("dba_name") or "").strip(),
                    (r.get("company_officer_1") or "").strip(),
                    phone, cell, email,
                    (r.get("bus_street_po") or "").strip(),
                    (r.get("bus_city") or "").strip(),
                    (r.get("bus_state_code") or "").strip(),
                    (r.get("bus_zip_code") or "").strip(),
                    (r.get("power_units") or "").strip(),
                    (r.get("total_drivers") or "").strip(),
                    when, None,
                    (r.get("insurance_company_name") or "").strip(),
                    (r.get("max_cov_amount") or "").strip(),
                    (r.get("insurance_status") or "").strip(), now()))
        return self.leads.bulk_insert(rows)


# --------------------------------------------------------------- demo seed
def seed_demo(app):
    """Populate a public DEMO instance with realistic SAMPLE data (no real PII).
    Only seeds when the lead table is empty, so it is safe on every start."""
    if app.leads.count() > 0:
        return
    pin = config.DEMO_PIN

    def ensure(name, role):
        r = app.users.by_name(name)
        return r["id"] if r else app.accounts.create_user(name, role, pin)

    ensure("Ava Chen", "admin")
    ag = {n: ensure(n, "agent") for n in ("A. Rivera", "J. Chen", "M. Okafor")}

    today = clock.today()
    # (days_old, name, officer, city, state, phone, cell, trucks, drivers)
    data = [
        (3, "Redline Freight LLC", "Marcus Hale", "Dallas", "TX", "2142075512", "2149930148", "12", "14"),
        (2, "Gulf Coast Logistics", "Dana Pierce", "Tampa", "FL", "8135550191", "", "6", "7"),
        (5, "Summit Carriers Inc", "Rosa Mendez", "Phoenix", "AZ", "6025550148", "6025550190", "8", "9"),
        (6, "Lone Star Hauling", "Billy Grant", "Houston", "TX", "7135550133", "", "19", "22"),
        (9, "Cascade Freight Systems", "Priya Nair", "Seattle", "WA", "2065550177", "2065550121", "11", "13"),
        (12, "Midwest Express Carriers", "Tom Becker", "Chicago", "IL", "3125550164", "", "24", "27"),
        (14, "Ironwood Logistics", "Sam Carter", "Atlanta", "GA", "4045550155", "", "21", "24"),
        (22, "Blue Ridge Haulers", "Nina Vance", "Knoxville", "TN", "8655550139", "", "5", "6"),
        (18, "Sierra Transport Group", "Owen Diaz", "Reno", "NV", "7755550112", "7755550167", "9", "10"),
        (31, "Pacific Drayage Co", "Grace Lim", "Fresno", "CA", "5595550144", "", "17", "19"),
        (27, "Empire Freight Lines", "Victor Ross", "Buffalo", "NY", "7165550128", "", "31", "35"),
        (44, "Granite State Transport", "Beth Cole", "Manchester", "NH", "6035550176", "", "3", "4"),
        (7, "Copper Valley Carriers", "Luis Romo", "Tucson", "AZ", "5205550183", "", "7", "8"),
        (10, "Harbor Point Logistics", "Mia Tran", "Oakland", "CA", "5105550159", "5105550102", "14", "16"),
    ]
    rows = []
    for i, (days, name, off, city, st, ph, cell, pu, dr) in enumerate(data):
        ad = (today - datetime.timedelta(days=days)).strftime("%Y%m%d")
        row = {c: "" for c in INSERT_COLS}
        row.update(usdot=f"90000{i:03d}", docket=f"MC090000{i:03d}", mc_number=f"090000{i:03d}",
                   number_format="MOTUS", legal_name=name, officer=off, phone=ph, cell=cell,
                   email="dispatch@" + name.split()[0].lower() + ".example", street="1200 Freight Way",
                   city=city, state=st, zip="00000", power_units=pu, drivers=dr, authority_date=ad,
                   insurance_company="Great West Casualty", coverage="1,000,000",
                   insurance_status="ON_FILE", created_at=now())
        rows.append(tuple(row[c] for c in INSERT_COLS))
    app.leads.bulk_insert(rows)

    with database.connect() as c:
        ids = [r[0] for r in c.execute("SELECT id FROM leads ORDER BY id")]
    app.leads.assign(ids[0:5], ag["A. Rivera"], "A. Rivera", "Ava Chen", app.activity)
    app.leads.assign(ids[5:9], ag["J. Chen"], "J. Chen", "Ava Chen", app.activity)
    app.leads.assign(ids[9:12], ag["M. Okafor"], "M. Okafor", "Ava Chen", app.activity)
    prog = {ids[0]: ("Interested", 4, "Owner keen, wants a quote Monday"),
            ids[2]: ("Contacted", 2, "Left message with dispatcher"),
            ids[3]: ("Interested", 3, "Hauling reefer out of Houston"),
            ids[5]: ("Attempted", 1, ""), ids[6]: ("Attempted", 2, ""),
            ids[9]: ("Not Interested", 1, "Using in-house dispatch"),
            ids[10]: ("Closed", 5, "Signed - 3 trucks"), ids[11]: ("DNC", 1, "Asked not to call")}
    for lid, (stt, calls, note) in prog.items():
        app.leads.update_fields(lid, {"status": stt, "call_attempts": calls, "notes": note},
                                "A. Rivera", app.activity)
