"""Run the live paper strategy backtest from local VPS-exported klines.

Usage (Mac, no Bybit needed):
    cd backend && PYTHONPATH=. python3 services/local_backtest.py

    python3 services/local_backtest.py --data-dir ../data --baseline  # old sell-Call
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.backtest import simulate_signal_set
from services.strategy_config import (
    BASELINE_CALL_EXIT,
    BASELINE_CALL_GEN_KWARGS,
    DEFAULT_SIGMA,
    EXPIRY_TARGET_HOURS,
    SPREAD_HALF_PCT,
    active_exit,
    active_gen_kwargs,
)
from services.strategy_registry import gen_sell_premium_iv_high

_IV_MAP = {"5": "5m", "15": "15m", "60": "1h"}


def _find_data_dir(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    here = Path(__file__).resolve()
    for candidate in (here.parents[2] / "data", Path("/data")):
        if (candidate / "eth_5m.json").exists():
            return candidate
    return here.parents[2] / "data"


def load_local(data_dir: Path) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for iv, fname in _IV_MAP.items():
        path = data_dir / f"eth_{fname}.json"
        if not path.exists():
            raise FileNotFoundError(f"missing {path}")
        candles = json.loads(path.read_text())
        print(f"  loaded {path.name}: {len(candles):,} bars", flush=True)
        out[iv] = candles
    return out


def fmt_ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def main() -> None:
    ap = argparse.ArgumentParser(description="Local backtest — live paper strategy")
    ap.add_argument("--data-dir", default=None, help="folder with eth_5m.json etc.")
    ap.add_argument("--out", default=None, help="JSON output path")
    ap.add_argument("--spread-pct", type=float, default=SPREAD_HALF_PCT * 2,
                    help="round-trip spread friction (default 2%%)")
    ap.add_argument("--baseline", action="store_true",
                    help="run pre-6be2fbc sell-Call baseline instead of live config")
    args = ap.parse_args()

    data_dir = _find_data_dir(args.data_dir)
    repo_root = Path(__file__).resolve().parents[2]
    out_path = Path(args.out) if args.out else repo_root / "sweep_results" / "local_backtest.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if args.baseline:
        gen = BASELINE_CALL_GEN_KWARGS
        ex = BASELINE_CALL_EXIT
        label = "baseline_call"
    else:
        gen = active_gen_kwargs()
        ex_raw = active_exit()
        ex = {
            "tp1_pct": ex_raw["tp1_pct"],
            "tp2_pct": ex_raw["tp2_pct"],
            "sl_pct": ex_raw["sl_pct"],
            "hold_h": ex_raw["hold_h"],
        }
        label = "live"

    spread = args.spread_pct

    print(f"=== Local backtest — {label} ===", flush=True)
    print(f"  data: {data_dir}", flush=True)
    print(f"  gen:  {gen}", flush=True)
    print(f"  exit: tp1={ex['tp1_pct']} tp2={ex['tp2_pct']} sl={ex['sl_pct']} hold={ex['hold_h']}h", flush=True)
    print(f"  sigma={DEFAULT_SIGMA}  spread={spread}%  expiry={EXPIRY_TARGET_HOURS}h", flush=True)
    t0 = time.time()

    print("\n[1] Loading klines...", flush=True)
    data = load_local(data_dir)
    k5, k15, k1h = data["5"], data["15"], data["60"]
    if k5:
        print(f"  range: {fmt_ts(k5[0]['start_ms'])} → {fmt_ts(k5[-1]['start_ms'])}", flush=True)

    print("\n[2] Generating signals...", flush=True)
    signals = gen_sell_premium_iv_high(k5, k15, k1h, **gen)
    print(f"  {len(signals)} signals", flush=True)

    print("\n[3] Simulating...", flush=True)
    sims = simulate_signal_set(
        signals, k5,
        sigma=DEFAULT_SIGMA,
        expiry_hours=float(EXPIRY_TARGET_HOURS),
        tp1_pct=ex["tp1_pct"],
        tp2_pct=ex["tp2_pct"],
        sl_pct=ex["sl_pct"],
        option_horizon_h=ex["hold_h"],
        spread_pct=spread,
    )

    trades = []
    for s in sims:
        opt = s.get("option", {})
        if "pnl_pct" not in opt:
            continue
        trades.append({
            "entry_ts": s["ts_ms"],
            "entry_dt": fmt_ts(s["ts_ms"]),
            "entry_price": s["close"],
            "side": s["side"],
            "signal_type": s.get("signal_type"),
            "strike": opt.get("strike"),
            "entry_credit_pct": opt.get("entry_credit_pct"),
            "exit_debit_pct": opt.get("exit_debit_pct"),
            "pnl_pct": opt["pnl_pct"],
            "exit_reason": opt.get("exit_reason"),
            "hold_h": opt.get("hold_h"),
            "exit_ts": opt.get("exit_ts"),
            "exit_dt": fmt_ts(opt["exit_ts"]) if opt.get("exit_ts") else None,
        })
    trades.sort(key=lambda t: t["entry_ts"])

    if not trades:
        print("\nNo trades generated.", flush=True)
        return

    pnls = [t["pnl_pct"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    reasons: dict[str, int] = {}
    for t in trades:
        r = t.get("exit_reason") or "unknown"
        reasons[r] = reasons.get(r, 0) + 1

    print(f"\n=== RESULTS ({len(trades)} trades) ===", flush=True)
    print(f"  Win rate:     {len(wins)/len(pnls)*100:.1f}%", flush=True)
    print(f"  Avg P&L:      {statistics.mean(pnls):+.2f}%", flush=True)
    print(f"  Median:       {statistics.median(pnls):+.2f}%", flush=True)
    print(f"  Best / Worst: {max(pnls):+.2f}% / {min(pnls):+.2f}%", flush=True)
    if len(pnls) > 1:
        print(f"  Stdev:        {statistics.stdev(pnls):.2f}%", flush=True)
        print(f"  Sharpe/trade: {statistics.mean(pnls)/statistics.stdev(pnls):.3f}", flush=True)
    print(f"  Avg win:      +{statistics.mean(wins):.2f}%  ({len(wins)} trades)", flush=True)
    print(f"  Avg loss:     {statistics.mean(losses):+.2f}%  ({len(losses)} trades)", flush=True)
    print(f"  Exit reasons: {reasons}", flush=True)

    SIZE = 100.0
    equity = 1000.0
    peak = equity
    max_dd = 0.0
    monthly: dict[str, dict] = {}
    for t in trades:
        equity += (t["pnl_pct"] / 100.0) * SIZE
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak * 100)
        t["equity_after"] = round(equity, 2)
        ym = t["entry_dt"][:7]
        m = monthly.setdefault(ym, {"pnl_sum": 0.0, "n": 0, "wins": 0})
        m["pnl_sum"] += t["pnl_pct"]
        m["n"] += 1
        if t["pnl_pct"] > 0:
            m["wins"] += 1

    print(f"\n=== EQUITY ($1000 start, $100/trade) ===", flush=True)
    print(f"  Final:  ${equity:.2f}  ({(equity/1000-1)*100:+.1f}%)", flush=True)
    print(f"  Max DD: {max_dd:.1f}%", flush=True)

    print(f"\n=== MONTHLY ===", flush=True)
    print(f"{'Month':<8} {'n':>4} {'WR':>6} {'sum':>9} {'avg':>7}", flush=True)
    for ym in sorted(monthly):
        m = monthly[ym]
        wr = m["wins"] / m["n"] * 100
        print(f"{ym:<8} {m['n']:>4} {wr:>5.1f}% {m['pnl_sum']:+8.1f}% {m['pnl_sum']/m['n']:+6.2f}%", flush=True)

    elapsed = round(time.time() - t0, 1)
    payload = {
        "label": label,
        "strategy": {"gen": gen, "exit": ex, "sigma": DEFAULT_SIGMA, "spread_pct": spread},
        "data_dir": str(data_dir),
        "summary": {
            "n_trades": len(trades),
            "win_rate": round(len(wins) / len(pnls), 3),
            "avg_pnl_pct": round(statistics.mean(pnls), 2),
            "median_pnl_pct": round(statistics.median(pnls), 2),
            "max_drawdown_pct": round(max_dd, 1),
            "final_equity": round(equity, 2),
            "exit_reasons": reasons,
        },
        "monthly": monthly,
        "trades": trades,
        "elapsed_s": elapsed,
    }
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"\nSaved → {out_path} ({elapsed}s)", flush=True)


if __name__ == "__main__":
    main()
