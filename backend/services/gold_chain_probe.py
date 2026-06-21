#!/usr/bin/env python3
"""Decisive data-reality probe for GOLD (XAUT) options on Bybit — run from VPS3.

Before designing any gold options strategy we must MEASURE (not assume) whether
a tradeable, fairly-priced gold option market exists on Bybit, and whether there
is a real variance-risk-premium (market IV vs realized vol) and of what sign.
The prior gold test (gold_oos_regime.py) priced premium off SYNTHETIC σ=RV×1.05;
this pulls the REAL market chain instead. STDLIB ONLY (no bot imports).

Reports:
  - chain existence: #contracts, expiries, strike spacing, min order qty
  - liquidity: per-expiry OI / 24h volume, ATM bid/ask spread (% of mark)
  - real VRP: ATM markIv (nearest sane expiry) vs realized vol (24h & 168h),
              for BOTH call and put — the sign/size of any sellable edge.
Run (VPS3):  python3 backend/services/gold_chain_probe.py XAUT
"""
from __future__ import annotations

import json
import math
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

BYBIT = "https://api.bybit.com"
UA = {"User-Agent": "curl/8"}
COIN = sys.argv[1] if len(sys.argv) > 1 else "XAUT"
PERP = f"{COIN}USDT"


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def parse_symbol(sym: str):
    p = sym.split("-")
    if len(p) < 4:
        return None
    try:
        d = datetime.strptime(p[1], "%d%b%y").replace(hour=8, tzinfo=timezone.utc)
        return int(d.timestamp() * 1000), float(p[2]), p[3].upper()
    except ValueError:
        return None


def realized_vol(interval: str, lookback: int) -> float | None:
    try:
        d = _get(f"{BYBIT}/v5/market/kline?category=linear&symbol={PERP}"
                 f"&interval={interval}&limit={lookback + 5}")
        closes = [float(r[4]) for r in d["result"]["list"]][::-1]
    except Exception as e:  # noqa: BLE001
        print(f"  RV fetch error ({interval}): {e!r}")
        return None
    if len(closes) < lookback // 2:
        return None
    closes = closes[-(lookback + 1):]
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
    if len(rets) < 10:
        return None
    m = sum(rets) / len(rets)
    var = sum((x - m) ** 2 for x in rets) / (len(rets) - 1)
    per_year = {"60": 24 * 365, "D": 365}[interval]
    return math.sqrt(var) * math.sqrt(per_year)


def main():
    now = int(time.time() * 1000)
    print(f"=== {COIN} options chain probe @ {datetime.now(timezone.utc).isoformat()} ===\n")

    # 1) instruments-info: strike step, min qty, expiries
    try:
        info = _get(f"{BYBIT}/v5/market/instruments-info?category=option&baseCoin={COIN}&limit=1000")
        ilist = info["result"]["list"]
    except Exception as e:  # noqa: BLE001
        print(f"instruments-info FAILED: {e!r}\n→ {COIN} options likely not listed on Bybit.")
        return 1
    print(f"instruments-info: {len(ilist)} contracts listed")
    if ilist:
        s0 = ilist[0]
        print(f"  sample: {s0.get('symbol')}  minOrderQty={s0.get('lotSizeFilter', {}).get('minOrderQty')}"
              f"  qtyStep={s0.get('lotSizeFilter', {}).get('qtyStep')}"
              f"  settle={s0.get('settleCoin')}")
        strikes = sorted({_f(parse_symbol(c['symbol'])[1]) for c in ilist if parse_symbol(c['symbol'])})
        diffs = sorted({round(strikes[i] - strikes[i - 1], 4) for i in range(1, len(strikes))})
        print(f"  strike range: {strikes[0]:.1f} .. {strikes[-1]:.1f}; spacings seen: {diffs[:8]}")

    # 2) tickers: live mark IV, bid/ask, OI, vol
    t = _get(f"{BYBIT}/v5/market/tickers?category=option&baseCoin={COIN}")
    cs = t["result"]["list"]
    spot = next((_f(c.get("underlyingPrice")) for c in cs if _f(c.get("underlyingPrice")) > 0), 0.0)
    print(f"\ntickers: {len(cs)} contracts; underlying spot={spot:.2f}")

    by_exp = defaultdict(lambda: {"oi": 0.0, "vol": 0.0, "n": 0})
    for c in cs:
        meta = parse_symbol(c.get("symbol", ""))
        if not meta:
            continue
        exp, _, _ = meta
        b = by_exp[exp]
        b["oi"] += _f(c.get("openInterest"))
        b["vol"] += _f(c.get("volume24h"))
        b["n"] += 1
    print("\n  per-expiry liquidity:")
    print(f"    {'expiry':<12} {'dte_h':>6} {'#':>4} {'OI':>12} {'vol24h':>12}")
    for exp in sorted(by_exp):
        b = by_exp[exp]
        dte = (exp - now) / 3_600_000
        ds = datetime.fromtimestamp(exp / 1000, tz=timezone.utc).strftime("%d%b%y")
        flag = "" if dte > 6 else "  (<6h, skip)"
        print(f"    {ds:<12} {dte:>6.1f} {b['n']:>4} {b['oi']:>12.2f} {b['vol']:>12.2f}{flag}")

    # 3) real VRP — ATM call & put of nearest sane (>12h, has live bid IV) expiry
    rv_d = realized_vol("D", 30)
    rv_h = realized_vol("60", 168)
    print(f"\n  realized vol: 30d-daily={rv_d and rv_d*100:.1f}%  168h-hourly={rv_h and rv_h*100:.1f}%"
          if rv_d and rv_h else f"\n  realized vol: 30d={rv_d} 168h={rv_h}")

    def atm(side):
        cand = []
        for c in cs:
            meta = parse_symbol(c.get("symbol", ""))
            if not meta or meta[2] != side:
                continue
            exp, strike, _ = meta
            dte = (exp - now) / 3_600_000
            if dte < 12 or _f(c.get("bid1Iv")) <= 0:
                continue
            cand.append((exp, abs(strike - spot), strike, dte, c))
        if not cand:
            return None
        cand.sort(key=lambda x: (x[0], x[1]))
        return cand[0]

    print("\n  === REAL ATM IV vs RV (the sellable-edge sign test) ===")
    for side, lbl in (("C", "CALL"), ("P", "PUT")):
        a = atm(side)
        if not a:
            print(f"  {lbl}: no ATM contract with live bid IV")
            continue
        exp, _, strike, dte, c = a
        miv, biv, aiv = _f(c.get("markIv")), _f(c.get("bid1Iv")), _f(c.get("ask1Iv"))
        bid, ask, mark = _f(c.get("bid1Price")), _f(c.get("ask1Price")), _f(c.get("markPrice"))
        spr = (ask - bid) / mark * 100 if mark > 0 else float("nan")
        vrp_h = (miv - rv_h) * 100 if rv_h else float("nan")
        print(f"  {lbl} {c['symbol']}  K={strike:.0f} dte={dte:.0f}h")
        print(f"       markIv={miv*100:.1f}%  bidIv={biv*100:.1f}%  askIv={aiv*100:.1f}%")
        print(f"       bid/ask=${bid:.2f}/${ask:.2f} mark=${mark:.2f}  spread={spr:.1f}% of mark")
        print(f"       VRP (markIv - RV168h) = {vrp_h:+.1f} vol-pts  "
              f"({'IV>RV sellable' if vrp_h > 0 else 'IV<RV — selling underpriced'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
