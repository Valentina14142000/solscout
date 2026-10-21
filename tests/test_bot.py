import tempfile
from pathlib import Path

from solscout import bot
from solscout.config import Config
from solscout.models import SourceError
from solscout.runner import Status
from solscout.store import Store

OWNER = "222"


def make(tmp=None):
    cfg = Config(db_path=str(Path(tmp or tempfile.mkdtemp()) / "t.db"), telegram_token="TOK", telegram_chat_id=OWNER)
    store, status = Store(cfg.db_path), Status()
    return cfg, store, status, bot.Responder(cfg, store, status)


def test_strangers_get_no_data_but_see_their_chat_id():
    _, store, _, r = make()
    store.add_signal(mint="M", symbol="SECRET", pair_address="P", score=80, entry_price=1.0, entry_liquidity=50000,
                     reasons=["x"], warnings=[], created_at=1.0)
    out = r.reply("999", "/signals")
    assert "SECRET" not in out and "999" in out and "private" in out


def test_owner_commands():
    _, store, _, r = make()
    assert "/status" in r.reply(OWNER, "/start") and "connected" in r.reply(OWNER, "/start")
    assert "/signals" in r.reply(OWNER, "/help")
    assert "No signals yet" in r.reply(OWNER, "/signals")
    assert "No signals yet" in r.reply(OWNER, "/stats")
    assert "No scan has finished" in r.reply(OWNER, "/status")
    store.add_signal(mint="M", symbol="KITE", pair_address="P", score=81, entry_price=1.0, entry_liquidity=50000,
                     reasons=["x"], warnings=[], created_at=r.clock() - 3600)
    out = r.reply(OWNER, "/signals")
    assert "KITE" in out and "score 81" in out and "pending" in out
    assert "1 signals" in r.reply(OWNER, "/stats") or "1 signals" in r.reply(OWNER, "/status")


def test_command_forms_and_plain_text():
    _, _, _, r = make()
    assert "/status" in r.reply(OWNER, "/help@Soscout_bot")
    assert "/HELP" not in r.reply(OWNER, "/HELP") and "/signals" in r.reply(OWNER, "/HELP")
    assert "do not know that command" in r.reply(OWNER, "/buy")
    assert "only understand commands" in r.reply(OWNER, "hello")
    assert "only understand commands" in r.reply(OWNER, "")


def test_status_after_a_scan():
    from solscout.engine import ScanResult
    _, _, status, r = make()
    status.record(ScanResult(candidates=84, evaluated=11), 0)
    out = r.reply(OWNER, "/status")
    assert "84 candidates" in out and "11 passed" in out and "1 scans" in out


class FakeTelegram:
    def __init__(self, batches):
        self.batches, self.sent, self.calls = list(batches), [], []

    def __call__(self, url, **k):
        method = url.rsplit("/", 1)[1]
        self.calls.append((method, k.get("body")))
        if method == "getUpdates":
            return {"ok": True, "result": self.batches.pop(0) if self.batches else []}
        if method == "sendMessage":
            self.sent.append(k["body"])
        return {"ok": True}


def with_fake(fake, run):
    old = bot.request_json
    bot.request_json = fake
    try:
        return run()
    finally:
        bot.request_json = old


def test_listener_answers_and_advances_offset():
    cfg, store, status, r = make()
    fake = FakeTelegram([[
        {"update_id": 10, "message": {"chat": {"id": 222}, "text": "/help"}},
        {"update_id": 11, "message": {"chat": {"id": 999}, "text": "/signals"}},
        {"update_id": 12, "message": {"chat": {"id": 222}, "photo": []}},   # no text: ignored
        {"update_id": 13, "edited_message": {"chat": {"id": 222}, "text": "x"}},  # not a message: ignored
    ]])
    lst = bot.BotListener(cfg, r, log=lambda *_: None)
    assert with_fake(fake, lambda: lst.poll_once(0)) == 2
    assert lst.offset == 14
    assert [m["chat_id"] for m in fake.sent] == [222, 999]
    assert "private bot" in fake.sent[1]["text"]
    assert with_fake(fake, lambda: lst.poll_once(0)) == 0
    assert fake.calls[-1][1]["offset"] == 14


def test_a_failed_send_does_not_stop_the_batch():
    cfg, _, _, r = make()
    state = {"n": 0}

    def fn(url, **k):
        if url.endswith("getUpdates"):
            return {"result": [{"update_id": 1, "message": {"chat": {"id": 222}, "text": "/help"}},
                               {"update_id": 2, "message": {"chat": {"id": 222}, "text": "/help"}}]}
        state["n"] += 1
        if state["n"] == 1:
            raise SourceError("api.telegram.org: HTTP 400")
        return {"ok": True}
    lst = bot.BotListener(cfg, r, log=lambda *_: None)
    assert with_fake(fn, lambda: lst.poll_once(0)) == 1 and lst.offset == 3


def test_conflict_hint_is_logged_once_and_token_never_logged():
    cfg, _, _, r = make()
    logs, stop = [], __import__("threading").Event()

    def fn(url, **k):
        if url.endswith("getUpdates"):
            if len(logs) >= 1:
                stop.set()
            raise SourceError("api.telegram.org: HTTP 409")
        return {"ok": True}
    lst = bot.BotListener(cfg, r, log=logs.append)
    stop.wait = lambda s: None
    old = bot.request_json
    bot.request_json = fn
    try:
        lst.run(stop)
    finally:
        bot.request_json = old
    assert len(logs) == 1 and "another copy" in logs[0].lower() and "TOK" not in logs[0]


def test_listener_only_starts_when_configured():
    import threading
    cfg = Config(db_path=str(Path(tempfile.mkdtemp()) / "t.db"))
    assert bot.start_listener(cfg, Store(cfg.db_path), Status(), threading.Event()) is None


def test_commands_menu_is_registered():
    cfg, _, _, r = make()
    fake = FakeTelegram([])
    with_fake(fake, lambda: bot.BotListener(cfg, r).register_commands())
    method, body = fake.calls[0]
    assert method == "setMyCommands" and {c["command"] for c in body["commands"]} == {"status", "signals", "stats", "help"}
