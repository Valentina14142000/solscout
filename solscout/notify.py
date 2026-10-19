from __future__ import annotations

from .http import request_json
from .models import SourceError


def format_signal(sig: dict, now_label: str = "") -> str:
    lines = [f"SIGNAL  {sig['symbol']}  score {sig['score']:.0f}/100", ""]
    lines += [f"+ {r}" for r in sig["reasons"]]
    lines += [f"! {w}" for w in sig["warnings"]]
    lines += [
        "",
        f"Entry (paper): ${sig['entry_price']:.8g}",
        f"Chart: https://dexscreener.com/solana/{sig['pair_address']}",
        f"Check: https://rugcheck.xyz/tokens/{sig['mint']}",
        "",
        "Automated analysis, not advice. Tracked as a paper trade only.",
    ]
    return "\n".join(lines)


class ConsoleNotifier:
    def send(self, text: str) -> bool:
        print("\n" + text + "\n")
        return True


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, timeout: float = 12.0):
        self.token, self.chat_id, self.timeout = token, chat_id, timeout

    def send(self, text: str) -> bool:
        try:
            out = request_json(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                body={"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True},
                timeout=self.timeout)
            return bool(out.get("ok"))
        except SourceError:
            return False


def make_notifier(cfg):
    if cfg.telegram_token and cfg.telegram_chat_id:
        return TelegramNotifier(cfg.telegram_token, cfg.telegram_chat_id, cfg.http_timeout_s)
    return ConsoleNotifier()
