import base64
import json
import threading
import urllib.error
import urllib.request

from solscout import runner
from solscout.config import Config
from solscout.fixtures import seed_demo
from solscout.models import Safety, SourceError
from solscout.sources import rpc_safety, rugcheck
from solscout.sources.chain import ChainedSafety
from solscout.store import Store
from solscout.web import make_server
from helpers import NOW, make


# ---- RPC safety: 429 backoff
def test_rpc_rate_limit_backs_off_without_hammering():
    calls = []
    t = [NOW]
    r = rpc_safety.RpcSafety("http://x", clock=lambda: t[0])

    def boom(method, params):
        calls.append(method)
        raise SourceError("x: HTTP 429")
    r._rpc = boom
    s = r.check("MintA")
    assert not s.checked and s.retry_later and calls == ["getAccountInfo"]
    r.check("MintB")
    assert calls == ["getAccountInfo"]          # blocked: no new request
    t[0] += 61
    r.check("MintB")
    assert len(calls) == 2                      # block expired


def test_rpc_success_is_cached():
    calls = []
    r = rpc_safety.RpcSafety("http://x", clock=lambda: NOW)

    def fake(method, params):
        calls.append(method)
        if method == "getAccountInfo":
            return {"value": {"owner": "x", "data": {"parsed": {"type": "mint", "info": {"supply": "1000", "mintAuthority": None, "freezeAuthority": None}}}}}
        return {"value": [{"amount": "300"}, {"amount": "50"}]}
    r._rpc = fake
    a = r.check("M")
    b = r.check("M")
    assert a.checked and a.largest_pct == 30.0 and a.top_holders_pct == 5.0 and a is b and len(calls) == 2


# ---- RugCheck parsing
def test_rugcheck_parse_authorities_holders_and_flags():
    rep = {"token": {"mintAuthority": None, "freezeAuthority": "F"},
           "topHolders": [{"pct": 40}, {"pct": 5}, {"pct": 5}],
           "risks": [{"name": "Low Liquidity", "level": "danger"}, {"name": "x", "level": "warn"}],
           "rugged": False}
    s = rugcheck.parse_report(rep)
    assert s.checked and s.freeze_authority == "F" and not s.mint_authority
    assert s.largest_pct == 40 and s.top_holders_pct == 10 and s.flags == ("Low Liquidity",) and not s.rugged


def test_rugcheck_fraction_holders_are_scaled_and_rugged_flag_read():
    s = rugcheck.parse_report({"token": {}, "topHolders": [{"pct": 0.4}, {"pct": 0.1}], "rugged": True})
    assert s.largest_pct == 40.0 and s.rugged


def test_rugcheck_rejects_garbage():
    assert not rugcheck.parse_report("nope").checked and not rugcheck.parse_report({"a": 1}).checked


# ---- chained safety
class Fixed:
    def __init__(self, s):
        self.s, self.calls = s, 0

    def check(self, mint):
        self.calls += 1
        return self.s


def test_chain_falls_back_to_the_next_source():
    good = Safety(checked=True)
    a, b = Fixed(Safety(error="rate limited", retry_later=True)), Fixed(good)
    assert ChainedSafety([a, b]).check("m") is good and a.calls == 1 and b.calls == 1


def test_chain_stops_at_first_verified_source():
    a, b = Fixed(Safety(checked=True)), Fixed(Safety(checked=True))
    ChainedSafety([a, b]).check("m")
    assert b.calls == 0


def test_chain_reports_all_failures():
    s = ChainedSafety([Fixed(Safety(error="a", retry_later=True)), Fixed(Safety(error="b", retry_later=True))]).check("m")
    assert not s.checked and s.retry_later and "a" in s.error and "b" in s.error


def test_chain_not_retry_later_if_any_source_gave_a_real_failure():
    s = ChainedSafety([Fixed(Safety(error="a", retry_later=True)), Fixed(Safety(error="bad payload"))]).check("m")
    assert not s.retry_later


