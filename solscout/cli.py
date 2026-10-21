from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

import threading

from . import bot, runner, tracker
from .config import Config
from .engine import Engine, ScanResult
from .models import SourceError
from .notify import ConsoleNotifier, make_notifier
from .sources import default_source
from .sources.chain import ChainedSafety
from .sources.rpc_safety import RpcSafety
from .sources.rugcheck import RugCheckSafety
from .store import Store


def _print_scan(res: ScanResult) -> None:
    print(f"candidates {res.candidates}  priced {res.pairs}  passed filters {res.evaluated}  signals {len(res.signals)}")
    for reason, n in sorted(res.dropped.items(), key=lambda x: -x[1]):
        print(f"  dropped {n:>3}  {reason}")
    for n in res.near[:5]:
        print(f"  near miss  {n['symbol']:<10} {n['score']:>5}  {n['why']}")
    for e in res.errors:
        print(f"  warning: {e}")


def _print_stats(store: Store, cfg: Config) -> None:
    st = store.stats(cfg.horizons_h)
    print(f"\nPaper-trade results ({st['signals']} signals, after {cfg.paper_cost_pct:.0f}% costs)")
    print(f"{'horizon':>8} {'n':>4} {'win%':>6} {'avg%':>8} {'median%':>8} {'worst%':>8} {'rugs%':>6}")
    for h, s in st["horizons"].items():
        if not s["n"]:
            print(f"{str(h) + 'h':>8} {0:>4}   no results yet")
            continue
        print(f"{str(h) + 'h':>8} {s['n']:>4} {s['win_rate'] * 100:>6.0f} {s['avg_return']:>8.1f} "
              f"{s['median_return']:>8.1f} {s['worst']:>8.1f} {s['rug_rate'] * 100:>6.0f}")
    print("Small samples mean nothing: wait for dozens of results before trusting these numbers.")


def cmd_demo(_args) -> int:
    """Whole pipeline on built-in sample data, with a simulated clock. No network, nothing real is touched."""
    from .fixtures import FixtureSafety, FixtureSource

    cfg = Config(db_path=str(Path(tempfile.mkdtemp()) / "demo.db"))
    clock = [1_800_000_000.0]
    now = lambda: clock[0]  # noqa: E731
    store, src = Store(cfg.db_path), FixtureSource(clock[0])
    engine = Engine(cfg, store, src, FixtureSafety(), ConsoleNotifier(), clock=now)

    print("== Scan 1: what would the bot do with this market? ==")
    res = engine.scan_once()
    _print_scan(res)
    print("\n== 1 hour later: ORBIT is up 25% ==")
    clock[0] += 1.1 * 3600
    src.move("ORBIT", 1.25)
    tracker.resolve(cfg, store, src, clock=now)
    print("== 6 hours later: ORBIT is up 60% from entry ==")
    clock[0] += 5 * 3600
    src.move("ORBIT", 1.6 / 1.25)
    tracker.resolve(cfg, store, src, clock=now)
    _print_stats(store, cfg)
    return 0


def cmd_check(args) -> int:
    """Live connectivity test: tells you which data sources work from this machine."""
    cfg = Config.from_env()
    src = default_source(cfg)
    ok = True
    try:
        mints = src.candidates()
        print(f"[ok]   discovery: {len(mints)} candidate tokens")
        for e in src.last_errors:
            print(f"[warn] {e}")
    except SourceError as e:
        print(f"[FAIL] discovery: {e}")
        return 1
    try:
        pairs = src.pairs(mints[:30])
        print(f"[ok]   market data: {len(pairs)} of the first 30 candidates have usable pair data")
        if pairs:
            p = pairs[0]
            print(f"       sample: {p.symbol} ${p.price_usd:.8g} liquidity ${p.liquidity_usd:,.0f}")
    except SourceError as e:
        print(f"[FAIL] market data: {e}")
        ok = False
        pairs = []
    if pairs:
        s = _safety(cfg).check(pairs[0].mint)
        if s.checked:
            print(f"[ok]   on-chain: mint authority {'ACTIVE' if s.mint_authority else 'revoked'}, "
                  f"freeze {'ACTIVE' if s.freeze_authority else 'revoked'}")
        else:
            print(f"[warn] on-chain check failed: {s.error} (try a dedicated RPC URL, see README)")
    if pairs:
        r = RugCheckSafety(cfg.http_timeout_s).check(pairs[0].mint)
        if r.checked:
            print("[ok]   rugcheck: report parsed")
        else:
            print(f"[warn] rugcheck: {r.error}")
    print("[ok]   telegram: " + ("configured" if cfg.telegram_token and cfg.telegram_chat_id else "not configured (alerts print to the console)"))
    return 0 if ok else 1


def _safety(cfg: Config) -> ChainedSafety:
    return ChainedSafety([RpcSafety(cfg.rpc_url, cfg.http_timeout_s), RugCheckSafety(cfg.http_timeout_s)])


def _build(cfg: Config) -> Engine:
    return Engine(cfg, Store(cfg.db_path), default_source(cfg), _safety(cfg), make_notifier(cfg))


def cmd_scan(_args) -> int:
    cfg = Config.from_env()
    eng = _build(cfg)
    _print_scan(eng.scan_once())
    return 0


def _log_cycle(res, status) -> None:
    snap = status.snapshot()
    if res is None:
        print(time.strftime("%H:%M:%S"), "cycle failed:", snap["last_error"], flush=True)
        return
    print(time.strftime("%H:%M:%S"), f"candidates {res.candidates}  passed filters {res.evaluated}  "
          f"signals {len(res.signals)}" + (f"  warnings {len(res.errors)}" if res.errors else ""), flush=True)


