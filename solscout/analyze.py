"""Offline strategy analysis over the recorded observations.

Every token that passed the market filters is watched for 24h (price + liquidity every few minutes), whether or
not it became a signal. That lets us ask, with data instead of opinions:

  * does a higher score actually predict better returns than picking tokens at random?
  * which exit rule (fixed hold, take-profit / stop-loss, trailing stop) works best?
  * how much does being a few minutes late cost?

Results are net of trading costs; a liquidity collapse counts as a total loss.
"""
from __future__ import annotations

import random
import statistics
from dataclasses import dataclass

from .config import Config

TICK_TOL = 1800   # a tick up to 30 minutes after the target time still counts as "at" the target
MIN_N = 30        # fewer completed results than this means nothing
RUG_TICKS = 2     # liquidity must stay collapsed for this many consecutive readings (one API glitch is not a rug)
EXCLUDED_STAGES = {"onchain_fail"}   # tokens the bot would have rejected on safety grounds


# ---------------------------------------------------------------- per-token outcome functions
def _entry(obs: dict, delay_s: float):
    t0 = obs["ts"] + delay_s
    for i, (ts, price, liq) in enumerate(obs["ticks"]):
        if ts >= t0:
            return i if ts - t0 <= TICK_TOL and price > 0 else None
    return None


def horizon_return(obs: dict, hours: float, *, delay_s: float = 0, cost: float = 3.0, rug_drop: float = 0.10):
    """Return (%) from entry to `hours` later. None if that moment has not been recorded yet."""
    i = _entry(obs, delay_s)
    if i is None:
        return None
    ts0, p0, l0 = obs["ticks"][i]
    target = ts0 + hours * 3600
    low = 0
    for ts, price, liq in obs["ticks"][i + 1:]:
        low = low + 1 if liq < l0 * rug_drop else 0
        if low >= RUG_TICKS:
            return -100.0
        if low:          # liquidity looks collapsed: this tick's price cannot be trusted, wait for the next one
            continue
        if ts >= target:
            return None if ts - target > TICK_TOL else (price / p0 - 1) * 100 - cost
    return None


def exit_return(obs: dict, *, tp=None, sl=None, trail=None, max_h: float = 24, delay_s: float = 0,
                cost: float = 3.0, rug_drop: float = 0.10):
    """Simulate an exit rule on the recorded path. tp/sl/trail are fractions (0.5 = 50%).

    Conservative on ambiguity: a stop is checked before a target within the same tick, and a stop that
    is gapped through fills at the tick's price, not the stop level."""
    i = _entry(obs, delay_s)
    if i is None:
        return None
    ts0, p0, l0 = obs["ticks"][i]
    peak, target = p0, ts0 + max_h * 3600
    low = 0
    for ts, price, liq in obs["ticks"][i + 1:]:
        low = low + 1 if liq < l0 * rug_drop else 0
        if low >= RUG_TICKS:
            return -100.0
        if low:          # liquidity looks collapsed: this tick's price cannot be trusted, wait for the next one
            continue
        r = price / p0 - 1
        peak = max(peak, price)
        if sl is not None and r <= -sl:
            return r * 100 - cost
        if tp is not None and r >= tp:
            return tp * 100 - cost
        if trail is not None and price <= peak * (1 - trail):
            return r * 100 - cost
        if ts >= target:
            return r * 100 - cost
    return None


# ---------------------------------------------------------------- statistics
@dataclass
class Stat:
    n: int
    mean: float | None = None
    median: float | None = None
    win: float | None = None
    lo: float | None = None
    hi: float | None = None
    rugs: float | None = None


def summarize(rets: list[float], seed: int = 1, boot: int = 1000) -> Stat:
    n = len(rets)
    if not n:
        return Stat(0)
    mean = sum(rets) / n
    lo = hi = None
    if n >= 5:  # bootstrap 95% interval for the mean
        rnd = random.Random(seed)
        means = sorted(sum(rnd.choices(rets, k=n)) / n for _ in range(boot))
        lo, hi = means[int(boot * 0.025)], means[int(boot * 0.975) - 1]
    return Stat(n, mean, statistics.median(rets), sum(1 for r in rets if r > 0) / n, lo, hi,
                sum(1 for r in rets if r <= -99.99) / n)


