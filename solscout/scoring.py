"""Turns features into a 0-100 score with a plain-language reason for every point awarded."""
from __future__ import annotations

from dataclasses import dataclass, field

from .features import Features, log_scale
from .models import Pair, Safety

# Maximum points per component (sum = 100). Tune these against real paper-trade results.
WEIGHTS = {"momentum": 25, "volume": 25, "buying": 15, "liquidity": 15, "age": 10, "safety": 10}


@dataclass
class Score:
    total: float
    parts: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _momentum(p: Pair, reasons: list[str], warnings: list[str]) -> float:
    c = p.change_h1
    if c <= 0:
        pts = 0.0
    elif c < 10:
        pts = 12 * c / 10
    elif c <= 80:
        pts = 25.0
    elif c <= 200:
        pts = 25 - 15 * (c - 80) / 120
    else:
        pts = 0.0
    if p.change_m5 < -5:
        pts = max(0.0, pts - 5)
        warnings.append(f"price already {p.change_m5:+.0f}% in the last 5 min")
    if pts > 0:
        reasons.append(f"price {c:+.0f}% in 1h")
    return pts


def score(p: Pair, f: Features, s: Safety | None, *, min_liquidity: float = 15_000) -> Score:
    reasons: list[str] = []
    warnings: list[str] = []
    parts: dict[str, float] = {}

    parts["momentum"] = _momentum(p, reasons, warnings)

    parts["volume"] = WEIGHTS["volume"] * log_scale(f.accel, 1.0, 8.0)
    if parts["volume"] > 0:
        label = "its earlier hourly average" if f.accel_basis == "baseline" else "the hour's average pace"
        reasons.append(f"volume running {f.accel:.1f}x {label}")

    if f.buy_ratio is not None and f.buy_ratio > 0.5:
        parts["buying"] = WEIGHTS["buying"] * min(1.0, (f.buy_ratio - 0.5) / 0.15)
        reasons.append(f"{f.buy_ratio:.0%} of last-hour trades are buys")
    else:
        parts["buying"] = 0.0

    liq = p.liquidity_usd
    liq_pts = 15 if liq >= 100_000 else 12 if liq >= 50_000 else 9 if liq >= 25_000 else 5 if liq >= min_liquidity else 0
    if f.turnover > 30:
        liq_pts = max(0, liq_pts - 5)
        warnings.append(f"24h volume is {f.turnover:.0f}x liquidity: possible wash trading")
    parts["liquidity"] = float(liq_pts)
    reasons.append(f"liquidity ${liq:,.0f}")

    age_h = (f.age_min or 0) / 60
    parts["age"] = 0.0 if age_h < 0.25 else 6.0 if age_h < 1 else 10.0 if age_h <= 12 else 8.0 if age_h <= 48 else 5.0
    reasons.append(f"age {age_h:.1f}h")

    parts["safety"] = 0.0
    if s and s.checked:
        if not s.mint_authority and not s.freeze_authority:
            parts["safety"] += 5
            reasons.append("mint and freeze authority revoked")
        t = s.top_holders_pct
        if t is not None:
            parts["safety"] += 5 if t <= 15 else 3 if t <= 25 else 1 if t <= 35 else 0
            reasons.append(f"holders ranked 2-10 own {t:.0f}% of supply")
        if s.largest_pct is not None and s.largest_pct > 50:
            warnings.append(f"largest holder owns {s.largest_pct:.0f}% (may be the liquidity pool)")

    if s and s.flags:
        warnings.append("RugCheck danger flags: " + ", ".join(s.flags[:4]))

    total = sum(parts.values())
    if p.change_h6 > 300:
        total -= 25
        warnings.append(f"already {p.change_h6:+.0f}% in 6h: this may be late")
    elif p.change_h6 > 150:
        total -= 8
        warnings.append(f"already {p.change_h6:+.0f}% in 6h: getting extended")

    return Score(total=max(0.0, min(100.0, total)), parts=parts, reasons=reasons, warnings=warnings)
