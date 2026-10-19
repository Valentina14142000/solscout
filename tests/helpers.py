import tempfile
from pathlib import Path

from solscout.config import Config
from solscout.engine import Engine
from solscout.fixtures import FixtureSafety, FixtureSource
from solscout.store import Store

NOW = 1_800_000_000.0


class Sink:
    def __init__(self, ok=True):
        self.sent, self.ok = [], ok

    def send(self, text):
        self.sent.append(text)
        return self.ok


def make(cfg=None, notifier=None, safety=None):
    cfg = cfg or Config()
    cfg.db_path = str(Path(tempfile.mkdtemp()) / "t.db")
    clock = [NOW]
    store, src = Store(cfg.db_path), FixtureSource(NOW)
    sink = notifier or Sink()
    eng = Engine(cfg, store, src, safety or FixtureSafety(), sink, clock=lambda: clock[0])
    return eng, store, src, sink, clock, cfg
