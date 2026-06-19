"""Streamlit dashboard for the Polymarket 10% take-profit bot.

Run with:  streamlit run app.py

Defaults to Live Data Paper Trading — real Polymarket prices, simulated fills.
No real orders are ever placed unless the operator switches the mode to LIVE
*and* ticks the "I understand — enable real orders" box in the sidebar.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from bot.config import Settings, TradingMode
from bot.engine import TradingEngine

st.set_page_config(page_title="Polymarket 10% TP Bot", page_icon="📈", layout="wide")


# --------------------------------------------------------------------------- #
# State helpers
# --------------------------------------------------------------------------- #
def _build_settings() -> Settings:
    return st.session_state.get("settings", Settings())


def _rebuild_engine(settings: Settings) -> None:
    """Create a fresh engine (e.g. after a mode change) but keep history."""
    old = st.session_state.get("engine")
    engine = TradingEngine(settings)
    if old is not None:
        engine.closed_trades = old.closed_trades
        engine.realized_pnl = old.realized_pnl
        engine.positions = old.positions
    st.session_state.engine = engine


def _apply_settings_to_open_positions(settings: Settings) -> None:
    """Recompute TP/SL levels on existing positions when the user changes %."""
    engine = st.session_state.get("engine")
    if engine is None:
        return
    for pos in engine.positions.values():
        pos.take_profit_price = settings.take_profit_price(pos.entry_price)
        pos.stop_loss_price = settings.stop_loss_price(pos.entry_price)
        pos.stop_loss_enabled = settings.stop_loss_enabled


# --------------------------------------------------------------------------- #
# Sidebar — settings
# --------------------------------------------------------------------------- #
def sidebar() -> Settings:
    st.sidebar.title("⚙️ Bot Settings")

    settings = _build_settings()

    st.sidebar.subheader("Risk Management")
    tp = st.sidebar.number_input(
        "Take Profit %", min_value=0.1, max_value=100.0,
        value=float(settings.take_profit_pct), step=0.5,
        help="Close the position when the token price rises this % above entry.",
    )
    sl_enabled = st.sidebar.checkbox(
        "Enable Stop Loss", value=settings.stop_loss_enabled,
        help="Optional. When off, positions are only closed by take-profit.",
    )
    sl = st.sidebar.number_input(
        "Stop Loss %", min_value=0.1, max_value=100.0,
        value=float(settings.stop_loss_pct), step=0.5,
        disabled=not sl_enabled,
        help="Close the position when the token price falls this % below entry.",
    )

    st.sidebar.subheader("Markets")
    restrict = st.sidebar.checkbox(
        "Only BTC/ETH short-duration markets", value=settings.restrict_to_crypto_shortterm,
        help="Restrict trading to crypto up/down markets for the selected assets and timeframes.",
    )
    assets = st.sidebar.multiselect(
        "Assets", options=["BTC", "ETH", "SOL", "XRP"],
        default=list(settings.assets), disabled=not restrict,
    )
    timeframes = st.sidebar.multiselect(
        "Timeframes", options=["5m", "15m", "1h", "1d"],
        default=list(settings.timeframes), disabled=not restrict,
    )

    st.sidebar.subheader("Entry Strategy")
    max_pos = st.sidebar.number_input(
        "Max Open Positions", min_value=1, max_value=50,
        value=int(settings.max_open_positions), step=1,
    )
    size = st.sidebar.number_input(
        "Order Size (USDC)", min_value=1.0, max_value=100000.0,
        value=float(settings.order_size_usdc), step=10.0,
    )
    band = st.sidebar.slider(
        "Entry Price Band", min_value=0.01, max_value=0.99,
        value=(float(settings.entry_price_min), float(settings.entry_price_max)),
        step=0.01,
        help="Only enter markets whose price sits inside this band.",
    )

    st.sidebar.subheader("Trading Mode")
    mode = st.sidebar.selectbox(
        "Mode",
        options=list(TradingMode),
        index=list(TradingMode).index(settings.mode),
        format_func=lambda m: m.value,
    )
    live_enabled = False
    if mode == TradingMode.LIVE:
        st.sidebar.error(
            "⚠️ LIVE mode submits REAL orders and risks REAL funds."
        )
        live_enabled = st.sidebar.checkbox(
            "I understand — enable real orders", value=False
        )
    else:
        st.sidebar.success("🧪 Paper trading — no real orders will be placed.")

    new_settings = Settings(
        take_profit_pct=tp,
        stop_loss_enabled=sl_enabled,
        stop_loss_pct=sl,
        mode=mode,
        live_trading_enabled=live_enabled,
        max_open_positions=int(max_pos),
        order_size_usdc=size,
        entry_price_min=band[0],
        entry_price_max=band[1],
        restrict_to_crypto_shortterm=restrict,
        assets=tuple(assets) or ("BTC", "ETH"),
        timeframes=tuple(timeframes) or ("5m", "15m"),
    )

    mode_changed = new_settings.mode != settings.mode
    st.session_state.settings = new_settings
    _apply_settings_to_open_positions(new_settings)
    if mode_changed or "engine" not in st.session_state:
        _rebuild_engine(new_settings)
    else:
        st.session_state.engine.settings = new_settings

    return new_settings


# --------------------------------------------------------------------------- #
# Main view
# --------------------------------------------------------------------------- #
def main() -> None:
    settings = sidebar()
    engine: TradingEngine = st.session_state.engine

    st.title("📈 Polymarket 10% Take-Profit Bot")

    # Controls
    c1, c2, c3, c4 = st.columns([1, 1, 1, 2])
    if c1.button("▶️ Run Tick", use_container_width=True):
        engine.tick()
    auto = c2.toggle("Auto-run", value=st.session_state.get("auto", False))
    st.session_state.auto = auto
    if c3.button("🧹 Close All", use_container_width=True):
        engine.close_all()
    refresh = c4.slider("Auto-run interval (s)", 1, 30, 3)

    # Feed / mode banners
    if engine.using_fallback:
        st.warning(f"📡 {engine.feed_message}")
    else:
        st.info(f"📡 {engine.feed_message}")
    if settings.real_orders_active:
        st.error("🔴 REAL ORDERS ARE ACTIVE — the bot will trade real funds.")
    else:
        st.caption(f"Mode: **{settings.mode.value}** · Real orders: **disabled**")

    # Account metrics
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Open Positions", len(engine.positions))
    m2.metric("Unrealized PnL", f"{engine.unrealized_pnl:,.2f} {settings.base_currency}")
    m3.metric("Realized PnL", f"{engine.realized_pnl:,.2f} {settings.base_currency}")
    m4.metric("Total PnL", f"{engine.total_pnl:,.2f} {settings.base_currency}")

    st.caption(
        f"Take-profit reason → `{settings.take_profit_reason}` · "
        f"Stop-loss reason → `{settings.stop_loss_reason}`"
        + ("" if settings.stop_loss_enabled else " (stop-loss disabled)")
    )

    # Open positions table
    st.subheader("Open Positions")
    open_rows = engine.open_rows()
    if open_rows:
        df = pd.DataFrame(open_rows)
        st.dataframe(
            df.style.map(_pnl_color, subset=["Unrealized PnL", "Unrealized PnL %"]),
            use_container_width=True, hide_index=True,
        )
    else:
        st.write("_No open positions. Run a tick to let the bot enter trades._")

    # Closed trades table
    st.subheader("Closed Trades")
    closed_rows = engine.closed_rows()
    if closed_rows:
        cdf = pd.DataFrame(closed_rows)
        st.dataframe(
            cdf.style.map(_pnl_color, subset=["Profit %", "Realized PnL"]),
            use_container_width=True, hide_index=True,
        )
        tp_count = sum(1 for t in engine.closed_trades if t.exit_reason.startswith("TAKE_PROFIT"))
        sl_count = sum(1 for t in engine.closed_trades if t.exit_reason.startswith("STOP_LOSS"))
        st.caption(f"Take-profit exits: {tp_count} · Stop-loss exits: {sl_count} · Total: {len(engine.closed_trades)}")
    else:
        st.write("_No closed trades yet._")

    # Auto-run loop
    if auto:
        engine.tick()
        import time
        time.sleep(refresh)
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
