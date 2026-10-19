"""RugCheck: free, no key. Used as a fallback safety source when the Solana RPC node is rate limited."""
from __future__ import annotations

import time

from ..http import request_json
from ..models import Safety, SourceError, num
from .rpc_safety import TOKEN_2022

BASE = "https://api.rugcheck.xyz/v1"
CACHE_S = 600


def parse_report(rep) -> Safety:
    """Defensive parser: the payload is third-party and may change shape."""
    if not isinstance(rep, dict) or ("token" not in rep and "risks" not in rep and "mint" not in rep):
        return Safety(error="unexpected RugCheck response")
    tok = rep.get("token") if isinstance(rep.get("token"), dict) else {}
    pcts = [num(h.get("pct")) for h in (rep.get("topHolders") or []) if isinstance(h, dict)]
    if pcts and sum(pcts) <= 1.0001:  # fractions rather than percentages
        pcts = [p * 100 for p in pcts]
    flags = tuple(str(r["name"]) for r in (rep.get("risks") or [])
                  if isinstance(r, dict) and r.get("level") == "danger" and r.get("name"))
    return Safety(
        checked=True,
        mint_authority=tok.get("mintAuthority") or rep.get("mintAuthority") or None,
        freeze_authority=tok.get("freezeAuthority") or rep.get("freezeAuthority") or None,
        token_2022=rep.get("tokenProgram") == TOKEN_2022,
        largest_pct=pcts[0] if pcts else None,
        top_holders_pct=sum(pcts[1:10]) if len(pcts) > 1 else None,
        rugged=bool(rep.get("rugged")),
        flags=flags,
    )


class RugCheckSafety:
    def __init__(self, timeout: float = 12.0, clock=time.time):
        self.timeout, self.clock = timeout, clock
        self._cache: dict[str, tuple[float, Safety]] = {}
        self._blocked_until = 0.0

    def check(self, mint: str) -> Safety:
        hit = self._cache.get(mint)
        if hit and self.clock() - hit[0] < CACHE_S:
            return hit[1]
        if self.clock() < self._blocked_until:
            return Safety(error="RugCheck rate limited", retry_later=True)
        try:
            result = parse_report(request_json(f"{BASE}/tokens/{mint}/report", timeout=self.timeout, retries=1))
        except SourceError as e:
            limited = "429" in str(e)
            if limited:
                self._blocked_until = self.clock() + 60
            return Safety(error=("RugCheck rate limited" if limited else str(e)[:120]), retry_later=limited)
        if result.checked:
            self._cache[mint] = (self.clock(), result)
        return result
