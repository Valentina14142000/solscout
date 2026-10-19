"""GeckoTerminal: free, no key. Used only for discovery (newly created and trending pools)."""
from __future__ import annotations

from ..http import request_json
from ..models import SourceError

BASE = "https://api.geckoterminal.com/api/v2/networks/solana"


def parse_pools(payload) -> list[str]:
    """JSON:API payload; the base token id looks like 'solana_<mint>'."""
    out: list[str] = []
    for item in (payload or {}).get("data", []) if isinstance(payload, dict) else []:
        try:
            tid = item["relationships"]["base_token"]["data"]["id"]
        except (KeyError, TypeError):
            continue
        if isinstance(tid, str) and tid.startswith("solana_"):
            out.append(tid[len("solana_"):])
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
