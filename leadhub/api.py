"""HTTP delivery layer: routing, auth guards, JSON in / JSON out.

Holds no business rules - every route delegates to a service. Adds transport
hardening here (security headers, body limits, generic errors, Origin checks)
without changing any route's behaviour or contract.
"""
import json
import mimetypes
import os
import socket
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import config, security
from .repositories import (ActivityRepository, CallLogRepository, LeadRepository,
                           UserRepository)
from .services import (AccountService, AssignmentService, AuthError, CalendarService,
                       LeadService, ReportService, SeedService, SyncService)


class Container:
    """Composition root - builds the object graph once (Dependency Inversion)."""

    def __init__(self):
        self.users = UserRepository()
        self.leads = LeadRepository()
        self.activity = ActivityRepository()
        self.calls = CallLogRepository()
        self.sessions = security.SessionStore()
        self.lockout = security.LockoutPolicy()
        self.accounts = AccountService(self.users, self.sessions, self.lockout)
        self.lead_service = LeadService(self.leads, self.activity, self.calls)
        self.assignment = AssignmentService(self.leads, self.users, self.activity)
        self.reports = ReportService(self.leads, self.users, self.activity)
        self.calendar = CalendarService(self.leads, self.users)
        self.sync = SyncService(self.leads, self.activity)
        self.seed = SeedService(self.leads)


