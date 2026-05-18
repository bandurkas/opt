from pybit.unified_trading import HTTP


class BybitClient:
    def __init__(self):
        self.session = HTTP(testnet=False)

    def get_spot_price(self, symbol: str = "ETHUSDT") -> float:
        try:
            response = self.session.get_tickers(category="linear", symbol=symbol)
            return float(response["result"]["list"][0]["lastPrice"])
        except Exception as e:
            print(f"[bybit] spot price error ({symbol}): {e}")
            return 0.0

    def get_klines(self, symbol: str = "ETHUSDT", interval: str = "60", limit: int = 50) -> list[dict]:
        """Returns list of candles sorted oldest→newest. Each item:
        {start_ms, open, high, low, close, volume, turnover}.
        """
        try:
            response = self.session.get_kline(
                category="linear", symbol=symbol, interval=interval, limit=limit
            )
            raw = response["result"]["list"]
        except Exception as e:
            print(f"[bybit] klines error ({symbol},{interval}): {e}")
            return []

        candles = []
        for row in reversed(raw):
            candles.append(
                {
                    "start_ms": int(row[0]),
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "volume": float(row[5]),
                    "turnover": float(row[6]),
                }
            )
        return candles

    def get_orderbook(self, symbol: str = "ETHUSDT", depth: int = 25) -> dict:
        try:
            response = self.session.get_orderbook(category="linear", symbol=symbol, limit=depth)
            r = response["result"]
            return {
                "bids": [(float(p), float(q)) for p, q in r.get("b", [])],
                "asks": [(float(p), float(q)) for p, q in r.get("a", [])],
            }
        except Exception as e:
            print(f"[bybit] orderbook error ({symbol}): {e}")
            return {"bids": [], "asks": []}

    def get_options_tickers(self, base_coin: str = "ETH") -> list[dict]:
        """Live options chain with bid/ask, IV, Greeks, OI, 24h volume."""
        try:
            response = self.session.get_tickers(category="option", baseCoin=base_coin)
            items = response.get("result", {}).get("list", [])
        except Exception as e:
            print(f"[bybit] options tickers error ({base_coin}): {e}")
            return []

        out: list[dict] = []
        for it in items:
            parsed = self._parse_option_symbol(it.get("symbol", ""))
            if not parsed:
                continue
            try:
                out.append(
                    {
                        "symbol": it["symbol"],
                        "base_coin": parsed["base"],
                        "expiry_ms": parsed["expiry_ms"],
                        "expiry_label": parsed["expiry_label"],
                        "strike": parsed["strike"],
                        "side": parsed["side"],  # "C" / "P"
                        "bid": _f(it.get("bid1Price")),
                        "bid_size": _f(it.get("bid1Size")),
                        "ask": _f(it.get("ask1Price")),
                        "ask_size": _f(it.get("ask1Size")),
                        "mark_price": _f(it.get("markPrice")),
                        "index_price": _f(it.get("indexPrice")),
                        "underlying_price": _f(it.get("underlyingPrice")),
                        "mark_iv": _f(it.get("markIv")),
                        "delta": _f(it.get("delta")),
                        "gamma": _f(it.get("gamma")),
                        "vega": _f(it.get("vega")),
                        "theta": _f(it.get("theta")),
                        "open_interest": _f(it.get("openInterest")),
                        "volume_24h": _f(it.get("volume24h")),
                        "turnover_24h": _f(it.get("turnover24h")),
                        "change_24h": _f(it.get("change24h")),
                    }
                )
            except Exception as e:
                print(f"[bybit] option parse error ({it.get('symbol')}): {e}")
        return out

    @staticmethod
    def _parse_option_symbol(symbol: str) -> dict | None:
        # Bybit option symbol format: BASE-DDMMMYY-STRIKE-{C|P}[-QUOTE]
        # e.g. ETH-30MAY26-3000-C or ETH-30MAY26-3000-C-USDT
        if not symbol:
            return None
        parts = symbol.split("-")
        if len(parts) < 4:
            return None
        base, date_part, strike_part, side = parts[0], parts[1], parts[2], parts[3]
        if side not in ("C", "P"):
            return None
        try:
            strike = float(strike_part)
        except ValueError:
            return None
        from datetime import datetime, timezone

        try:
            dt = datetime.strptime(date_part, "%d%b%y").replace(
                hour=8, minute=0, tzinfo=timezone.utc
            )
        except ValueError:
            return None
        return {
            "base": base,
            "strike": strike,
            "side": side,
            "expiry_ms": int(dt.timestamp() * 1000),
            "expiry_label": dt.strftime("%d%b%y").upper(),
        }


def _f(v) -> float:
    try:
        if v in (None, "", "null"):
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


bybit_client = BybitClient()
