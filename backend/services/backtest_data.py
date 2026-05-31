"""Paginated historical kline fetcher for Bybit V5.

Free public endpoint, no auth required. Returns up to 1000 candles per call.

Local fallback: export VPS klines to ``data/eth_{5m,15m,1h}.json`` and call
``load_local_set()`` when Bybit is unreachable (e.g. on Mac).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Literal

Interval = Literal["1", "3", "5", "15", "30", "60", "120", "240", "D"]

_session = None


def _get_session():
    global _session
    if _session is None:
        from pybit.unified_trading import HTTP
        _session = HTTP(testnet=False)
    return _session


def fetch_klines(
    symbol: str,
    interval: Interval,
    start_ms: int,
    end_ms: int,
    category: str = "linear",
    chunk: int = 1000,
    sleep_s: float = 0.05,
) -> list[dict]:
    """Fetch all klines in [start_ms, end_ms], paginated. Returns oldest→newest."""
    out: list[dict] = []
    cursor_end = end_ms
    while cursor_end > start_ms:
        try:
            r = _get_session().get_kline(
                category=category,
                symbol=symbol,
                interval=interval,
                start=start_ms,
                end=cursor_end,
                limit=chunk,
            )
        except Exception as e:  # noqa: BLE001
            print(f"[backtest_data] error: {e!r}", flush=True)
            break

        rows = r.get("result", {}).get("list", [])
        if not rows:
            break

        # Bybit returns newest first; rows[0] is most recent within the window.
        for row in rows:
            out.append({
                "start_ms": int(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
            })

        oldest_in_batch = int(rows[-1][0])
        if oldest_in_batch <= start_ms:
            break
        # next page: end just before the oldest we got
        cursor_end = oldest_in_batch - 1
        time.sleep(sleep_s)

    # dedup + sort ascending
    seen = set()
    uniq = []
    for c in sorted(out, key=lambda x: x["start_ms"]):
        if c["start_ms"] in seen:
            continue
        seen.add(c["start_ms"])
        uniq.append(c)
    return uniq


def fetch_set(
    symbol: str,
    days: int,
    intervals: tuple[str, ...] = ("5", "15", "60"),
) -> dict[str, list[dict]]:
    """Pull klines for multiple intervals over the last `days`. Returns dict keyed by interval."""
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 86_400_000
    out: dict[str, list[dict]] = {}
    for iv in intervals:
        print(f"[backtest_data] fetching {symbol} {iv} from {days}d ago...", flush=True)
        candles = fetch_klines(symbol, iv, start_ms, end_ms)  # type: ignore[arg-type]
        print(f"[backtest_data]   got {len(candles)} candles", flush=True)
        out[iv] = candles
    return out


_LOCAL_IV_MAP = {"5": "5m", "15": "15m", "60": "1h"}
_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def load_local_set(
    data_dir: Path | str | None = None,
    intervals: tuple[str, ...] = ("5", "15", "60"),
) -> dict[str, list[dict]]:
    """Load klines exported from VPS (``data/eth_5m.json`` etc.).

    Returns the same shape as ``fetch_set``: keys ``"5"``, ``"15"``, ``"60"``.
    """
    root = Path(data_dir) if data_dir else _DEFAULT_DATA_DIR
    out: dict[str, list[dict]] = {}
    for iv in intervals:
        fname = _LOCAL_IV_MAP.get(iv, iv)
        path = root / f"eth_{fname}.json"
        if not path.exists():
            raise FileNotFoundError(f"missing local klines: {path}")
        candles = json.loads(path.read_text())
        print(f"[backtest_data] loaded {path.name}: {len(candles)} candles", flush=True)
        out[iv] = candles
    return out
