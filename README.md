# SolScout

A signal bot for Solana tokens. It watches new and trending pairs, throws out the obvious traps (rugs, honeypots, unrevoked authorities, concentrated holders), scores what is left, and alerts you with the reasons. Every signal is **paper-traded**, so you can measure whether the bot has any edge before putting money anywhere near it.


![Dashboard](docs/screenshots/dashboard.png)

*The screenshot shows built-in demo data, not a real market.*

## How it works

```
 DexScreener ─┐                         ┌─ hard filters: liquidity, age, activity, honeypot
 GeckoTerminal├─► candidates ─► market ─┤
              │                  data   └─ score 0-100 with reasons
 Solana RPC ──┬─► on-chain safety: mint / freeze authority, Token-2022, holder concentration
 RugCheck ────┘   (RugCheck is the fallback when the RPC node rate-limits)
                                   │
                signals (cooldown + daily cap) ─► Telegram / dashboard
                                   │
                paper tracker: result at 1h / 6h / 24h, rug detection, costs deducted
```

Score components (max points): momentum 25, volume acceleration 25, buy pressure 15, liquidity 15, age 10, on-chain safety 10. A token already up hundreds of percent over 6 hours is penalised as late. If the on-chain check cannot run, the token is **not** signalled; it shows up under "Closest misses" with the reason.

## Try it (no network, no keys)

```bash
python3 -m solscout demo             # whole pipeline on sample tokens, in the terminal
python3 -m solscout serve --demo     # the dashboard with sample data
```

## Run it for real

No dependencies to install: it uses only the Python 3.12+ standard library.

```bash
cp .env.example .env
python3 -m solscout check            # which data sources work from this machine
python3 -m solscout serve            # dashboard at http://127.0.0.1:8080 + scanning worker
```

Other commands: `scan` (one scan, with near-misses printed), `run` (worker only), `signals`, `stats`.

### Recommended setup

1. **A dedicated RPC URL.** The public Solana node returns HTTP 429 quickly. Get a free key at [helius.dev](https://www.helius.dev), then set `SOLSCOUT_RPC_URL` in `.env`.
2. **Telegram alerts.** Create a bot with @BotFather, then set `SOLSCOUT_TELEGRAM_TOKEN` and `SOLSCOUT_TELEGRAM_CHAT_ID` (steps are in `.env.example`). Keep `.env` out of git.
3. **Leave it running.** Paper results only mean something after dozens of signals, which takes days.

## Reading the results honestly

- Paper results subtract 3% per trade for fees and slippage, and count a liquidity collapse as a 100% loss.
- Ten results is noise. Wait for dozens.
- Paper fills assume you could trade at the signal price. Real fills on thin pools are worse.
- "Holders ranked 2 to 10" excludes the largest holder because that is usually the liquidity pool. It is a heuristic, not a guarantee.
- A quiet dashboard is normal. The filters are strict on purpose.

## Deploying

The worker has to stay running, and its SQLite file has to survive restarts, so free "sleeping" web hosts are a poor fit (they stop scanning and wipe the data).

- **Your own computer or a small VPS:** `python3 -m solscout serve`. Simplest, and the data stays in `solscout.db`.
- **Docker with a volume** (Fly.io, Railway, any VPS):
  ```bash
  docker build -t solscout .
  docker run -d -p 8080:8080 -v solscout-data:/data --env-file .env solscout
  ```
- **Always set `SOLSCOUT_DASHBOARD_PASSWORD`** before exposing the dashboard to the internet. `/health` stays open for platform health checks.

## Configuration

Everything is a `SOLSCOUT_*` environment variable; defaults are in `solscout/config.py`. The ones you are most likely to change: `SIGNAL_THRESHOLD` (70), `MIN_LIQUIDITY_USD` (15000), `MAX_SIGNALS_PER_DAY` (10), `SCAN_INTERVAL_S` (60), `PAPER_COST_PCT` (3).

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

## Roadmap

- [x] Engine: filters, scoring, signals, paper tracker, Telegram, CLI
- [x] Dashboard, RPC rate-limit handling, RugCheck fallback, near-miss visibility
- [ ] Weight tuning from collected paper results
- [ ] Optional on-chain new-pool listener for lower latency

## License

MIT
