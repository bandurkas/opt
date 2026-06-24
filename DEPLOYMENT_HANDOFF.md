# 🚀 Mission Control Charts — Deployment Handoff

**Status:** ✅ Code complete, committed to Git | ⏳ Ready for VPS deployment  
**Date:** 2026-06-24  
**Time Estimate:** 3–5 hours (1.5-2.5h backend + 1-2h frontend + 30min testing)

---

## 📋 What's Being Deployed

### Phase 1: Backend API Endpoint (VPS, 1.5–2.5 hours)
- **File:** `docs/GROGU_POSITIONS_BACKEND.py` (400 lines, FastAPI)
- **Endpoint:** `GET /api/v1/grogu/positions?with_levels=true&cycle_id={optional}`
- **Response:** Real-time position data with klines, SL/TP levels, P&L

### Phase 2: Frontend React Component (Local Dashboard, 1–2 hours)
- **File:** `src/components/GroguPositionChart.tsx` (350 lines, TypeScript + Recharts)
- **Purpose:** Real-time charting with SL/TP visualization, P&L tracking, expiry countdown
- **Integration:** Drop into existing Mission Control dashboard

### Phase 3: Testing (30 minutes)
- Verify API returns JSON
- Verify frontend renders chart
- Verify real data flowing from Bybit

---

## 🔧 Critical Context

### VPS Details
- **Host:** `187.127.114.34`
- **User:** `root`
- **App Path:** `/root/opt-app`
- **FastAPI Port:** `8000`
- **Service Manager:** Docker Compose

### Frontend Details
- **Framework:** React + TypeScript
- **Dependencies:** Recharts (charting), Tailwind CSS (styling)
- **Dev Server:** `npm run dev` (localhost:3000)
- **Dashboard Path:** Depends on your project structure (typically `src/app/dashboard/page.tsx` or `src/pages/dashboard.tsx`)

### Data Flow
```
Bybit (real prices)
  ↓ (via WebSocket in eth_straddle_loop.py)
eth_straddle_loop.py (position opening + kline updates)
  ↓ (calls API functions)
/root/opt-app/api/grogu_positions.py (stores position + klines)
  ↓ (GET endpoint)
Frontend (GroguPositionChart.tsx, polls every 5 seconds)
  ↓ (renders)
Mission Control Dashboard
```

---

## 📍 Key Files

| File | Location | Purpose |
|------|----------|---------|
| **Backend Code** | `docs/GROGU_POSITIONS_BACKEND.py` | Copy to VPS: `/root/opt-app/api/grogu_positions.py` |
| **Frontend Component** | `src/components/GroguPositionChart.tsx` | Copy to dashboard: `src/components/GroguPositionChart.tsx` |
| **API Spec** | `docs/API_GROGU_POSITIONS_SPEC.md` | Reference for API contract |
| **Full Guide** | `docs/MISSION_CONTROL_CHARTS_DEPLOYMENT.md` | Detailed step-by-step instructions |
| **Quick Start** | `docs/CHARTS_QUICK_START.md` | TL;DR version |
| **Deploy Script** | `scripts/deploy_grogu_charts_vps.sh` | Automated backend copy (requires SSH key) |
| **Install Script** | `scripts/install_grogu_charts_frontend.sh` | Automated frontend installation |

---

## ⚡ Quick Deployment Steps

### Phase 1: Backend (VPS)

**1A. Copy backend file:**
```bash
scp docs/GROGU_POSITIONS_BACKEND.py root@187.127.114.34:/root/opt-app/api/grogu_positions.py
```

**1B. SSH into VPS and edit main.py:**
```bash
ssh root@187.127.114.34
cd /root/opt-app
nano main.py
```

**Add imports at top:**
```python
from fastapi.middleware.cors import CORSMiddleware
from api.grogu_positions import app as grogu_app
```

**Add CORS middleware (after `app = FastAPI()`):**
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**Add router (before or after other routes):**
```python
app.include_router(grogu_app.router)
```

Save and exit (Ctrl+O, Enter, Ctrl+X if using nano).

**1C. Verify syntax:**
```bash
python3 -m py_compile main.py
```

**1D. Rebuild Docker container:**
```bash
cd /root/opt-app
docker compose up -d --build web
sleep 10
docker compose logs web --tail 5
```

**1E. Test endpoint:**
```bash
curl -s "http://187.127.114.34:8000/api/v1/grogu/positions?with_levels=true" | jq .
```

✅ Should return JSON with `cycle_id`, `levels`, `klines`, `pnl`

### Phase 2: Frontend (Local)

**2A. Copy component:**
```bash
cd ~/Desktop/meta/ai-ads-agent
cp src/components/GroguPositionChart.tsx /path/to/mission-control/src/components/
```

**2B. Install dependencies:**
```bash
npm install recharts  # if not already installed
```

**2C. Add to dashboard:**

Find your dashboard page (`src/app/dashboard/page.tsx` or similar) and add:

```typescript
import GroguPositionChart from '@/components/GroguPositionChart';

export default function Dashboard() {
  return (
    <div className="container mx-auto p-6">
      <h1 className="text-4xl font-bold mb-8">Mission Control</h1>
      
      {/* Your existing content */}
      
      {/* NEW: Grogu1 Chart */}
      <section className="mt-8">
        <h2 className="text-2xl font-bold mb-4">📊 Live Positions</h2>
        <GroguPositionChart />
      </section>
    </div>
  );
}
```

