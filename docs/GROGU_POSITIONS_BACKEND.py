"""
Grogu1 Positions API - Backend Implementation
For FastAPI/Flask app on VPS (port 8000)

Add this to your Mission Control API handler.
"""

from fastapi import FastAPI, Query
from typing import Optional, List, Dict, Any
import asyncio
from datetime import datetime
import time

app = FastAPI()


# ============================================================================
# DATA MODELS
# ============================================================================

class KlineData:
    def __init__(self, time: int, open: float, high: float, low: float, close: float, volume: float):
        self.time = time
        self.open = open
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume

    def to_dict(self):
        return {
            'time': self.time,
            'open': self.open,
            'high': self.high,
            'low': self.low,
            'close': self.close,
            'volume': self.volume,
        }


class LegData:
    def __init__(self, contract: str, size: float, entry_price: float, entry_time: int):
        self.contract = contract
        self.size = size
        self.entry_price = entry_price
        self.entry_time = entry_time
        self.current_price = entry_price
        self.status = "OPEN"
        self.sl_hit = False
        self.tp1_hit = False
        self.tp2_hit = False

    @property
    def pnl(self) -> float:
        return (self.current_price - self.entry_price) * self.size * 100  # Rough calculation

    @property
    def pnl_pct(self) -> float:
        if self.entry_price == 0:
            return 0
        return ((self.current_price - self.entry_price) / self.entry_price) * 100

    def to_dict(self):
        return {
            'contract': self.contract,
            'size': self.size,
            'entry_price': self.entry_price,
            'entry_time': self.entry_time,
            'current_price': self.current_price,
            'pnl': self.pnl,
            'pnl_pct': self.pnl_pct,
            'status': self.status,
            'sl_hit': self.sl_hit,
            'tp1_hit': self.tp1_hit,
            'tp2_hit': self.tp2_hit,
        }


class PositionData:
    def __init__(self, cycle_id: int, symbol: str, entry_price: float, entry_time: int, expiry_time: int):
        self.cycle_id = cycle_id
        self.symbol = symbol
        self.entry_price = entry_price
        self.entry_time = entry_time
        self.expiry_time = expiry_time
        self.current_price = entry_price
        self.current_time = int(time.time())

        # Levels (from strategy)
        self.levels = {
            'call_entry': entry_price,
            'call_sl': entry_price + 70,      # Example: +$70 for ETH
            'call_tp1': entry_price + 50,
            'call_tp2': entry_price + 20,
            'put_entry': entry_price,
            'put_sl': entry_price - 70,
            'put_tp1': entry_price - 50,
            'put_tp2': entry_price - 20,
        }

        # Legs
        self.call_leg = LegData(f"{symbol}-CALL", 0.1, entry_price, entry_time)
        self.put_leg = LegData(f"{symbol}-PUT", 0.1, entry_price, entry_time)

        self.klines: List[KlineData] = []

    @property
    def cycle_pnl(self) -> float:
        return self.call_leg.pnl + self.put_leg.pnl

    @property
    def cycle_pnl_pct(self) -> float:
        total_entry = (self.call_leg.entry_price + self.put_leg.entry_price) * self.call_leg.size * 100
        if total_entry == 0:
            return 0
        return (self.cycle_pnl / total_entry) * 100

    @property
    def call_leg_status(self) -> str:
        if self.call_leg.sl_hit:
            return "CLOSED_SL"
        if self.call_leg.tp2_hit:
            return "CLOSED_TP2"
        if self.call_leg.tp1_hit:
            return "CLOSED_TP1"
        return "OPEN"

    @property
    def put_leg_status(self) -> str:
        if self.put_leg.sl_hit:
            return "CLOSED_SL"
        if self.put_leg.tp2_hit:
            return "CLOSED_TP2"
        if self.put_leg.tp1_hit:
            return "CLOSED_TP1"
        return "OPEN"

    def update_price(self, price: float):
        """Update current price and check SL/TP status"""
        self.current_price = price
        self.current_time = int(time.time())

        # Update legs
        self.call_leg.current_price = price
        self.put_leg.current_price = price

        # Check Call leg
        if price >= self.levels['call_sl']:
            self.call_leg.sl_hit = True
            self.call_leg.status = "CLOSED_SL"
        elif price >= self.levels['call_tp2']:
            self.call_leg.tp2_hit = True
            self.call_leg.status = "CLOSED_TP2"
        elif price >= self.levels['call_tp1']:
            self.call_leg.tp1_hit = True
            self.call_leg.status = "CLOSED_TP1"

        # Check Put leg
        if price <= self.levels['put_sl']:
            self.put_leg.sl_hit = True
            self.put_leg.status = "CLOSED_SL"
        elif price <= self.levels['put_tp2']:
            self.put_leg.tp2_hit = True
            self.put_leg.status = "CLOSED_TP2"
        elif price <= self.levels['put_tp1']:
            self.put_leg.tp1_hit = True
            self.put_leg.status = "CLOSED_TP1"

    def add_kline(self, kline: KlineData):
        """Add kline and keep rolling buffer of ~288 (24h @ 5min)"""
        self.klines.append(kline)
        if len(self.klines) > 288:
            self.klines.pop(0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'cycle_id': self.cycle_id,
            'symbol': self.symbol,
            'side': 'STRADDLE',
            'entry_price': self.entry_price,
            'entry_time': self.entry_time,
            'expiry_time': self.expiry_time,
            'current_price': self.current_price,
            'current_time': self.current_time,
            'levels': self.levels,
            'call_leg': self.call_leg.to_dict(),
            'put_leg': self.put_leg.to_dict(),
            'cycle_pnl': self.cycle_pnl,
            'cycle_pnl_pct': self.cycle_pnl_pct,
            'call_leg_status': self.call_leg_status,
            'put_leg_status': self.put_leg_status,
            'klines': [k.to_dict() for k in self.klines],
        }


