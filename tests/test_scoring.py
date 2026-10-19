from dataclasses import replace

from solscout import features, safety, scoring
from solscout.config import Config
from solscout.fixtures import sample_pairs
from solscout.models import Safety, Pair
from helpers import NOW

PAIRS = {p.symbol: p for p in sample_pairs(NOW)}
CLEAN = Safety(checked=True, top_holders_pct=14.0)


def sc(p, s=CLEAN):
    return scoring.score(p, features.compute(p, NOW), s)


def test_weights_sum_to_100():
    assert sum(scoring.WEIGHTS.values()) == 100


def test_strong_token_scores_high_with_reasons():
    s = sc(PAIRS["ORBIT"])
    assert s.total >= 80
    assert any("volume running" in r for r in s.reasons) and any("revoked" in r for r in s.reasons)


def test_score_is_bounded():
    for p in PAIRS.values():
        assert 0 <= sc(p).total <= 100


def test_quiet_token_scores_low():
    assert sc(PAIRS["QUIET"]).total < 40


def test_late_pump_is_penalised_below_threshold():
    s = sc(PAIRS["PUMPED"])
    assert s.total < Config().signal_threshold
    assert any("late" in w for w in s.warnings)


def test_momentum_peaks_in_the_sweet_spot_and_dies_when_extended():
    base = PAIRS["ORBIT"]
    pts = {c: scoring._momentum(replace(base, change_h1=c), [], []) for c in (-5, 5, 40, 150, 400)}
    assert pts[-5] == 0 and 0 < pts[5] < 25 and pts[40] == 25 and 0 < pts[150] < 25 and pts[400] == 0


def test_no_safety_data_gives_no_safety_points():
    assert sc(PAIRS["ORBIT"], None).parts["safety"] == 0


def test_features_use_run_rate_for_very_new_tokens():
    f = features.compute(replace(PAIRS["ORBIT"], created_at=int(NOW - 1800)), NOW)
    assert f.accel_basis == "run-rate"


def test_features_baseline_for_older_tokens():
    f = features.compute(PAIRS["ORBIT"], NOW)
    assert f.accel_basis == "baseline" and abs(f.accel - 4.0) < 0.01


def test_log_scale_endpoints():
    assert features.log_scale(1, 1, 8) == 0 and features.log_scale(8, 1, 8) == 1 and features.log_scale(100, 1, 8) == 1
