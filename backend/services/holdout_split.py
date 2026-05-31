"""True out-of-sample holdout protocol.

The optimizer must NEVER see the holdout window during ranking.
Pipeline:

  1. Load full 365d klines.
  2. ``train_pool_signals``  — signals before cutoff (kline last_ms − HOLDOUT_DAYS).
     Fed into ``local_optimizer.split_signals(0.70)`` → train + test for ranking.
  3. ``holdout_signals``     — signals at/after cutoff. ONLY used by
     finalize_best.py to validate the chosen winner.

Why ``local_optimizer.get_signals`` returns full-history signals today: it
runs ``gen_sell_premium_iv_high`` on full klines (105k bars) and that gen
returns 268 signals spread across 365 days. The OOS "test" 30% split then
takes the last ~109 days by signal index. For cd=12 sparse strategy, the
last 90 days by time ≈ 81 signals ≈ ALL of the 30% test split. So
``eval_holdout`` was scoring on the same bars used to pick winners.

This module fixes that by cutting the signal stream at the kline cutoff.
"""
from __future__ import annotations

import os
from typing import Iterable

# Configurable via env (HOLDOUT_DAYS=90 default). Single source of truth for
# any backtest tooling that needs to honor the protocol.
HOLDOUT_DAYS = int(os.getenv("HOLDOUT_DAYS", "90"))

_MS_PER_DAY = 86_400_000


def holdout_cutoff_ms(k5: list[dict]) -> int:
    """Cutoff timestamp: anything before is fair game for ranking, anything
    at/after is the held-out validation set."""
    if not k5:
        return 0
    return int(k5[-1]["start_ms"]) - HOLDOUT_DAYS * _MS_PER_DAY


def split_signals_by_holdout(signals: Iterable[dict], cutoff_ms: int) -> tuple[list[dict], list[dict]]:
    """(train_pool, holdout)."""
    train_pool: list[dict] = []
    holdout: list[dict] = []
    for s in signals:
        ts = int(s["ts_ms"])
        if ts < cutoff_ms:
            train_pool.append(s)
        else:
            holdout.append(s)
    return train_pool, holdout
