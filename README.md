# Polymarket 10% Take-Profit Bot

A Polymarket trading bot that trades normally but **closes positions at +10%
profit** instead of waiting for market resolution. Ships with an optional
stop-loss and a Streamlit dashboard.

By default it runs in **Live Data Paper Trading** mode: it reads real
Polymarket prices but simulates fills locally. **No real orders are ever
placed** unless you explicitly switch to Live mode *and* enable real orders.

## Market universe

By default the bot only trades **BTC and ETH short-duration up/down markets**
on the **5-minute and 15-minute** timeframes. Assets and timeframes are
configurable from the dashboard sidebar (or `--assets` / `--timeframes` on the
CLI), and the filter can be turned off to trade any market. Matching is done on
the market question and slug (e.g. `Bitcoin Up or Down — 5 minute`).

## Features

- **Take-profit on token price gain** — `take_profit_price = entry_price × 1.10`
  (e.g. entry `0.50` → TP `0.55`; entry `0.60` → TP `0.66`).
- **No waiting for resolution** — a position is closed the moment its mark
  price reaches the take-profit level.
- **Optional stop-loss** — default `stop_loss_price = entry_price × 0.90`
  (−10%). Can be toggled off entirely.
- **Dashboard** showing, per open position: entry price, current mark price,
  take-profit price, stop-loss price, unrealized PnL, and exit reason labels.
- **Closed-trades table** showing entry time, close time, entry price, close
  price, profit %, and exit reason
  (`TAKE_PROFIT_10_PERCENT` / `STOP_LOSS_10_PERCENT`).
- **Sidebar settings** — Take Profit % (default 10) and Stop Loss % (default 10),
  plus position sizing, entry band, and trading mode.

## Quick start

```bash
pip install -r requirements.txt

# Dashboard
streamlit run app.py

# Or headless paper loop (BTC/ETH 5m & 15m by default)
python run_bot.py --ticks 50 --interval 1

# Trade only BTC on 5-minute markets
python run_bot.py --assets BTC --timeframes 5m

# Trade any market (disable the crypto filter)
python run_bot.py --all-markets
```

## Live data

Live mode reads from Polymarket's public APIs:

- Market discovery: `https://gamma-api.polymarket.com`
- Live mid prices: `https://clob.polymarket.com`

If those hosts are unreachable (e.g. blocked by a network egress allowlist),
the bot automatically falls back to a **simulated price feed** so the dashboard
and paper loop keep working. To use live data, allowlist both hosts in your
environment's network egress settings.

## Exit logic

On every tick the engine:

1. Refreshes each position's mark price from the feed.
2. Closes any position where `mark >= take_profit_price`
   → exit reason `TAKE_PROFIT_<pct>_PERCENT`.
3. Closes any position (if stop-loss enabled) where `mark <= stop_loss_price`
   → exit reason `STOP_LOSS_<pct>_PERCENT`.
4. Opens new positions for markets priced inside the entry band, up to the
   max-open-positions limit.

The exit-reason labels track the configured percentages, so the defaults
produce exactly `TAKE_PROFIT_10_PERCENT` and `STOP_LOSS_10_PERCENT`.

## Safety

- Default mode is **Live Data Paper Trading**; `live_trading_enabled` defaults
  to `False`.
- Real orders require **both** `mode == LIVE` **and** the
  "I understand — enable real orders" checkbox.
- The real-order submission path (`TradingEngine._submit_real_order`) is left
  intentionally unimplemented — wire up signed CLOB orders with your own API
  credentials before going live.

## Tests

```bash
pip install pytest
pytest -q
```

Covers the take-profit / stop-loss price formulas, PnL math, exit triggering,
and the safety guarantees around real orders.
