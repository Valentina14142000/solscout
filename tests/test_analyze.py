from solscout import analyze as an
from solscout.config import Config
from simulate import TICK, make_obs, path

CFG = Config()
H = 3600 // TICK  # ticks per hour


def flat(n, v=1.0):
    return [v] * n


# ---------------------------------------------------------------- per-token functions
def test_horizon_return_net_of_cost():
    o = path(flat(H, 1.0) + [1.25] * (2 * H))
    assert abs(an.horizon_return(o, 1, cost=3.0) - 22.0) < 1e-9


def test_horizon_return_none_until_horizon_recorded():
    o = path(flat(H // 2))
    assert an.horizon_return(o, 1) is None


def test_horizon_return_detects_rug_even_if_price_recovers():
    liqs = [50_000.0] * 5 + [500.0] * 3 + [50_000.0] * (3 * H)
    o = path([1.0] * len(liqs), liqs)
    assert an.horizon_return(o, 1) == -100.0


def test_entry_delay_changes_entry_price():
    prices = [1.0] * 3 + [2.0] * (3 * H)          # jumps after 3 ticks (15 min)
    o = path(prices)
    now = an.horizon_return(o, 1, delay_s=0, cost=0)
    late = an.horizon_return(o, 1, delay_s=15 * 60, cost=0)
    assert now > 90 and abs(late) < 1e-9          # buying after the jump removes the gain


def test_gap_in_ticks_beyond_tolerance_is_missing_not_guessed():
    o = path([1.0, 1.0, 2.0], step=4 * 3600)     # next tick 4h later
    assert an.horizon_return(o, 1) is None


def test_take_profit_and_stop_loss():
    up = path([1.0, 1.1, 1.6, 1.0] + flat(3 * H, 1.0))
    assert abs(an.exit_return(up, tp=.5, sl=.2, cost=3.0) - 47.0) < 1e-9
    down = path([1.0, 0.95, 0.7, 2.0] + flat(3 * H, 2.0))
    assert abs(an.exit_return(down, tp=.5, sl=.2, cost=3.0) - (-30.0 - 3.0)) < 1e-9


def test_stop_gapped_through_fills_at_tick_price_not_stop_level():
    o = path([1.0, 0.5] + flat(3 * H, 0.5))
    assert abs(an.exit_return(o, sl=.2, cost=0) - (-50.0)) < 1e-9


def test_stop_checked_before_target_in_same_tick():
    # one tick that is both beyond the stop and the target is impossible, but a rug tick must win over everything
    liqs = [50_000.0, 500.0, 500.0] + [50_000.0] * (3 * H)
    o = path([1.0, 3.0, 3.0] + flat(3 * H, 3.0), liqs)
    assert an.exit_return(o, tp=.5, cost=3.0) == -100.0


def test_single_glitch_tick_is_not_a_rug():
    liqs = [50_000.0, 0.0] + [50_000.0] * (3 * H)      # API returned no liquidity once
    o = path(flat(2 + 3 * H), liqs)
    assert an.horizon_return(o, 1, cost=0) == 0.0
    assert an.exit_return(o, tp=.5, cost=0, max_h=1) == 0.0


def test_collapse_that_persists_is_a_rug():
    liqs = [50_000.0] + [10.0] * (3 * H)
    assert an.horizon_return(path(flat(1 + 3 * H), liqs), 1) == -100.0


def test_trailing_stop_locks_in_gains():
    o = path([1.0, 1.5, 2.0, 1.55] + flat(3 * H, 1.55))
    assert abs(an.exit_return(o, trail=.2, cost=0) - 55.0) < 1e-9


def test_open_window_returns_none():
    assert an.exit_return(path(flat(5)), tp=.5, cost=3.0) is None


# ---------------------------------------------------------------- statistics
def test_summarize_basic_and_interval_contains_mean():
    st = an.summarize([10, -5, 20, 0, 5, -10, 15, 8])
    assert st.n == 8 and abs(st.mean - 5.375) < 1e-9 and st.lo <= st.mean <= st.hi
    assert an.summarize([1, 2, 3, 4, 5, 6]) == an.summarize([1, 2, 3, 4, 5, 6])   # deterministic


def test_summarize_empty_and_rugs():
    assert an.summarize([]).n == 0
    assert an.summarize([-100, 5, 5, 5]).rugs == 0.25


# ---------------------------------------------------------------- whole report on synthetic markets
def test_report_finds_a_real_edge():
    out = an.analyze(make_obs(edge=0.0030), CFG, horizon=6, min_score=60)
    assert "PROMISING, NOT PROVEN" in out


def test_report_does_not_invent_an_edge_when_score_is_noise():
    verdicts = []
    for seed in (1, 2, 3, 4, 5):
        out = an.analyze(make_obs(edge=0.0, seed=seed), CFG, horizon=6, min_score=60)
        verdicts.append("PROMISING" in out)
    assert sum(verdicts) <= 1          # a 95% interval may fire by chance now and then, never routinely


def test_report_flags_a_losing_strategy():
    out = an.analyze(make_obs(edge=-0.0030), CFG, horizon=6, min_score=60)
    assert "LOSES MONEY" in out


def test_report_says_not_enough_data():
    out = an.analyze(make_obs(n=40), CFG, horizon=6, min_score=60)
    assert "NOT ENOUGH DATA" in out


def test_report_handles_empty_store():
    assert "No observations yet" in an.analyze([], CFG)


def test_report_has_every_section():
    out = an.analyze(make_obs(n=120), CFG, min_score=55)
    for head in ("1. WHAT DOES THE SCORE PREDICT", "2. THRESHOLD SWEEP", "3. WHICH INPUTS MATTER", "4. EXIT RULES",
                 "5. HOW MUCH DOES BEING LATE COST", "6. COST SENSITIVITY", "VERDICT"):
        assert head in out


def test_rejected_tokens_are_excluded_from_the_universe():
    obs = make_obs(n=60)
    for o in obs:
        o["stage"] = "onchain_fail"
    out = an.analyze(obs, CFG, min_score=55)
    assert "no completed results" in out


def test_price_on_a_collapsed_liquidity_tick_is_not_trusted():
    liqs = [50_000.0, 500.0] + [50_000.0] * (3 * H)    # one bad tick showing a fantasy price, then normal
    o = path([1.0, 9.0] + flat(3 * H, 1.0), liqs)
    assert an.exit_return(o, tp=.5, cost=0, max_h=1) == 0.0
