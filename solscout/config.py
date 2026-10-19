"""Settings. Every value can be overridden with a SOLSCOUT_* environment variable or a .env file."""
from __future__ import annotations

import os
from dataclasses import dataclass, fields
from pathlib import Path


def load_dotenv(path: str = ".env") -> None:
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


@dataclass
class Config:
    # --- storage / loop
    db_path: str = "solscout.db"
    scan_interval_s: int = 60
    max_candidates: int = 120          # tokens examined per scan
    max_safety_checks: int = 15        # on-chain lookups per scan (public RPC is rate limited)

    # --- hard filters (a token failing any of these is dropped)
    min_liquidity_usd: float = 15_000
    min_age_minutes: float = 15
    max_age_hours: float = 72
    min_txns_h1: int = 30
    honeypot_buys: int = 20            # this many buys with zero sells in the last hour = cannot sell
    max_top_holders_pct: float = 35.0  # holders ranked 2-10, as % of supply
    require_safety: bool = True        # drop tokens whose on-chain check failed
    allow_token_2022: bool = False     # Token-2022 extensions can include transfer fees / delegates

    # --- signals
    signal_threshold: float = 70
    cooldown_hours: float = 6
    max_signals_per_day: int = 10

    # --- paper trading
    horizons_h: tuple[int, ...] = (1, 6, 24)
    paper_cost_pct: float = 3.0        # round-trip fees + slippage deducted from every paper result
    rug_liquidity_drop: float = 0.10   # liquidity under 10% of entry counts as a rug

    # --- integrations
    rpc_url: str = "https://api.mainnet-beta.solana.com"
    telegram_token: str = ""
    telegram_chat_id: str = ""
    http_timeout_s: float = 12.0

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()
        cfg = cls()
        for f in fields(cls):
            raw = os.environ.get("SOLSCOUT_" + f.name.upper())
            if raw is None or raw == "":
                continue
            cur = getattr(cfg, f.name)
            if isinstance(cur, bool):
                val = raw.lower() in ("1", "true", "yes", "on")
            elif isinstance(cur, int):
                val = int(float(raw))
            elif isinstance(cur, float):
                val = float(raw)
            elif isinstance(cur, tuple):
                val = tuple(int(x) for x in raw.split(",") if x.strip())
            else:
                val = raw
            setattr(cfg, f.name, val)
        return cfg
