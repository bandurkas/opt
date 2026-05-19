"""Estimate the probability that the holder of a long option position
will be 'killed by theta' before either TP or SL fires.

Heuristic, not Black-Scholes — we want a single number in [0,1]
that:
  - grows with daily theta / premium ratio
  - grows with planned holding time
  - shrinks with |delta| (deep in/out-of-money options are less theta-sensitive)
"""
from __future__ import annotations


def theta_decay_probability(
    theta: float,
    mid_premium: float,
    hours_held: float,
    delta: float,
) -> float:
    """Return P(theta_victim) in [0,1]."""
    if mid_premium <= 0 or hours_held <= 0:
        return 0.0

    daily_decay_rate = min(1.0, abs(theta) / mid_premium)  # fraction of premium lost per day
    hours_factor = min(2.0, hours_held / 24.0)             # 24h trade → 1.0, capped at 2.0
    delta_protection = 1 - min(1.0, abs(delta))            # ATM (|d|≈0.5) gives 0.5; deep ITM (|d|=1) gives 0

    p = daily_decay_rate * hours_factor * delta_protection
    return max(0.0, min(1.0, p))


def classify(probability: float) -> str:
    if probability >= 0.5:
        return "critical"
    if probability >= 0.3:
        return "high"
    if probability >= 0.15:
        return "medium"
    return "low"
