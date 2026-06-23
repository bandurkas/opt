"""Sequence-of-returns check on the 4y ETH dollar-SL account sim.

eth_dollar_sl_4y_sweep.py showed every frac going net-negative over the full
4y window despite a positive per-trade avg everywhere, with month 1
(2022-07) immediately at -57%. This script asks the obvious follow-up: is
that catastrophe specific to starting the $400 account right at 2022-07, or
does a margin-constrained $400 account blow up from ANY starting point hit
by a crash? Re-runs run_engine() fresh (separate $400, reset peak/maxDD/cb)
from ~10 different quarterly anchors across the same 4y trade list, for both
the deployed dollar-SL frac=0.10 mix and the live %-SL=0.75 baseline.

Run:
    cd backend && PYTHONPATH=. python3 services/eth_dollar_sl_start_point_sweep.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from multiprocessing import cpu_count
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.local_optimizer import find_data_dir              # noqa: E402
from services.multi_coin_signals import load_coin                # noqa: E402
from services.option_futures_complement import gen_parallel      # noqa: E402
from services.strategy_config import CALL_GEN_KWARGS, PUT_GEN_KWARGS  # noqa: E402
from services.iv_mixed_deposit import build_trades, run_engine, PUT_96, CALL_24  # noqa: E402
from services.eth_dollar_sl_4y_sweep import build_trades_dollar_sl  # noqa: E402

ANCHORS = ["2022-07", "2022-10", "2023-01", "2023-04", "2023-07", "2023-10",
          "2024-01", "2024-04", "2024-07", "2024-10", "2025-01", "2025-04"]


def to_ts_ms(month_str: str) -> int:
    y, m = (int(x) for x in month_str.split("-"))
    return int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)


def main():
    ncore = cpu_count()
    print(f"[1] klines (4y) + parallel gen ({ncore} cores)...")
    k5, k15, k1h = load_coin("eth_long", find_data_dir(None))
    k1h = sorted(k1h, key=lambda c: c["start_ms"])

    calls = gen_parallel(k5, k15, k1h, CALL_GEN_KWARGS, ncore)
    puts = gen_parallel(k5, k15, k1h, PUT_GEN_KWARGS, ncore)
    print(f"    {len(calls)} call signals, {len(puts)} put signals")

    put_live = build_trades(puts, k5, k1h, PUT_96)
    call_live = build_trades(calls, k5, k1h, CALL_24)
    call_dollar10 = build_trades_dollar_sl(calls, k5, k1h, sl_dollar_frac=0.10)

    configs = {
        "LIVE %-SL=0.75":   call_live + put_live,
        "DEPLOYED $-SL=0.10": call_dollar10 + put_live,
    }

    last_ts = max(t["ts"] for trades in configs.values() for t in trades)
    print("\n---------- account sim from each quarterly start, fresh $400 ----------")
    results = []
    for anchor in ANCHORS:
        start_ts = to_ts_ms(anchor)
        if start_ts >= last_ts:
            break
        for label, trades in configs.items():
            sub = [t for t in trades if t["ts"] >= start_ts]
            if not sub:
                continue
            n_taken, equity, _ = run_engine(sub, f"=== [{anchor}] {label} ===")
            results.append((anchor, label, n_taken, equity))

    print("\n---------- summary ----------")
    print(f"{'anchor':<9}{'config':<22}{'taken':>7}{'FINAL':>12}{'ret%':>9}")
    for anchor, label, n_taken, equity in results:
        ret = (equity / 400 - 1) * 100
        print(f"{anchor:<9}{label:<22}{n_taken:>7}{equity:>12,.2f}{ret:>+9.1f}")


if __name__ == "__main__":
    main()
