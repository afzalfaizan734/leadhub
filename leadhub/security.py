"""Authentication concerns only: PIN hashing, sessions, brute-force lockout.

Kept apart from data access and HTTP so the rules live in one auditable place
(Single Responsibility).
"""
import hashlib
import secrets
import time

from . import config


# ----------------------------------------------------------------- PINs
def hash_pin(pin: str, salt_hex: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", str(pin).encode(),
                               bytes.fromhex(salt_hex), config.PBKDF2_ROUNDS).hex()


def new_pin_material(pin: str):
    """Returns (salt, hash) for storage. The PIN itself is never persisted."""
    salt = secrets.token_hex(16)
    return salt, hash_pin(pin, salt)


def verify_pin(row, pin: str) -> bool:
    if not row or not row["pin_hash"] or not row["pin_salt"]:
        return False
    return secrets.compare_digest(hash_pin(pin, row["pin_salt"]), row["pin_hash"])


# A fixed throwaway salt so that a login attempt for an unknown user does the same
# PBKDF2 work as a real one - removes the response-time oracle that would otherwise
# reveal which account names exist.
_DUMMY_SALT = secrets.token_hex(16)


def dummy_verify(pin: str) -> bool:
    try:
        hash_pin(pin, _DUMMY_SALT)
    except Exception:
        pass
    return False


def is_valid_pin(pin, min_len=None) -> bool:
    """Digits only, between min_len (role-dependent) and 12 long."""
    if min_len is None:
        min_len = config.PIN_MIN
    return isinstance(pin, str) and pin.isdigit() and min_len <= len(pin) <= 12


def random_pin(digits=None) -> str:
    if digits is None:
        digits = config.PIN_MIN
    digits = max(4, min(int(digits), 12))
    return str(secrets.randbelow(10 ** digits)).zfill(digits)


# ----------------------------------------------------------------- sessions
class SessionStore:
    """In-memory session table. Cleared when the server restarts, by design.

    Tokens expire on an idle timeout (SESSION_HOURS) AND an absolute maximum age
    (SESSION_MAX_HOURS), whichever comes first, so a stolen token cannot live
    forever by being used regularly.
    """

    def __init__(self):
        self._s = {}

    def create(self, user_row) -> str:
        token = secrets.token_urlsafe(24)
        t = time.time()
        self._s[token] = {"id": user_row["id"], "name": user_row["name"],
                          "role": user_row["role"], "seen": t, "born": t}
        return token

    def get(self, token):
        s = self._s.get(token or "")
        if not s:
            return None
        t = time.time()
        if (t - s["seen"] > config.SESSION_HOURS * 3600
                or t - s.get("born", s["seen"]) > config.SESSION_MAX_HOURS * 3600):
            self._s.pop(token, None)
            return None
        s["seen"] = t
        return s

    def drop(self, token):
        self._s.pop(token or "", None)

    def drop_user(self, user_id, except_token=None):
        """Invalidate every session for a user (e.g. after a PIN change/reset),
        optionally keeping one token (the actor's current session) alive."""
        for t in [k for k, v in self._s.items()
                  if v["id"] == user_id and k != except_token]:
            self._s.pop(t, None)


class LockoutPolicy:
    """Slows brute force against a short PIN, per account name AND per source IP,
    with a backoff that grows on continued failures. In-memory (reset on restart);
    adequate for a single long-lived process behind HTTPS."""

    def __init__(self):
        self._f = {}      # key -> [count, locked_until]

    def _locked(self, key) -> int:
        rec = self._f.get(key)
        if rec and rec[1] > time.time():
            return int(rec[1] - time.time())
        return 0

    def locked_for(self, name, source=None) -> int:
        w = self._locked("n:" + str(name))
        if source:
            w = max(w, self._locked("s:" + str(source)))
        return w

    def _bump(self, key) -> int:
        count = self._f.get(key, [0, 0])[0] + 1
        if count >= config.MAX_PIN_FAILS:
            extra = count - config.MAX_PIN_FAILS
            until = time.time() + config.PIN_LOCK_SECONDS * (2 ** min(extra, 6))
        else:
            until = 0
        self._f[key] = [count, until]
        return count

    def record_failure(self, name, source=None) -> int:
        count = self._bump("n:" + str(name))
        if source:
            self._bump("s:" + str(source))
        return max(0, config.MAX_PIN_FAILS - count)

    def clear(self, name, source=None):
        self._f.pop("n:" + str(name), None)
        if source:
            self._f.pop("s:" + str(source), None)
