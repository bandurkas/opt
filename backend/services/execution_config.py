"""Live-execution config — single source of truth for the trader's runtime mode,
safety caps, and order-execution tuning. Dependency-free (env-driven) like
strategy_config, so it can be imported anywhere without side effects.

ALL DEFAULTS ARE SAFE: mode=paper, kill-switch OFF (no real orders), conservative
caps. Going live requires explicitly setting TRADING_MODE + LIVE_ENABLED in env.

Modes:
  paper    — no broker calls; positions simulated in DB (current behaviour).
  testnet  — real orders on Bybit testnet (fake money). Uses BYBIT_TESTNET_* keys.
  live     — real orders on Bybit mainnet (real money). Uses BYBIT_API_* keys.
"""
from __future__ import annotations

import os


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


def _b(name: str, default: bool) -> bool:
    v = os.getenv(name, "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


# ───────────────────────── Mode ─────────────────────────
TRADING_MODE = (os.getenv("TRADING_MODE", "paper").strip().lower() or "paper")
if TRADING_MODE not in ("paper", "testnet", "live"):
    TRADING_MODE = "paper"

# Master kill-switch. Even in testnet/live mode, no order is placed unless this
# is explicitly true. Lets us deploy the trader "armed but safe", then flip on.
LIVE_ENABLED = _b("LIVE_ENABLED", False)

# File-based emergency stop: if this path exists, halt all NEW opens immediately
# (no redeploy needed — just `touch` it on the VPS). Existing positions still
# get monitored/closed normally.
KILLSWITCH_FILE = os.getenv("LIVE_KILLSWITCH_FILE", "/data/STOP_TRADING").strip()


# ───────────────────────── Risk caps ─────────────────────────
# NOTE: Bybit ETH options are USDT-settled (symbols `ETH-…-USDT`, settleCoin=USDT).
# All collateral / PnL is in USDT.
# Max total USDT collateral the bot may have locked across all open positions.
LIVE_MAX_CAPITAL_USDT = _f("LIVE_MAX_CAPITAL_USDT", 1000.0)
# Max simultaneous open positions. 0 = unlimited (size is margin-bound instead).
LIVE_MAX_CONCURRENT = _i("LIVE_MAX_CONCURRENT", 0)
# Hard cap on lots (0.1-ETH units) per single trade. 0 = unlimited (margin-bound).
LIVE_PER_TRADE_LOTS_CAP = _i("LIVE_PER_TRADE_LOTS_CAP", 0)
# Refuse to open if wallet USDT balance falls below this.
LIVE_MIN_WALLET_USDT = _f("LIVE_MIN_WALLET_USDT", 50.0)
# Halt new opens for the rest of the UTC day after realized losses exceed this.
LIVE_DAILY_LOSS_LIMIT_USDT = _f("LIVE_DAILY_LOSS_LIMIT_USDT", 100.0)
# Fraction of available margin the bot may use (buffer vs liquidation). 0.5 = 50%.
LIVE_MARGIN_UTILIZATION = _f("LIVE_MARGIN_UTILIZATION", 0.5)


# ───────────────────────── Order execution ─────────────────────────
# Total seconds to wait for a limit order to fill before escalating to market.
LIMIT_TIMEOUT_S = _i("LIMIT_TIMEOUT_S", 20)
# Poll interval while waiting for a limit fill.
LIMIT_POLL_S = _i("LIMIT_POLL_S", 2)
# Skip a signal if the option's bid/ask spread exceeds this % of mid (illiquid).
MAX_SPREAD_PCT = _f("MAX_SPREAD_PCT", 15.0)
# Alert (do not block) if the realized fill is worse than expected mid by this %.
MAX_SLIPPAGE_PCT = _f("MAX_SLIPPAGE_PCT", 25.0)


# ───────────────────────── Derived helpers ─────────────────────────
def is_paper() -> bool:
    return TRADING_MODE == "paper"


def use_testnet() -> bool:
    return TRADING_MODE == "testnet"


def killswitch_engaged() -> bool:
    """True if trading must be halted (kill-switch file present)."""
    try:
        return bool(KILLSWITCH_FILE) and os.path.exists(KILLSWITCH_FILE)
    except OSError:
        return True  # fail safe: if we can't check, assume halted


def trading_armed() -> bool:
    """Real orders are allowed only when: not paper, LIVE_ENABLED, no kill-switch."""
    return (not is_paper()) and LIVE_ENABLED and not killswitch_engaged()


def api_credentials() -> tuple[str | None, str | None]:
    """Return (api_key, api_secret) for the current mode. testnet keys are
    SEPARATE from mainnet keys on Bybit."""
    if use_testnet():
        return (os.getenv("BYBIT_TESTNET_API_KEY") or None,
                os.getenv("BYBIT_TESTNET_API_SECRET") or None)
    return (os.getenv("BYBIT_API_KEY") or None,
            os.getenv("BYBIT_API_SECRET") or None)


def summary() -> str:
    """One-line startup banner (no secrets)."""
    key, _ = api_credentials()
    return (f"TRADING_MODE={TRADING_MODE} armed={trading_armed()} "
            f"LIVE_ENABLED={LIVE_ENABLED} testnet={use_testnet()} "
            f"key={'set' if key else 'MISSING'} killswitch={killswitch_engaged()} "
            f"caps[cap=${LIVE_MAX_CAPITAL_USDT:.0f} max_conc={LIVE_MAX_CONCURRENT or '∞'} "
            f"lots<={LIVE_PER_TRADE_LOTS_CAP or '∞'} min_wallet=${LIVE_MIN_WALLET_USDT:.0f} "
            f"util={LIVE_MARGIN_UTILIZATION:.0%} daily_loss<=${LIVE_DAILY_LOSS_LIMIT_USDT:.0f}] "
            f"exec[limit_timeout={LIMIT_TIMEOUT_S}s poll={LIMIT_POLL_S}s "
            f"max_spread={MAX_SPREAD_PCT:.0f}% max_slip={MAX_SLIPPAGE_PCT:.0f}%]")