def build_handler(app: Container):

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        # Neutral Server header: no Python version disclosure.
        def version_string(self):
            return "LeadHub"

        # Keep ordinary request logging quiet; security events are audited in the
        # service layer, and unexpected errors are printed from the handlers below.
        def log_message(self, *a):
            pass

        # ---------------- helpers
        def _security_headers(self, ctype):
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Permissions-Policy",
                             "geolocation=(), microphone=(), camera=(), payment=()")
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            # CSP matters for HTML; harmless on JSON. Applied uniformly.
            self.send_header("Content-Security-Policy", config.CSP)
            if config.ENFORCE_HTTPS:
                self.send_header("Strict-Transport-Security",
                                 "max-age=31536000; includeSubDomains")

        def _send(self, payload, code=200, ctype="application/json; charset=utf-8"):
            body = payload if isinstance(payload, bytes) else \
                json.dumps(payload, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self._security_headers(ctype)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _read_raw(self):
            """Read (and thus consume) the whole request body once, with a size
            cap. Always calling this on POST prevents an unread body from
            desyncing the next request on a kept-alive connection."""
            n = int(self.headers.get("Content-Length") or 0)
            if n < 0:
                n = 0
            if n > config.MAX_BODY_BYTES:
                self.close_connection = True   # don't read a huge body; drop the connection
                raise AuthError("Request body is too large.", 413)
            return self.rfile.read(n) if n else b""

        def _body(self):
            try:
                data = json.loads(self._raw_body or b"{}")
            except ValueError:
                raise AuthError("Request body must be valid JSON.", 400)
            if not isinstance(data, dict):
                raise AuthError("Request body must be a JSON object.", 400)
            return data

        def _check_origin(self):
            """Defence-in-depth CSRF check: a cross-site Origin on a state-changing
            request is rejected. Same-origin (and non-browser, Origin-less) requests
            pass. The primary CSRF defence is the custom X-Token header."""
            origin = self.headers.get("Origin")
            if not origin:
                return
            netloc = urlparse(origin).netloc
            host = self.headers.get("Host", "")
            if netloc and netloc != host and origin not in config.ALLOWED_ORIGINS:
                raise AuthError("Cross-site request blocked.", 403)

        def _client_ip(self):
            xff = self.headers.get("X-Forwarded-For", "")
            if xff:
                return xff.split(",")[0].strip()
            return self.client_address[0] if self.client_address else None

        def _user(self):
            s = app.sessions.get(self.headers.get("X-Token"))
            if not s:
                raise AuthError("Please sign in again.", 401)
            return s

        def _admin(self):
            u = self._user()
            if u["role"] != "admin":
                raise AuthError("Admins only.")
            return u

        def _static(self, name):
            path = os.path.join(config.WEB_DIR, os.path.basename(name))
            if not os.path.exists(path):
                return self._send({"error": "not found"}, 404)
            ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
            with open(path, "rb") as f:
                self._send(f.read(), 200, ctype + "; charset=utf-8")

        def _fail(self, exc):
            """Unexpected error: log the detail server-side, return a generic message."""
            traceback.print_exc()
            return self._send({"error": "Something went wrong. Please try again."}, 400)

        # ---------------- GET
        def do_GET(self):
            url = urlparse(self.path)
            p, q = url.path, {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if p in ("/healthz", "/api/health"):
                    return self._send({"ok": True})
                if p in ("/", "/index.html"):
                    return self._static("index.html")
                if p.startswith("/static/"):
                    return self._static(p.split("/static/", 1)[1])
                if p == "/api/bootstrap":
                    boot = {"statuses": config.STATUSES,
                            "dispositions": config.DISPOSITIONS,
                            "buckets": config.BUCKETS,
                            "brand": {"name": config.BRAND_NAME,
                                      "accent": config.BRAND_ACCENT},
                            "public_login": config.PUBLIC_LOGIN,
                            "demo": config.DEMO,
                            "demo_login": ({"name": "Ava Chen", "pin": config.DEMO_PIN}
                                           if config.DEMO else None)}
                    # Only expose the user roster on the trusted-LAN login flow.
                    if not config.PUBLIC_LOGIN:
                        boot["users"] = app.users.all_active()
                    return self._send(boot)
                if p == "/api/leads":
                    return self._send(app.lead_service.search_for(self._user(), q))
                if p == "/api/followups/count":
                    return self._send(app.lead_service.followup_counts(self._user()))
                if p.startswith("/api/calllog/"):
                    return self._send(app.lead_service.call_log(
                        self._user(), int(p.rsplit("/", 1)[1])))
                # Calendar folders - available to agents (their own) and admins (all)
                if p == "/api/calendar/months":
                    return self._send(app.calendar.months(self._user(), q.get("agent")))
                if p == "/api/calendar/days":
                    return self._send(app.calendar.days(self._user(), q.get("month", ""),
                                                        q.get("agent")))
                if p == "/api/calendar/day":
                    return self._send(app.calendar.day(self._user(), q.get("day", ""),
                                                       q.get("agent")))
                if p == "/api/stats":
                    self._admin()
                    return self._send(app.reports.dashboard())
                if p == "/api/matrix":
                    self._admin()
                    return self._send(app.reports.status_matrix())
                if p == "/api/buckets":
                    self._admin()
                    return self._send(app.reports.buckets())
                if p.startswith("/api/bucket/"):
                    self._admin()
                    return self._send(app.reports.bucket_detail(p.rsplit("/", 1)[1]))
                if p.startswith("/api/agent/"):
                    self._admin()
                    return self._send(app.reports.agent(int(p.rsplit("/", 1)[1])))
                if p == "/api/sync/status":
                    self._admin()
                    return self._send(app.sync.status())
                if p == "/api/activity":
                    self._admin()
                    return self._send({"rows": app.activity.recent()})
            except AuthError as e:
                return self._send({"error": str(e)}, e.code)
            except (ValueError, KeyError):
                return self._send({"error": "Bad request."}, 400)
            except Exception as e:                       # never leak internals
                return self._fail(e)
            self._send({"error": "not found"}, 404)

        # ---------------- POST
        def do_POST(self):
            p = urlparse(self.path).path
            try:
                self._raw_body = self._read_raw()   # always consume the body (keep-alive safe)
                self._check_origin()
                if p == "/api/login":
                    d = self._body()
                    token, user = app.accounts.login(
                        (d.get("name") or "").strip(),
                        str(d.get("pin") or "").strip(),
                        source=self._client_ip())
                    return self._send({"ok": True, "token": token, "user": user})
                if p == "/api/logout":
                    app.sessions.drop(self.headers.get("X-Token"))
                    return self._send({"ok": True})
                if p == "/api/changepin":
                    d = self._body()
                    app.accounts.change_pin(self._user(), str(d.get("old") or ""),
                                            str(d.get("new") or ""),
                                            token=self.headers.get("X-Token"))
                    return self._send({"ok": True})
                if p == "/api/resetpin":
                    self._admin()
                    d = self._body()
                    name = app.accounts.reset_pin(int(d["user_id"]), str(d.get("pin") or ""))
                    return self._send({"ok": True, "name": name})
                if p == "/api/user":
                    self._admin()
                    d = self._body()
                    app.accounts.create_user((d.get("name") or "").strip(),
                                             d.get("role", "agent"),
                                             str(d.get("pin") or ""))
                    return self._send({"ok": True})
                if p.startswith("/api/calllog/"):
                    d = self._body()
                    return self._send(app.lead_service.log_call(
                        self._user(), int(p.rsplit("/", 1)[1]),
                        d.get("outcome", ""), d.get("note", ""),
                        bump=d.get("bump", True)))
                if p.startswith("/api/lead/"):
                    lead_id = int(p.rsplit("/", 1)[1])
                    return self._send({"ok": True,
                                       "lead": app.lead_service.update(self._user(),
                                                                       lead_id, self._body())})
                if p == "/api/assign":
                    actor = self._admin()
                    d = self._body()
                    return self._send(app.assignment.assign(
                        actor, int(d["agent_id"]), count=int(d.get("count") or 0),
                        ids=d.get("ids"), bucket=d.get("bucket") or None,
                        state=(d.get("state") or "").strip() or None,
                        number_format=(d.get("format") or "").strip() or None,
                        fresh=bool(d.get("fresh"))))
                if p == "/api/unassign":
                    actor = self._admin()
                    return self._send(app.assignment.unassign(actor,
                                                              self._body().get("ids", [])))
                if p == "/api/sync":
                    self._admin()
                    d = self._body()
                    app.sync.start(d.get("days", 14), d.get("format"))
                    return self._send({"ok": True, "started": True})
            except AuthError as e:
                return self._send({"error": str(e)}, e.code)
            except (ValueError, KeyError):
                return self._send({"error": "Bad request."}, 400)
            except Exception as e:
                return self._fail(e)
            self._send({"error": "not found"}, 404)

    return Handler


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def create_server(app: Container):
    handler = build_handler(app)
    for port in (config.PORT, config.FALLBACK_PORT):
        try:
            return ThreadingHTTPServer((config.BIND, port), handler), port
        except OSError:
            print(f"  port {port} is busy, trying the next one...")
    raise SystemExit(f"Neither port {config.PORT} nor {config.FALLBACK_PORT} is available.")
