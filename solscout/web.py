"""Tiny dashboard server (standard library only): one HTML page plus a read-only JSON API."""
from __future__ import annotations

import base64
import hmac
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import Config
from .runner import Status
from .store import Store

PAGE = Path(__file__).with_name("static") / "dashboard.html"
CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
       "connect-src 'self'; img-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def status_payload(cfg: Config, status: Status) -> dict:
    snap = status.snapshot()
    snap["now"] = time.time()
    snap["config"] = {
        "signal_threshold": cfg.signal_threshold, "min_liquidity_usd": cfg.min_liquidity_usd,
        "scan_interval_s": cfg.scan_interval_s, "paper_cost_pct": cfg.paper_cost_pct,
        "telegram": bool(cfg.telegram_token and cfg.telegram_chat_id),
        "dedicated_rpc": "mainnet-beta" not in cfg.rpc_url,
        "horizons_h": list(cfg.horizons_h),
    }
    return snap


def make_server(cfg: Config, store: Store, status: Status, host: str | None = None, port: int | None = None):
    password = cfg.dashboard_password

    class Handler(BaseHTTPRequestHandler):
        server_version = "SolScout"

        def log_message(self, *_):  # keep the console for scan output
            pass

        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", CSP)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200):
            self._send(code, json.dumps(obj).encode(), "application/json")

        def _authorised(self) -> bool:
            if not password:
                return True
            h = self.headers.get("Authorization", "")
            if h.startswith("Basic "):
                try:
                    given = base64.b64decode(h[6:]).decode("utf-8", "replace").partition(":")[2]
                    return hmac.compare_digest(given.encode(), password.encode())
                except ValueError:
                    return False
            return False

        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/health":
                return self._json({"ok": True})
            if not self._authorised():
                return self._send(401, b"Password required", "text/plain",
                                  {"WWW-Authenticate": 'Basic realm="SolScout"'})
            q = parse_qs(url.query)
            if url.path == "/":
                return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if url.path == "/api/status":
                return self._json(status_payload(cfg, status))
            if url.path == "/api/signals":
                try:
                    limit = max(1, min(200, int(q.get("limit", ["50"])[0])))
                except ValueError:
                    limit = 50
                return self._json(store.list_signals(limit))
            if url.path == "/api/lab":
                return self._json(store.lab_progress(time.time(), 6, cfg.signal_threshold - 10))
            if url.path == "/api/stats":
                return self._json(store.stats(cfg.horizons_h))
            self._json({"error": "not found"}, 404)

    return ThreadingHTTPServer((host or cfg.host, cfg.port if port is None else port), Handler)
