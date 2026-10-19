"""Hard filters. A token that fails any of these never becomes a signal, whatever its score."""
from __future__ import annotations

from .config import Config
from .features import Features
from .models import Pair, Safety


Fail = tuple[str, str]  # (code, human-readable reason)


def gate_market(p: Pair, f: Features, cfg: Config) -> list[Fail]:
    """Cheap checks on market data alone. Returns the reasons it failed (empty list = passed)."""
    fails: list[Fail] = []
    if p.liquidity_usd < cfg.min_liquidity_usd:
        fails.append(("low_liquidity", f"liquidity ${p.liquidity_usd:,.0f} is below ${cfg.min_liquidity_usd:,.0f}"))
    if f.age_min is None:
        fails.append(("age_unknown", "pair age unknown"))
    else:
        if f.age_min < cfg.min_age_minutes:
            fails.append(("too_new", f"only {f.age_min:.0f} min old (minimum {cfg.min_age_minutes:.0f})"))
        if f.age_min > cfg.max_age_hours * 60:
            fails.append(("too_old", f"older than {cfg.max_age_hours:.0f}h, not early any more"))
    if f.txns_h1 < cfg.min_txns_h1:
        fails.append(("low_activity", f"only {f.txns_h1} trades in the last hour"))
    if p.sells_h1 == 0 and p.buys_h1 >= cfg.honeypot_buys:
        fails.append(("honeypot", f"{p.buys_h1} buys and no sells in the last hour: likely a honeypot"))
    return fails


def gate_onchain(s: Safety, cfg: Config) -> list[Fail]:
    fails: list[Fail] = []
    if not s.checked:
        if cfg.require_safety:
            fails.append(("no_onchain", f"on-chain check unavailable ({s.error or 'no data'})"))
        return fails
    if s.mint_authority:
        fails.append(("mint_authority", "mint authority is still active: supply can be inflated"))
    if s.freeze_authority:
        fails.append(("freeze_authority", "freeze authority is still active: wallets can be frozen"))
    if s.token_2022 and not cfg.allow_token_2022:
        fails.append(("token_2022", "Token-2022 mint: extensions such as transfer fees can trap sellers"))
    if s.top_holders_pct is not None and s.top_holders_pct > cfg.max_top_holders_pct:
        fails.append(("concentrated", f"holders ranked 2-10 own {s.top_holders_pct:.0f}% of supply"))
    return fails
