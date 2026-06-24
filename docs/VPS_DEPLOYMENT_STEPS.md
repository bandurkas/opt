# VPS Deployment — Step-by-Step

**Time:** 2-3 hours  
**Prerequisites:** SSH access to VPS (187.127.114.34), FastAPI app running at port 8000

---

## PHASE 1: Backend Deployment (VPS)

### Step 1A: Copy Backend File (Local Machine)

```bash
cd ~/Desktop/meta/ai-ads-agent

# Run deployment script
bash scripts/deploy_grogu_charts_vps.sh

# Or manually copy:
scp docs/GROGU_POSITIONS_BACKEND.py root@187.127.114.34:/root/opt-app/api/grogu_positions.py
```

✅ File should now be at `/root/opt-app/api/grogu_positions.py` on VPS

---

### Step 1B: Update FastAPI App (SSH into VPS)

```bash
ssh root@187.127.114.34
```

Now on VPS:

```bash
cd /root/opt-app

# Backup original main.py
cp main.py main.py.backup

# Edit main.py with your favorite editor
nano main.py
# or vim main.py
```

**Add these imports at the top:**

```python
from fastapi.middleware.cors import CORSMiddleware
from api.grogu_positions import app as grogu_app
```

**Add CORS middleware** (after creating FastAPI app, before routes):

```python
app = FastAPI()

# ADD THIS:
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # or specify frontend domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rest of your code...
```

**Add route** (after all other routes):

```python
# At the end, before app startup:
app.include_router(grogu_app.router)
```

**Save and exit** (if using nano: Ctrl+O, Enter, Ctrl+X)

---

### Step 1C: Test Syntax

Still on VPS:

```bash
python3 -m py_compile main.py

# Should return with no output if OK
# If there's an error, fix the imports
```

✅ If no error, syntax is valid

---

### Step 1D: Restart FastAPI App

```bash
# If using Docker Compose:
docker compose restart <service-name>

# Examples:
docker compose restart web
docker compose restart api
docker compose restart mission_control

# Or if using systemd:
systemctl restart mission-control
```

Wait 10 seconds for app to start.

---

### Step 1E: Test Endpoint

Still on VPS (or from local machine):

```bash
curl -s "http://187.127.114.34:8000/api/v1/grogu/positions?with_levels=true" | jq .
```

✅ Should return JSON like:

```json
{
  "cycle_id": 7,
  "symbol": "ETH",
  "current_price": 3852.50,
  "expiry_time": 1719312345,
  "levels": {
    "call_sl": 3920.00,
    "call_tp1": 3900.00,
    ...
  },
  "klines": [...]
}
```

❌ If you get 404 or 500:
- Check main.py has correct imports
- Check `docker compose logs <service>` for errors
- Verify file at `/root/opt-app/api/grogu_positions.py` exists

---

### Step 1F: Integrate with eth_straddle_loop.py

Still on VPS, in `/root/opt-app`:

**Find eth_straddle_loop.py:**

```bash
find . -name "eth_straddle_loop.py" -o -name "straddle_loop.py"
```

**Edit the position opening function:**

```python
# At top of file, add imports:
from api.grogu_positions import update_position_from_eth_straddle, update_kline_data
from api.grogu_positions import KlineData
import time

# In open_straddle() function, after position opens:
def open_straddle(broker, cycle_state, logger):
    # ... existing opening logic ...
    
    # NEW: Notify API about position
    try:
        update_position_from_eth_straddle({
            'cycle_id': cycle_state.get('cycle_id', 1),
            'symbol': 'ETH',
            'entry_price': cycle_state['entry_price'],
            'entry_time': int(time.time()),
            'expiry_time': int(time.time()) + 86400,  # 24h
            'levels': {
                'call_sl': cycle_state.get('call_sl', cycle_state['entry_price'] + 70),
                'call_tp1': cycle_state.get('call_tp1', cycle_state['entry_price'] + 50),
                'call_tp2': cycle_state.get('call_tp2', cycle_state['entry_price'] + 20),
                'put_sl': cycle_state.get('put_sl', cycle_state['entry_price'] - 70),
                'put_tp1': cycle_state.get('put_tp1', cycle_state['entry_price'] - 50),
                'put_tp2': cycle_state.get('put_tp2', cycle_state['entry_price'] - 20),
            }
        })
    except Exception as e:
        logger.error(f"Failed to update position API: {e}")
    
    # ... rest of code ...
```

**For kline updates** (add to poller or wherever klines are received):

```python
# When receiving klines:
from api.grogu_positions import update_kline_data, KlineData

def on_new_kline(kline_dict):
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

### Step 1G: Restart App Again

```bash
docker compose restart <service-name>

# Wait for startup
sleep 5

# Check logs
docker compose logs <service-name> --tail 20
```

✅ Should see no errors

---

## PHASE 2: Frontend Deployment (Local Machine)

### Step 2A: Install Component

```bash
cd ~/mission-control  # or wherever your frontend is

