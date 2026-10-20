from __future__ import annotations

import json
import sqlite3
import statistics
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mint TEXT NOT NULL, symbol TEXT NOT NULL, pair_address TEXT NOT NULL,
  score REAL NOT NULL, entry_price REAL NOT NULL, entry_liquidity REAL NOT NULL,
  reasons TEXT NOT NULL, warnings TEXT NOT NULL DEFAULT '[]',
  created_at INTEGER NOT NULL, notified INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_signals_mint ON signals(mint, created_at);
CREATE TABLE IF NOT EXISTS outcomes(
  signal_id INTEGER NOT NULL REFERENCES signals(id) ON DELETE CASCADE,
  horizon_h INTEGER NOT NULL, price REAL, return_pct REAL NOT NULL,
  rugged INTEGER NOT NULL DEFAULT 0, checked_at INTEGER NOT NULL,
  PRIMARY KEY(signal_id, horizon_h));
CREATE TABLE IF NOT EXISTS evaluations(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mint TEXT NOT NULL, symbol TEXT NOT NULL, ts INTEGER NOT NULL,
  score REAL, price REAL, passed INTEGER NOT NULL, detail TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_eval_ts ON evaluations(ts);
CREATE TABLE IF NOT EXISTS observations(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mint TEXT NOT NULL, symbol TEXT NOT NULL, pair_address TEXT NOT NULL,
  ts INTEGER NOT NULL, price REAL NOT NULL, liquidity REAL NOT NULL,
  score REAL NOT NULL, stage TEXT NOT NULL, features TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_obs_ts ON observations(ts);
CREATE INDEX IF NOT EXISTS idx_obs_mint ON observations(mint, ts);
CREATE TABLE IF NOT EXISTS exit_alerts(
  signal_id INTEGER NOT NULL REFERENCES signals(id) ON DELETE CASCADE,
  kind TEXT NOT NULL, ts INTEGER NOT NULL, PRIMARY KEY(signal_id, kind));
CREATE TABLE IF NOT EXISTS ticks(
  obs_id INTEGER NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
  ts INTEGER NOT NULL, price REAL NOT NULL, liquidity REAL NOT NULL,
  PRIMARY KEY(obs_id, ts));
"""


class Store:
    def __init__(self, path: str = "solscout.db"):
        self.path = path
        with self._conn() as c:
            c.executescript(SCHEMA)
            cols = {r["name"] for r in c.execute("PRAGMA table_info(signals)")}
            if "features" not in cols:  # databases created by older versions
                c.execute("ALTER TABLE signals ADD COLUMN features TEXT NOT NULL DEFAULT '{}'")
            if "source" not in cols:
                c.execute("ALTER TABLE signals ADD COLUMN source TEXT NOT NULL DEFAULT ''")

    @contextmanager
    def _conn(self):
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        try:
            yield c
            c.commit()
        finally:
            c.close()

    # ---- signals
    def add_signal(self, *, mint, symbol, pair_address, score, entry_price, entry_liquidity,
                   reasons, warnings, created_at, features=None, source="") -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO signals(mint,symbol,pair_address,score,entry_price,entry_liquidity,reasons,warnings,"
                "created_at,features,source) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (mint, symbol, pair_address, score, entry_price, entry_liquidity,
                 json.dumps(reasons), json.dumps(warnings), int(created_at), json.dumps(features or {}), source))
            return cur.lastrowid

    def mark_notified(self, signal_id: int) -> None:
        with self._conn() as c:
            c.execute("UPDATE signals SET notified=1 WHERE id=?", (signal_id,))

    def recent_signal_mints(self, since: float) -> set[str]:
        with self._conn() as c:
            return {r["mint"] for r in c.execute("SELECT DISTINCT mint FROM signals WHERE created_at>=?", (int(since),))}

    def count_signals_since(self, since: float) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) n FROM signals WHERE created_at>=?", (int(since),)).fetchone()["n"]

    def list_signals(self, limit: int = 50) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM signals ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["reasons"] = json.loads(d["reasons"])
                d["warnings"] = json.loads(d["warnings"])
                d["outcomes"] = {o["horizon_h"]: dict(o) for o in c.execute(
                    "SELECT * FROM outcomes WHERE signal_id=?", (d["id"],))}
                out.append(d)
            return out

    # ---- outcomes
    def pending_outcomes(self, now: float, horizons: tuple[int, ...]) -> list[tuple[dict, int]]:
        """(signal, horizon) pairs whose horizon has elapsed and which have no result yet."""
        out = []
        with self._conn() as c:
            for h in horizons:
                rows = c.execute(
                    "SELECT s.* FROM signals s WHERE s.created_at<=? AND NOT EXISTS "
                    "(SELECT 1 FROM outcomes o WHERE o.signal_id=s.id AND o.horizon_h=?)",
                    (int(now - h * 3600), h)).fetchall()
                out.extend((dict(r), h) for r in rows)
        return out

    def add_outcome(self, signal_id: int, horizon_h: int, price: float | None, return_pct: float,
                    rugged: bool, checked_at: float) -> None:
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO outcomes(signal_id,horizon_h,price,return_pct,rugged,checked_at)"
                      " VALUES(?,?,?,?,?,?)", (signal_id, horizon_h, price, return_pct, int(rugged), int(checked_at)))

    def stats(self, horizons: tuple[int, ...]) -> dict:
        with self._conn() as c:
            total = c.execute("SELECT COUNT(*) n FROM signals").fetchone()["n"]
            per = {}
            for h in horizons:
                rows = c.execute("SELECT return_pct, rugged FROM outcomes WHERE horizon_h=?", (h,)).fetchall()
                rets = [r["return_pct"] for r in rows]
                n = len(rets)
                per[h] = {
                    "n": n,
                    "win_rate": (sum(1 for x in rets if x > 0) / n) if n else None,
                    "avg_return": (sum(rets) / n) if n else None,
                    "median_return": statistics.median(rets) if n else None,
                    "best": max(rets) if n else None,
                    "worst": min(rets) if n else None,
                    "rug_rate": (sum(r["rugged"] for r in rows) / n) if n else None,
                }
        return {"signals": total, "horizons": per}

    # ---- observations: every token that passed the cheap filters, with a price path for later analysis
    def add_observation(self, *, mint, symbol, pair_address, ts, price, liquidity, score, stage, features) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO observations(mint,symbol,pair_address,ts,price,liquidity,score,stage,features)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (mint, symbol, pair_address, int(ts), price, liquidity, score, stage, json.dumps(features)))
            oid = cur.lastrowid
            c.execute("INSERT INTO ticks(obs_id,ts,price,liquidity) VALUES(?,?,?,?)", (oid, int(ts), price, liquidity))
            return oid

    def update_observation(self, obs_id: int, stage: str, extra: dict | None = None) -> None:
        with self._conn() as c:
            row = c.execute("SELECT features FROM observations WHERE id=?", (obs_id,)).fetchone()
            if not row:
                return
            feats = json.loads(row["features"])
            feats.update(extra or {})
            c.execute("UPDATE observations SET stage=?, features=? WHERE id=?", (stage, json.dumps(feats), obs_id))

    def recently_observed(self, since: float) -> set[str]:
        with self._conn() as c:
            return {r["mint"] for r in c.execute("SELECT DISTINCT mint FROM observations WHERE ts>=?", (int(since),))}

    def open_observations(self, now: float, hours: float) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT id, mint, ts FROM observations WHERE ts>=?", (int(now - hours * 3600),))]

    def add_ticks(self, rows: list[tuple]) -> None:
        with self._conn() as c:
            c.executemany("INSERT OR IGNORE INTO ticks(obs_id,ts,price,liquidity) VALUES(?,?,?,?)",
                          [(o, int(t), p, l) for o, t, p, l in rows])

    def load_observations(self) -> list[dict]:
        """All observations with their tick paths (sorted by time)."""
        with self._conn() as c:
            obs = [dict(r) for r in c.execute("SELECT * FROM observations ORDER BY ts, id")]
            by = {o["id"]: o for o in obs}
            for o in obs:
                o["features"] = json.loads(o["features"])
                o["ticks"] = []
            for r in c.execute("SELECT obs_id, ts, price, liquidity FROM ticks ORDER BY obs_id, ts"):
                by[r["obs_id"]]["ticks"].append((r["ts"], r["price"], r["liquidity"]))
            return obs

    def count_observations(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) n FROM observations").fetchone()["n"]

    def prune_observations(self, before: float) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM observations WHERE ts<?", (int(before),))

    # ---- exit alerts
    def recent_signals(self, since: float) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT id,mint,symbol,pair_address,entry_price,entry_liquidity,created_at FROM signals WHERE created_at>=?",
                (int(since),))]

    def has_exit_alert(self, signal_id: int, kind: str) -> bool:
        with self._conn() as c:
            return c.execute("SELECT 1 FROM exit_alerts WHERE signal_id=? AND kind=?", (signal_id, kind)).fetchone() is not None

    def add_exit_alert(self, signal_id: int, kind: str, ts: float) -> None:
        with self._conn() as c:
            c.execute("INSERT OR IGNORE INTO exit_alerts(signal_id,kind,ts) VALUES(?,?,?)", (signal_id, kind, int(ts)))

    # ---- strategy-test progress (cheap SQL, safe to call from the dashboard)
    def lab_progress(self, now: float, horizon_h: int, min_score: float) -> dict:
        with self._conn() as c:
            total = c.execute("SELECT COUNT(*) n FROM observations").fetchone()["n"]
            first = c.execute("SELECT MIN(ts) t FROM observations").fetchone()["t"]
            watching = c.execute("SELECT COUNT(*) n FROM observations WHERE ts>=?", (int(now - 24 * 3600),)).fetchone()["n"]
            done = lambda extra: c.execute(  # noqa: E731
                "SELECT COUNT(*) n FROM observations o WHERE o.stage!='onchain_fail' " + extra +
                " AND EXISTS(SELECT 1 FROM ticks t WHERE t.obs_id=o.id AND t.ts>=o.ts+?)", (horizon_h * 3600,)).fetchone()["n"]
            completed = done("")
            c2 = c.execute(
                "SELECT COUNT(*) n FROM observations o WHERE o.stage!='onchain_fail' AND o.score>=?"
                " AND EXISTS(SELECT 1 FROM ticks t WHERE t.obs_id=o.id AND t.ts>=o.ts+?)", (min_score, horizon_h * 3600)).fetchone()["n"]
        return {"observations": total, "watching_now": watching, "completed": completed, "completed_candidates": c2,
                "hours_running": (now - first) / 3600 if first else 0.0, "horizon_h": horizon_h, "min_score": min_score}

    # ---- evaluations (every token that passed the cheap filters, for later tuning)
    def add_evaluation(self, mint, symbol, ts, score, price, passed: bool, detail) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO evaluations(mint,symbol,ts,score,price,passed,detail) VALUES(?,?,?,?,?,?,?)",
                      (mint, symbol, int(ts), score, price, int(passed), json.dumps(detail)))

    def prune_evaluations(self, before: float) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM evaluations WHERE ts<?", (int(before),))