**2D. Configure API endpoint (if needed):**

If VPS IP is different from `187.127.114.34`, edit line ~48 in `GroguPositionChart.tsx`:

```typescript
const apiUrl = 'http://YOUR_VPS_IP:8000/api/v1/grogu/positions?with_levels=true';
```

**2E. Start dev server:**
```bash
npm run dev
```

Open browser: `http://localhost:3000/dashboard`

✅ Chart should render (data may be empty until Grogu1 opens a position)

### Phase 3: Integration Testing

**3A. Verify backend endpoint:**
```bash
curl -s "http://187.127.114.34:8000/api/v1/grogu/positions?with_levels=true" | jq .
```

**3B. Verify frontend network requests:**
1. Open browser DevTools (F12)
2. Go to Network tab
3. Refresh dashboard
4. Look for request to `/api/v1/grogu/positions`
5. Response should be JSON (not error)

**3C. Verify no CORS errors:**
- Browser console should be clear
- No "Access to XMLHttpRequest..." errors

**3D. Monitor real data:**
- When Grogu1 opens a position, data should flow automatically
- Check after ~5 seconds (polling interval)

---

## 🔌 Integration with eth_straddle_loop.py

Once backend is live, optionally integrate real data by calling these functions in your trading loop:

**When position opens:**
```python
from api.grogu_positions import update_position_from_eth_straddle

update_position_from_eth_straddle({
    'cycle_id': 7,
    'symbol': 'ETH',
    'entry_price': 3850.25,
    'entry_time': int(time.time()),
    'expiry_time': int(time.time()) + 86400,  # 24h
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

**When klines arrive:**
```python
from api.grogu_positions import update_kline_data, KlineData

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

---

## ✅ Success Criteria

- [ ] Backend file copied to VPS
- [ ] main.py updated with imports, CORS, and router
- [ ] Python syntax verified (`python3 -m py_compile main.py`)
- [ ] Docker container rebuilt (`docker compose up -d --build web`)
- [ ] API endpoint returns 200 OK with valid JSON
- [ ] Frontend component installed in dashboard
- [ ] Dashboard page includes `<GroguPositionChart />`
- [ ] Dev server starts without errors (`npm run dev`)
- [ ] Chart renders in browser (localhost:3000/dashboard)
- [ ] Network tab shows API requests every 5 seconds
- [ ] No CORS errors in browser console
- [ ] Real position data displays when available

---

## 🚨 Troubleshooting

### Issue: CORS Error
```
Access to XMLHttpRequest at 'http://187.127.114.34:8000/...' 
has been blocked by CORS policy
```
**Fix:** 
1. Verify `CORSMiddleware` added to main.py
2. Restart container: `docker compose up -d --build web`
3. Check `allow_origins=["*"]` is set

### Issue: 404 Not Found
```
curl: (22) The requested URL returned error: 404
```
**Fix:**
1. Check file exists: `ls -lh /root/opt-app/api/grogu_positions.py`
2. Check imports in main.py: `from api.grogu_positions import app as grogu_app`
3. Check router included: `app.include_router(grogu_app.router)`
4. Restart: `docker compose up -d --build web`

### Issue: Connection Refused
```
curl: (7) Failed to connect to 187.127.114.34:8000
```
**Fix:**
1. Check VPS online: `ping 187.127.114.34`
2. Check service: `docker compose ps` (on VPS)
3. Check logs: `docker compose logs web --tail 20` (on VPS)

### Issue: Chart Not Updating
**Fix:**
1. Open DevTools → Network tab
2. Check requests every 5s
3. Verify response includes `klines` array
4. Check JS console for errors
5. Verify API endpoint in component

---

## 📞 Reference Documentation

All detailed documentation available in repo:

1. **Start here:** `docs/CHARTS_QUICK_START.md` — 5-min overview
2. **API Contract:** `docs/API_GROGU_POSITIONS_SPEC.md` — endpoint details
3. **Full Guide:** `docs/MISSION_CONTROL_CHARTS_DEPLOYMENT.md` — complete walkthrough
4. **Backend Code:** `docs/GROGU_POSITIONS_BACKEND.py` — implementation
5. **Frontend Code:** `src/components/GroguPositionChart.tsx` — React component

---

## 🎯 What Happens Next

**After successful deployment:**

1. **Wait for position close** — Grogu1 will close out current position
2. **Deploy VRP filter** — From previous work (`project_grogu_vrp_filter_ready_to_deploy.md`)
   - Filter code: Already written and tested
   - All 18 unit tests pass
   - Ready to integrate into eth_straddle_strategy.py
3. **Monitor live metrics** — Chart will show real-time P&L updates

---

## 📌 Key Contacts

- **VPS SSH:** `ssh root@187.127.114.34`
- **Frontend:** Depends on your dashboard project
- **Backend Testing:** `curl -s http://187.127.114.34:8000/api/v1/grogu/positions?with_levels=true | jq .`

---

**Deployment Status:** Ready to ship ✅  
**Estimated Time:** 3–5 hours total  
**Owner:** Passed to deployment team

Any agent can pick this up by:
1. Reading this file
2. Following the Quick Deployment Steps
3. Testing at each phase
4. Referencing detailed docs as needed

