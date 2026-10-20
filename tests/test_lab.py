import json
import threading
import urllib.request

from solscout import analyze, runner, tracker
from solscout.config import Config
from solscout.web import make_server
from helpers import Sink, make


def watch(eng, store, src, clock, cfg, hours=25, step=300):
    for _ in range(int(hours * 3600 / step)):
        clock[0] += step
        tracker.snapshot_open(cfg, store, src, clock=lambda: clock[0])


def test_scan_records_an_observation_for_every_token_that_passes_market_filters():
    eng, store, src, sink, clock, cfg = make()
    eng.scan_once()
    obs = {o["symbol"]: o for o in store.load_observations()}
    assert set(obs) == {"ORBIT", "FREEZY", "PUMPED", "QUIET"}      # honeypot and thin pool are never watched
    assert obs["ORBIT"]["stage"] == "signal" and obs["FREEZY"]["stage"] == "onchain_fail"
    assert obs["QUIET"]["stage"] == "score_low" and obs["ORBIT"]["features"]["accel"] > 3


def test_a_token_is_not_observed_twice_within_the_dedupe_window():
    eng, store, src, sink, clock, cfg = make()
    eng.scan_once()
    clock[0] += 1800
    eng.scan_once()
    assert store.count_observations() == 4


def test_watching_records_a_price_path_for_24_hours_then_stops():
    eng, store, src, sink, clock, cfg = make()
    eng.scan_once()
    watch(eng, store, src, clock, cfg, hours=25)
    watch(eng, store, src, clock, cfg, hours=2)         # keep calling: nothing more is recorded after the window
    for o in store.load_observations():
        assert len(o["ticks"]) == 25 * 12 + 1           # one every 5 minutes for 25h, plus the first
        assert o["ticks"][-1][0] - o["ticks"][0][0] == cfg.track_hours * 3600


def test_full_pipeline_ends_in_a_readable_report():
    eng, store, src, sink, clock, cfg = make()
    eng.scan_once()
    src.move("ORBIT", 1.3)
    watch(eng, store, src, clock, cfg)
    out = analyze.analyze(store.load_observations(), cfg, horizon=6, delay_min=5)
    assert "STRATEGY ANALYSIS" in out and "VERDICT" in out and "NOT ENOUGH DATA" in out
    assert "4 tokens watched" in out


def test_database_from_an_older_version_is_upgraded():
    import sqlite3
    import tempfile
    from solscout.store import Store
    path = tempfile.mkdtemp() + "/old.db"
    c = sqlite3.connect(path)
    c.executescript("CREATE TABLE signals(id INTEGER PRIMARY KEY AUTOINCREMENT, mint TEXT NOT NULL, symbol TEXT NOT NULL,"
                    " pair_address TEXT NOT NULL, score REAL NOT NULL, entry_price REAL NOT NULL, entry_liquidity REAL NOT NULL,"
                    " reasons TEXT NOT NULL, warnings TEXT NOT NULL DEFAULT '[]', created_at INTEGER NOT NULL,"
                    " notified INTEGER NOT NULL DEFAULT 0);")
    c.commit()
    c.close()
    s = Store(path)
    s.add_signal(mint="m", symbol="s", pair_address="p", score=70, entry_price=1, entry_liquidity=1, reasons=[],
                 warnings=[], created_at=1)
    assert len(s.list_signals()) == 1


# ---------------------------------------------------------------- exit alerts
def signal_setup():
    eng, store, src, sink, clock, cfg = make()
    eng.scan_once()
    sink.sent.clear()
    return eng, store, src, sink, clock, cfg


def exits(eng, store, src, sink, clock, cfg):
    return tracker.check_exits(cfg, store, src, sink, clock=lambda: clock[0])


def test_no_exit_alert_while_price_is_unremarkable():
    ctx = signal_setup()
    assert exits(*ctx) == [] and ctx[3].sent == []


def test_take_profit_alert_fires_once():
    ctx = signal_setup()
    ctx[2].move("ORBIT", 1.6)
    assert [f["kind"] for f in exits(*ctx)] == ["target"]
    assert exits(*ctx) == [] and len(ctx[3].sent) == 1
    assert "nothing has been sold" in ctx[3].sent[0].lower()


def test_stop_alert_and_target_alert_are_independent():
    ctx = signal_setup()
    ctx[2].move("ORBIT", 1.6)
    exits(*ctx)
    ctx[2].move("ORBIT", 0.45)         # 1.6 * 0.45 = 0.72 of entry
    assert [f["kind"] for f in exits(*ctx)] == ["stop"]


def test_liquidity_drain_takes_priority_over_a_price_target():
    ctx = signal_setup()
    ctx[2].move("ORBIT", 3.0, 0.2)
    assert [f["kind"] for f in exits(*ctx)] == ["liquidity"]


def test_old_signals_are_no_longer_watched():
    ctx = signal_setup()
    ctx[4][0] += 25 * 3600
    ctx[2].move("ORBIT", 2.0)
    assert exits(*ctx) == []


def test_runner_cycle_sends_exit_alerts_too():
    eng, store, src, sink, clock, cfg = make()
    st = runner.Status()
    runner.run_cycle(eng, st)
    sink.sent.clear()
    src.move("ORBIT", 1.7)
    clock[0] += 60
    runner.run_cycle(eng, st)
    assert any("EXIT ALERT" in m for m in sink.sent)


# ---------------------------------------------------------------- progress
def test_lab_progress_counts_only_completed_candidates():
    eng, store, src, sink, clock, cfg = make()
    eng.scan_once()
    now0 = clock[0]
    p = store.lab_progress(now0, 6, 60)
    assert p["observations"] == 4 and p["completed"] == 0 and p["completed_candidates"] == 0
    watch(eng, store, src, clock, cfg, hours=7)
    p = store.lab_progress(clock[0], 6, 60)
    assert p["completed"] == 3 and p["completed_candidates"] >= 1      # FREEZY (rejected) is never counted
    assert p["hours_running"] > 6


def test_lab_endpoint():
    cfg = Config()
    eng, store, src, sink, clock, cfg = make(cfg)
    eng.scan_once()
    srv = make_server(cfg, store, runner.Status(), host="127.0.0.1", port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        d = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/api/lab", timeout=5).read())
        assert d["observations"] == 4 and d["horizon_h"] == 6
    finally:
        srv.shutdown()
