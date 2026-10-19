"""Shared scan loop used by `run` and `serve`, plus the status object the dashboard reads."""
from __future__ import annotations

import threading
import time
from dataclasses import asdict

from . import tracker
from .engine import Engine, ScanResult


class Status:
    def __init__(self):
        self.lock = threading.Lock()
        self.started_at = time.time()
        self.scans = 0
        self.last_scan_at: float | None = None
        self.last: dict | None = None
        self.last_error = ""
        self.demo = False

    def record(self, res: ScanResult, outcomes: int) -> None:
        with self.lock:
            self.scans += 1
            self.last_scan_at = time.time()
            d = asdict(res)
            d["signals"] = len(res.signals)
            d["outcomes_recorded"] = outcomes
            self.last, self.last_error = d, ""

    def fail(self, err: str) -> None:
        with self.lock:
            self.last_error = err

    def snapshot(self) -> dict:
        with self.lock:
            return {"started_at": self.started_at, "scans": self.scans, "last_scan_at": self.last_scan_at,
                    "last": self.last, "last_error": self.last_error, "demo": self.demo}


def run_cycle(engine: Engine, status: Status) -> ScanResult | None:
    try:
        res = engine.scan_once()
        n = tracker.resolve(engine.cfg, engine.store, engine.source)
        status.record(res, n)
        return res
    except Exception as e:  # one bad cycle must never kill the worker
        status.fail(repr(e)[:200])
        return None


def loop(engine: Engine, status: Status, stop: threading.Event, on_cycle=None) -> None:
    while not stop.is_set():
        res = run_cycle(engine, status)
        if on_cycle:
            on_cycle(res, status)
        stop.wait(engine.cfg.scan_interval_s)
