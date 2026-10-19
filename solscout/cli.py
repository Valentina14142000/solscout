from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

from . import tracker
from .config import Config
from .engine import Engine, ScanResult
from .models import SourceError
from .notify import ConsoleNotifier, make_notifier
from .sources import default_source
from .sources.rpc_safety import RpcSafety
from .store import Store


def _print_scan(res: ScanResult) -> None:
    print(f"candidates {res.candidates}  evaluated {res.evaluated}  signals {len(res.signals)}")
    for reason, n in sorted(res.dropped.items(), key=lambda x: -x[1]):
        print(f"  dropped {n:>3}  {reason}")
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
        pairs = src.pairs(mints[:10])
        print(f"[ok]   market data: {len(pairs)} pairs parsed")
        if pairs:
            p = pairs[0]
            print(f"       sample: {p.symbol} ${p.price_usd:.8g} liquidity ${p.liquidity_usd:,.0f}")
    except SourceError as e:
        print(f"[FAIL] market data: {e}")
        ok = False
        pairs = []
    if pairs:
        s = RpcSafety(cfg.rpc_url, cfg.http_timeout_s).check(pairs[0].mint)
        if s.checked:
            print(f"[ok]   on-chain: mint authority {'ACTIVE' if s.mint_authority else 'revoked'}, "
                  f"freeze {'ACTIVE' if s.freeze_authority else 'revoked'}")
        else:
            print(f"[warn] on-chain check failed: {s.error} (try a dedicated RPC URL, see README)")
    print("[ok]   telegram: " + ("configured" if cfg.telegram_token and cfg.telegram_chat_id else "not configured (alerts print to the console)"))
    return 0 if ok else 1


def _build(cfg: Config) -> Engine:
    return Engine(cfg, Store(cfg.db_path), default_source(cfg), RpcSafety(cfg.rpc_url, cfg.http_timeout_s), make_notifier(cfg))


def cmd_scan(_args) -> int:
    cfg = Config.from_env()
    eng = _build(cfg)
    _print_scan(eng.scan_once())
    return 0


def cmd_run(_args) -> int:
    cfg = Config.from_env()
    eng = _build(cfg)
    print(f"Running every {cfg.scan_interval_s}s. Ctrl+C to stop.")
    while True:
        try:
            res = eng.scan_once()
            n = tracker.resolve(cfg, eng.store, eng.source)
            print(time.strftime("%H:%M:%S"), f"candidates {res.candidates} evaluated {res.evaluated} "
                  f"signals {len(res.signals)} outcomes {n}" + (f" warnings {len(res.errors)}" if res.errors else ""), flush=True)
        except KeyboardInterrupt:
            return 0
        except Exception as e:  # keep the worker alive whatever one cycle does
            print("cycle failed:", repr(e), flush=True)
        try:
            time.sleep(cfg.scan_interval_s)
        except KeyboardInterrupt:
            return 0


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
    sp = sub.add_parser("signals", help="list recent signals")
    sp.add_argument("--limit", type=int, default=20)
    sp.set_defaults(fn=cmd_signals)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
