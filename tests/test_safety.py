from dataclasses import replace

from solscout import features, safety
from solscout.config import Config
from solscout.fixtures import sample_pairs
from solscout.models import Safety
from helpers import NOW

PAIRS = {p.symbol: p for p in sample_pairs(NOW)}
CFG = Config()


def codes(p):
    return [c for c, _ in safety.gate_market(p, features.compute(p, NOW), CFG)]


def test_good_token_passes_market_gate():
    assert codes(PAIRS["ORBIT"]) == []


def test_low_liquidity_fails():
    assert "low_liquidity" in codes(PAIRS["THIN"])


def test_buys_without_sells_is_honeypot():
    assert "honeypot" in codes(PAIRS["MOONDOG"])


def test_too_new_and_too_old():
    assert "too_new" in codes(replace(PAIRS["ORBIT"], created_at=int(NOW - 5 * 60)))
    assert "too_old" in codes(replace(PAIRS["ORBIT"], created_at=int(NOW - 100 * 3600)))


def test_unknown_age_fails():
    assert "age_unknown" in codes(replace(PAIRS["ORBIT"], created_at=None))


def test_low_activity_fails():
    assert "low_activity" in codes(replace(PAIRS["ORBIT"], buys_h1=5, sells_h1=5))


def onchain(s, cfg=CFG):
    return [c for c, _ in safety.gate_onchain(s, cfg)]


def test_clean_token_passes_onchain_gate():
    assert onchain(Safety(checked=True, top_holders_pct=10)) == []


def test_active_authorities_fail():
    got = onchain(Safety(checked=True, mint_authority="x", freeze_authority="y"))
    assert got == ["mint_authority", "freeze_authority"]


def test_concentrated_holders_fail():
    assert onchain(Safety(checked=True, top_holders_pct=60)) == ["concentrated"]


def test_token_2022_blocked_unless_allowed():
    assert onchain(Safety(checked=True, token_2022=True)) == ["token_2022"]
    assert onchain(Safety(checked=True, token_2022=True), Config(allow_token_2022=True)) == []


def test_unverified_token_fails_when_safety_required():
    assert onchain(Safety(checked=False, error="rpc down")) == ["no_onchain"]
    assert onchain(Safety(checked=False), Config(require_safety=False)) == []