bash ~/Desktop/meta/ai-ads-agent/scripts/install_grogu_charts_frontend.sh $(pwd)
```

✅ Component should be installed at `src/components/GroguPositionChart.tsx`

---

### Step 2B: Add to Dashboard

Edit your dashboard page (example: `src/app/dashboard/page.tsx`):

```typescript
import GroguPositionChart from '@/components/GroguPositionChart';

export default function DashboardPage() {
  return (
    <div className="container mx-auto p-6">
      <h1 className="text-4xl font-bold mb-8">Mission Control</h1>

      {/* Existing dashboard content */}
      
      {/* NEW: Grogu1 Chart */}
      <section className="mt-8">
        <h2 className="text-2xl font-bold mb-4">📊 Live Positions</h2>
        <GroguPositionChart />
      </section>
    </div>
  );
}
```

---

### Step 2C: Configure API Endpoint (Optional)

If your VPS is different from `187.127.114.34`:

Edit `src/components/GroguPositionChart.tsx` line ~48:

```typescript
const url = cycleId
  ? `http://YOUR_VPS_IP:8000/api/v1/grogu/positions?with_levels=true&cycle_id=${cycleId}`
  : `http://YOUR_VPS_IP:8000/api/v1/grogu/positions?with_levels=true`;
```

---

### Step 2D: Install Dependencies

```bash
npm install recharts  # if not already installed
npm install tailwindcss  # if not already installed
```

---

### Step 2E: Start Dev Server

```bash
npm run dev
```

Open: http://localhost:3000/dashboard

✅ Should see chart rendering (might be empty data initially if no live positions)

---

## PHASE 3: Integration Testing

### Test 1: Backend Endpoint

```bash
# From your local machine
curl -s "http://187.127.114.34:8000/api/v1/grogu/positions?with_levels=true" | jq .

# Should return valid JSON within 50ms
```

### Test 2: Frontend Rendering

- Open browser dev tools (F12)
- Go to Network tab
- Refresh dashboard
- Should see request to `/api/v1/grogu/positions`
- Response should be JSON (not error)

### Test 3: Live Data

- Check if position data is flowing
- Chart should show recent klines
- Timer should count down
- P&L should calculate

### Test 4: Real Position

When Grogu1 opens a new cycle:
- Wait 5 seconds
- Refresh dashboard
- Chart should show new position
- Price line should match current spot price

---

## ✅ Success Checklist

- [ ] Backend file copied to VPS
- [ ] main.py updated with imports + CORS
- [ ] eth_straddle_loop.py integration added
- [ ] FastAPI app restarted
- [ ] Endpoint test returns 200 OK
- [ ] Frontend component installed
- [ ] Dashboard page includes component
- [ ] Frontend loads without errors
- [ ] Chart displays (with or without data)
- [ ] Network tab shows API calls every 5s
- [ ] No CORS errors in browser console
- [ ] Real position data flowing when available

---

## 🚨 Troubleshooting

### Issue: CORS Error

```
Access to XMLHttpRequest at 'http://187.127.114.34:8000/...' 
has been blocked by CORS policy
```

**Solution:**
1. Verify CORS middleware added to main.py
2. Restart FastAPI app
3. Check if `allow_origins=["*"]` is set

---

### Issue: 404 Not Found

```
curl: (22) The requested URL returned error: 404
```

**Solution:**
1. Verify file at `/root/opt-app/api/grogu_positions.py` exists
2. Check imports in main.py: `from api.grogu_positions import app as grogu_app`
3. Check router is included: `app.include_router(grogu_app.router)`
4. Check FastAPI was restarted

---

### Issue: Connection Refused

```
curl: (7) Failed to connect to 187.127.114.34:8000
```

**Solution:**
1. Verify VPS is online: `ping 187.127.114.34`
2. Check FastAPI service is running: `docker compose ps`
3. Check port 8000 is listening: `netstat -tlnp | grep 8000`

---

### Issue: Chart Not Updating

**Solution:**
1. Open browser dev tools → Network tab
2. Verify requests to API every 5s
3. Check response includes `klines` array
4. Check console for JavaScript errors
5. Verify API endpoint in GroguPositionChart.tsx is correct

---

### Issue: Python Syntax Error

```
Syntax error in python file
```

**Solution:**
1. Check imports are correct: `from fastapi.middleware.cors import CORSMiddleware`
2. Verify indentation (FastAPI is whitespace-sensitive)
3. Run: `python3 -m py_compile main.py` to see exact error
4. Fix and restart

---

## 📞 Quick Commands Reference

### VPS

```bash
# SSH in
ssh root@187.127.114.34

# Check service status
docker compose ps

# View logs
docker compose logs <service-name> --tail 20

# Restart service
docker compose restart <service-name>

# Test endpoint
curl "http://127.0.0.1:8000/api/v1/grogu/positions?with_levels=true"
```

### Local

```bash
# Install component
bash scripts/install_grogu_charts_frontend.sh /path/to/dashboard

# Start dev server
npm run dev

# Check network requests
# Browser → F12 → Network tab
```

---

**Expected total time:** 2-3 hours  
**Status:** Ready to deploy  
**Next:** Follow steps in order, test at each stage
