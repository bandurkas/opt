"""Mission Control auth — single shared password, stdlib-only (no new deps for
the lightest-weight piece of this stack). PBKDF2 password hash + HMAC-signed
session token, set as a cookie.

NOTE: the dashboard is served over plain HTTP (no TLS on VPS3 today), so the
cookie is NOT marked Secure — it would otherwise never be sent. This means the
password and session token both travel in cleartext on the wire. Acceptable
as a strict improvement over today's zero-auth baseline, but putting TLS
(e.g. Caddy/certbot) in front of :3000/:8000 is the real fix and still open.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

SESSION_COOKIE = "mc_session"
SESSION_TTL_S = 7 * 24 * 3600  # 7 days
PBKDF2_ITERATIONS = 260_000


def _secret_key() -> bytes:
    key = os.getenv("AUTH_SECRET_KEY", "").strip()
    if not key:
        raise RuntimeError("AUTH_SECRET_KEY not set — required for Mission Control auth")
    return key.encode("utf-8")


def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algo, iter_s, salt_b64, digest_b64 = stored_hash.split("$")
        if algo != "pbkdf2_sha256":
            return False
        iterations = int(iter_s)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
    except (ValueError, TypeError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def _sign(payload_b64: str) -> str:
    return hmac.new(_secret_key(), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()


def issue_token(*, ttl_s: int = SESSION_TTL_S) -> str:
    payload = {"exp": int(time.time()) + ttl_s}
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"{payload_b64}.{_sign(payload_b64)}"


def verify_token(token: str) -> bool:
    try:
        payload_b64, sig = token.split(".", 1)
    except ValueError:
        return False
    if not hmac.compare_digest(_sign(payload_b64), sig):
        return False
    try:
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, TypeError):
        return False
    return int(payload.get("exp", 0)) >= int(time.time())


# ───────────────────────── Login rate limiting ─────────────────────────
# In-memory only (single API process, single VPS) — good enough to blunt
# online password guessing against the one shared password. Not persisted
# across restarts; that's fine, a restart is a rare event compared to an
# attack window.
LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCKOUT_S = 5 * 60

_login_attempts: dict[str, list[float]] = {}


def login_rate_limited(client_id: str) -> bool:
    """True if `client_id` (e.g. source IP) has hit LOGIN_MAX_ATTEMPTS failed
    logins within the last LOGIN_LOCKOUT_S seconds."""
    now = time.time()
    attempts = [t for t in _login_attempts.get(client_id, []) if now - t < LOGIN_LOCKOUT_S]
    if attempts:
        _login_attempts[client_id] = attempts
    else:
        # Nothing left in the window — drop the entry instead of leaving an
        # empty list forever. Internet-facing login endpoints get probed by
        # scanners; without this, one dict entry per distinct source IP that
        # ever failed once (and never came back) accumulates for the life of
        # the process.
        _login_attempts.pop(client_id, None)
    return len(attempts) >= LOGIN_MAX_ATTEMPTS


def record_failed_login(client_id: str) -> None:
    _login_attempts.setdefault(client_id, []).append(time.time())


def clear_failed_logins(client_id: str) -> None:
    _login_attempts.pop(client_id, None)


if __name__ == "__main__":
    # One-time setup helper: prints the two .env lines needed for Mission Control
    # auth. Usage: python -m services.auth <password>
    import sys

    if len(sys.argv) != 2:
        print("usage: python -m services.auth <password>")
        raise SystemExit(1)
    print(f"ADMIN_PASSWORD_HASH={hash_password(sys.argv[1])}")
    print(f"AUTH_SECRET_KEY={base64.b64encode(os.urandom(32)).decode()}")
