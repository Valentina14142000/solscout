"""Synthetic market for testing the analysis code: score may or may not predict returns."""
import math
import random

TICK = 300


def make_obs(n=600, edge=0.0, rug_p=0.02, seed=7, noise=0.015, start=1_800_000_000):
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        score = rnd.uniform(30, 90)
        drift = edge * (score - 60) / 30
        price, liq = 0.001, 50_000.0
        ts0 = start + i * 1200
        rug_at = rnd.randint(10, 280) if rnd.random() < rug_p else None
        ticks = []
        for k in range(25 * 3600 // TICK + 1):
            if k:
                price *= math.exp(drift + noise * rnd.gauss(0, 1))
            if rug_at is not None and k >= rug_at:
                liq = 500.0
            ticks.append((ts0 + k * TICK, price, liq))
        out.append({"id": i, "mint": f"M{i}", "symbol": f"T{i}", "ts": ts0, "price": 0.001, "liquidity": 50_000.0,
                    "score": score, "stage": "unchecked",
                    "features": {"accel": rnd.uniform(1, 8), "buy_ratio": rnd.uniform(.4, .8),
                                 "change_h1": rnd.uniform(0, 100), "age_min": rnd.uniform(15, 600)},
                    "ticks": ticks})
    return out


def path(prices, liqs=None, start=1_800_000_000, step=TICK):
    liqs = liqs or [50_000.0] * len(prices)
    ticks = [(start + i * step, p, l) for i, (p, l) in enumerate(zip(prices, liqs))]
    return {"id": 1, "mint": "M", "symbol": "T", "ts": start, "price": prices[0], "liquidity": liqs[0],
            "score": 70, "stage": "signal", "features": {}, "ticks": ticks}