# ---- engine behaviour with unverifiable tokens
def test_engine_stops_checking_after_repeated_failures_and_explains():
    class Down:
        calls = 0

        def check(self, mint):
            Down.calls += 1
            return Safety(error="RPC rate limited", retry_later=True)
    eng, store, src, sink, clock, cfg = make(safety=Down())
    res = eng.scan_once()
    assert Down.calls == 2 and res.signals == []
    assert any("SOLSCOUT_RPC_URL" in e for e in res.errors)


def test_scan_reports_near_misses_and_rejections():
    eng, *_ = make()
    res = eng.scan_once()
    assert {r["symbol"] for r in res.rejected} == {"MOONDOG", "FREEZY", "THIN"}
    assert any(n["symbol"] == "QUIET" for n in res.near)
    assert res.near == sorted(res.near, key=lambda x: -x["score"])


# ---- runner
def test_run_cycle_records_status_and_survives_exceptions():
    eng, *_ = make()
    st = runner.Status()
    assert runner.run_cycle(eng, st) is not None
    snap = st.snapshot()
    assert snap["scans"] == 1 and snap["last"]["signals"] == 1 and snap["last_error"] == ""

    def boom():
        raise RuntimeError("kaboom")
    eng.scan_once = boom
    assert runner.run_cycle(eng, st) is None
    assert "kaboom" in st.snapshot()["last_error"]


def test_loop_stops_when_asked():
    eng, *_ = make()
    eng.cfg.scan_interval_s = 0
    stop, st, n = threading.Event(), runner.Status(), []
    runner.loop(eng, st, stop, on_cycle=lambda r, s: (n.append(1), stop.set()))
    assert n == [1]


# ---- web
def serve(cfg=None):
    cfg = cfg or Config()
    eng, store, *_ = make(cfg)
    st = runner.Status()
    runner.run_cycle(eng, st)
    srv = make_server(cfg, store, st, host="127.0.0.1", port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def fetch(url, auth=None):
    req = urllib.request.Request(url)
    if auth:
        req.add_header("Authorization", "Basic " + base64.b64encode(auth.encode()).decode())
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_dashboard_endpoints_and_security_headers():
    srv, base = serve()
    try:
        code, h, body = fetch(base + "/")
        assert code == 200 and b"SolScout" in body and "default-src 'none'" in h["Content-Security-Policy"]
        assert h["X-Content-Type-Options"] == "nosniff"
        st = json.loads(fetch(base + "/api/status")[2])
        assert st["scans"] == 1 and st["config"]["signal_threshold"] == 70 and "telegram_token" not in json.dumps(st)
        sigs = json.loads(fetch(base + "/api/signals?limit=5")[2])
        assert sigs[0]["symbol"] == "ORBIT"
        assert "horizons" in json.loads(fetch(base + "/api/stats")[2])
        assert fetch(base + "/nope")[0] == 404
        assert json.loads(fetch(base + "/api/signals?limit=abc")[2])  # bad input falls back to default
    finally:
        srv.shutdown()


def test_dashboard_password():
    srv, base = serve(Config(dashboard_password="s3cret"))
    try:
        assert fetch(base + "/health")[0] == 200            # health check stays open for hosting platforms
        code, h, _ = fetch(base + "/api/status")
        assert code == 401 and "Basic" in h["WWW-Authenticate"]
        assert fetch(base + "/api/status", auth="me:wrong")[0] == 401
        assert fetch(base + "/api/status", auth="me:s3cret")[0] == 200
    finally:
        srv.shutdown()


def test_demo_seed_populates_everything():
    import tempfile
    store = Store(tempfile.mkdtemp() + "/d.db")
    res = seed_demo(store, NOW)
    sigs = store.list_signals()
    assert len(sigs) == 7 and res.near and res.rejected
    assert store.stats((1, 6, 24))["horizons"][1]["n"] == 6


def test_config_reads_port_from_hosting_env():
    import os
    old = {k: os.environ.pop(k, None) for k in ("PORT", "SOLSCOUT_PORT")}
    try:
        os.environ["PORT"] = "10000"
        assert Config.from_env().port == 10000
        os.environ["SOLSCOUT_PORT"] = "9000"
        assert Config.from_env().port == 9000
    finally:
        for k, v in old.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
