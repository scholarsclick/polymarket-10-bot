"""Data models for open positions and closed trades."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Position:
    """An open paper position in a single Polymarket outcome token."""

    market_id: str
    token_id: str
    question: str
    outcome: str  # e.g. "Yes" / "No"

    entry_price: float
    size: float  # number of outcome shares held
    entry_time: datetime = field(default_factory=_now)

    take_profit_price: float = 0.0
    stop_loss_price: float = 0.0
    stop_loss_enabled: bool = True

    # Updated on every tick from the market feed.
    mark_price: float = 0.0

    def __post_init__(self) -> None:
        if self.mark_price == 0.0:
            self.mark_price = self.entry_price

    # --- Derived metrics --------------------------------------------------
    @property
    def cost_basis(self) -> float:
        return self.entry_price * self.size

    @property
    def market_value(self) -> float:
        return self.mark_price * self.size

    @property
    def unrealized_pnl(self) -> float:
        """Unrealized PnL in the base currency."""
        return round((self.mark_price - self.entry_price) * self.size, 6)

    @property
    def unrealized_pnl_pct(self) -> float:
        """Unrealized PnL as a percentage of the entry price."""
        if self.entry_price == 0:
            return 0.0
        return round((self.mark_price / self.entry_price - 1.0) * 100.0, 4)

    # --- Exit checks ------------------------------------------------------
    def hit_take_profit(self) -> bool:
        return self.mark_price >= self.take_profit_price

    def hit_stop_loss(self) -> bool:
        return self.stop_loss_enabled and self.mark_price <= self.stop_loss_price

    def to_dashboard_row(self) -> dict:
        """Row used by the open-positions table in the dashboard."""
        return {
            "Market": self.question,
            "Outcome": self.outcome,
            "Entry Price": round(self.entry_price, 4),
            "Mark Price": round(self.mark_price, 4),
            "Take-Profit": round(self.take_profit_price, 4),
            "Stop-Loss": round(self.stop_loss_price, 4) if self.stop_loss_enabled else None,
            "Size": round(self.size, 2),
            "Unrealized PnL": self.unrealized_pnl,
            "Unrealized PnL %": self.unrealized_pnl_pct,
            "Entry Time": self.entry_time.strftime("%Y-%m-%d %H:%M:%S"),
        }


@dataclass
class ClosedTrade:
    """A completed round-trip trade, recorded for the closed-trades table."""

    market_id: str
    question: str
    outcome: str

    entry_time: datetime
    close_time: datetime
    entry_price: float
    close_price: float
    size: float
    exit_reason: str

    @property
    def profit_pct(self) -> float:
        """Realized profit as a percentage of entry price."""
        if self.entry_price == 0:
            return 0.0
        return round((self.close_price / self.entry_price - 1.0) * 100.0, 4)

    @property
    def realized_pnl(self) -> float:
        return round((self.close_price - self.entry_price) * self.size, 6)

    @property
    def hold_seconds(self) -> float:
        return (self.close_time - self.entry_time).total_seconds()

    def to_dashboard_row(self) -> dict:
        """Row used by the closed-trades table in the dashboard."""
        return {
            "Market": self.question,
            "Outcome": self.outcome,
            "Entry Time": self.entry_time.strftime("%Y-%m-%d %H:%M:%S"),
            "Close Time": self.close_time.strftime("%Y-%m-%d %H:%M:%S"),
            "Entry Price": round(self.entry_price, 4),
            "Close Price": round(self.close_price, 4),
            "Profit %": self.profit_pct,
            "Realized PnL": self.realized_pnl,
            "Exit Reason": self.exit_reason,
        }
