#!/usr/bin/env python3
"""Out-of-band SL safety net for the straddle paper bots.

Runs from the VPS HOST crontab — deliberately outside `docker compose`'s
lifecycle, so it keeps checking stop-losses even while `eth_straddle_paper` /
`btc_straddle_paper` are down for a redeploy, crashed, or OOM-killed.

Root cause this guards against (2026-06-23): a redeploy restarted
eth_straddle_paper while a Put leg was open; the container was unmonitored
for ~2h45m, during which the position blew through its $5.71 dollar-SL and
closed at -$24.58 (~11x worse) the moment the container came back up. See
ETH_STRADDLE_PAPER_BOT_HANDOFF.md / memory finding_grogu_sl_incident_deploy_gap.

Deliberately minimal: SL-only (not TP/time-stop — those aren't the danger
case and duplicating them risks drifting from the bots' own pricing). Talks
to Postgres via `docker exec` (the postgres container is not part of the
trading-bot redeploy) and to Bybit's public ticker endpoint directly (no
auth, no dependency on the backend container). Stdlib only — no pip installs
needed on the host.

Safe to run concurrently with the main bot loops: every close is a single
`UPDATE ... WHERE status='open'` — whichever of (watchdog, main bot) commits
first wins, the other's update just affects 0 rows.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request

OPT_APP_DIR = "/root/opt-app"
PSQL_BASE = ["docker", "exec", "opt-app-postgres-1", "psql", "-U", "user", "-d", "options_assistant"]

BOTS = {
    "eth_straddle": {"table": "eth_straddle_positions", "lot": 0.10},
    "btc_straddle": {"table": "btc_straddle_positions", "lot": 0.01},
}

# Same friction model as eth_straddle_loop.py / btc_straddle_loop.py — kept
# in sync manually since this script intentionally has zero imports from
# the app (it must work even if that code can't even be imported right now).
SPREAD_HALF_PCT = 1.0
FEE_RATE = 0.0003
FEE_CAP_PCT_OF_PREMIUM = 0.125

LOG_PATH = f"{OPT_APP_DIR}/watchdog_sl.log"


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def psql(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(PSQL_BASE + args, capture_output=True, text=True, timeout=20)


def fetch_open(table: str) -> list[dict]:
    q = (f"SELECT id, leg, strike, contracts, entry_credit_usd, sl_dollar_trip_usd, "
         f"signal_payload->>'symbol' FROM {table} WHERE status='open';")
    r = psql(["-t", "-A", "-F", "|", "-c", q])
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip())
    rows = []
    for line in r.stdout.strip().splitlines():
        if not line:
            continue
        pid, leg, strike, contracts, entry_credit, sl_trip, symbol = line.split("|")
        rows.append({
            "id": int(pid), "leg": leg, "strike": float(strike),
            "contracts": float(contracts), "entry_credit_usd": float(entry_credit),
            "sl_dollar_trip_usd": float(sl_trip), "symbol": symbol,
        })
    return rows


def bybit_ask(symbol: str) -> float | None:
    url = f"https://api.bybit.com/v5/market/tickers?category=option&symbol={urllib.parse.quote(symbol)}"
    with urllib.request.urlopen(url, timeout=8) as resp:
        data = json.load(resp)
    items = (data.get("result") or {}).get("list") or []
    if not items:
        return None
    ask = float(items[0].get("ask1Price") or 0)
    return ask if ask > 0 else None


def is_tripped(entry_credit: float, ask: float, qty: float, sl_trip_per_lot: float, lot: float) -> bool:
    if qty <= 0 or lot <= 0:
        return False
    unrealized_loss = (ask - entry_credit) * qty
    trip = sl_trip_per_lot * (qty / lot)
    return unrealized_loss >= trip


def close_position(table: str, pid: int, mark: float, entry_credit: float,
                   contracts: float, strike: float) -> tuple[bool, float, float, float]:
    exit_gross = mark * (1 + SPREAD_HALF_PCT / 100.0)
    notional = strike * contracts
    premium_paid = exit_gross * contracts
    fee = min(notional * FEE_RATE, abs(premium_paid) * FEE_CAP_PCT_OF_PREMIUM)
    exit_net = exit_gross + fee / max(contracts, 1e-9)
    pnl_per_contract = entry_credit - exit_net
    pnl_usd = pnl_per_contract * contracts
    pnl_pct = (pnl_per_contract / entry_credit * 100) if entry_credit > 0 else 0.0
    now_ms = int(time.time() * 1000)
    q = (f"UPDATE {table} SET status='closed', closed_at_ms={now_ms}, "
        f"exit_debit_usd={exit_net:.6f}, pnl_pct={pnl_pct:.6f}, pnl_usd={pnl_usd:.6f}, "
        f"exit_reason='watchdog_sl' WHERE id={pid} AND status='open';")
    r = psql(["-c", q])
    won = r.returncode == 0 and "UPDATE 1" in r.stdout
    return won, pnl_usd, pnl_pct, exit_net


def telegram_alert(text: str) -> None:
    token = chat_id = None
    try:
        with open(f"{OPT_APP_DIR}/.env") as f:
            for line in f:
                if line.startswith("TELEGRAM_BOT_TOKEN="):
                    token = line.split("=", 1)[1].strip()
                elif line.startswith("TELEGRAM_CHAT_ID="):
                    chat_id = line.split("=", 1)[1].strip()
    except OSError:
        return
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    try:
        urllib.request.urlopen(url, data=body, timeout=5)
    except Exception as e:  # noqa: BLE001
        log(f"telegram alert failed: {e!r}")


def main() -> None:
    fired = []
    for bot_name, info in BOTS.items():
        try:
            rows = fetch_open(info["table"])
        except Exception as e:  # noqa: BLE001
            log(f"[{bot_name}] DB query failed (postgres down?): {e!r}")
            continue
        for p in rows:
            try:
                ask = bybit_ask(p["symbol"])
            except Exception as e:  # noqa: BLE001
                log(f"[{bot_name}] #{p['id']} bybit fetch failed for {p['symbol']}: {e!r}")
                continue
            if ask is None:
                continue
            if not is_tripped(p["entry_credit_usd"], ask, p["contracts"],
                              p["sl_dollar_trip_usd"], info["lot"]):
                continue
            won, pnl_usd, pnl_pct, exit_net = close_position(
                info["table"], p["id"], ask, p["entry_credit_usd"], p["contracts"], p["strike"])
            if not won:
                log(f"[{bot_name}] #{p['id']} SL tripped but main bot closed it first — no-op")
                continue
            msg = (f"\U0001f6d1 WATCHDOG closed {bot_name} #{p['id']} leg={p['leg']} "
                  f"reason=watchdog_sl mark=${ask:.2f} debit_net=${exit_net:.2f} "
                  f"pnl={pnl_pct:+.2f}% (${pnl_usd:+.2f})")
            log(msg)
            fired.append(msg)
    for m in fired:
        telegram_alert(m)


if __name__ == "__main__":
    main()
