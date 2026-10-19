"""On-chain safety facts straight from a Solana RPC node: mint/freeze authority and holder concentration."""
from __future__ import annotations

import time

from ..http import request_json
from ..models import Safety, SourceError

TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
CACHE_S = 600


def parse_safety(mint_info: dict, largest: list[dict], supply_raw: int) -> Safety:
    """Pure function so it can be tested without a network.

    mint_info  = getAccountInfo(jsonParsed).value
    largest    = getTokenLargestAccounts().value  (sorted, biggest first)
    supply_raw = getTokenSupply().value.amount
    """
    parsed = ((mint_info or {}).get("data") or {}).get("parsed") or {}
    if parsed.get("type") != "mint":
        return Safety(checked=False, error="not a token mint")
    info = parsed.get("info") or {}
    amounts = [int(a.get("amount", 0)) for a in largest or []]
    largest_pct = top_pct = None
    if supply_raw > 0 and amounts:
        largest_pct = 100.0 * amounts[0] / supply_raw
        top_pct = 100.0 * sum(amounts[1:10]) / supply_raw
    return Safety(
        checked=True,
        mint_authority=info.get("mintAuthority"),
        freeze_authority=info.get("freezeAuthority"),
        token_2022=(mint_info or {}).get("owner") == TOKEN_2022,
        largest_pct=largest_pct,
        top_holders_pct=top_pct,
    )


class RpcSafety:
    def __init__(self, rpc_url: str, timeout: float = 12.0, clock=time.time):
        self.url = rpc_url
        self.timeout = timeout
        self.clock = clock
        self._cache: dict[str, tuple[float, Safety]] = {}

    def _rpc(self, method: str, params: list):
        out = request_json(self.url, body={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                           timeout=self.timeout)
        if not isinstance(out, dict) or "result" not in out:
            raise SourceError(f"rpc {method}: {(out or {}).get('error', 'no result')}")
        return out["result"]

    def check(self, mint: str) -> Safety:
        hit = self._cache.get(mint)
        if hit and self.clock() - hit[0] < CACHE_S:
            return hit[1]
        try:
            acct = self._rpc("getAccountInfo", [mint, {"encoding": "jsonParsed"}])["value"]
            largest = self._rpc("getTokenLargestAccounts", [mint])["value"]
            supply = int(self._rpc("getTokenSupply", [mint])["value"]["amount"])
            result = parse_safety(acct, largest, supply)
        except (SourceError, KeyError, TypeError, ValueError) as e:
            return Safety(checked=False, error=str(e)[:120])  # not cached: retry next scan
        self._cache[mint] = (self.clock(), result)
        return result
