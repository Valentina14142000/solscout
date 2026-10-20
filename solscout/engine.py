from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import features, safety, scoring
from .config import Config
from .models import SourceError
from .notify import format_signal
from .store import Store

MAX_UNVERIFIED_STREAK = 2  # stop on-chain checks for this scan after this many consecutive failures


@dataclass
class ScanResult:
    candidates: int = 0
    pairs: int = 0              # candidates for which market data was found
    evaluated: int = 0          # passed the cheap market filters
    dropped: dict[str, int] = field(default_factory=dict)   # reason code -> count
    signals: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    near: list[dict] = field(default_factory=list)          # best tokens that did not qualify, with why
    rejected: list[dict] = field(default_factory=list)      # sample of hard-filter rejections


class Engine:
    def __init__(self, cfg: Config, store: Store, source, safety_checker, notifier, clock=time.time):
        self.cfg, self.store, self.source = cfg, store, source
        self.safety_checker, self.notifier, self.clock = safety_checker, notifier, clock

    def _observe(self, p, f, prelim_total: float, stage: str, now: float) -> int:
        feats = {"age_min": f.age_min, "accel": f.accel, "accel_basis": f.accel_basis, "buy_ratio": f.buy_ratio,
                 "txns_h1": f.txns_h1, "turnover": f.turnover, "change_m5": p.change_m5, "change_h1": p.change_h1,
                 "change_h6": p.change_h6, "change_h24": p.change_h24, "volume_h1": p.volume_h1,
                 "fdv": p.fdv, "dex": p.dex}
        return self.store.add_observation(mint=p.mint, symbol=p.symbol, pair_address=p.pair_address, ts=now,
                                          price=p.price_usd, liquidity=p.liquidity_usd, score=prelim_total,
                                          stage=stage, features=feats)

    def _stage(self, obs_id, stage: str, extra: dict | None = None) -> None:
        if obs_id is not None:
            self.store.update_observation(obs_id, stage, extra)

    @staticmethod
    def _drop(res: ScanResult, p, fails: list) -> None:
        code = fails[0][0]
        res.dropped[code] = res.dropped.get(code, 0) + 1
        if len(res.rejected) < 12:
            res.rejected.append({"symbol": p.symbol, "mint": p.mint, "code": code, "why": fails[0][1]})

    def scan_once(self) -> ScanResult:
        cfg, now, res = self.cfg, self.clock(), ScanResult()

        try:
            mints = self.source.candidates()
        except SourceError as e:
            res.errors.append(str(e))
            return res
        res.errors.extend(getattr(self.source, "last_errors", []))

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

        res.pairs = len(pairs)
        observed = self.store.recently_observed(now - cfg.observe_dedupe_h * 3600)

        # 1) cheap filters + preliminary score (no on-chain data yet)
        shortlist = []
        for p in pairs:
            f = features.compute(p, now)
            fails = safety.gate_market(p, f, cfg)
            if fails:
                self._drop(res, p, fails)
                continue
            res.evaluated += 1
            prelim = scoring.score(p, f, None, min_liquidity=cfg.min_liquidity_usd)
            low = prelim.total + 10 < cfg.signal_threshold  # the on-chain check adds at most 10 points
            obs_id = None
            if p.mint not in observed:  # watch this token's price for the strategy analysis
                obs_id = self._observe(p, f, prelim.total, "score_low" if low else "unchecked", now)
                observed.add(p.mint)
            if low:
                self.store.add_evaluation(p.mint, p.symbol, now, prelim.total, p.price_usd, False, {"stage": "score"})
                res.near.append({"symbol": p.symbol, "mint": p.mint, "score": round(prelim.total, 1),
                                 "why": f"score too low (needs {cfg.signal_threshold:.0f})"})
                continue
            shortlist.append((prelim.total, p, f, obs_id))

        # 2) on-chain checks, best candidates first, within the RPC budget
        shortlist.sort(key=lambda x: -x[0])
        streak = 0
        for prelim_total, p, f, obs_id in shortlist[: cfg.max_safety_checks]:
            s = self.safety_checker.check(p.mint)
            if not s.checked:
                streak += 1
                self._stage(obs_id, "unverified")
                if cfg.require_safety:
                    res.near.append({"symbol": p.symbol, "mint": p.mint, "score": round(prelim_total, 1),
                                     "why": f"could not verify on-chain ({s.error or 'no data'})"})
                if streak >= MAX_UNVERIFIED_STREAK:
                    res.errors.append("on-chain checks failing: " + (s.error or "unknown") +
                                      ". Set SOLSCOUT_RPC_URL to a dedicated RPC endpoint (see README).")
                    break
                if cfg.require_safety:
                    continue
            else:
                streak = 0
            fails = safety.gate_onchain(s, cfg)
            if fails:
                self._drop(res, p, fails)
                self._stage(obs_id, "onchain_fail", {"fail": fails[0][0]})
                self.store.add_evaluation(p.mint, p.symbol, now, None, p.price_usd, False,
                                          {"stage": "onchain", "fails": [m for _, m in fails]})
                continue
            sc = scoring.score(p, f, s, min_liquidity=cfg.min_liquidity_usd)
            is_signal = sc.total >= cfg.signal_threshold
            self._stage(obs_id, "signal" if is_signal else "below",
                        {"final_score": sc.total, "top_holders_pct": s.top_holders_pct, "largest_pct": s.largest_pct})
            self.store.add_evaluation(p.mint, p.symbol, now, sc.total, p.price_usd, is_signal, {"stage": "final"})
            if not is_signal:
                res.near.append({"symbol": p.symbol, "mint": p.mint, "score": round(sc.total, 1),
                                 "why": f"score {sc.total:.0f} is below {cfg.signal_threshold:.0f}"})
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

        res.near.sort(key=lambda x: -x["score"])
        res.near = res.near[:8]
        self.store.prune_evaluations(now - 14 * 86400)
        self.store.prune_observations(now - 45 * 86400)
        return res
