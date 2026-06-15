"""Authenticated Bybit execution client for live/testnet options trading.

Wraps pybit's unified_trading.HTTP with:
  - lazy auth (keys + testnet flag from execution_config)
  - instrument-filter rounding (tickSize / qtyStep) so orders aren't rejected
  - a read-only auth probe (account type + key permissions + wallet)
  - high-level sell_to_open / buy_to_close with LIMIT→MARKET fallback

Design rules (money path):
  - Every Bybit call is wrapped; on any error we log + return a falsy/None result.
  - We NEVER assume a fill — fills are read back from the exchange (cumExecQty /
    avgPrice / cumExecFee). A signal whose order can't be confirmed filled is
    treated as not-opened.
  - The HTTP session can be injected for unit tests (no network).

qty is in ETH (Bybit ETH options: min 0.1, step 0.1). price is the option
premium per unit in USDT (Bybit ETH options are USDT-settled).
"""
from __future__ import annotations

import time
from typing import Any, NamedTuple

from . import execution_config as cfg


class OrderResult(NamedTuple):
    order_id: str
    avg_price: float      # weighted-average fill price (premium/unit, USDT)
    filled_qty: float     # ETH filled
    fees: float           # total fees paid (USDT)
    status: str           # 'Filled' | 'PartiallyFilled' | 'Failed'

    @property
    def is_filled(self) -> bool:
        return self.filled_qty > 0 and self.status in ("Filled", "PartiallyFilled")


class ExecutionError(Exception):
    pass


# Terminal/known Bybit order states
_TERMINAL = {"Filled", "Cancelled", "Rejected", "Deactivated"}