def _row(label: str, st: Stat) -> str:
    if not st.n:
        return f"  {label:<26}{0:>5}   no completed results"
    ci = f"[{st.lo:+.0f}, {st.hi:+.0f}]" if st.lo is not None else ""
    return (f"  {label:<26}{st.n:>5} {st.mean:>+8.1f}% {st.median:>+8.1f}% {st.win * 100:>5.0f}% "
            f"{st.rugs * 100:>5.0f}%  {ci}")


HEADER = f"  {'':<26}{'n':>5} {'mean':>9} {'median':>9} {'win':>6} {'rugs':>6}  95% interval for the mean"


def _rets(obs_list, fn):
    return [r for r in (fn(o) for o in obs_list) if r is not None]


def _terciles(values: list[tuple[float, dict]]):
    values = sorted(values, key=lambda x: x[0])
    k = len(values) // 3
    return [values[:k], values[k:2 * k], values[2 * k:]] if k else [[], [], []]


# ---------------------------------------------------------------- report
def analyze(observations: list[dict], cfg: Config, *, horizon: int = 6, delay_min: float = 0,
            holdout: float = 0.3, min_score: float | None = None) -> str:
    out: list[str] = []
    w = out.append
    delay_s = delay_min * 60
    min_score = cfg.signal_threshold - 10 if min_score is None else min_score  # prelim score excludes safety points
    kw = dict(delay_s=delay_s, cost=cfg.paper_cost_pct, rug_drop=cfg.rug_liquidity_drop)

    universe = [o for o in observations if o["stage"] not in EXCLUDED_STAGES]
    w("STRATEGY ANALYSIS")
    w("=" * 70)
    if not observations:
        w("No observations yet. Leave `solscout serve` running; every token that passes the filters is watched for 24h.")
        return "\n".join(out)

    span_h = (observations[-1]["ts"] - observations[0]["ts"]) / 3600
    stages: dict[str, int] = {}
    for o in observations:
        stages[o["stage"]] = stages.get(o["stage"], 0) + 1
    w(f"{len(observations)} tokens watched over {span_h:.0f}h. Stages: " + ", ".join(f"{k} {v}" for k, v in sorted(stages.items())))
    w(f"Costs {cfg.paper_cost_pct:.0f}% per round trip, entry delay {delay_min:g} min, results at {horizon}h unless stated.")
    for h in cfg.horizons_h:
        done = len(_rets(universe, lambda o, h=h: horizon_return(o, h, **kw)))
        w(f"  completed at {h}h: {done}")
    w("")

    hfn = lambda o: horizon_return(o, horizon, **kw)  # noqa: E731
    base = summarize(_rets(universe, hfn))

    # 1) baseline + score buckets
    w(f"1. WHAT DOES THE SCORE PREDICT?  (return after {horizon}h)")
    w(HEADER)
    w(_row("everything watched (baseline)", base))
    edges = [0, 40, 50, 60, 70, 80, 101]
    for a, b in zip(edges, edges[1:]):
        grp = [o for o in universe if a <= o["score"] < b]
        w(_row(f"score {a}-{min(b - 1, 100)}", summarize(_rets(grp, hfn))))
    w("  If higher buckets do not beat the baseline row, the score has no edge.")
    w("")

    # 2) threshold sweep with held-out data
    cut = int(len(universe) * (1 - holdout))
    train, test = universe[:cut], universe[cut:]
    w(f"2. THRESHOLD SWEEP  (tuning = first {100 - holdout * 100:.0f}% of the data, held-out = the most recent {holdout * 100:.0f}%)")
    w(HEADER)
    for t in (40, 50, 55, 60, 65, 70, 75, 80):
        w(_row(f"score >= {t} tuning", summarize(_rets([o for o in train if o["score"] >= t], hfn))))
        w(_row(f"score >= {t} held-out", summarize(_rets([o for o in test if o["score"] >= t], hfn))))
    w(f"  {len(train)} tokens in tuning, {len(test)} held-out. Pick a threshold on the tuning rows only, then judge it on the held-out rows.")
    w("")

    # 3) which features matter
    w(f"3. WHICH INPUTS MATTER?  (mean return after {horizon}h, lowest / middle / highest third)")
    feats = {
        "volume acceleration": lambda o: o["features"].get("accel"),
        "buy ratio": lambda o: o["features"].get("buy_ratio"),
        "price change 1h": lambda o: o["features"].get("change_h1"),
        "liquidity": lambda o: o["liquidity"],
        "age (minutes)": lambda o: o["features"].get("age_min"),
    }
    for name, get in feats.items():
        pairs = [(get(o), o) for o in universe if get(o) is not None and hfn(o) is not None]
        thirds = _terciles(pairs)
        cells = []
        for grp in thirds:
            r = [hfn(o) for _, o in grp]
            cells.append(f"{sum(r) / len(r):>+7.1f}% (n={len(r)})" if r else "     n/a")
        w(f"  {name:<22}" + "   ".join(cells))
    w("")

    # 4) exit rules
    sel = [o for o in universe if o["score"] >= min_score]
    w(f"4. EXIT RULES  (tokens with score >= {min_score:g}; the 24h window must be complete)")
    w(HEADER)
    rules = [
        ("hold 1h", lambda o: horizon_return(o, 1, **kw)),
        ("hold 6h", lambda o: horizon_return(o, 6, **kw)),
        ("hold 24h", lambda o: horizon_return(o, 24, **kw)),
        ("take 30% / stop 15%", lambda o: exit_return(o, tp=.30, sl=.15, **kw)),
        ("take 50% / stop 20%", lambda o: exit_return(o, tp=.50, sl=.20, **kw)),
        ("take 100% / stop 30%", lambda o: exit_return(o, tp=1.0, sl=.30, **kw)),
        ("trailing stop 20%", lambda o: exit_return(o, trail=.20, **kw)),
        ("trailing stop 30%", lambda o: exit_return(o, trail=.30, **kw)),
    ]
    for label, fn in rules:
        w(_row(label, summarize(_rets(sel, fn))))
    w("  Prices are sampled every few minutes, so real stops can fill worse than shown on fast moves.")
    w("")

    # 5) lateness + cost sensitivity
    w(f"5. HOW MUCH DOES BEING LATE COST?  (score >= {min_score:g}, hold {horizon}h)")
    w(HEADER)
    for d in (0, 5, 10, 15):
        k2 = dict(kw, delay_s=d * 60)
        w(_row(f"enter {d} min after signal", summarize(_rets(sel, lambda o, k2=k2: horizon_return(o, horizon, **k2)))))
    w("")
    w(f"6. COST SENSITIVITY  (score >= {min_score:g}, hold {horizon}h, {delay_min:g} min delay)")
    w(HEADER)
    for c in (1, 3, 5, 8):
        k3 = dict(kw, cost=c)
        w(_row(f"cost {c}% per round trip", summarize(_rets(sel, lambda o, k3=k3: horizon_return(o, horizon, **k3)))))
    w("")

    # verdict
    held = summarize(_rets([o for o in test if o["score"] >= min_score], hfn))
    w("VERDICT")
    w("-" * 70)
    if held.n < MIN_N:
        w(f"  NOT ENOUGH DATA. Only {held.n} completed held-out results for score >= {min_score:g}; at least {MIN_N} are needed, and")
        w("  several hundred before trusting small differences. Keep the bot running.")
    elif held.lo is not None and held.lo > 0 and held.mean > (base.mean or 0):
        w(f"  PROMISING, NOT PROVEN. Held-out mean {held.mean:+.1f}% (interval {held.lo:+.0f} to {held.hi:+.0f}) beats the baseline {base.mean:+.1f}%.")
        w("  Confirm on a fresh batch of data before risking money, and expect real fills to be worse.")
    elif held.hi is not None and held.hi < 0:
        w(f"  LOSES MONEY. Held-out mean {held.mean:+.1f}% and the whole interval is below zero. Do not trade this.")
    else:
        w(f"  NO PROVEN EDGE. Held-out mean {held.mean:+.1f}% (interval {held.lo:+.0f} to {held.hi:+.0f}) cannot be told apart from zero or from the baseline.")
    w("  Testing many thresholds and exit rules finds winners by luck. Only the held-out rows count, and only once.")
    return "\n".join(out)
