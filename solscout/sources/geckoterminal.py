"""GeckoTerminal: free, no key. Used only for discovery (newly created and trending pools)."""
from __future__ import annotations

from ..http import request_json
from ..models import SourceError

BASE = "https://api.geckoterminal.com/api/v2/networks/solana"


# Pools are often listed as SOL/TOKEN, so the "base" token can be the quote currency itself.
QUOTE_MINTS = {
    "So11111111111111111111111111111111111111112",   # wrapped SOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}


def _mint(item, rel: str) -> str | None:
    try:
        tid = item["relationships"][rel]["data"]["id"]
    except (KeyError, TypeError):
        return None
    return tid[len("solana_"):] if isinstance(tid, str) and tid.startswith("solana_") else None


def parse_pools(payload) -> list[str]:
    """JSON:API payload; token ids look like 'solana_<mint>'. Returns the non-quote token of each pool."""
    out: list[str] = []
    for item in (payload or {}).get("data", []) if isinstance(payload, dict) else []:
        base, quote = _mint(item, "base_token"), _mint(item, "quote_token")
        pick = base if base and base not in QUOTE_MINTS else quote if quote and quote not in QUOTE_MINTS else None
        if pick:
            out.append(pick)
    return out


class GeckoTerminal:
    def __init__(self, timeout: float = 12.0):
        self.timeout = timeout

    def candidates(self) -> list[str]:
        mints: dict[str, None] = {}
        errors = 0
        for path in ("/new_pools", "/trending_pools"):
            try:
                for m in parse_pools(request_json(BASE + path, timeout=self.timeout)):
                    mints.setdefault(m, None)
            except SourceError:
                errors += 1
        if errors == 2:
            raise SourceError("api.geckoterminal.com: pool lists unavailable")
        return list(mints)
