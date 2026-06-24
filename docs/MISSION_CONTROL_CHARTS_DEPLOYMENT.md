# Mission Control Charts Integration Guide

## Overview

This adds **real-time position charts** to Mission Control dashboard:
- Live ETH price chart with SL/TP levels
- P&L tracking
- Expiry countdown timer
- Scenario indicators

## Phase 1: Backend API Endpoint (2–3 hours)

### Step 1: Add Endpoint to VPS Mission Control App

**Location:** VPS at `187.127.114.34:8000`

Copy the implementation from `docs/GROGU_POSITIONS_BACKEND.py` and integrate into your FastAPI app:

```bash
# On VPS
cd /root/opt-app
cp docs/GROGU_POSITIONS_BACKEND.py ./api/grogu_positions.py
```

Then add the import and route to your main app:

```python
# In main.py or app.py
from api.grogu_positions import app as grogu_app
app.include_router(grogu_app.router)
```

### Step 2: Integrate with eth_straddle_loop.py

The backend needs to receive position updates from `eth_straddle_loop.py`:

```python
# In eth_straddle_loop.py (on VPS at /root/opt-app)

from api.grogu_positions import update_position_from_eth_straddle, update_kline_data

# After opening a cycle:
def open_straddle(broker, cycle_state, logger):
    # ... existing code ...

    # NEW: Notify API about new position
    update_position_from_eth_straddle({
        'cycle_id': cycle_state['cycle_id'],
        'symbol': 'ETH',
        'entry_price': cycle_state['entry_price'],
        'entry_time': int(time.time()),
        'expiry_time': int(time.time()) + 86400,
        'levels': {
            'call_sl': cycle_state['call_sl'],
            'call_tp1': cycle_state['call_tp1'],
            'call_tp2': cycle_state['call_tp2'],
            'put_sl': cycle_state['put_sl'],
            'put_tp1': cycle_state['put_tp1'],
            'put_tp2': cycle_state['put_tp2'],
        }
    })

    # ... rest of code ...


# When receiving new klines from poller:
def on_kline_update(kline_dict):
    """Called by poller every 5 minutes"""
    kline = KlineData(
        time=kline_dict['time'],
        open=kline_dict['open'],
        high=kline_dict['high'],
        low=kline_dict['low'],
        close=kline_dict['close'],
        volume=kline_dict['volume'],
    )
    update_kline_data('ETH', kline)
```

### Step 3: Test the Endpoint

```bash
# From local machine
curl -s "http://187.127.114.34:8000/api/v1/grogu/positions?with_levels=true" | jq .

# Should return:
{
  "cycle_id": 7,
  "symbol": "ETH",
  "current_price": 3852.50,
  "expiry_time": 1719312345,
  "levels": {
    "call_sl": 3920.00,
    "put_sl": 3780.00,
    ...
  },
  "klines": [...]
}
```

✅ **Deployment Time:** 2–3 hours

---

## Phase 2: Frontend React Component (1–2 hours)

### Step 1: Install Dependencies

The `GroguPositionChart.tsx` component uses **Recharts** (already in Next.js).

```bash
cd ~/Desktop/meta/ai-ads-agent
npm install recharts
# Already available in most React setups
```

### Step 2: Add Component to Mission Control Dashboard

**Location:** Add to your dashboard page:

```typescript
// In your Mission Control dashboard page/component

import GroguPositionChart from '@/components/GroguPositionChart';

export default function DashboardPage() {
  return (
    <div className="p-6">
      {/* Existing dashboard content */}
      
      <section className="mt-8">
        <h2 className="text-2xl font-bold mb-4">Live Positions</h2>
        
        {/* Grogu1 Chart */}
        <GroguPositionChart />
        
        {/* Other positions... */}
      </section>
    </div>
  );
}
```

### Step 3: Configure API Endpoint

If your frontend is on a different domain, enable CORS on the backend:

```python
# In main.py on VPS
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://your-mission-control-domain.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### Step 4: Test in Browser

1. Start your Mission Control frontend
2. Navigate to dashboard
3. Should see real-time chart with:
   - ETH price line
   - Call SL (orange dashed)
   - Put SL (orange dashed)
   - TP2 levels (green dashed)
   - Entry price (blue solid)
   - Real-time klines
   - Countdown timer

✅ **Deployment Time:** 1–2 hours

---

## Phase 3: Enhanced Dashboard Features (Optional, 4–6 hours)

### Add These Features (One at a Time)

#### Feature 1: Multiple Active Positions

Show all active cycles (if Grogu1 can have overlapping positions):

```typescript
// Fetch all positions instead of just latest
const positions = await fetch(
  'http://187.127.114.34:8000/api/v1/grogu/positions?with_levels=true'
).then(r => r.json());

// Grid of charts
positions.forEach(pos => <GroguPositionChart key={pos.cycle_id} cycleId={pos.cycle_id} />);
```

#### Feature 2: Historical P&L Chart

Track daily/weekly P&L:

```typescript
// New endpoint: GET /api/v1/grogu/history?days=30
// Returns: [{date, cycle_count, total_pnl, win_rate}, ...]

<LineChart data={historyData}>
  <Line dataKey="total_pnl" stroke="#10b981" />
</LineChart>
```

#### Feature 3: Filter View (Active / Closed / All)

```typescript
const [filter, setFilter] = useState('active');

