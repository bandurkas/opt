# Mission Control Charts — Quick Start Guide

## 📦 What You Get

```
✅ Real-time position chart with SL/TP levels
✅ Live ETH price ticker
✅ Countdown timer to expiry
✅ Scenario indicators (SL hit? TP2 hit?)
✅ P&L tracking (both legs)
✅ 24h kline history
```

## 🚀 3-Step Deployment

### Step 1: Backend Endpoint (VPS, 2-3 hours)

```bash
# Copy implementation file to VPS
scp docs/GROGU_POSITIONS_BACKEND.py root@187.127.114.34:/root/opt-app/api/

# Add route to FastAPI app
# Import: from api.grogu_positions import app as grogu_app
# Include: app.include_router(grogu_app.router)

# Test
curl "http://187.127.114.34:8000/api/v1/grogu/positions?with_levels=true"
```

✅ Response should include `cycle_id`, `levels`, `klines`, `pnl`

### Step 2: Frontend Component (Local, 1-2 hours)

```bash
# Copy component
cp src/components/GroguPositionChart.tsx ~/Desktop/mission-control/src/components/

# Install dependency
npm install recharts  # (usually already installed)

# Add to dashboard
import GroguPositionChart from '@/components/GroguPositionChart';

export default function Dashboard() {
  return <GroguPositionChart />;
}
```

### Step 3: Enable CORS (VPS, 10 min)

```python
# In FastAPI main app
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://your-domain.com"],
    allow_methods=["*"],
    allow_headers=["*"],
)
```

---

## 📋 What's Included

| File | Purpose | Location |
|------|---------|----------|
| `GroguPositionChart.tsx` | React chart component | `src/components/` |
| `API_GROGU_POSITIONS_SPEC.md` | Full API specification | `docs/` |
| `GROGU_POSITIONS_BACKEND.py` | Backend implementation | Deploy to VPS |
| `MISSION_CONTROL_CHARTS_DEPLOYMENT.md` | Full integration guide | `docs/` |
| `CHARTS_QUICK_START.md` | This file | `docs/` |

---

## 🎯 Expected Behavior

### Chart Shows:
- **Blue line**: ETH spot price (real-time)
- **Orange dashed**: Call SL + Put SL levels
- **Green dashed**: TP2 targets
- **Blue solid**: Entry price
- **Red background**: SL triggered zone
- **Green background**: TP2 profit zone

### Expiry Timer:
- Counts down from 24h
- Updates every 1 second
- Shows: `18h 34m 22s`
- Turns red when < 1h remaining

### P&L Display:
- Both legs shown separately
- Total cycle P&L in header
- Real-time calculation from mark prices

---

## 🔧 Configuration

### Backend Integration Points

**When position opens** (in `eth_straddle_loop.py`):
```python
update_position_from_eth_straddle({
    'cycle_id': 7,
    'symbol': 'ETH',
    'entry_price': 3850.25,
    'entry_time': 1719225945,
    'expiry_time': 1719312345,
    'levels': {
        'call_sl': 3920,
        'call_tp1': 3900,
        'call_tp2': 3870,
        'put_sl': 3780,
        'put_tp1': 3800,
        'put_tp2': 3830,
    }
})
```

**When klines arrive** (from poller):
```python
kline = KlineData(time=1719226100, open=3850.1, high=3851.5, low=3849.8, close=3850.5, volume=1250)
update_kline_data('ETH', kline)
```

### Frontend Polling

Default: **5 seconds** (configurable)

```typescript
// To change interval:
const interval = setInterval(fetchPosition, 10000); // 10 seconds
```

---

## 📊 API Endpoint Reference

```
GET /api/v1/grogu/positions?with_levels=true&cycle_id={optional}
```

**Required:** `?with_levels=true`  
**Optional:** `?cycle_id=7` (else returns latest)

**Response:** ~5KB JSON with:
- Position metadata (cycle_id, entry_price, expiry_time, etc)
- SL/TP levels for both legs
- Current P&L for each leg
- 288 klines (24h @ 5min)
- Leg status (OPEN / CLOSED_TP1 / CLOSED_TP2 / CLOSED_SL)

**Response Time:** <50ms (cached)

---

## ✅ Testing Checklist

### Backend
- [ ] Endpoint returns 200 OK
- [ ] Response includes all fields (cycle_id, levels, klines)
- [ ] Klines update every 5 minutes
- [ ] P&L calculates correctly
- [ ] SL/TP status updates when price crosses levels

### Frontend
- [ ] Component renders without errors
- [ ] Chart displays with correct scale
- [ ] Reference lines visible (SL, TP, Entry)
- [ ] Price ticker updates every 5s
- [ ] Timer counts down every 1s
- [ ] Scenario boxes show correct indicators

### Integration
- [ ] CORS enabled on backend
- [ ] Frontend can reach backend API
- [ ] Real price data flowing (not mock)
- [ ] Multiple cycles load correctly (if using cycle_id param)

---

## 🚨 Common Issues

| Issue | Solution |
|-------|----------|
| **CORS error in browser** | Enable CORS middleware on VPS |
| **Old price data** | Check Bybit WebSocket connection in poller |
| **Chart not updating** | Verify API endpoint is live and polling |
| **SL not triggering** | Check levels match eth_straddle_strategy.py config |
| **Timer not counting down** | Check browser console for JS errors |
| **API 404 error** | Verify endpoint route added to FastAPI app |

---

## 📈 Next Phase: Cycle Filters

After charts are live, start **Phase 2 (Cycle Filters)**:

1. **Backtest** each filter independently (last 3 months)
   - IV filter (VRP > 70.9) ← Ready to test
   - Trend filter (skip down-trend)
   - Volatility filters (skip extreme vol)
   - Time filters (skip near-market-open)

2. **Measure** for each:
   - Trade count
   - Win rate
   - Avg P&L per cycle
   - Sharpe ratio
   - Max drawdown

3. **Find best combo** (e.g., IV + Trend + Volatility)

4. **Code filters** in eth_straddle_strategy.py (same way as VRP filter from previous work)

5. **Paper test** 1–2 weeks

6. **Live deploy** (if metrics pass)

---

## 📞 Files to Review

1. Read first:
   - `docs/CHARTS_QUICK_START.md` ← You are here
   - `docs/API_GROGU_POSITIONS_SPEC.md` ← API contract

2. Implementation:
   - `src/components/GroguPositionChart.tsx` ← Frontend code
   - `docs/GROGU_POSITIONS_BACKEND.py` ← Backend code

3. Full guide:
   - `docs/MISSION_CONTROL_CHARTS_DEPLOYMENT.md` ← Step-by-step

---

## 🎯 Success Criteria

✅ Charts deployed when:
1. Backend endpoint live at `http://187.127.114.34:8000/api/v1/grogu/positions?with_levels=true`
2. Frontend component renders in Mission Control dashboard
3. Real-time price data displays
4. Timer counts down correctly
5. SL/TP levels visible on chart
6. Real kline data from Bybit (not mock)

**Estimated time:** 3–5 hours total (1–2 hours backend setup + 1–2 hours frontend + 30min testing)

---

**Ready to deploy?** Start with Step 1 (Backend) in "3-Step Deployment" above.

Questions? Check `MISSION_CONTROL_CHARTS_DEPLOYMENT.md` for detailed guide.
