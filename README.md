# The Backtest Room — Telegram bot (Beta v1.0)

Telegram: **@soley_backtest_room_bot**

Logs backtest trades and tracks prop-firm trailing drawdown (Intraday like Apex, or EOD).

## Commands
- `/start` — new backtest (Intraday/EOD → Target → Drawdown → SL → TP → RR → confirm)
- `/restart` — start over
- `/print` — performance sheet + CSV trade log

## How it runs
GitHub Actions runs `bot.py` (see `.github/workflows/bot.yml`). A new run starts every 4 hours and
replaces the old one, so the bot stays online. Backtests are saved to `data/backtests.json` every minute.

The bot token is stored in **Settings → Secrets and variables → Actions → `BOT_TOKEN`** (never in the code).

To start it manually: **Actions → Run Backtest Room bot → Run workflow**.

## Math
- P&L starts at $0. TP adds TP size, SL subtracts SL size. Threshold starts at −Drawdown.
- Intraday: threshold = highest P&L − Drawdown, after every trade.
- EOD: threshold moves only when the day changes, using the previous day's closing P&L.
- Threshold stops trailing at +$100 (`LOCK_AT` in `engine.py`).
- Blown when P&L ≤ threshold. Passed when P&L ≥ target.
