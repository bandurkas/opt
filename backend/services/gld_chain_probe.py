#!/usr/bin/env python3
"""REAL gold-options reality probe via GLD (gold ETF) options — no IBKR needed.

Source = CBOE delayed-quotes JSON (free, real bid/ask/IV/OI/greeks). GLD options
trade off the SAME gold vol surface as the CME gold-futures options (OG/OMG) we'd
trade via IBKR, so this honestly answers what synthetic σ never could:
  - real ATM implied-vol TERM STRUCTURE (call & put per expiry)
  - real bid/ask spread (% of mid) + OI/volume  → is it liquid? (vs dead XAUT)
  - VRP = real IV - gold realized vol           → sellable edge sign/size
Realized vol is taken from local gold history (xaut_1h.json daily closes) since
GLD tracks the same metal. STDLIB ONLY. Run:  python3 backend/services/gld_chain_probe.py GLD
"""
from __future__ import annotations

import json
import math
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

TICK = sys.argv[1] if len(sys.argv) > 1 else "GLD"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


def cboe_chain(sym: str) -> dict:
    u = f"https://cdn.cboe.com/api/global/delayed_quotes/options/{sym}.json"
    req = urllib.request.Request(u, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def parse_occ(sym: str):
    """'GLD260618C00110000' -> (expiry_date, 'C', strike)."""
    body = sym[len(TICK):] if sym.startswith(TICK) else sym
    try:
        y, m, d = 2000 + int(body[0:2]), int(body[2:4]), int(body[4:6])
        side = body[6]
        strike = int(body[7:]) / 1000.0
        return datetime(y, m, d, tzinfo=timezone.utc), side, strike
    except (ValueError, IndexError):
        return None


def gold_realized_vol():
    """Annualized RV of gold from local xaut_1h.json daily closes (20/60/120d)."""
    for p in (Path("data/xaut_1h.json"), Path(__file__).resolve().parents[2] / "data/xaut_1h.json"):
        if p.exists():
            rows = json.loads(p.read_text())
            break
    else:
        return {}
    # one close per UTC day (last bar of the day)
    by_day = {}
    for c in rows:
        ts = int(c.get("start_ms") or c.get("startTime") or c.get("t"))
        day = ts // 86_400_000
        by_day[day] = float(c["close"])
    closes = [by_day[d] for d in sorted(by_day)]

    def rv(lb):
        if len(closes) < lb + 1:
            return None
        w = closes[-(lb + 1):]
        rets = [math.log(w[i] / w[i - 1]) for i in range(1, len(w)) if w[i - 1] > 0]
        m = sum(rets) / len(rets)
        var = sum((x - m) ** 2 for x in rets) / (len(rets) - 1)
        return math.sqrt(var) * math.sqrt(252)
    return {"20d": rv(20), "60d": rv(60), "120d": rv(120), "ndays": len(closes)}


def main():
    now = int(time.time())
    print(f"=== {TICK} gold-options reality probe (CBOE) @ {datetime.now(timezone.utc).isoformat()} ===\n")
    try:
        d = cboe_chain(TICK)["data"]
    except Exception as e:  # noqa: BLE001
        print(f"CBOE fetch FAILED: {e!r}")
        return 1
    spot = float(d.get("current_price") or d.get("close") or 0)
    opts = d.get("options", [])
    print(f"{TICK} underlying=${spot:.2f}   {len(opts)} contracts listed (delayed)")

    rvs = gold_realized_vol()
    if rvs:
        print(f"gold realized vol (xaut daily, {rvs.get('ndays')}d hist): "
              f"20d={rvs['20d'] and rvs['20d']*100:.1f}%  60d={rvs['60d'] and rvs['60d']*100:.1f}%  "
              f"120d={rvs['120d'] and rvs['120d']*100:.1f}%")
    rv_ref = (rvs.get("60d") or rvs.get("20d") or 0) * 100

    # group by expiry, pick ATM (closest strike with live bid&ask) per side
    by_exp = defaultdict(lambda: {"C": [], "P": []})
    for o in opts:
        pm = parse_occ(o.get("option", ""))
        if not pm:
            continue
        exp, side, strike = pm
        if side in ("C", "P"):
            by_exp[exp][side].append((strike, o))

    def atm(lst):
        live = [(k, o) for k, o in lst if (o.get("bid") or 0) > 0 and (o.get("ask") or 0) > 0
                and (o.get("iv") or 0) > 0]
        if not live:
            return None
        return min(live, key=lambda t: abs(t[0] - spot))

    print(f"\n  {'expiry':<12}{'dte_d':>6} {'side':>4} {'K':>7} {'IV%':>7} {'bid':>7} {'ask':>7} "
          f"{'mid':>7} {'spr%mid':>8} {'OI':>8} {'vol':>7}")
    vrp = defaultdict(list)
    for exp in sorted(by_exp):
        dte = (exp.timestamp() - now) / 86400
        if dte < 3 or dte > 120:
            continue
        ds = exp.strftime("%d%b%y")
        for side in ("C", "P"):
            a = atm(by_exp[exp][side])
            if not a:
                continue
            k, o = a
            iv = (o.get("iv") or 0) * 100
            bid, ask = o.get("bid") or 0, o.get("ask") or 0
            mid = (bid + ask) / 2
            spr = (ask - bid) / mid * 100 if mid > 0 else float("nan")
            print(f"  {ds:<12}{dte:>6.0f} {side:>4} {k:>7.1f} {iv:>7.1f} {bid:>7.2f} {ask:>7.2f} "
                  f"{mid:>7.2f} {spr:>8.0f} {o.get('open_interest') or 0:>8.0f} {o.get('volume') or 0:>7.0f}")
            if 20 <= dte <= 75:
                vrp[side].append((iv - rv_ref, spr))

    print(f"\n  === REAL VRP (GLD ATM IV - gold RV60 {rv_ref:.0f}%) on 20-75d ===")
    for side in ("C", "P"):
        xs = vrp[side]
        if xs:
            v = sum(a for a, _ in xs) / len(xs)
            s = sum(b for _, b in xs) / len(xs)
            print(f"  {side}: avg VRP={v:+.1f} vol-pts  avg spread={s:.1f}% of mid  "
                  f"({'IV>RV → sellable' if v > 0 else 'IV<=RV → no sell edge'})")
        else:
            print(f"  {side}: no ATM contract in 20-75d window")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
