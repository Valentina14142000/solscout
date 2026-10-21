"""Two-way Telegram bot: answers commands sent to it. Alerts still go out through notify.py.

Only the configured chat gets data. Anyone else who finds the bot gets a short refusal and their own chat id
(which is harmless, and is what the owner needs during setup)."""
from __future__ import annotations

import threading
import time

from .config import Config
from .http import request_json
from .models import SourceError

COMMANDS = [
    ("status", "Is the bot running, and what did the last scan see"),
    ("signals", "Latest signals and how they are doing"),
    ("stats", "Paper-trade results so far"),
    ("help", "What this bot does"),
]

HELP = (
    "SolScout watches new and trending Solana tokens, filters out likely rugs and honeypots, and sends a "
    "signal when one looks interesting.\n\n"
    "/status  is it running, last scan\n"
    "/signals  latest signals\n"
    "/stats  paper-trade results\n"
    "/help  this message\n\n"
    "Alerts only: it never trades and never asks for your wallet or keys. Automated analysis, not financial "
    "advice. Most new tokens lose money."
)


def _ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f"{seconds}s ago"
    if seconds < 5400:
        return f"{seconds // 60} min ago"
    if seconds < 172800:
        return f"{seconds / 3600:.1f} h ago"
    return f"{seconds // 86400} days ago"


class Responder:
    """Turns an incoming message into a reply. Pure apart from reading the store and status."""

    def __init__(self, cfg: Config, store, status, clock=time.time):
        self.cfg, self.store, self.status, self.clock = cfg, store, status, clock

    def reply(self, chat_id: str, text: str) -> str:
        if str(chat_id) != str(self.cfg.telegram_chat_id):
            return f"This is a private bot. If you are its owner, your chat ID is {chat_id}."
        parts = (text or "").strip().split()
        cmd = parts[0].lower().lstrip("/").split("@")[0] if parts and parts[0].startswith("/") else ""
        handler = {"start": self._start, "help": self._help, "status": self._status,
                   "signals": self._signals, "stats": self._stats}.get(cmd)
        if handler:
            return handler()
        if cmd:
            return "I do not know that command.\n\n" + HELP
        return "I only understand commands, I am not a chat bot.\n\n" + HELP

    def _start(self) -> str:
        return "SolScout is connected.\n\n" + HELP

    def _help(self) -> str:
        return HELP

    def _status(self) -> str:
        s, now = self.status.snapshot(), self.clock()
        lines = [f"Running for {_ago(now - s['started_at']).replace(' ago', '')}, {s['scans']} scans so far."]
        if s["last_scan_at"]:
            last = s["last"] or {}
            lines.append(f"Last scan {_ago(now - s['last_scan_at'])}: {last.get('candidates', 0)} candidates, "
                         f"{last.get('evaluated', 0)} passed the filters, {last.get('signals', 0)} signals.")
        else:
            lines.append("No scan has finished yet.")
        if s["last_error"]:
            lines.append(f"Last error: {s['last_error']}")
        lines.append(f"Strategy lab: {self.store.count_observations()} tokens recorded, "
                     f"{self.store.stats(self.cfg.horizons_h)['signals']} signals.")
        lines.append(f"Signals fire at score {self.cfg.signal_threshold:.0f} or more.")
        return "\n".join(lines)

    def _signals(self) -> str:
        rows = self.store.list_signals(5)
        if not rows:
            return ("No signals yet. That is normal: the filters are strict, and a quiet day means nothing "
                    "looked good enough.")
        out = ["Latest signals (paper trades):"]
        for r in rows:
            res = " ".join(f"{h}h {o['return_pct']:+.0f}%" for h, o in sorted(r["outcomes"].items())) or "pending"
            when = _ago(self.clock() - r["created_at"])
            out.append(f"{r['symbol']}  score {r['score']:.0f}  {when}  {res}")
        return "\n".join(out)

    def _stats(self) -> str:
        st = self.store.stats(self.cfg.horizons_h)
        if not st["signals"]:
            return "No signals yet, so there are no results to show."
        out = [f"Paper-trade results: {st['signals']} signals, after {self.cfg.paper_cost_pct:.0f}% costs."]
        for h, s in st["horizons"].items():
            out.append(f"{h}h: no results yet" if not s["n"] else
                       f"{h}h: {s['n']} done, {s['win_rate'] * 100:.0f}% won, average {s['avg_return']:+.0f}%, "
                       f"median {s['median_return']:+.0f}%, worst {s['worst']:+.0f}%")
        out.append("A handful of results proves nothing. Wait for dozens.")
        return "\n".join(out)


class BotListener:
    """Long-polls Telegram for new messages and answers them. Runs in a daemon thread."""

    def __init__(self, cfg: Config, responder: Responder, log=print):
        self.cfg, self.responder, self.log = cfg, responder, log
        self.base = f"https://api.telegram.org/bot{cfg.telegram_token}"
        self.offset = 0
        self._warned = False

    def _call(self, method: str, body: dict, timeout: float):
        return request_json(f"{self.base}/{method}", body=body, timeout=timeout, retries=0)

    def register_commands(self) -> None:
        try:
            self._call("setMyCommands", {"commands": [{"command": c, "description": d} for c, d in COMMANDS]},
                       self.cfg.http_timeout_s)
        except SourceError:
            pass  # cosmetic: the bot works without the menu

    def poll_once(self, wait_s: int = 25) -> int:
        """Fetch pending messages, answer them, and return how many were handled."""
        out = self._call("getUpdates", {"offset": self.offset, "timeout": wait_s, "allowed_updates": ["message"]},
                         wait_s + 10)
        handled = 0
        for upd in out.get("result", []) if isinstance(out, dict) else []:
            self.offset = max(self.offset, int(upd.get("update_id", 0)) + 1)
            msg = upd.get("message") or {}
            chat, text = (msg.get("chat") or {}), msg.get("text")
            if "id" not in chat or not isinstance(text, str):
                continue
            try:
                answer = self.responder.reply(str(chat["id"]), text)
                self._call("sendMessage", {"chat_id": chat["id"], "text": answer, "disable_web_page_preview": True},
                           self.cfg.http_timeout_s)
                handled += 1
            except SourceError:
                pass
            except Exception as e:  # a bug in one reply must not stop the bot answering the next message
                self.log(f"bot: reply failed ({e!r})")
        return handled

    def run(self, stop: threading.Event) -> None:
        self.register_commands()
        while not stop.is_set():
            try:
                self.poll_once()
                self._warned = False
            except SourceError as e:
                if not self._warned:
                    hint = (" Another copy of SolScout is probably running with the same bot token; "
                            "only one can listen at a time.") if "409" in str(e) else ""
                    self.log(f"bot: cannot read Telegram messages ({e}).{hint}")
                    self._warned = True
                stop.wait(5)


def start_listener(cfg: Config, store, status, stop: threading.Event, log=print) -> threading.Thread | None:
    if not (cfg.telegram_token and cfg.telegram_chat_id):
        return None
    t = threading.Thread(target=BotListener(cfg, Responder(cfg, store, status), log).run, args=(stop,), daemon=True)
    t.start()
    return t
