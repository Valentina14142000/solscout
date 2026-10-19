"""Data sources. Each exposes the same two methods, so the engine never cares where data comes from.

    candidates() -> list[str]            token mints worth looking at
    pairs(mints) -> list[Pair]           best (most liquid) pair per mint
"""
from __future__ import annotations

from ..models import Pair, SourceError
from .dexscreener import DexScreener
from .geckoterminal import GeckoTerminal


class Combined:
    """Candidates from several discovery sources (any one may fail); pair data from DexScreener."""

    def __init__(self, discovery: list, market: DexScreener):
        self.discovery = discovery
        self.market = market
        self.last_errors: list[str] = []

    def candidates(self) -> list[str]:
        seen: dict[str, None] = {}
        self.last_errors = []
        for src in self.discovery:
            try:
                for m in src.candidates():
                    seen.setdefault(m, None)
            except SourceError as e:
                self.last_errors.append(str(e))
        if not seen and self.last_errors:
            raise SourceError("; ".join(self.last_errors))
        return list(seen)

    def pairs(self, mints: list[str]) -> list[Pair]:
        return self.market.pairs(mints)


def default_source(cfg) -> Combined:
    market = DexScreener(timeout=cfg.http_timeout_s)
    return Combined([GeckoTerminal(timeout=cfg.http_timeout_s), market], market)
