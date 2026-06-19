"""Streamlit dashboard for the Polymarket 10% take-profit bot.

Run with:  streamlit run app.py

Defaults to Live Data Paper Trading — real prices, simulated fills. No real
orders are placed unless the operator switches to LIVE mode *and* ticks the
"enable real orders" box. Trades only BTC/ETH 5m & 15m up/down markets.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from bot.config import Settings, TradingMode
from bot.engine import TradingEngine
from bot.performance import compute_performance, daily_stats

try:
    from streamlit_autorefresh import st_autorefresh
    HAVE_AUTOREFRESH = True
except ImportError:  # graceful fallback if the helper package isn't installed
    HAVE_AUTOREFRESH = False

st.set_page_config(page_title="Polymarket 10% TP Bot", page_icon="📈", layout="wide")


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #
def _rebuild_engine(settings: Settings) -> None:
    old = st.session_state.get("engine")
    engine = TradingEngine(settings)
    if old is not None:
        engine.closed_trades = old.closed_trades
        engine.realized_pnl = old.realized_pnl
        engine.positions = old.positions
    engine.tick()  # one scan so the dashboard renders with data immediately
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
def sidebar() -> tuple[Settings, int]:
    st.sidebar.title("⚙️ Bot Settings")
    settings = st.session_state.get("settings", Settings())

    st.sidebar.subheader("Risk Management")
    tp = st.sidebar.number_input("Take Profit %", 0.1, 100.0, float(settings.take_profit_pct), 0.5)
    sl_enabled = st.sidebar.checkbox("Enable Stop Loss", settings.stop_loss_enabled)
    sl = st.sidebar.number_input("Stop Loss %", 0.1, 100.0, float(settings.stop_loss_pct), 0.5,
                                 disabled=not sl_enabled)

    st.sidebar.subheader("Markets")
    assets = st.sidebar.multiselect("Assets", ["BTC", "ETH", "SOL", "XRP"], list(settings.assets))
    timeframes = st.sidebar.multiselect("Timeframes", ["5m", "15m", "1h", "1d"], list(settings.timeframes))

    st.sidebar.subheader("Signal & Sizing")
    momentum = st.sidebar.number_input("Momentum threshold %", 0.0, 5.0,
                                       float(settings.momentum_threshold_pct), 0.01, format="%.3f")
    size = st.sidebar.number_input("Order Size (USDC)", 1.0, 100000.0, float(settings.order_size_usdc), 10.0)
    max_pos = st.sidebar.number_input("Max Open Positions", 1, 50, int(settings.max_open_positions), 1)

    st.sidebar.subheader("Trading Mode")
    mode = st.sidebar.selectbox("Mode", list(TradingMode),
                                index=list(TradingMode).index(settings.mode),
                                format_func=lambda m: m.value)
    live_enabled = False
    if mode == TradingMode.LIVE:
        st.sidebar.error("⚠️ LIVE mode submits REAL orders and risks REAL funds.")
        live_enabled = st.sidebar.checkbox("I understand — enable real orders", value=False)
    else:
        st.sidebar.success("🧪 Paper trading — no real orders.")

    st.sidebar.subheader("Auto-refresh")
    auto = st.sidebar.toggle("Auto-run", value=st.session_state.get("auto", True))
    st.session_state.auto = auto
    interval = st.sidebar.slider("Refresh interval (s)", 10, 15, st.session_state.get("interval", 12))
    st.session_state.interval = interval

    new_settings = Settings(
        take_profit_pct=tp, stop_loss_enabled=sl_enabled, stop_loss_pct=sl,
        mode=mode, live_trading_enabled=live_enabled,
        max_open_positions=int(max_pos), order_size_usdc=size,
        momentum_threshold_pct=momentum,
        assets=tuple(assets) or ("BTC", "ETH"),
        timeframes=tuple(timeframes) or ("5m", "15m"),
    )

    mode_changed = (new_settings.mode != settings.mode
                    or new_settings.assets != settings.assets)
    st.session_state.settings = new_settings
    _apply_settings_to_open_positions(new_settings)
    if mode_changed or "engine" not in st.session_state:
        _rebuild_engine(new_settings)
    else:
        st.session_state.engine.settings = new_settings
    return new_settings, interval


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #
def section_live_prices(engine, settings):
    st.subheader("1 · Live Prices")
    feed = engine.spot_feed
    cols = st.columns(len(feed.assets) + 3)
    for i, asset in enumerate(feed.assets):
        cols[i].metric(f"{asset} price",
                       f"${feed.price(asset):,.2f}",
                       f"{feed.momentum(asset):+.3f}% mom")
    cols[-3].metric("Price source", feed.source)
    cols[-2].metric("Last updated", feed.last_updated_str)
    status_ok = feed.source != "Simulated"
    cols[-1].metric("API status", "🟢 live" if status_ok else "🟡 fallback")


def section_scanner(engine):
    st.subheader("2 · Opportunity Scanner")
    report = engine.last_report
    st.caption(f"Last scan: {report.time_str} · returned {report.markets_returned} · "
               f"accepted {report.markets_accepted}")
    rows = [o.to_dashboard_row() for o in report.opportunities]
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.write("_No markets matched the BTC/ETH 5m·15m universe this scan._")


def section_open_positions(engine):
    st.subheader("3 · Open Positions")
    rows = engine.open_rows()
    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df.style.map(_pnl_color, subset=["Unrealized PnL", "PnL %"]),
                     use_container_width=True, hide_index=True)
    else:
        st.write("_No open positions._")


def section_closed_trades(engine):
    st.subheader("4 · Closed Trades")
    rows = engine.closed_rows()
    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df.style.map(_pnl_color, subset=["Profit %", "PnL"]),
                     use_container_width=True, hide_index=True)
        tp = sum(1 for t in engine.closed_trades if t.exit_reason.startswith("TAKE_PROFIT"))
        sl = sum(1 for t in engine.closed_trades if t.exit_reason.startswith("STOP_LOSS"))
        st.caption(f"Take-profit exits: {tp} · Stop-loss exits: {sl} · Total: {len(engine.closed_trades)}")
    else:
        st.write("_No closed trades yet._")


def section_performance(engine):
    st.subheader("5 · Performance")
    p = compute_performance(engine.closed_trades)
    pf = "∞" if p["profit_factor"] == float("inf") else f"{p['profit_factor']:.2f}"
    c = st.columns(5)
    c[0].metric("Total trades", p["total_trades"])
    c[1].metric("Wins", p["wins"])
    c[2].metric("Losses", p["losses"])
    c[3].metric("Win rate", f"{p['win_rate']:.1f}%")
    c[4].metric("Net PnL", f"{p['net_pnl']:,.2f}")
    c = st.columns(5)
    c[0].metric("ROI", f"{p['roi_pct']:.2f}%")
    c[1].metric("Avg win", f"{p['avg_win']:,.2f}")
    c[2].metric("Avg loss", f"{p['avg_loss']:,.2f}")
    c[3].metric("Profit factor", pf)
    c[4].metric("Max drawdown", f"{p['max_drawdown']:,.2f}")


def section_daily(engine):
    st.subheader("6 · Daily Stats (today, UTC)")
    d = daily_stats(engine.closed_trades)
    c = st.columns(3)
    c[0].metric("Trades today", d["trades_today"])
    c[1].metric("Win rate today", f"{d['win_rate_today']:.1f}%")
    c[2].metric("PnL today", f"{d['pnl_today']:,.2f}")


def section_debug(engine):
    st.subheader("7 · Debug Panel")
    report = engine.last_report
    feed = engine.spot_feed
    c = st.columns(2)
    with c[0]:
        st.markdown("**API status**")
        st.write({
            "Polymarket": engine.feed_message,
            "Polymarket using fallback": engine.using_fallback,
            "Spot source": feed.source,
            "Binance": feed.binance_status,
            "Coinbase": feed.coinbase_status,
        })
        st.markdown("**Scan counts**")
        st.write({
            "last scan": report.time_str,
            "markets returned": report.markets_returned,
            "markets accepted": report.markets_accepted,
            "opportunities": len(report.opportunities),
            "skipped": len(report.skipped),
        })
    with c[1]:
        st.markdown("**Skipped markets (reasons)**")
        if report.skipped:
            st.dataframe(pd.DataFrame(report.skipped, columns=["Market", "Reason"]),
                         use_container_width=True, hide_index=True, height=320)
        else:
            st.write("_Nothing skipped this scan._")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> None:
    settings, interval = sidebar()

    # Auto-refresh: drive a tick every `interval` seconds.
    if st.session_state.get("auto", True):
        if HAVE_AUTOREFRESH:
            st_autorefresh(interval=interval * 1000, key="auto_refresh")
            st.session_state.engine.tick()
    engine: TradingEngine = st.session_state.engine

    st.title("📈 Polymarket 10% Take-Profit Bot — BTC/ETH 5m·15m")

    top = st.columns([1, 1, 1, 3])
    if top[0].button("▶️ Scan now", use_container_width=True):
        engine.tick()
    if top[1].button("🧹 Close all", use_container_width=True):
        engine.close_all()
    refresh_note = (f"auto every {interval}s" if st.session_state.get("auto", True) else "manual")
    top[2].metric("Mode", "PAPER" if not settings.real_orders_active else "LIVE")
    top[3].caption(
        f"Refresh: **{refresh_note}** · Last scan: **{engine.last_report.time_str}** · "
        f"Universe: **{'/'.join(settings.assets)} @ {'/'.join(settings.timeframes)}** · "
        f"TP **{settings.take_profit_pct:.0f}%** / SL "
        f"**{settings.stop_loss_pct:.0f}%{'' if settings.stop_loss_enabled else ' (off)'}**"
    )

    if settings.real_orders_active:
        st.error("🔴 REAL ORDERS ARE ACTIVE — the bot will trade real funds.")
    elif engine.using_fallback:
        st.warning(f"📡 {engine.feed_message}")
    else:
        st.info(f"📡 {engine.feed_message}")

    section_live_prices(engine, settings)
    st.divider()
    section_scanner(engine)
    st.divider()
    section_open_positions(engine)
    st.divider()
    section_closed_trades(engine)
    st.divider()
    section_performance(engine)
    st.divider()
    section_daily(engine)
    st.divider()
    section_debug(engine)

    # Fallback auto-refresh when the helper package isn't installed.
    if st.session_state.get("auto", True) and not HAVE_AUTOREFRESH:
        import time
        engine.tick()
        time.sleep(interval)
        st.rerun()


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
