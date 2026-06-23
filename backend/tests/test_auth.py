"""Unit tests for services.auth — password hashing + signed session tokens.
No DB, no network. Run: cd backend && python3 -m pytest tests/test_auth.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("AUTH_SECRET_KEY", "test-secret-key-not-for-prod")

from services import auth  # noqa: E402


def test_password_hash_roundtrip():
    h = auth.hash_password("correct horse battery staple")
    assert auth.verify_password("correct horse battery staple", h)


def test_password_hash_rejects_wrong_password():
    h = auth.hash_password("correct horse battery staple")
    assert not auth.verify_password("wrong password", h)


def test_password_hash_is_salted():
    h1 = auth.hash_password("same password")
    h2 = auth.hash_password("same password")
    assert h1 != h2  # different random salt each time
    assert auth.verify_password("same password", h1)
    assert auth.verify_password("same password", h2)


def test_verify_password_rejects_malformed_hash():
    assert not auth.verify_password("anything", "not-a-valid-hash")
    assert not auth.verify_password("anything", "bcrypt$1$2$3")  # wrong algo tag


def test_token_roundtrip():
    token = auth.issue_token()
    assert auth.verify_token(token)


def test_token_rejects_tampered_payload():
    token = auth.issue_token()
    payload_b64, sig = token.split(".", 1)
    tampered = payload_b64 + "x." + sig  # mutate payload, keep old signature
    assert not auth.verify_token(tampered)


def test_token_rejects_tampered_signature():
    token = auth.issue_token()
    payload_b64, sig = token.split(".", 1)
    flipped_sig = ("0" if sig[0] != "0" else "1") + sig[1:]
    assert not auth.verify_token(f"{payload_b64}.{flipped_sig}")


def test_token_rejects_garbage():
    assert not auth.verify_token("not-even-two-parts")
    assert not auth.verify_token("")


def test_token_expires(monkeypatch):
    token = auth.issue_token(ttl_s=1)
    assert auth.verify_token(token)
    future = time.time() + 2
    monkeypatch.setattr(time, "time", lambda: future)
    assert not auth.verify_token(token)


def test_login_rate_limit_locks_after_max_attempts():
    auth._login_attempts.clear()
    client = "1.2.3.4"
    for _ in range(auth.LOGIN_MAX_ATTEMPTS):
        assert not auth.login_rate_limited(client)
        auth.record_failed_login(client)
    assert auth.login_rate_limited(client)


def test_login_rate_limit_is_per_client():
    auth._login_attempts.clear()
    for _ in range(auth.LOGIN_MAX_ATTEMPTS):
        auth.record_failed_login("attacker")
    assert auth.login_rate_limited("attacker")
    assert not auth.login_rate_limited("someone-else")


def test_clear_failed_logins_resets_lockout():
    auth._login_attempts.clear()
    client = "5.6.7.8"
    for _ in range(auth.LOGIN_MAX_ATTEMPTS):
        auth.record_failed_login(client)
    assert auth.login_rate_limited(client)
    auth.clear_failed_logins(client)
    assert not auth.login_rate_limited(client)


def test_login_rate_limit_window_expires(monkeypatch):
    auth._login_attempts.clear()
    client = "9.9.9.9"
    for _ in range(auth.LOGIN_MAX_ATTEMPTS):
        auth.record_failed_login(client)
    assert auth.login_rate_limited(client)
    future = time.time() + auth.LOGIN_LOCKOUT_S + 1
    monkeypatch.setattr(time, "time", lambda: future)
    assert not auth.login_rate_limited(client)


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
