# Grogu1 Positions API Specification

## Endpoint

```
GET /api/v1/grogu/positions?with_levels=true&cycle_id={optional}
```

## Purpose
Serves real-time position data with SL/TP levels for Mission Control charting.

## Query Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `with_levels` | bool | Yes | — | Include SL/TP levels in response |
| `cycle_id` | int | No | latest | Fetch specific cycle (else latest) |
| `include_klines` | bool | No | true | Include OHLC kline data |
| `kline_limit` | int | No | 288 | Max klines to return (288 = 24h @ 5min) |

## Response Format

```json
{
  "cycle_id": 7,
  "symbol": "ETH",
  "side": "STRADDLE",
  "entry_price": 3850.25,
  "entry_time": 1719225945,
  "expiry_time": 1719312345,
  "current_price": 3852.50,
  "current_time": 1719226200,
  
  "levels": {
    "call_entry": 3850.25,
    "call_sl": 3920.00,
    "call_tp1": 3900.00,
    "call_tp2": 3870.00,
    
    "put_entry": 3850.25,
    "put_sl": 3780.00,
    "put_tp1": 3800.00,
    "put_tp2": 3830.00
  },
  
  "call_leg": {
    "contract": "ETH-25JUN26-3920-C-USDT",
    "size": 0.1,
    "entry_price": 42.50,
    "entry_time": 1719225945,
    "current_price": 45.20,
    "pnl": 27.00,
    "pnl_pct": 6.35,
    "status": "OPEN",
    "sl_hit": false,
    "tp1_hit": false,
    "tp2_hit": false
  },
  
  "put_leg": {
    "contract": "ETH-25JUN26-3780-P-USDT",
    "size": 0.1,
    "entry_price": 38.25,
    "entry_time": 1719225945,
    "current_price": 35.80,
    "pnl": -24.50,
    "pnl_pct": -6.40,
    "status": "OPEN",
    "sl_hit": false,
    "tp1_hit": false,
    "tp2_hit": false
  },
  
  "cycle_pnl": 2.50,
  "cycle_pnl_pct": 0.31,
  "call_leg_status": "OPEN",
  "put_leg_status": "OPEN",
  
  "klines": [
    {
      "time": 1719225900,
      "open": 3850.00,
      "high": 3852.75,
      "low": 3849.50,
      "close": 3850.25,
      "volume": 1250.5
    },
    {
      "time": 1719225960,
      "open": 3850.25,
      "high": 3851.00,
      "low": 3850.00,
      "close": 3850.75,
      "volume": 920.3
    }
  ]
}
```

## Response Fields

### Top Level
- `cycle_id` - Cycle number (starts at 1 per day)
- `symbol` - Asset (ETH)
- `side` - Always "STRADDLE" for Grogu1
- `entry_price` - Entry price of the cycle
- `entry_time` - Unix timestamp of entry
- `expiry_time` - Unix timestamp of contract expiry
- `current_price` - Current spot price
- `current_time` - Current Unix timestamp

### Levels
- **Call leg**: Entry → SL → TP1 → TP2
  - `call_sl`: Liquidation level for call
  - `call_tp1`: First profit target (usually 50% of max)
  - `call_tp2`: Second profit target (max profit zone)
  
- **Put leg**: Entry → SL → TP1 → TP2
  - `put_sl`: Liquidation level for put
  - `put_tp1`: First profit target
  - `put_tp2`: Second profit target (opposite direction)

### Leg Status
- `status`: "OPEN", "CLOSED_TP1", "CLOSED_TP2", "CLOSED_SL"
- `sl_hit`: Boolean flag if SL triggered
- `tp1_hit`: Boolean flag if TP1 triggered
- `tp2_hit`: Boolean flag if TP2 triggered

### P&L
- `pnl`: Absolute profit/loss in USDT
- `pnl_pct`: Percentage profit/loss
- `cycle_pnl`: Combined P&L of both legs

### Klines
- 5-minute OHLC data (or 1-minute if higher frequency available)
- Last 288 candles = 24 hours
- Includes volume

## HTTP Status Codes

| Code | Meaning |
|------|---------|
| 200 | Success |
| 400 | Invalid query params (missing `with_levels=true`) |
| 404 | Cycle not found |
| 500 | Server error |

## Error Responses

```json
{
  "error": "with_levels=true required",
  "code": "MISSING_PARAM"
}
```

## Real-Time Updates

Frontend polls this endpoint every **5 seconds** to update:
1. Current price
2. P&L (both legs)
3. Klines (append new candles)
4. SL/TP status (if legs closed)
5. Expiry countdown

## Usage in Mission Control

```typescript
// Frontend component fetches:
const url = `http://187.127.114.34:8000/api/v1/grogu/positions?with_levels=true`;
const response = await fetch(url);
const position = await response.json();

// Renders:
// - Chart with klines + SL/TP lines
// - Real-time price ticker
// - Countdown timer to expiry
// - Scenario indicators (SL hit? TP2 hit?)
```

## Backend Implementation Notes

### Data Sources
- **Klines**: Real-time ETH/USDT 5-min candles from Bybit websocket
- **Levels**: From `eth_straddle_loop.py` when cycle opens
- **P&L**: Live calculation from current mark prices
- **Status**: From order book or position manager

### Cache Strategy
- Cache position data **in-memory** (update every 1-2 sec from poller)
- Cache klines in **rotating buffer** (last 288 candles)
- Only rebuild response on each request (lightweight, ~10ms)

### Performance
- Response time: <50ms (mostly I/O from cache)
- Network: 5 requests/sec × 5KB = 250KB/min = acceptable

## Deployment Checklist

- [ ] Add endpoint to FastAPI/Flask app at port 8000
- [ ] Fetch real-time klines from Bybit WebSocket
- [ ] Calculate P&L from live mark prices
- [ ] Test with `curl http://187.127.114.34:8000/api/v1/grogu/positions?with_levels=true`
- [ ] Verify response time <50ms
- [ ] Enable CORS for frontend (if on different domain)
