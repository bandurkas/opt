import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from db.engine import apply_schema
from db import paper_repo
from db import btc_straddle_repo
from db import eth_straddle_repo
from db.repository import (
    latest_snapshot_age_seconds,
    persist_signal,
    recent_klines,
    recent_signals,
)
from services.paper_strategy import START_EQUITY_USD
from services.signal_freshness import compute_freshness
from services.analysis import (
    STRATEGIES,
    build_mtf_context,
    build_watchlist,
    distance,
    scan_top_opportunities,
    time_to_expiry,
)
from services.bybit_client import bybit_client
from services.market_data import build_market_snapshot


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        apply_schema()
        print("[main] DB schema applied", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[main] WARN: could not apply schema: {e!r}", flush=True)
    yield


app = FastAPI(
    title="ETH Options Assistant API",
    description="Real-time ETH options scanner with MTF momentum and ranked entry signals",
    version="3.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def read_root():
    return {"status": "ok", "version": "3.0.0"}


@app.get("/api/v1/market/eth-price")
def get_eth_price():
    price = bybit_client.get_spot_price("ETHUSDT")
    return {"symbol": "ETHUSDT", "price": price}


@app.get("/api/v1/market/snapshot")
def get_market_snapshot(symbol: str = "ETHUSDT"):
    spot = bybit_client.get_spot_price(symbol)
    if spot <= 0:
        raise HTTPException(status_code=502, detail="Bybit spot price unavailable")

    candles_1h = recent_klines(symbol, "1h", limit=50) or bybit_client.get_klines(symbol, "60", 50)
    snap = build_market_snapshot(spot, candles_1h, int(time.time() * 1000))

    mtf_ctx = build_mtf_context(symbol)
    return {
        **snap.__dict__,
        "mtf": mtf_ctx["mtf"],
        "regime": mtf_ctx["regime"],
        "atr_15m": mtf_ctx["atr_15m"],
        "data_freshness": {
            **mtf_ctx["data_freshness"],
            "last_snapshot_age_s": latest_snapshot_age_seconds(),
        },
    }


@app.get("/api/v1/strategies")
def list_strategies():
    return {
        "default": "fade_long_dated",
        "strategies": [
            {"id": sid, **{k: v for k, v in cfg.items() if k != "generators"}}
            for sid, cfg in STRATEGIES.items()
        ],
    }


@app.get("/api/v1/analysis/top")
def get_top_opportunities(
    base_coin: str = Query("ETH", description="Underlying coin (ETH/BTC)"),
    top_n: int = Query(3, ge=1, le=10),
    side: str | None = Query(None, description="Filter: 'call', 'put', or None for both"),
    max_distance_pct: float = Query(8.0, ge=0.5, le=30.0),
    max_hours: float | None = Query(None, description="Override strategy default"),
    min_hours: float | None = Query(None, description="Override strategy default"),
    min_score: float = Query(4.0, ge=0.0, le=10.0),
    risk_budget_usd: float = Query(100.0, ge=10.0, le=10000.0),
    strategy: str = Query("fade_long_dated", description="fade_long_dated | trend_continuation_legacy"),
    include_pullback: bool | None = Query(None),
    include_continuation: bool | None = Query(None),
    persist: bool = Query(True),
):
    symbol = f"{base_coin}USDT"
    spot = bybit_client.get_spot_price(symbol)
    if spot <= 0:
        raise HTTPException(status_code=502, detail="Bybit spot price unavailable")

    now_ms = int(time.time() * 1000)
    candles_1h = recent_klines(symbol, "1h", limit=50) or bybit_client.get_klines(symbol, "60", 50)
    market = build_market_snapshot(spot, candles_1h, now_ms)
    mtf_ctx = build_mtf_context(symbol)

    options = bybit_client.get_options_tickers(base_coin=base_coin)
    if side:
        side_filter = "C" if side.lower().startswith("c") else "P"
        options = [o for o in options if o["side"] == side_filter]

    top = scan_top_opportunities(
        options=options,
        market=market,
        now_ms=now_ms,
        mtf_ctx=mtf_ctx,
        top_n=top_n,
        min_hours=min_hours,
        max_hours=max_hours,
        max_distance_pct=max_distance_pct,
        min_score=min_score,
        risk_budget_usd=risk_budget_usd,
        strategy=strategy,
        include_pullback=include_pullback,
        include_continuation=include_continuation,
    )

    if persist:
        for op in top:
            try:
                persist_signal(
                    generated_at_ms=now_ms,
                    symbol=op["symbol"],
                    side=op["side"][0],
                    strike=float(op["strike"]),
                    expiry_ms=None,
                    score=float(op["scoring"]["score"]),
                    signal_type=op["scoring"]["signal_type"],
                    payload=op,
                )
            except Exception as e:  # noqa: BLE001
                print(f"[main] persist_signal failed for {op['symbol']}: {e!r}", flush=True)

    # When no active signal, build a passive watchlist for monitoring
    watchlist = build_watchlist(options, market, now_ms) if not top else []

    return {
        "generated_at_ms": now_ms,
        "market": {
            **market.__dict__,
            "mtf": mtf_ctx["mtf"],
            "regime": mtf_ctx["regime"],
            "atr_15m": mtf_ctx["atr_15m"],
        },
        "data_freshness": {
            **mtf_ctx["data_freshness"],
            "last_snapshot_age_s": latest_snapshot_age_seconds(),
        },
        "scanned_options": len(options),
        "top_opportunities": top,
        "watchlist": watchlist,
        "disclaimer": "Образовательный сигнал, не финансовая рекомендация. Управляй риском.",
    }


@app.get("/api/v1/signals/recent")
def get_recent_signals(limit: int = Query(50, ge=1, le=500)):
    return {"signals": recent_signals(limit=limit)}


# ───────────────────────── Paper trading API ─────────────────────────

@app.get("/api/v1/paper/state")
def paper_state():
    state = paper_repo.ensure_state(START_EQUITY_USD)
    stats = paper_repo.position_stats()
    latest = paper_repo.latest_equity()
    exit_counts = paper_repo.exit_reason_counts()
    try:
        freshness = compute_freshness()
    except Exception as e:  # noqa: BLE001
        freshness = {
            "last_signal_ts_ms": None,
            "last_signal_age_h": None,
            "bars_since_last_signal_5m": None,
            "signals_24h": 0,
            "window_5m_bars": 0,
            "error": repr(e),
        }
    cur_eq = float(latest["equity_usd"]) if latest else float(state["start_equity_usd"])
    return {
        "start_equity_usd": float(state["start_equity_usd"]),
        "started_at_ms": int(state["started_at_ms"]),
        "cb_cooldown_until_ms": int(state["cb_cooldown_until_ms"]),
        "cb_active": int(state["cb_cooldown_until_ms"]) > time.time() * 1000,
        "consec_losses": int(state["consec_losses"]),
        "current_equity_usd": cur_eq,
        # Realized vs unrealized split (latest snapshot)
        "realized_usd": float(latest["realized_usd"]) if latest else 0.0,
        "unrealized_usd": float(latest["unrealized_usd"]) if latest else 0.0,
        # Max drawdown from latest snapshot (running peak-to-trough since started)
        "max_dd_pct": float(latest["max_dd_pct"]) if latest and latest.get("max_dd_pct") is not None else 0.0,
        "n_open": stats["n_open"],
        "n_closed": stats["n_closed"],
        "wins": stats["wins"],
        "losses": stats["losses"],
        "win_rate": (stats["wins"] / stats["n_closed"]) if stats["n_closed"] else None,
        "avg_pnl_pct": stats["avg_pnl_pct"],
        # Exit-reason breakdown
        "exit_counts": exit_counts,
        **freshness,
    }


@app.get("/api/v1/paper/positions")
def paper_positions_endpoint(
    status: str = Query("open", description="'open' | 'recent' | 'all'"),
    limit: int = Query(50, ge=1, le=500),
):
    if status == "open":
        rows = paper_repo.open_positions()
    else:
        rows = paper_repo.recent_positions(limit=limit)
    # Cast Decimal → float for JSON
    out = []
    for r in rows:
        out.append({k: (float(v) if hasattr(v, "real") and not isinstance(v, bool) else v)
                    if v is not None else None for k, v in r.items()})
    return {"positions": out, "count": len(out)}


@app.get("/api/v1/paper/conditions")
def paper_conditions():
    """Live check: does the current 5m bar satisfy all entry conditions?

    V2 trend-following hybrid: includes active_side (P / C / None for range+neutral)
    and 7d return. Frontend reads `ret_threshold_put` / `ret_threshold_call` as
    the symmetric V2 boundaries (e.g. ±0.5%).
    """
    from services.adx_score import compute_adx_score
    from services.paper_strategy import entry_proximity, evaluate_conditions
    from services.strategy_config import (
        CALL_GEN_KWARGS,
        PUT_GEN_KWARGS,
        RET_7D_THRESHOLD,
    )

    symbol = "ETHUSDT"
    k5 = recent_klines(symbol, "5m", limit=2100)
    k15 = recent_klines(symbol, "15m", limit=220)
    k1h = recent_klines(symbol, "1h", limit=270)
    cond = evaluate_conditions(k5, k15, k1h)
    cond["checked_at_ms"] = int(time.time() * 1000)
    cond["bars_available"] = {"5m": len(k5), "15m": len(k15), "1h": len(k1h)}

    # ADX readiness score + entry-proximity gauge (display only — see entry_proximity).
    adx = compute_adx_score(k1h)
    cond["adx"] = adx
    cond["proximity"] = entry_proximity(cond, adx.get("score", 0.0))

    active_side = cond.get("active_side")
    # V2 thresholds: Put when ret > +T, Call when ret < -T. UI fields kept
    # for backward compat with frontend (ret_threshold_put / _call).
    side_gen = CALL_GEN_KWARGS if active_side == "C" else PUT_GEN_KWARGS
    cond["thresholds"] = {
        # NOTE: under V2 the names are inverted relative to Config B —
        # ret_threshold_put is the MIN ret to allow Put (positive boundary).
        "ret_threshold_put": +RET_7D_THRESHOLD,
        "ret_threshold_call": -RET_7D_THRESHOLD,
        "ret_7d": cond.get("ret_7d"),
        "active_side": active_side,
        "dead_zone": False,  # V2: no dead zone — range allows both
        "vol_threshold": side_gen["vol_threshold"],
        "regime_filter": list(side_gen["regime_filter"] or []),
        "mtf_direction_filter": side_gen["mtf_direction_filter"],
        "mtf_min_aligned": 2,
        "bull_market_ratio_max": side_gen["bull_market_ratio_max"],
    }
    return cond


@app.get("/api/v1/paper/audit")
def paper_audit(hours: int = Query(24, ge=1, le=168)):
    """Signal audit log — every signal check with accept/reject reason."""
    rows = paper_repo.recent_signal_audit(hours=hours)
    # Compute summary stats
    total = len(rows)
    generated = sum(1 for r in rows if r.get("signal_generated"))
    accepted = sum(1 for r in rows if r.get("accepted"))
    rejected = sum(1 for r in rows if r.get("accepted") is False)
    dead_zone = sum(1 for r in rows if r.get("dead_zone"))
    by_reason: dict[str, int] = {}
    for r in rows:
        reason = r.get("reject_reason") or "accepted"
        by_reason[reason] = by_reason.get(reason, 0) + 1

    return {
        "hours": hours,
        "total_checks": total,
        "signal_generated": generated,
        "accepted": accepted,
        "rejected": rejected,
        "dead_zone": dead_zone,
        "reject_reasons": by_reason,
        "entries": rows,
    }


@app.get("/api/v1/paper/equity_history")
def paper_equity_history(hours: int = Query(168, ge=1, le=8760)):
    rows = paper_repo.equity_history(hours=hours)
    return {
        "hours": hours,
        "points": [
            {
                "ts_ms": int(r["ts_ms"]),
                "equity": float(r["equity_usd"]),
                "realized": float(r["realized_usd"]),
                "unrealized": float(r["unrealized_usd"]),
                "n_open": int(r["n_open"]),
                "n_closed": int(r["n_closed"]),
            } for r in rows
        ],
    }


# ───────────────────────── BTC straddle bot API ─────────────────────────
# Read-only mirror of the /api/v1/paper/* trio above, against btc_straddle_repo
# instead of paper_repo — separate book, separate tables, same dashboard pattern.

@app.get("/api/v1/btc-straddle/state")
def btc_straddle_state():
    from services.btc_straddle_loop import START_EQUITY_USD as BTC_START_EQUITY_USD

    state = btc_straddle_repo.ensure_state(BTC_START_EQUITY_USD)
    stats = btc_straddle_repo.position_stats()
    latest = btc_straddle_repo.latest_equity()
    exit_counts = btc_straddle_repo.exit_reason_counts()
    cur_eq = float(latest["equity_usd"]) if latest else float(state["start_equity_usd"])
    return {
        "start_equity_usd": float(state["start_equity_usd"]),
        "started_at_ms": int(state["started_at_ms"]),
        "last_cycle_id": int(state["last_cycle_id"]),
        "current_equity_usd": cur_eq,
        "realized_usd": float(latest["realized_usd"]) if latest else 0.0,
        "unrealized_usd": float(latest["unrealized_usd"]) if latest else 0.0,
        "max_dd_pct": float(latest["max_dd_pct"]) if latest and latest.get("max_dd_pct") is not None else 0.0,
        "n_open": stats["n_open"],
        "n_closed": stats["n_closed"],
        "wins": stats["wins"],
        "losses": stats["losses"],
        "win_rate": (stats["wins"] / stats["n_closed"]) if stats["n_closed"] else None,
        "avg_pnl_pct": stats["avg_pnl_pct"],
        "exit_counts": exit_counts,
    }


@app.get("/api/v1/btc-straddle/positions")
def btc_straddle_positions_endpoint(
    status: str = Query("open", description="'open' | 'recent' | 'all'"),
    limit: int = Query(50, ge=1, le=500),
):
    if status == "open":
        rows = btc_straddle_repo.open_positions()
    else:
        rows = btc_straddle_repo.recent_positions(limit=limit)
    out = []
    for r in rows:
        out.append({k: (float(v) if hasattr(v, "real") and not isinstance(v, bool) else v)
                    if v is not None else None for k, v in r.items()})
    return {"positions": out, "count": len(out)}


@app.get("/api/v1/btc-straddle/equity_history")
def btc_straddle_equity_history(hours: int = Query(168, ge=1, le=8760)):
    rows = btc_straddle_repo.equity_history(hours=hours)
    return {
        "hours": hours,
        "points": [
            {
                "ts_ms": int(r["ts_ms"]),
                "equity": float(r["equity_usd"]),
                "realized": float(r["realized_usd"]),
                "unrealized": float(r["unrealized_usd"]),
                "n_open": int(r["n_open"]),
                "n_closed": int(r["n_closed"]),
            } for r in rows
        ],
    }


# ───────────────────────── ETH straddle bot API ─────────────────────────
# Read-only mirror of the /api/v1/btc-straddle/* trio above, against
# eth_straddle_repo instead — separate book, separate tables, same pattern.

@app.get("/api/v1/eth-straddle/state")
def eth_straddle_state():
    from services.eth_straddle_loop import START_EQUITY_USD as ETH_START_EQUITY_USD

    state = eth_straddle_repo.ensure_state(ETH_START_EQUITY_USD)
    stats = eth_straddle_repo.position_stats()
    latest = eth_straddle_repo.latest_equity()
    exit_counts = eth_straddle_repo.exit_reason_counts()
    cur_eq = float(latest["equity_usd"]) if latest else float(state["start_equity_usd"])
    return {
        "start_equity_usd": float(state["start_equity_usd"]),
        "started_at_ms": int(state["started_at_ms"]),
        "last_cycle_id": int(state["last_cycle_id"]),
        "current_equity_usd": cur_eq,
        "realized_usd": float(latest["realized_usd"]) if latest else 0.0,
        "unrealized_usd": float(latest["unrealized_usd"]) if latest else 0.0,
        "max_dd_pct": float(latest["max_dd_pct"]) if latest and latest.get("max_dd_pct") is not None else 0.0,
        "n_open": stats["n_open"],
        "n_closed": stats["n_closed"],
        "wins": stats["wins"],
        "losses": stats["losses"],
        "win_rate": (stats["wins"] / stats["n_closed"]) if stats["n_closed"] else None,
        "avg_pnl_pct": stats["avg_pnl_pct"],
        "exit_counts": exit_counts,
    }


@app.get("/api/v1/eth-straddle/positions")
def eth_straddle_positions_endpoint(
    status: str = Query("open", description="'open' | 'recent' | 'all'"),
    limit: int = Query(50, ge=1, le=500),
):
    if status == "open":
        rows = eth_straddle_repo.open_positions()
    else:
        rows = eth_straddle_repo.recent_positions(limit=limit)
    out = []
    for r in rows:
        out.append({k: (float(v) if hasattr(v, "real") and not isinstance(v, bool) else v)
                    if v is not None else None for k, v in r.items()})
    return {"positions": out, "count": len(out)}


@app.get("/api/v1/eth-straddle/equity_history")
def eth_straddle_equity_history(hours: int = Query(168, ge=1, le=8760)):
    rows = eth_straddle_repo.equity_history(hours=hours)
    return {
        "hours": hours,
        "points": [
            {
                "ts_ms": int(r["ts_ms"]),
                "equity": float(r["equity_usd"]),
                "realized": float(r["realized_usd"]),
                "unrealized": float(r["unrealized_usd"]),
                "n_open": int(r["n_open"]),
                "n_closed": int(r["n_closed"]),
            } for r in rows
        ],
    }


@app.get("/api/v1/analysis/test")
def test_analysis(current_price: float = 2121.0, strike: float = 2150.0, hours_to_expiry: int = 18):
    """Legacy stub kept for the Telegram bot's /eth command."""
    expiry_ms = int(time.time() * 1000) + (hours_to_expiry * 60 * 60 * 1000)
    now_ms = int(time.time() * 1000)
    return {
        "contract": f"Call {strike}",
        "distance": distance(current_price, strike),
        "time": time_to_expiry(expiry_ms, now_ms),
        "entry_evaluation": {"score": 7, "signal": "Хороший"},
    }
