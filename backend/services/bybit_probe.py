"""Read-only Bybit readiness probe. Verifies the account is ready for live
options trading WITHOUT placing any order:

  - API key permissions (Options trade enabled? read-only? withdrawal off?)
  - Account type (UTA — required for options)
  - USDT wallet balance (Bybit ETH options are USDT-settled)

Run inside the trader/paper container (keys come from env):
    PYTHONPATH=. python3 services/bybit_probe.py

Respects TRADING_MODE: in testnet mode it probes testnet keys, else mainnet.
Exits 0 if ready, 1 if a blocking issue is found.
"""
from __future__ import annotations

import sys

from services import execution_config as cfg
from services.execution import ExecutionClient, ExecutionError


def main() -> int:
    print(f"[probe] {cfg.summary()}", flush=True)
    key, secret = cfg.api_credentials()
    if not key or not secret:
        print("[probe] ❌ API keys not set for this mode — cannot probe.", flush=True)
        return 1

    try:
        client = ExecutionClient()
    except ExecutionError as e:
        print(f"[probe] ❌ {e}", flush=True)
        return 1

    info = client.account_info()
    if info.get("error"):
        print(f"[probe] ❌ account_info failed: {info['error']}", flush=True)
        return 1

    uta = info.get("uta")
    perms = info.get("permissions") or {}
    read_only = info.get("readOnly")
    opt_perms = perms.get("Options") or []
    spot_perms = perms.get("Spot") or []
    derivatives = perms.get("Derivatives") or []
    can_withdraw = bool(perms.get("Wallet") and "Withdraw" in (perms.get("Wallet") or []))

    print(f"[probe] UTA={uta} readOnly={read_only}", flush=True)
    print(f"[probe] permissions: Options={opt_perms} Derivatives={derivatives} "
          f"Spot={spot_perms} can_withdraw={can_withdraw}", flush=True)

    wallet = client.wallet_usdt()
    avail = client.available_usdt()
    print(f"[probe] USDT wallet balance: {wallet} · available: {avail}", flush=True)

    ok = True
    if uta not in (1, "1"):
        print("[probe] ⚠️  account is NOT Unified (UTA) — Bybit options require UTA.", flush=True)
        ok = False
    if read_only in (1, "1", True):
        print("[probe] ❌ API key is READ-ONLY — cannot place orders.", flush=True)
        ok = False
    if not opt_perms and not derivatives:
        print("[probe] ⚠️  key has no Options/Derivatives trade permission visible.", flush=True)
        ok = False
    if can_withdraw:
        print("[probe] ⚠️  key has WITHDRAW permission — recommend disabling for a trading bot.", flush=True)
    if wallet is None:
        print("[probe] ❌ could not read wallet balance.", flush=True)
        ok = False

    print(f"[probe] {'✅ READY' if ok else '❌ NOT READY'} for {cfg.TRADING_MODE} options trading.", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
