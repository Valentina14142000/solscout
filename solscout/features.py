from __future__ import annotations

import math
from dataclasses import dataclass

from .models import Pair


@dataclass(frozen=True)
class Features:
    age_min: float | None
    accel: float            # recent hourly volume vs. the token's own earlier hourly average
    accel_basis: str        # "baseline" (long history) or "run-rate" (very new token)
    buy_ratio: float | None  # buys / (buys + sells) over the last hour
    txns_h1: int
    turnover: float         # 24h volume / liquidity


def compute(p: Pair, now: float) -> Features:
    age_min = (now - p.created_at) / 60 if p.created_at else None
    hours_active = min((age_min or 24 * 60) / 60, 24.0)

    if hours_active >= 2 and p.volume_h24 > p.volume_h1:
        baseline = (p.volume_h24 - p.volume_h1) / max(hours_active - 1, 1)
        accel = p.volume_h1 / baseline if baseline > 0 else 1.0
        basis = "baseline"
    else:
        # too young for a baseline: compare the last 5 minutes (annualised to an hour) with the hour average
        accel = (p.volume_m5 * 12) / p.volume_h1 if p.volume_h1 > 0 else 1.0
        basis = "run-rate"

    trades = p.buys_h1 + p.sells_h1
    return Features(
        age_min=age_min,
        accel=accel,
        accel_basis=basis,
        buy_ratio=(p.buys_h1 / trades) if trades else None,
        txns_h1=trades,
        turnover=(p.volume_h24 / p.liquidity_usd) if p.liquidity_usd > 0 else 0.0,
    )


def log_scale(x: float, lo: float, hi: float) -> float:
    """0 at x<=lo, 1 at x>=hi, logarithmic in between."""
    if x <= lo:
        return 0.0
    if x >= hi:
        return 1.0
    return math.log(x / lo) / math.log(hi / lo)
