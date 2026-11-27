"""Password login for the dashboard when it is reachable from the internet.

- One password, from DASHBOARD_PASSWORD (Render generates it; it is never in the repo).
- Plus the owner's own password, so the owner can sign in without opening the Render dashboard. Only a
  salted PBKDF2 hash of it is in the repo (OWNER_PASSWORD_HASH), never the password itself. To change it,
  replace the hash with `python -m trading.webauth hash-password`; set OWNER_PASSWORD=off to turn it off.
- Sessions are signed cookies (HMAC-SHA256) that last SESSION_DAYS (default 30), so
  the home-screen app stays signed in. The signing key mixes SESSION_SECRET with the
  password, so changing either signs everyone out.
- Failed logins are rate limited per client and globally.
- Two-step login: with DASHBOARD_TOTP_SECRET set, sign-in also needs the 6-digit code from an
  authenticator app (Google Authenticator, Authy, 1Password: RFC 6238, 30-second codes). Each code
  works once. Make a secret with `python -m trading.webauth new-totp`.
- Fails closed: with auth on and no password set, the dashboard refuses to start.

Auth is off only when DASHBOARD_AUTH=off (local Docker, port bound to localhost)
or when the dashboard listens on a loopback address, unless DASHBOARD_AUTH=force (needed for real money on
one's own computer: approvals need the two-step code).
"""

import base64
import hashlib
import hmac
import os
import secrets
import struct
import sys
import time
import urllib.parse
from collections import defaultdict, deque

COOKIE = "ats_session"
MIN_PASSWORD_LENGTH = 12
PER_CLIENT_FAILURES = 5
GLOBAL_FAILURES = 50
FAILURE_WINDOW = 15 * 60
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
MAX_TRACKED_CLIENTS = 1024  # above this, expired entries are swept
TOTP_STEP = 30
TOTP_DRIFT = 1  # accept the code before and after the current one (phone clocks drift)
# The owner's sign-in password as "iterations$salt$hash" (PBKDF2-HMAC-SHA256). Works alongside DASHBOARD_PASSWORD.
OWNER_PASSWORD_HASH = "400000$03bea6841759723701ca071987b7f4b5$0566c1b3ed99d7288091f2af56579435525d43cda8c481230cd59b8848d94fbd"


def hash_password(password: str, iterations: int = 400_000) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"{iterations}${salt.hex()}${digest.hex()}"


