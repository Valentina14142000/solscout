from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import features, safety, scoring
from .config import Config
from .models import SourceError
from .notify import format_signal
from .store import Store


@dataclass
class ScanResult:
    candidates: int = 0
    evaluated: int = 0          # passed the cheap market filters
    dropped: dict[str, int] = field(default_factory=dict)   # reason -> count
    signals: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class Engine:
    def __init__(self, cfg: Config, store: Store, source, safety_checker, notifier, clock=time.time):
        self.cfg, self.store, self.source = cfg, store, source
        self.safety_checker, self.notifier, self.clock = safety_checker, notifier, clock

    def _drop(self, res: ScanResult, fails: list) -> None:
        code = fails[0][0]
        res.dropped[code] = res.dropped.get(code, 0) + 1

    def scan_once(self) -> ScanResult:
        cfg, now, res = self.cfg, self.clock(), ScanResult()

        try:
            mints = self.source.candidates()
        except SourceError as e:
            res.errors.append(str(e))
            return res
        for e in getattr(self.source, "last_errors", []):
            res.errors.append(e)

        cooling = self.store.recent_signal_mints(now - cfg.cooldown_hours * 3600)
        mints = [m for m in mints if m not in cooling][: cfg.max_candidates]
        res.candidates = len(mints)
        if not mints:
            return res

        try:
            pairs = self.source.pairs(mints)
        except SourceError as e:
            res.errors.append(str(e))
            return res

        # 1) cheap filters + preliminary score (no on-chain data yet)
        shortlist = []
        for p in pairs:
            f = features.compute(p, now)
            fails = safety.gate_market(p, f, cfg)
            if fails:
                self._drop(res, fails)
                continue
            res.evaluated += 1
            prelim = scoring.score(p, f, None, min_liquidity=cfg.min_liquidity_usd)
            # the on-chain check can add at most 10 points
            if prelim.total + 10 < cfg.signal_threshold:
                self.store.add_evaluation(p.mint, p.symbol, now, prelim.total, p.price_usd, False, {"stage": "score"})
                continue
            shortlist.append((prelim.total, p, f))

        # 2) on-chain checks, best candidates first, within the RPC budget
        shortlist.sort(key=lambda x: -x[0])
        for _, p, f in shortlist[: cfg.max_safety_checks]:
            s = self.safety_checker.check(p.mint)
            fails = safety.gate_onchain(s, cfg)
            if fails:
                self._drop(res, fails)
                self.store.add_evaluation(p.mint, p.symbol, now, None, p.price_usd, False, {"stage": "onchain", "fails": [m for _, m in fails]})
                continue
            sc = scoring.score(p, f, s, min_liquidity=cfg.min_liquidity_usd)
            is_signal = sc.total >= cfg.signal_threshold
            self.store.add_evaluation(p.mint, p.symbol, now, sc.total, p.price_usd, is_signal, {"stage": "final"})
            if not is_signal:
                continue
            if self.store.count_signals_since(now - 86400) >= cfg.max_signals_per_day:
                res.errors.append("daily signal cap reached")
                break
            sid = self.store.add_signal(
                mint=p.mint, symbol=p.symbol, pair_address=p.pair_address, score=sc.total,
                entry_price=p.price_usd, entry_liquidity=p.liquidity_usd,
                reasons=sc.reasons, warnings=sc.warnings, created_at=now)
            sig = {"id": sid, "mint": p.mint, "symbol": p.symbol, "pair_address": p.pair_address,
                   "score": sc.total, "entry_price": p.price_usd, "reasons": sc.reasons, "warnings": sc.warnings}
            if self.notifier.send(format_signal(sig)):
                self.store.mark_notified(sid)
            res.signals.append(sig)

        self.store.prune_evaluations(now - 14 * 86400)
        return res
