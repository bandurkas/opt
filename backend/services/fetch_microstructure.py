"""Fetch market microstructure data from Bybit for research.

Endpoints:
  1. Funding Rate history   (market/funding/history) — hourly
  2. Open Interest history  (market/open-interest)   — 5m/15m/30m/1h/4h/1d
  3. Long/Short Ratio       (market/account-ratio)   — hourly (top trader accounts)
  4. Global Long/Short      (market/account-ratio)   — hourly (global ratio)

Data stored as JSON in data/eth_funding.json, data/eth_oi.json, data/eth_long_short.json

Run:
    cd backend && PYTHONPATH=. python3 services/fetch_microstructure.py --days 365
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, ".")
from services.bybit_client import bybit_client

def fetch_funding_rate(symbol="ETHUSDT", hours=8760):
    """Fetch funding rate history. Returns list of {ts_ms, funding_rate}."""
    print(f"  Fetching funding rate for {hours}h...", flush=True)
    all_data = []
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - hours * 3_600_000
    
    # Fetch in chunks of 200 records (Bybit limit)
    cursor = None
    while True:
        try:
            params = {"category": "linear", "symbol": symbol, "limit": 200}
            if cursor:
                params["cursor"] = cursor
            resp = bybit_client.session.get_funding_rate_history(**params)
            items = resp["result"]["list"]
            if not items:
                break
            for item in items:
                ts = int(item["fundingRateTimestamp"])
                if ts * 1000 < start_ms:
                    break
                all_data.append({
                    "ts_ms": ts * 1000,
                    "funding_rate": float(item["fundingRate"]),
                })
            # Bybit returns newest first, so cursor is the first ts
            cursor = items[-1]["fundingRateTimestamp"]
            if int(items[-1]["fundingRateTimestamp"]) * 1000 < start_ms:
                break
            time.sleep(0.5)
        except Exception as e:
            print(f"  Funding rate fetch error: {e}", flush=True)
            break
    
    all_data.sort(key=lambda x: x["ts_ms"])
    print(f"  Got {len(all_data)} funding rate records", flush=True)
    return all_data


def fetch_open_interest(symbol="ETHUSDT", interval="15m", hours=8760):
    """Fetch open interest history. Returns list of {ts_ms, open_interest}."""
    print(f"  Fetching OI ({interval}) for {hours}h...", flush=True)
    all_data = []
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - hours * 3_600_000
    
    cursor = None
    while True:
        try:
            params = {"category": "linear", "symbol": symbol, "intervalTime": interval, "limit": 200}
            if cursor:
                params["cursor"] = cursor
            resp = bybit_client.session.get_open_interest(**params)
            items = resp["result"]["list"]
            if not items:
                break
            for item in items:
                ts = int(item["openTimestamp"])
                if ts < start_ms:
                    break
                all_data.append({
                    "ts_ms": ts,
                    "open_interest": float(item["openInterest"]),
                })
            cursor = items[-1]["openTimestamp"]
            if int(items[-1]["openTimestamp"]) < start_ms:
                break
            time.sleep(0.5)
        except Exception as e:
            print(f"  OI fetch error: {e}", flush=True)
            break
    
    all_data.sort(key=lambda x: x["ts_ms"])
    print(f"  Got {len(all_data)} OI records", flush=True)
    return all_data


def fetch_long_short_ratio(symbol="ETHUSDT", period="5min", hours=8760):
    """Fetch top trader long/short ratio. Returns list of {ts_ms, long_short_ratio}."""
    print(f"  Fetching L/S ratio ({period}) for {hours}h...", flush=True)
    all_data = []
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - hours * 3_600_000
    
    cursor = None
    while True:
        try:
            params = {"category": "linear", "symbol": symbol, "period": period, "limit": 200}
            if cursor:
                params["cursor"] = cursor
            resp = bybit_client.session.get_account_ratio(**params)
            items = resp["result"]["list"]
            if not items:
                break
            for item in items:
                ts = int(item["timestamp"])
                if ts * 1000 < start_ms:
                    break
                all_data.append({
                    "ts_ms": ts * 1000,
                    "long_short_ratio": float(item["ratio"]),
                })
            cursor = items[-1]["timestamp"]
            if int(items[-1]["timestamp"]) * 1000 < start_ms:
                break
            time.sleep(0.5)
        except Exception as e:
            print(f"  L/S ratio fetch error: {e}", flush=True)
            break
    
    all_data.sort(key=lambda x: x["ts_ms"])
    print(f"  Got {len(all_data)} L/S ratio records", flush=True)
    return all_data


def main():
    days = 365
    if "--days" in sys.argv:
        days = int(sys.argv[sys.argv.index("--days") + 1])
    
    hours = days * 24
    print(f"=== Fetching Microstructure Data ({days} days) ===", flush=True)
    
    data_dir = Path(__file__).resolve().parents[2] / "data"
    data_dir.mkdir(exist_ok=True)
    
    t0 = time.time()
    
    # 1. Funding Rate
    funding = fetch_funding_rate(hours=hours)
    (data_dir / "eth_funding.json").write_text(json.dumps(funding, indent=2))
    
    # 2. Open Interest (15m interval)
    oi = fetch_open_interest(interval="15min", hours=hours)
    (data_dir / "eth_oi.json").write_text(json.dumps(oi, indent=2))
    
    # 3. Long/Short Ratio (5min)
    ls_ratio = fetch_long_short_ratio(period="5min", hours=hours)
    (data_dir / "eth_long_short.json").write_text(json.dumps(ls_ratio, indent=2))
    
    elapsed = round(time.time() - t0, 1)
    print(f"\nDone in {elapsed}s", flush=True)
    print(f"  {data_dir}/eth_funding.json — {len(funding)} records", flush=True)
    print(f"  {data_dir}/eth_oi.json — {len(oi)} records", flush=True)
    print(f"  {data_dir}/eth_long_short.json — {len(ls_ratio)} records", flush=True)


if __name__ == "__main__":
    main()
