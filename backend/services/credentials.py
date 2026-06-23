"""Encrypted Bybit API credentials, DB-backed (Postgres `exchange_credentials`,
account-keyed for future multi-account support). Falls back to the legacy
.env vars (BYBIT_API_KEY/SECRET, BYBIT_TESTNET_API_KEY/SECRET) when no DB row
exists yet, so the existing live-trading config keeps working unchanged
during the transition.

Encryption: Fernet (cryptography lib), master key from CREDENTIALS_MASTER_KEY
in .env — never stored in the DB. Reads are cached in-process for CACHE_TTL_S
to avoid a DB round-trip on every chain fetch.
"""
from __future__ import annotations

import os
import time

from cryptography.fernet import Fernet, InvalidToken

from db import accounts_repo

CACHE_TTL_S = 60

_cache: dict[int, tuple[float, tuple[str | None, str | None]]] = {}


def _fernet() -> Fernet:
    key = os.getenv("CREDENTIALS_MASTER_KEY", "").strip()
    if not key:
        raise RuntimeError("CREDENTIALS_MASTER_KEY not set — required to read/write credentials")
    return Fernet(key.encode("utf-8"))


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")


def set_credentials(account_id: int, api_key: str, api_secret: str) -> None:
    accounts_repo.upsert_credentials(
        account_id,
        api_key_encrypted=encrypt(api_key),
        api_secret_encrypted=encrypt(api_secret),
    )
    _cache.pop(account_id, None)


def get_credentials(account_id: int, *, env_fallback: tuple[str | None, str | None] = (None, None)
                     ) -> tuple[str | None, str | None]:
    """Returns (api_key, api_secret). Cached for CACHE_TTL_S. Falls back to
    env_fallback if no DB row exists (transition period) or decryption fails."""
    now = time.time()
    cached = _cache.get(account_id)
    if cached and now - cached[0] < CACHE_TTL_S:
        return cached[1]

    row = accounts_repo.get_credentials_row(account_id)
    if row is None:
        result = env_fallback
    else:
        try:
            result = (decrypt(row["api_key_encrypted"]), decrypt(row["api_secret_encrypted"]))
        except (InvalidToken, RuntimeError):
            result = env_fallback
    _cache[account_id] = (now, result)
    return result


def masked(value: str | None) -> str | None:
    """Last-4-chars display mask for the settings UI. Never returns the real key."""
    if not value:
        return None
    if len(value) <= 4:
        return "*" * len(value)
    return "*" * (len(value) - 4) + value[-4:]