const filtered = positions.filter(p =>
  filter === 'active' ? p.call_leg_status === 'OPEN' : true
);
```

#### Feature 4: Alerts

Notify when SL/TP triggered:

```typescript
// In component useEffect
if (position.call_leg_status !== prevStatus.call_leg_status) {
  // Send Telegram alert, browser notification, etc.
  broker.send_telegram(`⏭️ Grogu1 Call SL hit at $${position.current_price}`);
}
```

---

## Full Page Layout Example

```
┌──────────────────────────────────────────────────────┐
│  Mission Control Dashboard                           │
├──────────────────────────────────────────────────────┤
│                                                      │
│  ┌─ Live Positions ─────────────────────────────┐   │
│  │                                               │   │
│  │  Grogu1 (ETH) Cycle #7 ⏱️ 18h 34m            │   │
│  │  ┌──────────────────────────────────────┐    │   │
│  │  │          [Real-time Chart]           │    │   │
│  │  │  Entry: $3850 ─────────────────────  │    │   │
│  │  │           Call SL                    │    │   │
│  │  │  ╱════════╲ ╱════════╲  TP2         │    │   │
│  │  │ ╱          ╲╱          ╲            │    │   │
│  │  │╱            ╲            ╲          │    │   │
│  │  │              ╲            ╱         │    │   │
│  │  │               ╲          ╱          │    │   │
│  │  │                ╲════════╱           │    │   │
│  │  │           Put SL                    │    │   │
│  │  │                                     │    │   │
│  │  │  ETH: $3852.50 ↗                    │    │   │
│  │  │                                     │    │   │
│  │  │  🔴 Call SL: $3920.00               │    │   │
│  │  │  🔴 Put SL: $3780.00                │    │   │
│  │  │  🟢 Both TP2: ±$2 @ $3870           │    │   │
│  │  │  💰 P&L: +$2.50 (0.31%)            │    │   │
│  │  └──────────────────────────────────────┘    │   │
│  │                                               │   │
│  │  Scenarios:                                   │   │
│  │  🔴 Call SL hit → margin +$50  🟡 Put SL hit │   │
│  │  🟢 Both TP2 → profit +$120    🔵 Pending   │   │
│  │                                               │   │
│  └───────────────────────────────────────────────┘   │
│                                                      │
│  ┌─ Closed Positions (Today) ────────────────────┐   │
│  │  Cycle #6: TP2 @ $3850 → +$125 (3.2%)       │   │
│  │  Cycle #5: SL @ $3920 → -$60 (-1.5%)        │   │
│  │  Cycle #4: TP2 @ $3840 → +$110 (2.8%)       │   │
│  └───────────────────────────────────────────────┘   │
│                                                      │
│  Daily Stats: +$175 (4.5%) | 3 wins, 1 loss       │   │
│                                                      │
└──────────────────────────────────────────────────────┘
```

---

## Performance Considerations

| Metric | Target | Notes |
|--------|--------|-------|
| API Response Time | <50ms | Cached in-memory |
| Chart Render | <100ms | Recharts optimized |
| Update Frequency | 5s | Frontend poll interval |
| Kline Storage | 288 candles | = 24h @ 5min |
| Memory per Position | ~200KB | (klines + state) |

---

## Troubleshooting

### Issue: "CORS error"
**Solution:** Enable CORS on backend (see Phase 2, Step 3)

### Issue: "404 endpoint not found"
**Solution:** Make sure `/api/v1/grogu/positions` route is added to FastAPI app

### Issue: "Old price data in chart"
**Solution:** Klines lag behind current price. Check WebSocket connection to Bybit is alive.

### Issue: "SL not triggering"
**Solution:** Check `levels` match actual risk config in eth_straddle_strategy.py

---

## Deployment Checklist

### Backend (VPS)
- [ ] Add `GROGU_POSITIONS_BACKEND.py` to `/root/opt-app/api/`
- [ ] Integrate with `eth_straddle_loop.py`
- [ ] Test endpoint: `curl http://187.127.114.34:8000/api/v1/grogu/positions?with_levels=true`
- [ ] Enable CORS
- [ ] Monitor API response time (should be <50ms)

### Frontend (Local)
- [ ] Copy `GroguPositionChart.tsx` to `src/components/`
- [ ] Install recharts: `npm install recharts`
- [ ] Add component to dashboard page
- [ ] Test in browser
- [ ] Verify real-time updates every 5s

### Testing
- [ ] [ ] Manually trigger SL on paper account
- [ ] [ ] Verify chart updates correctly
- [ ] [ ] Check timer counts down to expiry
- [ ] [ ] Monitor memory usage (should stay <500MB for API)

---

## Next Steps

After Phase 1–2, you can proceed to **Phase 2 (Cycle Filters)** backtest work:

1. Run backtests with each filter ON/OFF
2. Measure: trades, win rate, avg P&L, Sharpe, drawdown
3. Find best combo
4. Code filters in eth_straddle_strategy.py
5. Paper test 1–2 weeks

**Expected Timeline:**
- Phase 1 (Backend): 2–3 hours
- Phase 2 (Frontend): 1–2 hours
- **Total: 3–5 hours to live charts**
- Then Phase 2 filters (3–5 days backtest)
