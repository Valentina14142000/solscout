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
"""


class Store:
    def __init__(self, path: str = "solscout.db"):
        self.path = path
        with self._conn() as c:
            c.executescript(SCHEMA)

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
                   reasons, warnings, created_at) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO signals(mint,symbol,pair_address,score,entry_price,entry_liquidity,reasons,warnings,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (mint, symbol, pair_address, score, entry_price, entry_liquidity,
                 json.dumps(reasons), json.dumps(warnings), int(created_at)))
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

    # ---- evaluations (every token that passed the cheap filters, for later tuning)
    def add_evaluation(self, mint, symbol, ts, score, price, passed: bool, detail) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO evaluations(mint,symbol,ts,score,price,passed,detail) VALUES(?,?,?,?,?,?,?)",
                      (mint, symbol, int(ts), score, price, int(passed), json.dumps(detail)))

    def prune_evaluations(self, before: float) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM evaluations WHERE ts<?", (int(before),))
