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
CLI), and the filter can be turned off to trade any market.

**Timeframe detection is duration-based**: the bot computes each market's window
(`endDate − startDate`) and maps it to the nearest bucket (5m / 15m / 1h / 1d),
falling back to text matching on the title/slug. This is far more reliable than
parsing titles, which don't always spell out the interval.

### Diagnosing "no markets found"

If the scanner shows no markets, the **Debug Panel** lists the raw markets the
API returned with their detected asset/timeframe. You can also run the probe:

```bash
python probe.py            # dumps live markets + detected asset/timeframe
python probe.py --limit 1000 --show 60
```

This confirms whether BTC/ETH 5m·15m markets are actually being served and how
they're classified — handy for tuning if Polymarket changes its market shapes.

## Adaptive learning

When enabled (default), the bot keeps a Beta-Bernoulli **win rate per
(asset, timeframe, direction)** bucket, learning from every closed trade and
**persisting to `.botstate/learning.json`** so it keeps improving across runs.
The learned win rate:

- **adjusts confidence** in the scanner, and
- **vetoes** a bucket once it has enough samples and its win rate falls below a
  floor (default 45%), with the reason shown in the scanner / debug panel.

## Features

- **Take-profit on token price gain** — `take_profit_price = entry_price × 1.10`
  (e.g. entry `0.50` → TP `0.55`; entry `0.60` → TP `0.66`).
- **No waiting for resolution** — a position is closed the moment its mark
  price reaches the take-profit level.
- **Optional stop-loss** — default `stop_loss_price = entry_price × 0.90`
  (−10%). Can be toggled off entirely.
- **Momentum signal** — direction (UP/DOWN) is chosen from short-term spot
  momentum (BTC/ETH price from Binance, falling back to Coinbase, then a
  simulated feed), with a confidence score and a written reason per market.
- **Adaptive learning** — learns win rates per asset/timeframe/direction from
  closed trades and feeds them back into confidence and trade gating.
- **Dual-cadence dashboard** — prices/PnL refresh on a fast loop (default 3 s)
  while market scanning runs on a slower loop (default 10 s), via Streamlit
  fragments, with charts for spot history and the equity curve.

## Dashboard sections

1. **Live Prices** — BTC/ETH spot price, momentum, price source, last updated,
   API status.
2. **Opportunity Scanner** — market title, asset, timeframe, expiry, UP/DOWN
   prices, spread, signal direction, confidence, and reason to trade / not trade.
3. **Open Positions** — market, side, entry price, current mark price,
   take-profit, stop-loss, unrealized PnL, time left, status.
4. **Closed Trades** — market, side, entry/exit price, profit %, PnL, result
   (WIN/LOSS), exit reason, close time.
5. **Performance** — total trades, wins, losses, win rate, net PnL, ROI,
   average win, average loss, profit factor, max drawdown.
6. **Daily Stats** — trades today, win rate today, PnL today.
7. **Debug Panel** — Polymarket API status, Binance/Coinbase API status,
   markets returned, markets accepted, and skipped trades with reasons.

**Sidebar settings** — Take Profit % (default 10), Stop Loss % (default 10),
assets, timeframes, momentum threshold, order size, max positions, trading
mode, and the auto-refresh interval.

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

Live mode reads from these public APIs:

- Market discovery: `https://gamma-api.polymarket.com`
- Live mid prices: `https://clob.polymarket.com`
- Spot prices (signal): `https://api.binance.com` → `https://api.coinbase.com`

If any host is unreachable (e.g. blocked by a network egress allowlist), the
bot automatically falls back to a **simulated feed** for that source so the
dashboard and paper loop keep working. To use fully live data, allowlist the
hosts above in your environment's network egress settings. The Debug Panel
shows exactly which sources are live vs. simulated.

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

Covers the universe filter, the momentum signal/scanner, take-profit /
stop-loss price formulas, PnL and performance math, exit triggering, scan
diagnostics, and the safety guarantees around real orders.
