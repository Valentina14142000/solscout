"""DexScreener: free, no key. https://docs.dexscreener.com/api/reference"""
from __future__ import annotations

from ..http import request_json
from ..models import Pair, SourceError

BASE = "https://api.dexscreener.com"
CHUNK = 30  # the tokens endpoint accepts up to 30 addresses per call


def parse_candidates(payload) -> list[str]:
    """token-profiles / token-boosts endpoints return a list of {chainId, tokenAddress}."""
    out: list[str] = []
    if isinstance(payload, list):
        for it in payload:
            if isinstance(it, dict) and it.get("chainId") == "solana" and it.get("tokenAddress"):
                out.append(it["tokenAddress"])
    return out


def best_pairs(raw_pairs, wanted: set[str] | None = None) -> list[Pair]:
    """Keep the most liquid Solana pair for each mint.

    DexScreener's price is always the *base* token's. A pair where our token is the quote would
    carry another token's price, so only pairs whose base token is one we asked about are kept."""
    best: dict[str, Pair] = {}
    for d in raw_pairs or []:
        p = Pair.from_dexscreener(d)
        if p and (wanted is None or p.mint in wanted) and (p.mint not in best or p.liquidity_usd > best[p.mint].liquidity_usd):
            best[p.mint] = p
    return list(best.values())


class DexScreener:
    def __init__(self, timeout: float = 12.0):
        self.timeout = timeout

    def candidates(self) -> list[str]:
        mints: dict[str, None] = {}
        errors = 0
        for path in ("/token-profiles/latest/v1", "/token-boosts/latest/v1"):
            try:
                for m in parse_candidates(request_json(BASE + path, timeout=self.timeout)):
                    mints.setdefault(m, None)
            except SourceError:
                errors += 1
        if errors == 2:
            raise SourceError("api.dexscreener.com: candidate lists unavailable")
        return list(mints)

    def pairs(self, mints: list[str]) -> list[Pair]:
        raw: list = []
        for i in range(0, len(mints), CHUNK):
            chunk = ",".join(mints[i:i + CHUNK])
            try:
                data = request_json(f"{BASE}/tokens/v1/solana/{chunk}", timeout=self.timeout)
            except SourceError:
                # older endpoint shape: {"pairs": [...]}
                data = request_json(f"{BASE}/latest/dex/tokens/{chunk}", timeout=self.timeout)
            raw.extend(data.get("pairs", []) if isinstance(data, dict) else data)
        return best_pairs(raw, set(mints))
