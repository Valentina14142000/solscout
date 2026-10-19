"""Recorded-style sample market used by the tests and by `solscout demo` (no network needed)."""
from __future__ import annotations

from dataclasses import replace

from .models import Pair, Safety


def _pair(now: float, mint: str, symbol: str, *, age_h: float, price=0.0042, liq=80_000, v5=5_000, v1=60_000,
          v6=110_000, v24=120_000, b1=520, s1=230, c5=2.0, c1=38.0, c6=60.0, c24=90.0) -> Pair:
    return Pair(mint=mint, symbol=symbol, name=symbol.title(), pair_address="pair" + mint[:6], dex="raydium",
                price_usd=price, liquidity_usd=liq, volume_m5=v5, volume_h1=v1, volume_h6=v6, volume_h24=v24,
                buys_m5=b1 // 12, sells_m5=s1 // 12, buys_h1=b1, sells_h1=s1, buys_h24=b1 * 4, sells_h24=s1 * 4,
                change_m5=c5, change_h1=c1, change_h6=c6, change_h24=c24, fdv=liq * 12,
                created_at=int(now - age_h * 3600), url="")


def sample_pairs(now: float) -> list[Pair]:
    return [
        _pair(now, "ORBITmint111111111111111111111111111111111", "ORBIT", age_h=5),
        _pair(now, "MOONDOGmint11111111111111111111111111111111", "MOONDOG", age_h=0.7, liq=30_000, v5=40_000,
              v1=200_000, v6=200_000, v24=200_000, b1=900, s1=0, c5=60, c1=420, c6=420, c24=420),
        _pair(now, "FREEZYmint111111111111111111111111111111111", "FREEZY", age_h=6),
        _pair(now, "PUMPEDmint111111111111111111111111111111111", "PUMPED", age_h=30, v1=70_000, v6=400_000,
              v24=520_000, c1=25, c6=380, c24=700),
        _pair(now, "THINmint11111111111111111111111111111111111", "THIN", age_h=4, liq=4_000),
        _pair(now, "QUIETmint1111111111111111111111111111111111", "QUIET", age_h=9, v5=300, v1=4_000, v6=30_000,
              v24=90_000, b1=20, s1=18, c1=0.5, c6=2, c24=-3),
    ]


class FixtureSource:
    """Serves the sample market. `move(mint, factor)` changes a price so outcomes can be simulated."""

    def __init__(self, now: float):
        self._pairs = {p.mint: p for p in sample_pairs(now)}
        self.last_errors: list[str] = []

    def candidates(self) -> list[str]:
        return list(self._pairs)

    def pairs(self, mints: list[str]) -> list[Pair]:
        return [self._pairs[m] for m in mints if m in self._pairs]

    def move(self, symbol: str, price_factor: float | None = None, liquidity_factor: float | None = None) -> None:
        for m, p in list(self._pairs.items()):
            if p.symbol == symbol:
                self._pairs[m] = replace(
                    p,
                    price_usd=p.price_usd * (price_factor if price_factor is not None else 1),
                    liquidity_usd=p.liquidity_usd * (liquidity_factor if liquidity_factor is not None else 1))


class FixtureSafety:
    def check(self, mint: str) -> Safety:
        if mint.startswith("FREEZY"):
            return Safety(checked=True, freeze_authority="SomeWallet111", top_holders_pct=12.0, largest_pct=30.0)
        if mint.startswith("MOONDOG"):
            return Safety(checked=True, mint_authority="SomeWallet111", top_holders_pct=61.0, largest_pct=40.0)
        return Safety(checked=True, top_holders_pct=14.0, largest_pct=38.0)


def seed_demo(store, now: float):
    """Fill a store with plausible history for `serve --demo`, and return a ScanResult for the status panel."""
    from .engine import Engine, ScanResult
    from .config import Config
    from .notify import ConsoleNotifier

    hist = [  # (symbol, hours ago, score, returns at 1h / 6h / 24h or None, rugged-at)
        ("KITE", 40, 81, (14.0, 38.0, 22.0), None), ("MANTA", 33, 74, (-3.0, -12.0, -31.0), None),
        ("NOVA", 27, 77, (9.0, 61.0, 140.0), None), ("PIXL", 20, 72, (-100.0, None, None), 1),
        ("ECHO", 12, 79, (6.0, -8.0, None), None), ("DUNE", 5, 83, (18.0, None, None), None),
        ("FERN", 0.5, 76, (None, None, None), None),
    ]
    for i, (sym, ago_h, score, rets, rug) in enumerate(hist):
        created = now - ago_h * 3600
        sid = store.add_signal(
            mint=f"{sym}mint{i:0<38}", symbol=sym, pair_address=f"pair{sym}{i}", score=score,
            entry_price=0.0005 * (i + 1), entry_liquidity=60_000 + i * 9_000,
            reasons=[f"price +{25 + i * 4}% in 1h", f"volume running {3 + i % 3}.2x its earlier hourly average",
                     "66% of last-hour trades are buys", f"liquidity ${60 + i * 9},000", "mint and freeze authority revoked"],
            warnings=["largest holder owns 54% (may be the liquidity pool)"] if i % 3 == 0 else [], created_at=created)
        for h, r in zip((1, 6, 24), rets):
            if r is not None:
                store.add_outcome(sid, h, 0.001, r, bool(rug) and r == -100.0, created + h * 3600)
    cfg = Config()
    res = ScanResult(candidates=84, evaluated=11, dropped={"honeypot": 3, "low_liquidity": 52, "freeze_authority": 2})
    res.near = [{"symbol": "ORCA", "mint": "x", "score": 66.0, "why": "score 66 is below 70"},
                {"symbol": "LUMA", "mint": "x", "score": 61.5, "why": "score too low (needs 70)"},
                {"symbol": "BOLT", "mint": "x", "score": 58.0, "why": "score too low (needs 70)"}]
    res.rejected = [{"symbol": "MOONDOG", "mint": "x", "code": "honeypot", "why": "900 buys and no sells in the last hour: likely a honeypot"},
                    {"symbol": "FREEZY", "mint": "x", "code": "freeze_authority", "why": "freeze authority is still active: wallets can be frozen"},
                    {"symbol": "THIN", "mint": "x", "code": "low_liquidity", "why": "liquidity $4,000 is below $15,000"}]
    res.errors = ["Demo data: nothing here is a real market"]
    return res
