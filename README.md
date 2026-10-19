# SolScout

A signal bot for Solana tokens. It watches new and trending pairs, throws out the obvious traps (rugs, honeypots, unrevoked authorities, concentrated holders), scores what is left, and alerts you with the reasons. Every signal is **paper-traded**, so you can measure whether the bot has any edge before putting money anywhere near it.

> **Read this first.** SolScout never holds keys and never buys anything. It uses public APIs, so it will always see a token seconds to minutes after on-chain snipers do. Its value is filtering and discipline, not speed. Most new Solana tokens lose money. Nothing here is financial advice.

## How it works

```
 DexScreener ─┐                         ┌─ hard filters: liquidity, age, activity, honeypot
 GeckoTerminal├─► candidates ─► market ─┤
              │                  data   └─ score 0-100 with reasons
 Solana RPC ──┴─► on-chain safety: mint / freeze authority, Token-2022, holder concentration
                                   │
                signals (cooldown + daily cap) ─► Telegram / console
                                   │
                paper tracker: result at 1h / 6h / 24h, rug detection, costs deducted
```

Score components (max points): momentum 25, volume acceleration 25, buy pressure 15, liquidity 15, age 10, on-chain safety 10. A token already up hundreds of percent over 6 hours is penalised as late.

## Try it (no network, no keys)

```bash
python3 -m solscout demo
```

Runs the whole pipeline on built-in sample tokens: one genuine candidate, plus a honeypot, an active freeze authority, a thin pool, a late pump and a quiet token, all of which get rejected.

## Run it live

```bash
cp .env.example .env
python3 -m solscout check      # tells you which data sources work from your machine
python3 -m solscout scan       # one scan
python3 -m solscout run        # scan every 60s, resolve paper trades
python3 -m solscout signals    # recent signals and their results
python3 -m solscout stats      # win rate, average and median return, rug rate
```

No dependencies to install: it uses only the Python 3.12+ standard library.

For Telegram alerts, fill in `SOLSCOUT_TELEGRAM_TOKEN` and `SOLSCOUT_TELEGRAM_CHAT_ID` in `.env` (steps are in `.env.example`). Keep that file out of git.

## Reading the results honestly

- Paper results subtract 3% per trade for fees and slippage, and count a liquidity collapse as a 100% loss.
- Dozens of results are the minimum before any number means something. Ten is noise.
- Paper fills assume you could trade at the signal price. Real fills on thin pools are worse.
- "Holders ranked 2 to 10" excludes the largest holder because that is usually the liquidity pool. It is a heuristic, not a guarantee.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

## Roadmap

- [x] Engine: filters, scoring, signals, paper tracker, Telegram, CLI
- [ ] Web dashboard (live signals, scores with reasons, paper P&L)
- [ ] Always-on deployment
- [ ] Weight tuning from collected results

## License

MIT
