from solscout import cli, notify
from solscout.models import SourceError


def with_fake(fn_json, run):
    old = notify.request_json
    notify.request_json = fn_json
    try:
        return run()
    finally:
        notify.request_json = old


def test_find_chat_id_uses_the_most_recent_message():
    payload = {"ok": True, "result": [
        {"message": {"chat": {"id": 111, "first_name": "Old"}}},
        {"message": {"chat": {"id": 222, "first_name": "Valentina"}}}]}
    assert with_fake(lambda url, **k: payload, lambda: notify.find_chat_id("T")) == ("222", "Valentina")


def test_find_chat_id_when_nobody_has_written_yet():
    assert with_fake(lambda url, **k: {"ok": True, "result": []}, lambda: notify.find_chat_id("T")) == (None, "")
    assert with_fake(lambda url, **k: {"ok": True, "result": [{"my_chat_member": {}}]}, lambda: notify.find_chat_id("T")) == (None, "")


def test_telegram_notifier_reports_failure():
    def boom(url, **k):
        raise SourceError("api.telegram.org: HTTP 400")
    assert with_fake(boom, lambda: notify.TelegramNotifier("T", "1").send("x")) is False
    assert with_fake(lambda url, **k: {"ok": True}, lambda: notify.TelegramNotifier("T", "1").send("x")) is True


def test_error_messages_never_contain_the_token():
    import io
    import os
    import contextlib
    os.environ["SOLSCOUT_TELEGRAM_TOKEN"] = "SECRET-TOKEN-123"
    os.environ.pop("SOLSCOUT_TELEGRAM_CHAT_ID", None)

    def boom(url, **k):
        raise SourceError("api.telegram.org: HTTP 401")
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            code = with_fake(boom, lambda: cli.cmd_telegram(None))
    finally:
        os.environ.pop("SOLSCOUT_TELEGRAM_TOKEN", None)
    assert code == 1 and "SECRET-TOKEN-123" not in buf.getvalue()


def test_telegram_command_without_token_explains_what_to_do():
    import io
    import os
    import contextlib
    os.environ.pop("SOLSCOUT_TELEGRAM_TOKEN", None)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.cmd_telegram(None)
    assert code == 1 and "SOLSCOUT_TELEGRAM_TOKEN" in buf.getvalue()
