import time

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from services.bybit_client import bybit_client
from services.market_data import build_market_snapshot
from services.analysis import scan_top_opportunities, time_to_expiry, distance


app = FastAPI(
    title="ETH Options Assistant API",
    description="Real-time ETH options scanner with entry signals",
    version="2.0.0",
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
    return {"status": "ok", "version": "2.0.0"}


@app.get("/api/v1/market/eth-price")
def get_eth_price():
    price = bybit_client.get_spot_price("ETHUSDT")
    return {"symbol": "ETHUSDT", "price": price}


@app.get("/api/v1/market/snapshot")
def get_market_snapshot(symbol: str = "ETHUSDT"):
    spot = bybit_client.get_spot_price(symbol)
    if spot <= 0:
        raise HTTPException(status_code=502, detail="Bybit spot price unavailable")
    candles = bybit_client.get_klines(symbol=symbol, interval="60", limit=50)
    snap = build_market_snapshot(spot, candles, int(time.time() * 1000))
    return snap.__dict__


@app.get("/api/v1/analysis/top")
def get_top_opportunities(
    base_coin: str = Query("ETH", description="Underlying coin (ETH/BTC)"),
    top_n: int = Query(3, ge=1, le=10),
    side: str | None = Query(None, description="Filter: 'call', 'put', or None for both"),
    max_distance_pct: float = Query(8.0, ge=0.5, le=30.0),
    max_hours: float = Query(30 * 24.0, ge=1, le=120 * 24.0),
):
    symbol = f"{base_coin}USDT"
    spot = bybit_client.get_spot_price(symbol)
    if spot <= 0:
        raise HTTPException(status_code=502, detail="Bybit spot price unavailable")

    candles = bybit_client.get_klines(symbol=symbol, interval="60", limit=50)
    now_ms = int(time.time() * 1000)
    market = build_market_snapshot(spot, candles, now_ms)

    options = bybit_client.get_options_tickers(base_coin=base_coin)
    if side:
        side_filter = "C" if side.lower().startswith("c") else "P"
        options = [o for o in options if o["side"] == side_filter]

    top = scan_top_opportunities(
        options=options,
        market=market,
        now_ms=now_ms,
        top_n=top_n,
        max_distance_pct=max_distance_pct,
        max_hours=max_hours,
    )

    return {
        "generated_at_ms": now_ms,
        "market": market.__dict__,
        "scanned_options": len(options),
        "top_opportunities": top,
        "disclaimer": "Образовательный сигнал, не финансовая рекомендация. Управляй риском.",
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
