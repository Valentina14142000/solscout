"""Paper-trade tracker: revisit every signal after 1h / 6h / 24h and record what would have happened."""
from __future__ import annotations

import time

from .config import Config
from .models import SourceError
from .store import Store

GRACE_S = 12 * 3600  # a pair missing from the API this long after its horizon is treated as gone (rug)


def resolve(cfg: Config, store: Store, source, clock=time.time) -> int:
    """Record outcomes that are due. Returns how many were recorded."""
    now = clock()
    pending = store.pending_outcomes(now, cfg.horizons_h)
    if not pending:
        return 0
    try:
        current = {p.mint: p for p in source.pairs(sorted({s["mint"] for s, _ in pending}))}
    except SourceError:
        return 0

    done = 0
    for sig, horizon in pending:
        p = current.get(sig["mint"])
        due_at = sig["created_at"] + horizon * 3600
        if p is None:
            if now - due_at > GRACE_S:  # long gone: count as a total loss
                store.add_outcome(sig["id"], horizon, None, -100.0, True, now)
                done += 1
            continue
        rugged = p.liquidity_usd < sig["entry_liquidity"] * cfg.rug_liquidity_drop
        gross = (p.price_usd / sig["entry_price"] - 1) * 100
        ret = -100.0 if rugged else gross - cfg.paper_cost_pct
        store.add_outcome(sig["id"], horizon, p.price_usd, ret, rugged, now)
        done += 1
    return done