class ExecutionClient:
    """Thin, defensive wrapper around pybit HTTP for option order execution."""

    CATEGORY = "option"

    def __init__(self, session: Any = None, *, require_keys: bool = True):
        self._instrument_cache: dict[str, dict] = {}
        if session is not None:
            self.session = session
            return
        key, secret = cfg.api_credentials()
        if require_keys and (not key or not secret):
            raise ExecutionError(
                f"missing API keys for mode={cfg.TRADING_MODE} "
                f"(need {'BYBIT_TESTNET_*' if cfg.use_testnet() else 'BYBIT_API_*'})"
            )
        from pybit.unified_trading import HTTP  # local import keeps module light
        self.session = HTTP(testnet=cfg.use_testnet(), api_key=key, api_secret=secret)

    # ───────────────────── low-level read ─────────────────────

    @staticmethod
    def _list(resp: dict) -> list[dict]:
        return (resp or {}).get("result", {}).get("list", []) or []

    def account_info(self) -> dict:
        """Read-only probe: API-key permissions + account type. Used to verify
        the account is ready (UTA + Options trade perm) before going live."""
        out: dict[str, Any] = {}
        try:
            info = self.session.get_api_key_information()
            res = (info or {}).get("result", {})
            out["uta"] = res.get("uta")  # 1 = Unified Trading Account
            out["permissions"] = res.get("permissions", {})
            out["readOnly"] = res.get("readOnly")
        except Exception as e:  # noqa: BLE001
            out["error"] = repr(e)
        return out

    def wallet_usdt(self) -> float | None:
        """Wallet balance in USDT (Bybit ETH options are USDT-settled)."""
        try:
            resp = self.session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
            rows = self._list(resp)
            if not rows:
                return 0.0
            for c in rows[0].get("coin", []):
                if c.get("coin") == "USDT":
                    return float(c.get("walletBalance") or 0.0)
            return 0.0
        except Exception as e:  # noqa: BLE001
            print(f"[exec] wallet_usdt error: {e!r}", flush=True)
            return None

    def available_usdt(self) -> float | None:
        """Funds available to open new positions (UTA total available balance, USD-value)."""
        try:
            resp = self.session.get_wallet_balance(accountType="UNIFIED")
            rows = self._list(resp)
            if not rows:
                return 0.0
            return float(rows[0].get("totalAvailableBalance") or 0.0)
        except Exception as e:  # noqa: BLE001
            print(f"[exec] available_usdt error: {e!r}", flush=True)
            return None

    def positions(self, base_coin: str = "ETH") -> list[dict] | None:
        try:
            resp = self.session.get_positions(category=self.CATEGORY, baseCoin=base_coin)
            return self._list(resp)
        except Exception as e:  # noqa: BLE001
            print(f"[exec] positions error: {e!r}", flush=True)
            return None

    def instrument(self, symbol: str) -> dict | None:
        """Cached instrument filters (tickSize, qtyStep, minOrderQty)."""
        if symbol in self._instrument_cache:
            return self._instrument_cache[symbol]
        try:
            resp = self.session.get_instruments_info(category=self.CATEGORY, symbol=symbol)
            rows = self._list(resp)
            if not rows:
                return None
            it = rows[0]
            info = {
                "tick_size": float(it.get("priceFilter", {}).get("tickSize") or 0.1),
                "qty_step": float(it.get("lotSizeFilter", {}).get("qtyStep") or 0.1),
                "min_qty": float(it.get("lotSizeFilter", {}).get("minOrderQty") or 0.1),
            }
            self._instrument_cache[symbol] = info
            return info
        except Exception as e:  # noqa: BLE001
            print(f"[exec] instrument error {symbol}: {e!r}", flush=True)
            return None

    def _fetch_order(self, symbol: str, order_id: str) -> dict | None:
        """Return the order dict from open orders, falling back to history."""
        try:
            resp = self.session.get_open_orders(category=self.CATEGORY, symbol=symbol, orderId=order_id)
            rows = self._list(resp)
            if rows:
                return rows[0]
        except Exception as e:  # noqa: BLE001
            print(f"[exec] get_open_orders error {order_id}: {e!r}", flush=True)
        try:
            resp = self.session.get_order_history(category=self.CATEGORY, symbol=symbol, orderId=order_id)
            rows = self._list(resp)
            if rows:
                return rows[0]
        except Exception as e:  # noqa: BLE001
            print(f"[exec] get_order_history error {order_id}: {e!r}", flush=True)
        return None

    # ───────────────────── rounding ─────────────────────

    @staticmethod
    def _round_to(value: float, step: float) -> float:
        if step <= 0:
            return value
        return round(round(value / step) * step, 10)

    def _round_price(self, symbol: str, price: float) -> float:
        info = self.instrument(symbol)
        return self._round_to(price, info["tick_size"]) if info else round(price, 2)

    def _round_qty(self, symbol: str, qty: float) -> float:
        info = self.instrument(symbol)
        if not info:
            return round(qty, 1)
        q = self._round_to(qty, info["qty_step"])
        return q if q >= info["min_qty"] else 0.0

    # ───────────────────── low-level write ─────────────────────

    def place_order(self, *, symbol: str, side: str, qty: float,
                    order_type: str, price: float | None = None,
                    reduce_only: bool = False, tif: str = "GTC") -> str | None:
        """Place an order. Returns orderId or None on failure."""
        params: dict[str, Any] = {
            "category": self.CATEGORY, "symbol": symbol, "side": side,
            "orderType": order_type, "qty": str(qty),
            "timeInForce": tif, "reduceOnly": reduce_only,
        }
        if order_type == "Limit" and price is not None:
            params["price"] = str(price)
        try:
            resp = self.session.place_order(**params)
            oid = (resp or {}).get("result", {}).get("orderId")
            if not oid:
                print(f"[exec] place_order no orderId: {resp}", flush=True)
                return None
            return oid
        except Exception as e:  # noqa: BLE001
            print(f"[exec] place_order FAILED {side} {qty} {symbol} {order_type}: {e!r}", flush=True)
            return None

    def cancel_order(self, symbol: str, order_id: str) -> bool:
        try:
            self.session.cancel_order(category=self.CATEGORY, symbol=symbol, orderId=order_id)
            return True
        except Exception as e:  # noqa: BLE001
            print(f"[exec] cancel_order {order_id}: {e!r}", flush=True)
            return False

    @staticmethod
    def _order_fill(order: dict | None) -> tuple[float, float, float, str]:
        """Extract (filled_qty, avg_price, fee, status) from an order dict."""
        if not order:
            return (0.0, 0.0, 0.0, "Unknown")
        filled = float(order.get("cumExecQty") or 0.0)
        avg = float(order.get("avgPrice") or 0.0)
        fee = float(order.get("cumExecFee") or 0.0)
        status = order.get("orderStatus") or "Unknown"
        return (filled, avg, fee, status)

    def _wait_fill(self, symbol: str, order_id: str, timeout_s: int) -> dict | None:
        """Poll until the order is terminal or timeout. Returns last order dict."""
        deadline = time.time() + timeout_s
        last = None
        while True:
            last = self._fetch_order(symbol, order_id)
            _, _, _, status = self._order_fill(last)
            if status in _TERMINAL:
                return last
            if time.time() >= deadline:
                return last
            time.sleep(cfg.LIMIT_POLL_S)

    # ───────────────────── high-level entry/exit ─────────────────────

    def _execute(self, *, symbol: str, side: str, qty: float, ref_mid: float,
                 reduce_only: bool) -> OrderResult | None:
        """LIMIT at ref_mid, then MARKET for any unfilled remainder. Returns the
        aggregated fill, or None if nothing filled / fatal error."""
        qty = self._round_qty(symbol, qty)
        if qty <= 0:
            print(f"[exec] {side} {symbol}: qty rounds to 0 (below min)", flush=True)
            return None
        limit_px = self._round_price(symbol, ref_mid)

        oid = self.place_order(symbol=symbol, side=side, qty=qty,
                               order_type="Limit", price=limit_px, reduce_only=reduce_only)
        if not oid:
            return None

        order = self._wait_fill(symbol, oid, cfg.LIMIT_TIMEOUT_S)
        filled, avg, fee, status = self._order_fill(order)

        if status == "Filled":
            return OrderResult(oid, avg, filled, fee, "Filled")

        # Not fully filled within timeout → cancel resting remainder, sweep with MARKET.
        self.cancel_order(symbol, oid)
        time.sleep(cfg.LIMIT_POLL_S)
        order = self._fetch_order(symbol, oid)
        filled, avg, fee, status = self._order_fill(order)

        remaining = self._round_qty(symbol, qty - filled)
        if remaining <= 0:
            # cancel raced with a full fill
            return OrderResult(oid, avg, filled, fee, status if filled > 0 else "Failed") \
                if filled > 0 else None

        moid = self.place_order(symbol=symbol, side=side, qty=remaining,
                                order_type="Market", reduce_only=reduce_only)
        if not moid:
            # we may still hold a partial limit fill
            return OrderResult(oid, avg, filled, fee, "PartiallyFilled") if filled > 0 else None

        morder = self._wait_fill(symbol, moid, cfg.LIMIT_TIMEOUT_S)
        m_filled, m_avg, m_fee, _ = self._order_fill(morder)

        tot_qty = filled + m_filled
        if tot_qty <= 0:
            return None
        tot_fee = fee + m_fee
        # weighted-average price across the limit + market legs
        wavg = ((avg * filled) + (m_avg * m_filled)) / tot_qty
        final_status = "Filled" if abs(tot_qty - qty) < 1e-9 else "PartiallyFilled"
        return OrderResult(moid, wavg, tot_qty, tot_fee, final_status)

    def sell_to_open(self, symbol: str, qty: float, ref_mid: float) -> OrderResult | None:
        """Open a short option (collect premium)."""
        return self._execute(symbol=symbol, side="Sell", qty=qty,
                              ref_mid=ref_mid, reduce_only=False)

    def buy_to_close(self, symbol: str, qty: float, ref_mid: float) -> OrderResult | None:
        """Close a short option (pay debit)."""
        return self._execute(symbol=symbol, side="Buy", qty=qty,
                              ref_mid=ref_mid, reduce_only=True)
