from __future__ import annotations

from dataclasses import dataclass


def num(x, default: float = 0.0) -> float:
    try:
        v = float(x)
        return v if v == v else default  # NaN guard
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Pair:
    """One trading pair, normalised from whichever source produced it."""
    mint: str
    symbol: str
    name: str
    pair_address: str
    dex: str
    price_usd: float
    liquidity_usd: float
    volume_m5: float
    volume_h1: float
    volume_h6: float
    volume_h24: float
    buys_m5: int
    sells_m5: int
    buys_h1: int
    sells_h1: int
    buys_h24: int
    sells_h24: int
    change_m5: float
    change_h1: float
    change_h6: float
    change_h24: float
    fdv: float
    created_at: int | None  # epoch seconds
    url: str = ""

    @classmethod
    def from_dexscreener(cls, d: dict) -> "Pair | None":
        """Parse one DexScreener pair object. Returns None for non-Solana or unusable entries."""
        if not isinstance(d, dict) or d.get("chainId") != "solana":
            return None
        base = d.get("baseToken") or {}
        mint = base.get("address")
        price = num(d.get("priceUsd"))
        if not mint or price <= 0:
            return None
        tx = d.get("txns") or {}
        vol = d.get("volume") or {}
        chg = d.get("priceChange") or {}

        def t(win: str, side: str) -> int:
            return int(num((tx.get(win) or {}).get(side)))

        created = d.get("pairCreatedAt")
        return cls(
            mint=mint,
            symbol=str(base.get("symbol") or "?")[:24],
            name=str(base.get("name") or "")[:60],
            pair_address=str(d.get("pairAddress") or ""),
            dex=str(d.get("dexId") or ""),
            price_usd=price,
            liquidity_usd=num((d.get("liquidity") or {}).get("usd")),
            volume_m5=num(vol.get("m5")), volume_h1=num(vol.get("h1")),
            volume_h6=num(vol.get("h6")), volume_h24=num(vol.get("h24")),
            buys_m5=t("m5", "buys"), sells_m5=t("m5", "sells"),
            buys_h1=t("h1", "buys"), sells_h1=t("h1", "sells"),
            buys_h24=t("h24", "buys"), sells_h24=t("h24", "sells"),
            change_m5=num(chg.get("m5")), change_h1=num(chg.get("h1")),
            change_h6=num(chg.get("h6")), change_h24=num(chg.get("h24")),
            fdv=num(d.get("fdv")),
            created_at=int(num(created) / 1000) if created else None,
            url=str(d.get("url") or ""),
        )


@dataclass(frozen=True)
class Safety:
    """On-chain facts about a token. `checked=False` means we could not verify."""
    checked: bool = False
    mint_authority: str | None = None
    freeze_authority: str | None = None
    token_2022: bool = False
    largest_pct: float | None = None       # biggest holder (often the liquidity pool)
    top_holders_pct: float | None = None   # holders ranked 2-10
    rugged: bool = False                   # flagged as rugged by a third-party checker
    flags: tuple[str, ...] = ()            # extra danger flags (informational)
    retry_later: bool = False              # transient failure (rate limit); do not treat as a verdict
    error: str = ""


class SourceError(Exception):
    """A data source failed (network, rate limit, bad payload)."""
