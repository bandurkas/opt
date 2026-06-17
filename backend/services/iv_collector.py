#!/usr/bin/env python3
"""Real-IV collector — snapshots Bybit ATM ETH option implied vol to build the
forward history we lack (IV_DIVERGENCE_SCANNER_RESEARCH.md §6). Append-only.

WHY: every backtest so far prices premium off σ = realized-vol (synthetic, and
circular — we impose σ=RV). The genuine option-seller edge is the variance risk
premium (IV > RV), which we have never *measured* because there is no IV history.
This daemon records the market-quoted Bybit ATM IV alongside realized vol so that,
after a few weeks, we can test IV-richness for real (sell only when IV ≥ RV×margin).

DESIGN: self-contained, STDLIB ONLY (no bot imports, no deps) so a plain VPS cron
runs it forever without touching the paper bot. Bybit options are reachable only
from the VPS (Mac is geoblocked). Each run appends ONE json line:
  ts_ms, spot, rv_168h, and for ATM call & put of the nearest (>6h) expiry:
  symbol, strike, expiry_ms, dte_h, markIv, bid1Iv, ask1Iv, bid, ask, markPrice,
  delta, gamma, theta, vega, oi, vol24h.

Run (VPS):  python3 /root/opt-app/backend/services/iv_collector.py >> /dev/null
Output:     data/iv_history.jsonl  (override with $IV_HISTORY_PATH or argv[1])
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

BYBIT = "https://api.bybit.com"
UA = {"User-Agent": "curl/8"}
MIN_DTE_H = 6.0  # ignore contracts expiring within 6h (matches paper_loop filter)


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _f(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def parse_symbol(sym: str):
    """'ETH-25SEP26-3200-C-USDT' -> (expiry_ms@08:00UTC, strike, side)."""
    parts = sym.split("-")
    if len(parts) < 4:
        return None
    _, dstr, kstr, side = parts[0], parts[1], parts[2], parts[3]
    try:
        d = datetime.strptime(dstr, "%d%b%y").replace(
            hour=8, tzinfo=timezone.utc)  # Bybit options settle 08:00 UTC
        return int(d.timestamp() * 1000), float(kstr), side.upper()
    except ValueError:
        return None


def realized_vol_168h() -> float | None:
    """Annualized realized vol from the last 168 hourly ETH log-returns."""
    try:
        d = _get(f"{BYBIT}/v5/market/kline?category=linear&symbol=ETHUSDT&interval=60&limit=200")
        rows = d["result"]["list"]  # newest-first: [start, o, h, l, c, vol, turnover]
        closes = [float(r[4]) for r in rows][::-1]  # -> oldest-first
    except Exception:  # noqa: BLE001
        return None
    if len(closes) < 50:
        return None
    closes = closes[-169:]
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))
            if closes[i - 1] > 0]
    if len(rets) < 24:
        return None
    mean = sum(rets) / len(rets)
    var = sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(24 * 365)  # hourly -> annualized


def pick_atm(contracts: list[dict], spot: float, side: str, now_ms: int):
    """Nearest-expiry (>6h), then closest-to-spot strike, with a live bid IV."""
    cands = []
    for c in contracts:
        meta = parse_symbol(c.get("symbol", ""))
        if not meta:
            continue
        expiry_ms, strike, sd = meta
        if sd != side:
            continue
        dte_h = (expiry_ms - now_ms) / 3_600_000
        if dte_h < MIN_DTE_H:
            continue
        if _f(c.get("bid1Iv")) <= 0:  # require a live bid (liquidity)
            continue
        cands.append((expiry_ms, abs(strike - spot), strike, dte_h, c))
    if not cands:
        return None
    cands.sort(key=lambda t: (t[0], t[1]))  # nearest expiry, then closest strike
    expiry_ms, _, strike, dte_h, c = cands[0]
    return {
        "symbol": c["symbol"], "strike": strike, "expiry_ms": expiry_ms,
        "dte_h": round(dte_h, 2),
        "markIv": _f(c.get("markIv")), "bid1Iv": _f(c.get("bid1Iv")),
        "ask1Iv": _f(c.get("ask1Iv")),
        "bid": _f(c.get("bid1Price")), "ask": _f(c.get("ask1Price")),
        "markPrice": _f(c.get("markPrice")),
        "delta": _f(c.get("delta")), "gamma": _f(c.get("gamma")),
        "theta": _f(c.get("theta")), "vega": _f(c.get("vega")),
        "oi": _f(c.get("openInterest")), "vol24h": _f(c.get("volume24h")),
    }


def collect() -> dict:
    now_ms = int(time.time() * 1000)
    d = _get(f"{BYBIT}/v5/market/tickers?category=option&baseCoin=ETH")
    contracts = d["result"]["list"]
    spot = 0.0
    for c in contracts:
        spot = _f(c.get("underlyingPrice"))
        if spot > 0:
            break
    return {
        "ts_ms": now_ms,
        "ts_iso": datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).isoformat(),
        "spot": spot,
        "rv_168h": realized_vol_168h(),
        "n_contracts": len(contracts),
        "atm_call": pick_atm(contracts, spot, "C", now_ms),
        "atm_put": pick_atm(contracts, spot, "P", now_ms),
    }


def main():
    path = (sys.argv[1] if len(sys.argv) > 1
            else os.getenv("IV_HISTORY_PATH")
            or os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), "data", "iv_history.jsonl"))
    try:
        row = collect()
    except Exception as e:  # noqa: BLE001 — a cron miss must never crash the loop
        sys.stderr.write(f"[iv_collector] {datetime.now(timezone.utc).isoformat()} ERROR {e!r}\n")
        return 1
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(row, separators=(",", ":")) + "\n")
    c, p = row.get("atm_call"), row.get("atm_put")
    rv = row.get("rv_168h")
    rv_s = f"{rv:.3f}" if rv is not None else "NA"
    c_s = f"C[{c['symbol']} iv={c['markIv']:.3f} dte={c['dte_h']}h]" if c else "C[none]"
    p_s = f"P[{p['symbol']} iv={p['markIv']:.3f} dte={p['dte_h']}h]" if p else "P[none]"
    sys.stderr.write(
        f"[iv_collector] {row['ts_iso']} spot={row['spot']:.2f} rv={rv_s} {c_s} {p_s}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
