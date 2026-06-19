"""Streamlit dashboard for the Polymarket 10% take-profit bot.

Run with:  streamlit run app.py

Defaults to Live Data Paper Trading — real prices, simulated fills. No real
orders unless LIVE mode + the explicit enable box. Trades only BTC/ETH 5m & 15m
up/down markets. Prices/PnL refresh on a fast loop; market scanning on a slower
loop; both via Streamlit fragments.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from bot.config import Settings, TradingMode
from bot.engine import TradingEngine
from bot.performance import compute_performance, daily_stats

st.set_page_config(page_title="Polymarket 10% TP Bot", page_icon="📈", layout="wide")

ACCENT = {"UP": "🟢", "DOWN": "🔴", "NEUTRAL": "⚪"}


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #
def _engine() -> TradingEngine:
    return st.session_state.engine


def _settings() -> Settings:
    return st.session_state.settings


def _rebuild_engine(settings: Settings) -> None:
    old = st.session_state.get("engine")
    engine = TradingEngine(settings)
    if old is not None:
        engine.closed_trades = old.closed_trades
        engine.realized_pnl = old.realized_pnl
        engine.positions = old.positions
        engine.equity_curve = old.equity_curve
        if old.learner is not None:
            engine.learner = old.learner
    engine.refresh_prices()
    engine.scan()
    st.session_state.engine = engine


def _apply_settings_to_open_positions(settings: Settings) -> None:
    engine = st.session_state.get("engine")
    if engine is None:
        return
    for pos in engine.positions.values():
        pos.take_profit_price = settings.take_profit_price(pos.entry_price)
        pos.stop_loss_price = settings.stop_loss_price(pos.entry_price)
        pos.stop_loss_enabled = settings.stop_loss_enabled


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #
def sidebar() -> tuple[Settings, int, int, bool]:
    st.sidebar.title("⚙️ Bot Settings")
    settings = st.session_state.get("settings", Settings())

    st.sidebar.subheader("Risk Management")
    tp = st.sidebar.number_input("Take Profit %", 0.1, 100.0, float(settings.take_profit_pct), 0.5)
    sl_on = st.sidebar.checkbox("Enable Stop Loss", settings.stop_loss_enabled)
    sl = st.sidebar.number_input("Stop Loss %", 0.1, 100.0, float(settings.stop_loss_pct), 0.5,
                                 disabled=not sl_on)

    st.sidebar.subheader("Markets")
    assets = st.sidebar.multiselect("Assets", ["BTC", "ETH", "SOL", "XRP"], list(settings.assets))
    timeframes = st.sidebar.multiselect("Timeframes", ["5m", "15m", "1h", "1d"], list(settings.timeframes))

    st.sidebar.subheader("Signal & Sizing")
    momentum = st.sidebar.number_input("Momentum threshold %", 0.0, 5.0,
                                       float(settings.momentum_threshold_pct), 0.01, format="%.3f")
    size = st.sidebar.number_input("Order Size (USDC)", 1.0, 100000.0, float(settings.order_size_usdc), 10.0)
    max_pos = st.sidebar.number_input("Max Open Positions", 1, 50, int(settings.max_open_positions), 1)
    learning = st.sidebar.checkbox("Adaptive learning", settings.learning_enabled,
                                   help="Learn win rates per asset/timeframe/direction and adjust trading.")

    st.sidebar.subheader("Trading Mode")
    mode = st.sidebar.selectbox("Mode", list(TradingMode),
                                index=list(TradingMode).index(settings.mode),
                                format_func=lambda m: m.value)
    live_enabled = False
    if mode == TradingMode.LIVE:
        st.sidebar.error("⚠️ LIVE mode submits REAL orders.")
        live_enabled = st.sidebar.checkbox("I understand — enable real orders", value=False)
    else:
        st.sidebar.success("🧪 Paper trading — no real orders.")

    st.sidebar.subheader("Refresh")
    auto = st.sidebar.toggle("Auto-refresh", value=st.session_state.get("auto", True))
    st.session_state.auto = auto
    price_secs = st.sidebar.slider("Price refresh (s)", 1, 10, int(settings.price_refresh_secs))
    scan_secs = st.sidebar.slider("Market scan (s)", 5, 30, int(settings.scan_refresh_secs))

    new_settings = Settings(
        take_profit_pct=tp, stop_loss_enabled=sl_on, stop_loss_pct=sl,
        mode=mode, live_trading_enabled=live_enabled,
        max_open_positions=int(max_pos), order_size_usdc=size,
        momentum_threshold_pct=momentum, learning_enabled=learning,
        price_refresh_secs=price_secs, scan_refresh_secs=scan_secs,
        assets=tuple(assets) or ("BTC", "ETH"),
        timeframes=tuple(timeframes) or ("5m", "15m"),
    )

    structural = (new_settings.mode != settings.mode
                  or new_settings.assets != settings.assets
                  or new_settings.learning_enabled != settings.learning_enabled)
    st.session_state.settings = new_settings
    _apply_settings_to_open_positions(new_settings)
    if structural or "engine" not in st.session_state:
        _rebuild_engine(new_settings)
    else:
        st.session_state.engine.settings = new_settings
    return new_settings, price_secs, scan_secs, auto


# --------------------------------------------------------------------------- #
# Renderers (each reads current engine/settings from session_state)
# --------------------------------------------------------------------------- #
def render_header():
    engine, settings = _engine(), _settings()
    c = st.columns([1, 1, 1, 1, 2])
    c[0].metric("Total PnL", f"{engine.total_pnl:,.2f}")
    c[1].metric("Realized", f"{engine.realized_pnl:,.2f}")
    c[2].metric("Unrealized", f"{engine.unrealized_pnl:,.2f}")
    c[3].metric("Open", len(engine.positions))
    feed = engine.spot_feed
    badge = "🟢 live" if (feed.source not in ("Simulated", "—")) else "🟡 sim"
    c[4].metric("Spot source", f"{feed.source} {badge}", f"updated {feed.last_updated_str}")


def render_prices():
    """FAST loop: refresh spot + marks + exits, then draw live prices."""
    engine, settings = _engine(), _settings()
    engine.refresh_prices()

    render_header()
    st.subheader("1 · Live Prices")
    feed = engine.spot_feed
    cols = st.columns(len(feed.assets))
    for i, asset in enumerate(feed.assets):
        with cols[i]:
            st.metric(f"{asset}/USD", f"${feed.price(asset):,.2f}", f"{feed.momentum(asset):+.3f}% mom")
            hist = list(feed.ticks[asset].history)
            if len(hist) >= 2:
                st.line_chart(pd.DataFrame({asset: hist}), height=120)
    st.caption(f"Source: **{feed.source}** · Last updated: **{feed.last_updated_str}** · "
               f"API: Binance `{feed.binance_status}` · Coinbase `{feed.coinbase_status}`")


def render_scanner():
    """SLOW loop: discover markets + open trades, then draw the scanner."""
    engine, settings = _engine(), _settings()
    engine.scan()

    r = engine.last_report
    st.subheader("2 · Opportunity Scanner")
    st.caption(f"Last scan: **{r.time_str}** · returned **{r.markets_returned}** · "
               f"accepted **{r.markets_accepted}** (BTC/ETH {'/'.join(settings.timeframes)})")
    rows = [o.to_dashboard_row() for o in sorted(
        r.opportunities, key=lambda o: (o.will_trade, o.confidence), reverse=True)]
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.warning(
            "No markets matched the BTC/ETH 5m·15m universe this scan. "
            "Open the Debug panel below to see the raw markets the API returned "
            "and their detected asset/timeframe."
        )


def render_positions():
    engine = _engine()
    st.subheader("3 · Open Positions")
    cc = st.columns([1, 5])
    if cc[0].button("🧹 Close all", use_container_width=True):
        engine.close_all()
    rows = engine.open_rows()
    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df.style.map(_pnl_color, subset=["Unrealized PnL", "PnL %"]),
                     use_container_width=True, hide_index=True)
    else:
        cc[1].write("_No open positions yet._")


def render_trades_and_performance():
    engine = _engine()

    st.subheader("4 · Closed Trades")
    rows = engine.closed_rows()
    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df.style.map(_pnl_color, subset=["Profit %", "PnL"]),
                     use_container_width=True, hide_index=True)
    else:
        st.write("_No closed trades yet._")

    st.subheader("5 · Performance")
    p = compute_performance(engine.closed_trades)
    pf = "∞" if p["profit_factor"] == float("inf") else f"{p['profit_factor']:.2f}"
    a = st.columns(5)
    a[0].metric("Total trades", p["total_trades"])
    a[1].metric("Wins", p["wins"])
    a[2].metric("Losses", p["losses"])
    a[3].metric("Win rate", f"{p['win_rate']:.1f}%")
    a[4].metric("Net PnL", f"{p['net_pnl']:,.2f}")
    b = st.columns(5)
    b[0].metric("ROI", f"{p['roi_pct']:.2f}%")
    b[1].metric("Avg win", f"{p['avg_win']:,.2f}")
    b[2].metric("Avg loss", f"{p['avg_loss']:,.2f}")
    b[3].metric("Profit factor", pf)
    b[4].metric("Max drawdown", f"{p['max_drawdown']:,.2f}")
    if engine.equity_curve:
        st.caption("Equity curve (cumulative realized PnL)")
        st.area_chart(pd.DataFrame({"PnL": engine.equity_curve}), height=160)

    st.subheader("6 · Daily Stats (today, UTC)")
    d = daily_stats(engine.closed_trades)
    c = st.columns(3)
    c[0].metric("Trades today", d["trades_today"])
    c[1].metric("Win rate today", f"{d['win_rate_today']:.1f}%")
    c[2].metric("PnL today", f"{d['pnl_today']:,.2f}")

    if engine.learner is not None:
        with st.expander("🧠 Learned model (win rate per asset · timeframe · direction)", expanded=False):
            lrows = engine.learner.stats_rows()
            if lrows:
                st.dataframe(pd.DataFrame(lrows), use_container_width=True, hide_index=True)
            else:
                st.write("_No learning data yet — it builds as trades close and persists to disk._")


def render_debug():
    engine, settings = _engine(), _settings()
    r = engine.last_report
    feed = engine.spot_feed
    st.subheader("7 · Debug Panel")
    c = st.columns(2)
    with c[0]:
        st.markdown("**API status**")
        st.write({
            "Polymarket": engine.feed_message,
            "Polymarket fallback": engine.using_fallback,
            "Spot source": feed.source,
            "Binance": feed.binance_status,
            "Coinbase": feed.coinbase_status,
        })
        st.markdown("**Scan counts**")
        st.write({
            "last scan": r.time_str,
            "markets returned": r.markets_returned,
            "markets accepted": r.markets_accepted,
            "opportunities": len(r.opportunities),
            "skipped": len(r.skipped),
        })
        st.markdown("**Raw markets returned (crypto first) — copyable**")
        st.caption("If 'accepted' is 0, paste this block so detection can be tuned.")
        if r.raw_sample:
            st.json(r.raw_sample, expanded=False)
        else:
            st.write("_No raw markets captured._")
    with c[1]:
        st.markdown("**Skipped markets (reasons)**")
        if r.skipped:
            st.dataframe(pd.DataFrame(r.skipped, columns=["Market", "Reason"]),
                         use_container_width=True, hide_index=True, height=480)
        else:
            st.write("_Nothing skipped this scan._")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> None:
    settings, price_secs, scan_secs, auto = sidebar()

    st.title("📈 Polymarket 10% Take-Profit Bot — BTC/ETH 5m·15m")
    top = st.columns([1, 1, 4])
    if top[0].button("🔄 Refresh now", use_container_width=True):
        _engine().refresh_prices()
    if top[1].button("🔎 Scan now", use_container_width=True):
        _engine().scan()
    mode_txt = "🔴 LIVE (real orders)" if settings.real_orders_active else "🧪 PAPER"
    top[2].caption(
        f"{mode_txt} · TP **{settings.take_profit_pct:.0f}%** / SL "
        f"**{settings.stop_loss_pct:.0f}%{'' if settings.stop_loss_enabled else ' off'}** · "
        f"refresh **{price_secs}s** · scan **{scan_secs}s** · "
        f"learning **{'on' if settings.learning_enabled else 'off'}**"
    )
    if settings.real_orders_active:
        st.error("🔴 REAL ORDERS ARE ACTIVE — the bot will trade real funds.")
    elif _engine().using_fallback:
        st.warning(f"📡 {_engine().feed_message}")

    # Fragments: fast loop for prices/PnL, slow loop for market scanning.
    fast = price_secs if auto else None
    slow = scan_secs if auto else None

    st.fragment(render_prices, run_every=fast)()
    st.fragment(render_scanner, run_every=slow)()
    st.fragment(render_positions, run_every=fast)()
    st.fragment(render_trades_and_performance, run_every=fast)()
    st.fragment(render_debug, run_every=slow)()


def _pnl_color(val):
    try:
        v = float(val)
    except (TypeError, ValueError):
        return ""
    if v > 0:
        return "color: #1a7f37;"
    if v < 0:
        return "color: #cf222e;"
    return ""


if __name__ == "__main__":
    main()
