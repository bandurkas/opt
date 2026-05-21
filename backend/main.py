import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from db.engine import apply_schema
from db import paper_repo
from db.repository import (
    latest_snapshot_age_seconds,
    persist_signal,
    recent_klines,
    recent_signals,
)
from services.paper_strategy import START_EQUITY_USD
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
    return {
        "start_equity_usd": float(state["start_equity_usd"]),
        "started_at_ms": int(state["started_at_ms"]),
        "cb_cooldown_until_ms": int(state["cb_cooldown_until_ms"]),
        "cb_active": int(state["cb_cooldown_until_ms"]) > time.time() * 1000,
        "consec_losses": int(state["consec_losses"]),
        "current_equity_usd": float(latest["equity_usd"]) if latest else float(state["start_equity_usd"]),
        "realized_usd": stats["realized_usd"],
        "n_open": stats["n_open"],
        "n_closed": stats["n_closed"],
        "wins": stats["wins"],
        "losses": stats["losses"],
        "win_rate": (stats["wins"] / stats["n_closed"]) if stats["n_closed"] else None,
        "avg_pnl_pct": stats["avg_pnl_pct"],
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
    Returns per-condition booleans for UI indicators."""
    from services.paper_strategy import evaluate_conditions

    symbol = "ETHUSDT"
    k5 = recent_klines(symbol, "5m", limit=600)
    k15 = recent_klines(symbol, "15m", limit=220)
    k1h = recent_klines(symbol, "1h", limit=270)
    cond = evaluate_conditions(k5, k15, k1h)
    cond["checked_at_ms"] = int(time.time() * 1000)
    cond["bars_available"] = {"5m": len(k5), "15m": len(k15), "1h": len(k1h)}
    return cond


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
