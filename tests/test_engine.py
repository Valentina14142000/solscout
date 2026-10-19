from solscout import tracker
from solscout.models import Safety, SourceError
from helpers import Sink, make


def test_scan_emits_only_the_good_token():
    eng, store, src, sink, clock, cfg = make()
    res = eng.scan_once()
    assert [s["symbol"] for s in res.signals] == ["ORBIT"]
    assert len(sink.sent) == 1 and "ORBIT" in sink.sent[0] and "not advice" in sink.sent[0]
    assert res.dropped == {"honeypot": 1, "low_liquidity": 1, "freeze_authority": 1}


def test_signal_is_saved_and_marked_notified():
    eng, store, *_ = make()
    eng.scan_once()
    [s] = store.list_signals()
    assert s["symbol"] == "ORBIT" and s["notified"] == 1 and s["reasons"]


def test_cooldown_prevents_repeat_signal():
    eng, store, src, sink, clock, cfg = make()
    eng.scan_once()
    clock[0] += 3600
    assert eng.scan_once().signals == []
    clock[0] += cfg.cooldown_hours * 3600
    assert [s["symbol"] for s in eng.scan_once().signals] == ["ORBIT"]


def test_daily_cap():
    eng, store, src, sink, clock, cfg = make()
    cfg.max_signals_per_day = 0
    res = eng.scan_once()
    assert res.signals == [] and "daily signal cap reached" in res.errors


def test_failed_notification_keeps_the_signal_unnotified():
    eng, store, *_ = make(notifier=Sink(ok=False))
    eng.scan_once()
    assert store.list_signals()[0]["notified"] == 0


def test_unverifiable_token_never_signals_and_is_explained():
    class Down:
        def check(self, mint):
            return Safety(checked=False, error="rpc down")
    eng, store, src, sink, clock, cfg = make(safety=Down())
    res = eng.scan_once()
    assert res.signals == []
    assert any("could not verify" in n["why"] for n in res.near)


def test_source_failure_is_reported_not_raised():
    eng, *_ = make()

    def boom():
        raise SourceError("api down")
    eng.source.candidates = boom
    res = eng.scan_once()
    assert res.errors == ["api down"] and res.signals == []


def test_safety_check_budget_is_respected():
    calls = []

    class Counting:
        def check(self, mint):
            calls.append(mint)
            return Safety(checked=True, top_holders_pct=10)
    eng, store, src, sink, clock, cfg = make(safety=Counting())
    cfg.max_safety_checks = 1
    eng.scan_once()
    assert len(calls) == 1


# ---- tracker
def test_tracker_records_return_after_costs():
    eng, store, src, sink, clock, cfg = make()
    eng.scan_once()
    clock[0] += 1.1 * 3600
    src.move("ORBIT", 1.25)
    assert tracker.resolve(cfg, store, src, clock=lambda: clock[0]) == 1
    o = store.list_signals()[0]["outcomes"][1]
    assert abs(o["return_pct"] - (25.0 - cfg.paper_cost_pct)) < 1e-6 and not o["rugged"]


def test_tracker_does_not_record_early_or_twice():
    eng, store, src, sink, clock, cfg = make()
    eng.scan_once()
    assert tracker.resolve(cfg, store, src, clock=lambda: clock[0]) == 0
    clock[0] += 1.1 * 3600
    assert tracker.resolve(cfg, store, src, clock=lambda: clock[0]) == 1
    assert tracker.resolve(cfg, store, src, clock=lambda: clock[0]) == 0


def test_tracker_flags_a_rug_when_liquidity_collapses():
    eng, store, src, sink, clock, cfg = make()
    eng.scan_once()
    clock[0] += 1.1 * 3600
    src.move("ORBIT", price_factor=0.5, liquidity_factor=0.02)
    tracker.resolve(cfg, store, src, clock=lambda: clock[0])
    o = store.list_signals()[0]["outcomes"][1]
    assert o["rugged"] == 1 and o["return_pct"] == -100.0


def test_tracker_waits_when_pair_missing_then_gives_up():
    eng, store, src, sink, clock, cfg = make()
    eng.scan_once()
    src._pairs.pop(next(m for m in src._pairs if m.startswith("ORBIT")))
    clock[0] += 1.1 * 3600
    assert tracker.resolve(cfg, store, src, clock=lambda: clock[0]) == 0
    clock[0] += 13 * 3600
    assert tracker.resolve(cfg, store, src, clock=lambda: clock[0]) >= 1
    assert store.list_signals()[0]["outcomes"][1]["rugged"] == 1


def test_stats_aggregate():
    eng, store, src, sink, clock, cfg = make()
    eng.scan_once()
    clock[0] += 1.1 * 3600
    src.move("ORBIT", 1.25)
    tracker.resolve(cfg, store, src, clock=lambda: clock[0])
    st = store.stats(cfg.horizons_h)
    assert st["signals"] == 1 and st["horizons"][1]["n"] == 1 and st["horizons"][1]["win_rate"] == 1.0
    assert st["horizons"][24]["n"] == 0 and st["horizons"][24]["win_rate"] is None
