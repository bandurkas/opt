"""Detect 'walls' in an option's orderbook — large resting orders that block price."""
from __future__ import annotations

from .bybit_client import bybit_client


WALL_SIZE_MULTIPLIER = 5.0   # >= 5× median level size = wall
MAX_DEPTH = 25


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def analyze_book(symbol: str) -> dict:
    book = bybit_client.get_option_orderbook(symbol, depth=MAX_DEPTH)
    bids = book["bids"]
    asks = book["asks"]
    out: dict = {
        "has_book": bool(bids and asks),
        "best_bid": bids[0][0] if bids else 0.0,
        "best_ask": asks[0][0] if asks else 0.0,
        "bid_size_top": bids[0][1] if bids else 0.0,
        "ask_size_top": asks[0][1] if asks else 0.0,
        "bid_walls": [],
        "ask_walls": [],
        "imbalance": 0.0,  # >0 → bids heavier (buying pressure); <0 → asks heavier
    }
    if not bids or not asks:
        return out

    bid_sizes = [q for _, q in bids]
    ask_sizes = [q for _, q in asks]
    bid_median = _median(bid_sizes)
    ask_median = _median(ask_sizes)

    for p, q in bids:
        if bid_median > 0 and q >= bid_median * WALL_SIZE_MULTIPLIER:
            out["bid_walls"].append({"price": p, "size": q})
    for p, q in asks:
        if ask_median > 0 and q >= ask_median * WALL_SIZE_MULTIPLIER:
            out["ask_walls"].append({"price": p, "size": q})

    total_bid = sum(bid_sizes[:5])
    total_ask = sum(ask_sizes[:5])
    if total_bid + total_ask > 0:
        out["imbalance"] = round((total_bid - total_ask) / (total_bid + total_ask), 3)

    return out


def wall_penalty(book: dict, exit_premium: float, entry_premium: float, side_long: bool = True) -> dict:
    """For a long premium position, walls on the ASK side between entry and exit
    means heavy resting sellers blocking premium appreciation → bad.
    """
    blocking_walls: list[dict] = []
    if not book["has_book"]:
        return {"blocking_walls": [], "penalty": 0.0, "note": "no orderbook"}

    # We hold long premium → for TP we need premium to rise → walls are sell-walls (asks) above entry.
    for w in book["ask_walls"]:
        if entry_premium <= w["price"] <= exit_premium:
            blocking_walls.append(w)

    if not blocking_walls:
        return {"blocking_walls": [], "penalty": 0.0, "note": "clear path"}

    # Penalty grows with number and size of walls in the path
    total_wall_size = sum(w["size"] for w in blocking_walls)
    penalty = min(2.0, 0.5 * len(blocking_walls) + total_wall_size / 50)
    return {
        "blocking_walls": blocking_walls,
        "penalty": round(penalty, 2),
        "note": f"{len(blocking_walls)} стенка(и) ask между премией {entry_premium} и {exit_premium}",
    }
