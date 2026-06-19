"""Configuration for the Polymarket take-profit bot.

The bot defaults to **Live Data Paper Trading**: it reads real Polymarket
prices but never sends real orders. Real order placement only happens when
``live_trading_enabled`` is explicitly turned on by the operator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class TradingMode(str, Enum):
    """How the bot interacts with the market."""

    #: Read live Polymarket prices, simulate fills locally. No real money.
    LIVE_DATA_PAPER = "Live Data Paper Trading"
    #: Fully offline simulated price feed (used when live data is unavailable).
    SIMULATED_PAPER = "Simulated Paper Trading"
    #: Sends real orders. Requires live_trading_enabled to be True.
    LIVE = "Live Trading (REAL ORDERS)"


@dataclass
class Settings:
    """Tunable settings for the trading engine.

    Take-profit and stop-loss are expressed as percentages of the entry
    price, matching the sidebar controls in the dashboard.
    """

    # --- Sidebar-exposed risk settings -----------------------------------
    take_profit_pct: float = 10.0  # close position at +10% on the token price
    stop_loss_enabled: bool = True
    stop_loss_pct: float = 10.0  # close position at -10% on the token price

    # --- Safety / mode ----------------------------------------------------
    mode: TradingMode = TradingMode.LIVE_DATA_PAPER
    #: Master safety switch. Real orders are *impossible* unless this is True
    #: AND mode == LIVE. Defaults to False so the bot can never trade real
    #: money by accident.
    live_trading_enabled: bool = False

    # --- Entry strategy ---------------------------------------------------
    max_open_positions: int = 5
    order_size_usdc: float = 100.0  # notional per new position
    #: Only enter markets whose YES price sits inside this band, so there is
    #: room for the price to move toward take-profit.
    entry_price_min: float = 0.30
    entry_price_max: float = 0.70

    # --- Market universe filter ------------------------------------------
    #: Restrict trading to short-duration crypto up/down markets only.
    restrict_to_crypto_shortterm: bool = True
    #: Which underlying assets to trade.
    assets: tuple[str, ...] = ("BTC", "ETH")
    #: Which market durations to trade.
    timeframes: tuple[str, ...] = ("5m", "15m")

    # --- Data feed --------------------------------------------------------
    #: Hosts must be reachable (allowlisted) for live data. If unreachable
    #: the engine automatically falls back to the simulated feed.
    gamma_api_url: str = "https://gamma-api.polymarket.com"
    clob_api_url: str = "https://clob.polymarket.com"
    market_scan_limit: int = 200

    # Internal: tags used in exit reasons / dashboards
    base_currency: str = "USDC"

    def take_profit_price(self, entry_price: float) -> float:
        """take_profit_price = entry_price * (1 + tp%/100). e.g. 0.50 -> 0.55."""
        return round(entry_price * (1.0 + self.take_profit_pct / 100.0), 6)

    def stop_loss_price(self, entry_price: float) -> float:
        """stop_loss_price = entry_price * (1 - sl%/100). e.g. 0.50 -> 0.45."""
        return round(entry_price * (1.0 - self.stop_loss_pct / 100.0), 6)

    @property
    def take_profit_reason(self) -> str:
        """Exit reason label, e.g. ``TAKE_PROFIT_10_PERCENT`` at the default."""
        return f"TAKE_PROFIT_{_fmt_pct(self.take_profit_pct)}_PERCENT"

    @property
    def stop_loss_reason(self) -> str:
        """Exit reason label, e.g. ``STOP_LOSS_10_PERCENT`` at the default."""
        return f"STOP_LOSS_{_fmt_pct(self.stop_loss_pct)}_PERCENT"

    @property
    def real_orders_active(self) -> bool:
        """True only when the bot will actually submit real orders."""
        return self.mode == TradingMode.LIVE and self.live_trading_enabled


def _fmt_pct(pct: float) -> str:
    """Render 10.0 -> ``10`` and 7.5 -> ``7_5`` for use in reason labels."""
    if float(pct).is_integer():
        return str(int(pct))
    return str(pct).replace(".", "_")