def matches_hash(attempt: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        iterations, salt, digest = stored.split("$")
        got = hashlib.pbkdf2_hmac("sha256", attempt.encode(), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(got, bytes.fromhex(digest))
    except ValueError:
        return False


def _totp_key(secret: str) -> bytes:
    s = secret.replace(" ", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def totp(secret: str, at: float, step: int = TOTP_STEP) -> str:
    """The 6-digit RFC 6238 code for `at` (unix time)."""
    digest = hmac.new(_totp_key(secret), struct.pack(">Q", int(at // step)), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    return f"{(struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


class WebAuth:
    def __init__(self, password: str | None, secret: str | None, enabled: bool = True,
                 session_days: float = 30, clock=time.time, totp_secret: str | None = None,
                 owner_hash: str | None = None):
        if enabled and (not password or len(password) < MIN_PASSWORD_LENGTH):
            raise SystemExit(f"DASHBOARD_PASSWORD must be set (at least {MIN_PASSWORD_LENGTH} characters) "
                             "before the dashboard can be reachable from the internet.")
        if totp_secret:
            try:
                if len(_totp_key(totp_secret)) < 10:
                    raise ValueError
            except (ValueError, base64.binascii.Error):
                raise SystemExit("DASHBOARD_TOTP_SECRET must be a base32 secret of at least 16 characters "
                                 "(make one with: python -m trading.webauth new-totp).") from None
        self.totp_secret = totp_secret or None
        self.owner_hash = owner_hash or None
        self._used_steps: deque = deque(maxlen=8)  # a code works once
        self.enabled = enabled
        self.ttl = int(session_days * 86400)
        self.clock = clock
        self._password_digest = hashlib.sha256((password or "").encode()).digest()
        # Without SESSION_SECRET a random key is used, so a restart signs everyone out.
        base = (secret or secrets.token_hex(32)).encode()
        self._key = hmac.new(base, self._password_digest, hashlib.sha256).digest()
        self._failures: dict[str, deque] = defaultdict(deque)
        self._global: deque = deque()

    @classmethod
    def from_env(cls, host: str, env=os.environ) -> "WebAuth":
        mode = env.get("DASHBOARD_AUTH", "on").lower()
        # "force" keeps login on even on this computer's own address: real money needs the two-step code there too.
        enabled = mode == "force" or (mode != "off" and host not in LOOPBACK)
        return cls(env.get("DASHBOARD_PASSWORD"), env.get("SESSION_SECRET"), enabled,
                   float(env.get("SESSION_DAYS", 30)), totp_secret=env.get("DASHBOARD_TOTP_SECRET"),
                   owner_hash=None if env.get("OWNER_PASSWORD", "on").lower() == "off" else OWNER_PASSWORD_HASH)

    @property
    def max_age(self) -> int:
        return self.ttl

    def check_password(self, attempt: str) -> bool:
        main = hmac.compare_digest(hashlib.sha256(attempt.encode()).digest(), self._password_digest)
        return matches_hash(attempt, self.owner_hash) or main

    @property
    def two_step(self) -> bool:
        return self.enabled and self.totp_secret is not None

    def check_code(self, code: str) -> bool:
        """True for a fresh authenticator code (always True when two-step login is off)."""
        if not self.two_step:
            return True
        code = "".join(c for c in code if c.isdigit())
        now = self.clock()
        for drift in range(-TOTP_DRIFT, TOTP_DRIFT + 1):
            at = now + drift * TOTP_STEP
            step = int(at // TOTP_STEP)
            if step not in self._used_steps and hmac.compare_digest(code, totp(self.totp_secret, at)):
                self._used_steps.append(step)
                return True
        return False

    def check(self, password: str, code: str = "") -> bool:
        """Password and, when two-step login is on, the authenticator code. Both are always checked."""
        ok_password = self.check_password(password)
        ok_code = self.check_code(code) if ok_password else False
        return ok_password and ok_code

    def _sign(self, payload: str) -> str:
        return hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()

    def issue(self) -> str:
        payload = f"{int(self.clock()) + self.ttl}.{secrets.token_hex(8)}"
        return f"{payload}.{self._sign(payload)}"

    def valid(self, token: str | None) -> bool:
        if not self.enabled:
            return True
        if not token or token.count(".") != 2:
            return False
        payload, sig = token.rsplit(".", 1)
        if not hmac.compare_digest(sig, self._sign(payload)):
            return False
        try:
            return int(payload.split(".", 1)[0]) > self.clock()
        except ValueError:
            return False

    def _prune(self, q: deque) -> None:
        cutoff = self.clock() - FAILURE_WINDOW
        while q and q[0] < cutoff:
            q.popleft()

    def allowed(self, client: str) -> bool:
        # Look up without creating an entry, so unknown clients cost no memory.
        q = self._failures.get(client)
        if q is not None:
            self._prune(q)
            if not q:
                del self._failures[client]
                q = None
        self._prune(self._global)
        return (len(q) if q else 0) < PER_CLIENT_FAILURES and len(self._global) < GLOBAL_FAILURES

    def record_failure(self, client: str) -> None:
        now = self.clock()
        if len(self._failures) >= MAX_TRACKED_CLIENTS:
            for key in list(self._failures):
                self._prune(self._failures[key])
                if not self._failures[key]:
                    del self._failures[key]
        self._failures[client].append(now)
        self._global.append(now)

    def record_success(self, client: str) -> None:
        self._failures.pop(client, None)


if __name__ == "__main__":
    if sys.argv[1:] == ["new-totp"]:
        new = new_totp_secret()
        label = urllib.parse.quote("ATS Matrix:owner")
        print(f"DASHBOARD_TOTP_SECRET={new}")
        print(f"Add it to your authenticator app by typing the secret, or open: "
              f"otpauth://totp/{label}?secret={new}&issuer=ATS%20Matrix")
    elif sys.argv[1:] == ["hash-password"]:
        import getpass
        print(hash_password(getpass.getpass("New owner password: ")))
    else:
        print("usage: python -m trading.webauth new-totp | hash-password", file=sys.stderr)
        sys.exit(2)
