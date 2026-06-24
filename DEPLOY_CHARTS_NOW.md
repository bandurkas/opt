# ⚡ DEPLOY CHARTS NOW — START HERE

**Time:** 2-3 hours  
**Status:** All code ready, follow steps below

---

## 🎯 YOUR CHECKLIST (Copy-Paste Ready)

### Phase 1A: Copy Backend to VPS (LOCAL MACHINE)

```bash
cd ~/Desktop/meta/ai-ads-agent
bash scripts/deploy_grogu_charts_vps.sh
```

**Expected output:**
```
✅ Backend file found
✅ Copied to VPS
✅ File verified on VPS
✅ Python syntax valid
```

⏱️ **Time:** 1 minute

---

### Phase 1B: Update FastAPI App (SSH INTO VPS)

```bash
ssh root@187.127.114.34
cd /root/opt-app
nano main.py
```

**Find the line:**
```python
app = FastAPI()
```

**Add BELOW it:**
```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**At TOP of file, add:**
```python
from api.grogu_positions import app as grogu_app
```

**At END of file (before if __name__), add:**
```python
app.include_router(grogu_app.router)
```

**Save:** Ctrl+O, Enter, Ctrl+X

**Verify syntax:**
```bash
python3 -m py_compile main.py
```

⏱️ **Time:** 5 minutes

---

### Phase 1C: Restart FastAPI (VPS)

```bash
# Find your service name:
docker compose ps | grep api

# Restart it (example):
docker compose restart web

# Wait 5 seconds
sleep 5

# Check logs for errors:
docker compose logs web --tail 10
```

**Should show NO errors**

⏱️ **Time:** 1 minute

---

### Phase 1D: Test Backend (VPS or LOCAL)

```bash
curl -s "http://187.127.114.34:8000/api/v1/grogu/positions?with_levels=true" | jq .
```

**Should return JSON:**
```json
{
  "cycle_id": 7,
  "symbol": "ETH",
  "current_price": 3850.25,
  "levels": {...},
  "klines": [...]
}
```

❌ **If 404 error:** Check main.py imports and restart  
❌ **If connection refused:** Check service is running (`docker compose ps`)  
✅ **If JSON returned:** Backend working! Continue to Phase 2.

⏱️ **Time:** 2 minutes

---

### Phase 2A: Install Frontend Component (LOCAL MACHINE)

**Determine your Mission Control path:**
```bash
# Is it here?
ls ~/mission-control/src/components/

# Or here?
ls /root/opt-app/frontend/src/components/

# Or elsewhere? Find it:
find ~ -name "dashboard" -type d 2>/dev/null | grep -E "(src|app)" | head -5
```

**Once you find it, install:**
```bash
cd ~/Desktop/meta/ai-ads-agent

# Replace /path/to/mission-control with actual path
bash scripts/install_grogu_charts_frontend.sh /path/to/mission-control
```

**Expected output:**
```
✅ Source component found
✅ Mission Control path valid
✅ Components directory exists
✅ recharts installed (or already installed)
```

⏱️ **Time:** 2 minutes

---

### Phase 2B: Add Component to Dashboard (LOCAL MACHINE)

**Find your dashboard page:**
```bash
# Usually here:
/path/to/mission-control/src/app/dashboard/page.tsx
# Or here:
/path/to/mission-control/src/pages/dashboard.tsx
```

**Edit the file and add:**

```typescript
// At top with other imports:
import GroguPositionChart from '@/components/GroguPositionChart';

// In the component JSX, add this section:
<section className="mt-8 mb-8">
  <h2 className="text-2xl font-bold mb-4">📊 Live Positions</h2>
  <GroguPositionChart />
</section>
```

**Example full page:**
```typescript
import GroguPositionChart from '@/components/GroguPositionChart';

export default function DashboardPage() {
  return (
    <div className="container mx-auto p-6 bg-slate-900 min-h-screen">
      <h1 className="text-4xl font-bold text-white mb-8">Mission Control</h1>

      {/* Your existing content */}
      
      {/* NEW: Add this */}
      <section className="mt-8 mb-8">
        <h2 className="text-2xl font-bold text-white mb-4">📊 Live Positions</h2>
        <GroguPositionChart />
      </section>
    </div>
  );
}
```

⏱️ **Time:** 3 minutes

---

### Phase 2C: Install Dependencies & Start (LOCAL MACHINE)

```bash
cd /path/to/mission-control

# Install chart library if needed
npm install recharts

# Start dev server
npm run dev
```

**Expected output:**
```
▲ Next.js dev server running at http://localhost:3000
```

⏱️ **Time:** 2 minutes

---

### Phase 2D: Test Frontend (LOCAL)

**In browser:**
1. Open: http://localhost:3000/dashboard
2. Press F12 (developer tools)
3. Click "Network" tab
4. Refresh page
5. Look for request to `/api/v1/grogu/positions`

**Expected:**
- ✅ Request shows 200 (or successful response)
- ✅ Response is JSON (click request, see "Response" tab)
- ✅ Chart displays (might show placeholder if no position)
- ✅ No CORS errors in console

❌ **If 404:** Check API endpoint is running on VPS  
❌ **If CORS error:** Check CORS middleware on VPS  
✅ **If JSON loads:** Frontend working!

⏱️ **Time:** 3 minutes

---

## 🎉 YOU'RE DONE!

Chart is now live on Mission Control! 

**Total time:** ~30 minutes hands-on

---

## 🔄 WHAT HAPPENS NEXT?

### When Grogu1 Opens a Position
1. eth_straddle_loop.py calls `update_position_from_eth_straddle()`
2. API stores position + levels
3. Poller sends klines → API updates them
4. Frontend polls API every 5s
5. Chart updates with real data

### Optional: Integrate eth_straddle_loop.py (VPS)

If you want real data flowing (instead of mock):

```bash
ssh root@187.127.114.34
nano /root/opt-app/eth_straddle_loop.py
```

Add at top:
```python
from api.grogu_positions import update_position_from_eth_straddle, update_kline_data, KlineData
```

In position opening function:
```python
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
```

Then restart app.

---

## 📚 Full References

- **Full deployment guide:** `docs/VPS_DEPLOYMENT_STEPS.md`
- **API spec:** `docs/API_GROGU_POSITIONS_SPEC.md`
- **Component code:** `src/components/GroguPositionChart.tsx`
- **Backend code:** `docs/GROGU_POSITIONS_BACKEND.py`

---

## 🚨 QUICK TROUBLESHOOTING

| Problem | Fix |
|---------|-----|
| **VPS: 404 error** | Check main.py has import + router include. Restart service. |
| **Frontend: CORS error** | SSH to VPS, check CORS middleware in main.py, restart. |
| **Frontend: Connection refused** | Verify VPS service running: `docker compose ps` |
| **Chart empty** | Normal if no active position. Wait for Grogu1 to open cycle. |
| **Backend syntax error** | Run `python3 -m py_compile /root/opt-app/main.py` to see exact error. |

---

## ✅ DONE!

Charts deployed. Ready for Phase 2 (filter backtest) whenever you want!

**Next:** Wait for Grogu1 position to close, then deploy VRP filter.