def cmd_run(_args) -> int:
    cfg = Config.from_env()
    eng, status, stop = _build(cfg), runner.Status(), threading.Event()
    print(f"Scanning every {cfg.scan_interval_s}s. Ctrl+C to stop.")
    if bot.start_listener(cfg, eng.store, status, stop):
        print("Telegram bot is answering messages (/help).")
    try:
        runner.loop(eng, status, stop, _log_cycle)
    except KeyboardInterrupt:
        stop.set()
    return 0


def cmd_serve(args) -> int:
    """Dashboard plus the scanning worker in one process."""
    from .web import make_server

    cfg = Config.from_env()
    status = runner.Status()
    stop = threading.Event()
    if args.demo:
        from .fixtures import seed_demo
        cfg.db_path = str(Path(tempfile.mkdtemp()) / "demo.db")
        store = Store(cfg.db_path)
        status.demo = True
        status.record(seed_demo(store, time.time()), 0)
    else:
        eng = _build(cfg)
        store = eng.store
        threading.Thread(target=runner.loop, args=(eng, status, stop, _log_cycle), daemon=True).start()
        if bot.start_listener(cfg, store, status, stop):
            print("Telegram bot is answering messages (/help).")
    if args.port:
        cfg.port = args.port
    srv = make_server(cfg, store, status)
    print(f"Dashboard: http://{cfg.host}:{srv.server_address[1]}" + ("  (DEMO DATA)" if args.demo else ""))
    if cfg.host not in ("127.0.0.1", "localhost") and not cfg.dashboard_password:
        print("WARNING: dashboard is reachable from the network with no password. Set SOLSCOUT_DASHBOARD_PASSWORD.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    stop.set()
    return 0


def cmd_analyze(args) -> int:
    from .analyze import analyze
    cfg = Config.from_env()
    store = Store(cfg.db_path)
    print(analyze(store.load_observations(), cfg, horizon=args.horizon, delay_min=args.delay_min,
                  holdout=args.holdout, min_score=args.min_score))
    return 0


def cmd_telegram(_args) -> int:
    """Set up and test Telegram alerts without ever putting the token in a URL."""
    from .notify import TelegramNotifier, find_chat_id

    cfg = Config.from_env()
    if not cfg.telegram_token:
        print("No token found. Add this line to your .env file (no spaces, no quotes), then run this again:")
        print("  SOLSCOUT_TELEGRAM_TOKEN=<the token @BotFather gave you>")
        return 1
    if not cfg.telegram_chat_id:
        try:
            chat_id, name = find_chat_id(cfg.telegram_token, cfg.http_timeout_s)
        except SourceError as e:
            print(f"Could not reach Telegram: {e}. Check that the token is correct and complete.")
            return 1
        if not chat_id:
            print("Telegram has no messages for your bot yet. Open your bot in Telegram, press Start, send it any")
            print("message, then run this command again.")
            return 1
        print(f"Found your chat{' (' + name + ')' if name else ''}. Add this line to your .env file, then run this again:")
        print(f"  SOLSCOUT_TELEGRAM_CHAT_ID={chat_id}")
        return 0
    ok = TelegramNotifier(cfg.telegram_token, cfg.telegram_chat_id, cfg.http_timeout_s).send(
        "SolScout is connected. Signals and exit alerts will arrive here.\n\nAutomated analysis, not advice.")
    print("Test message sent. Check Telegram." if ok else
          "Telegram refused the message. Check the chat ID, and that you pressed Start in your bot.")
    return 0 if ok else 1


def cmd_stats(_args) -> int:
    cfg = Config.from_env()
    store = Store(cfg.db_path)
    tracker.resolve(cfg, store, default_source(cfg))
    _print_stats(store, cfg)
    return 0


def cmd_signals(args) -> int:
    cfg = Config.from_env()
    for s in Store(cfg.db_path).list_signals(args.limit):
        out = " ".join(f"{h}h:{o['return_pct']:+.0f}%" for h, o in sorted(s["outcomes"].items())) or "pending"
        print(f"#{s['id']:<4} {time.strftime('%m-%d %H:%M', time.localtime(s['created_at']))}  "
              f"{s['symbol']:<10} score {s['score']:.0f}  {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="solscout", description="Solana signal bot (paper trading only).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn, help_ in [("demo", cmd_demo, "run the pipeline on built-in sample data"),
                            ("check", cmd_check, "test that live data sources work from this machine"),
                            ("scan", cmd_scan, "run one scan"), ("run", cmd_run, "scan continuously"),
                            ("stats", cmd_stats, "paper-trade results")]:
        sub.add_parser(name, help=help_).set_defaults(fn=fn)
    an = sub.add_parser("analyze", help="test the strategy on everything recorded so far")
    an.add_argument("--horizon", type=int, default=6, help="hours to hold (default 6)")
    an.add_argument("--delay-min", type=float, default=5, help="minutes between signal and entry (default 5: you are never instant)")
    an.add_argument("--holdout", type=float, default=0.3, help="share of the newest data kept for judging (default 0.3)")
    an.add_argument("--min-score", type=float, default=None, help="score cut-off for sections 4-6 (default threshold-10)")
    an.set_defaults(fn=cmd_analyze)
    sub.add_parser("telegram", help="set up and test Telegram alerts").set_defaults(fn=cmd_telegram)
    sv = sub.add_parser("serve", help="dashboard + scanning worker")
    sv.add_argument("--port", type=int, default=0)
    sv.add_argument("--demo", action="store_true", help="show the dashboard with sample data, no scanning")
    sv.set_defaults(fn=cmd_serve)
    sp = sub.add_parser("signals", help="list recent signals")
    sp.add_argument("--limit", type=int, default=20)
    sp.set_defaults(fn=cmd_signals)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
