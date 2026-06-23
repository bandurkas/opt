"""Unit tests for services.credentials — Fernet encryption + masking.
No DB, no network. Run: cd backend && python3 -m pytest tests/test_credentials.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography.fernet import Fernet

os.environ.setdefault("CREDENTIALS_MASTER_KEY", Fernet.generate_key().decode())

from services import credentials as creds  # noqa: E402


def test_encrypt_decrypt_roundtrip():
    secret = "super-secret-api-key-12345"
    token = creds.encrypt(secret)
    assert token != secret
    assert creds.decrypt(token) == secret


def test_encrypt_is_nondeterministic():
    # Fernet includes a random IV/timestamp — same plaintext should NOT produce
    # the same ciphertext twice (defends against pattern-matching at rest).
    t1 = creds.encrypt("same-value")
    t2 = creds.encrypt("same-value")
    assert t1 != t2


def test_masked_short_value():
    assert creds.masked("abcd") == "****"
    assert creds.masked("") is None
    assert creds.masked(None) is None


def test_masked_long_value_keeps_last_four():
    masked = creds.masked("BYBIT_API_KEY_ABCDEFGH1234")
    assert masked.endswith("1234")
    assert masked[:-4] == "*" * (len("BYBIT_API_KEY_ABCDEFGH1234") - 4)


def test_get_credentials_falls_back_to_env_when_no_db_row(monkeypatch):
    from db import accounts_repo

    monkeypatch.setattr(accounts_repo, "get_credentials_row", lambda account_id: None)
    creds._cache.clear()
    key, secret = creds.get_credentials(999, env_fallback=("env-key", "env-secret"))
    assert (key, secret) == ("env-key", "env-secret")


def test_get_credentials_decrypts_db_row(monkeypatch):
    from db import accounts_repo

    enc_key = creds.encrypt("db-key")
    enc_secret = creds.encrypt("db-secret")
    monkeypatch.setattr(
        accounts_repo, "get_credentials_row",
        lambda account_id: {"api_key_encrypted": enc_key, "api_secret_encrypted": enc_secret},
    )
    creds._cache.clear()
    key, secret = creds.get_credentials(1, env_fallback=("env-key", "env-secret"))
    assert (key, secret) == ("db-key", "db-secret")


def test_get_credentials_caches_result(monkeypatch):
    from db import accounts_repo

    calls = {"n": 0}

    def fake_row(account_id):
        calls["n"] += 1
        return None

    monkeypatch.setattr(accounts_repo, "get_credentials_row", fake_row)
    creds._cache.clear()
    creds.get_credentials(42, env_fallback=("k", "s"))
    creds.get_credentials(42, env_fallback=("k", "s"))
    assert calls["n"] == 1  # second call served from cache


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