# ============================================================================
# GLOBAL STATE (In production, use a database)
# ============================================================================

positions: Dict[int, PositionData] = {}
current_price = 3850.25


# ============================================================================
# API ENDPOINT
# ============================================================================

@app.get("/api/v1/grogu/positions")
async def get_grogu_positions(
    with_levels: Optional[bool] = Query(False),
    cycle_id: Optional[int] = Query(None),
    include_klines: Optional[bool] = Query(True),
    kline_limit: Optional[int] = Query(288),
):
    """
    Get Grogu1 position data with SL/TP levels for Mission Control.

    Required: with_levels=true
    Optional: cycle_id (else latest)
    """

    if not with_levels:
        return {
            "error": "with_levels=true required",
            "code": "MISSING_PARAM"
        }, 400

    # Get position (latest or specified)
    if cycle_id:
        position = positions.get(cycle_id)
        if not position:
            return {
                "error": f"Cycle {cycle_id} not found",
                "code": "NOT_FOUND"
            }, 404
    else:
        # Return latest position
        if not positions:
            return {
                "error": "No positions found",
                "code": "NOT_FOUND"
            }, 404
        position = positions[max(positions.keys())]

    # Build response
    response = position.to_dict()

    # Optionally limit klines
    if not include_klines:
        response['klines'] = []
    elif kline_limit:
        response['klines'] = response['klines'][-kline_limit:]

    return response, 200


# ============================================================================
# MOCK DATA GENERATION (for testing)
# ============================================================================

async def simulate_trading_loop():
    """
    Simulate Grogu1 trading cycles (replace with real eth_straddle_loop.py integration)
    """
    global current_price

    cycle_id = 1
    base_price = 3850.25

    while True:
        await asyncio.sleep(5)  # Update every 5 seconds

        # Create position if not exists
        if cycle_id not in positions:
            now = int(time.time())
            position = PositionData(
                cycle_id=cycle_id,
                symbol="ETH",
                entry_price=base_price,
                entry_time=now,
                expiry_time=now + 86400,  # 24h expiry
            )
            positions[cycle_id] = position

        # Simulate price movement (random walk)
        import random
        change = random.uniform(-2, 2)
        current_price += change
        current_price = max(3700, min(4000, current_price))  # Keep in range

        # Update position
        position = positions[cycle_id]
        position.update_price(current_price)

        # Add mock kline every 5 seconds
        import random as rnd
        kline = KlineData(
            time=int(time.time()),
            open=current_price - rnd.uniform(0, 1),
            high=current_price + rnd.uniform(0, 2),
            low=current_price - rnd.uniform(0, 2),
            close=current_price,
            volume=rnd.uniform(100, 500),
        )
        position.add_kline(kline)

        # Cycle expires after 24h, start new cycle
        if position.current_time >= position.expiry_time:
            cycle_id += 1


# ============================================================================
# STARTUP / INTEGRATION
# ============================================================================

@app.on_event("startup")
async def startup_event():
    """
    Start trading loop simulation.
    In production, integrate with real eth_straddle_loop.py:

    1. Import eth_straddle_loop module
    2. Subscribe to position updates via callback:
       eth_straddle_loop.on_position_update(lambda pos: update_position_data(pos))
    3. Subscribe to kline updates:
       eth_straddle_loop.on_kline_update(lambda kline: add_kline_data(kline))
    """
    asyncio.create_task(simulate_trading_loop())


# ============================================================================
# INTEGRATION HOOKS
# ============================================================================

def update_position_from_eth_straddle(position_event: Dict[str, Any]):
    """
    Called by eth_straddle_loop.py when position opens/updates.

    Example: eth_straddle_loop.py calls this after opening a cycle:
        api.update_position_from_eth_straddle({
            'cycle_id': 7,
            'symbol': 'ETH',
            'entry_price': 3850.25,
            'entry_time': 1719225945,
            'expiry_time': 1719312345,
            'levels': {
                'call_sl': 3920.00,
                'call_tp1': 3900.00,
                'call_tp2': 3870.00,
                'put_sl': 3780.00,
                'put_tp1': 3800.00,
                'put_tp2': 3830.00,
            }
        })
    """
    cycle_id = position_event['cycle_id']
    position = PositionData(
        cycle_id=cycle_id,
        symbol=position_event.get('symbol', 'ETH'),
        entry_price=position_event['entry_price'],
        entry_time=position_event['entry_time'],
        expiry_time=position_event['expiry_time'],
    )
    position.levels = position_event.get('levels', position.levels)
    positions[cycle_id] = position


def update_kline_data(symbol: str, kline: KlineData):
    """
    Called by eth_straddle_loop.py when new kline arrives.
    Adds to latest position.
    """
    if not positions:
        return
    latest = positions[max(positions.keys())]
    latest.add_kline(kline)


# ============================================================================
# TESTING
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
