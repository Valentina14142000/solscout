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


def snapshot_open(cfg: Config, store: Store, source, clock=time.time) -> int:
    """Record a price/liquidity point for every token still inside its watch window."""
    now = clock()
    open_ = store.open_observations(now, cfg.track_hours)
    if not open_:
        return 0
    try:
        current = {p.mint: p for p in source.pairs(sorted({o["mint"] for o in open_}))}
    except SourceError:
        return 0
    rows = [(o["id"], now, current[o["mint"]].price_usd, current[o["mint"]].liquidity_usd)
            for o in open_ if o["mint"] in current]
    store.add_ticks(rows)
    return len(rows)


def check_exits(cfg: Config, store: Store, source, notifier, clock=time.time) -> list[dict]:
    """Alert (once per kind) when a recent signal hits the take-profit level, the stop level, or its pool is drained.
    These are prompts for a human to act on; nothing is sold automatically."""
    now = clock()
    sigs = store.recent_signals(now - cfg.exit_watch_hours * 3600)
    if not sigs:
        return []
    try:
        current = {p.mint: p for p in source.pairs(sorted({s["mint"] for s in sigs}))}
    except SourceError:
        return []
    fired = []
    for s in sigs:
        p = current.get(s["mint"])
        if p is None:
            continue
        ret = (p.price_usd / s["entry_price"] - 1) * 100
        if p.liquidity_usd < s["entry_liquidity"] * cfg.exit_liquidity_drop:
            kind, why = "liquidity", f"liquidity fell to ${p.liquidity_usd:,.0f} (was ${s['entry_liquidity']:,.0f}). The pool may be being drained."
        elif ret <= -cfg.exit_stop_loss_pct:
            kind, why = "stop", f"price is {ret:+.0f}% since the signal (stop level -{cfg.exit_stop_loss_pct:.0f}%)."
        elif ret >= cfg.exit_take_profit_pct:
            kind, why = "target", f"price is {ret:+.0f}% since the signal (take-profit level +{cfg.exit_take_profit_pct:.0f}%)."
        else:
            continue
        if store.has_exit_alert(s["id"], kind):
            continue
        text = (f"EXIT ALERT  {s['symbol']}\n\n{why}\n"
                f"Chart: https://dexscreener.com/solana/{s['pair_address']}\n\n"
                "A prompt to review your position, not an instruction. Nothing has been sold for you.")
        notifier.send(text)
        store.add_exit_alert(s["id"], kind, now)
        fired.append({"signal_id": s["id"], "symbol": s["symbol"], "kind": kind})
    return fired
