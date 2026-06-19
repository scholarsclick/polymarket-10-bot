"""Data models for open positions and closed trades."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _time_left_str(expiry: datetime | None) -> str:
    if expiry is None:
        return "—"
    delta = (expiry - _now()).total_seconds()
    if delta <= 0:
        return "expired"
    m, s = divmod(int(delta), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


@dataclass
class Position:
    """An open paper position in a single Polymarket outcome token."""

    market_id: str
    token_id: str
    question: str
    outcome: str  # e.g. "Up" / "Down"
    side: str  # "UP" / "DOWN"

    entry_price: float
    size: float  # number of outcome shares held
    entry_time: datetime = field(default_factory=_now)
    expiry: datetime | None = None
    asset: str = ""
    timeframe: str = ""

    take_profit_price: float = 0.0
    stop_loss_price: float = 0.0
    stop_loss_enabled: bool = True

    # Updated on every tick from the market feed.
    mark_price: float = 0.0
    status: str = "OPEN"

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
        return round((self.mark_price - self.entry_price) * self.size, 6)

    @property
    def unrealized_pnl_pct(self) -> float:
        if self.entry_price == 0:
            return 0.0
        return round((self.mark_price / self.entry_price - 1.0) * 100.0, 4)

    def time_left_str(self) -> str:
        return _time_left_str(self.expiry)

    # --- Exit checks ------------------------------------------------------
    def hit_take_profit(self) -> bool:
        return self.mark_price >= self.take_profit_price

    def hit_stop_loss(self) -> bool:
        return self.stop_loss_enabled and self.mark_price <= self.stop_loss_price

    def live_status(self) -> str:
        if self.hit_take_profit():
            return "TP HIT"
        if self.hit_stop_loss():
            return "SL HIT"
        if self.unrealized_pnl > 0:
            return "IN PROFIT"
        if self.unrealized_pnl < 0:
            return "IN LOSS"
        return "OPEN"

    def to_dashboard_row(self) -> dict:
        return {
            "Market": self.question,
            "Side": self.side,
            "Entry Price": round(self.entry_price, 4),
            "Mark Price": round(self.mark_price, 4),
            "Take-Profit": round(self.take_profit_price, 4),
            "Stop-Loss": round(self.stop_loss_price, 4) if self.stop_loss_enabled else None,
            "Unrealized PnL": self.unrealized_pnl,
            "PnL %": self.unrealized_pnl_pct,
            "Time Left": self.time_left_str(),
            "Status": self.live_status(),
        }


@dataclass
class ClosedTrade:
    """A completed round-trip trade, recorded for the closed-trades table."""

    market_id: str
    question: str
    outcome: str
    side: str

    entry_time: datetime
    close_time: datetime
    entry_price: float
    close_price: float
    size: float
    exit_reason: str
    asset: str = ""
    timeframe: str = ""

    @property
    def profit_pct(self) -> float:
        if self.entry_price == 0:
            return 0.0
        return round((self.close_price / self.entry_price - 1.0) * 100.0, 4)

    @property
    def realized_pnl(self) -> float:
        return round((self.close_price - self.entry_price) * self.size, 6)

    @property
    def cost_basis(self) -> float:
        return self.entry_price * self.size

    @property
    def result(self) -> str:
        if self.realized_pnl > 0:
            return "WIN"
        if self.realized_pnl < 0:
            return "LOSS"
        return "FLAT"

    @property
    def hold_seconds(self) -> float:
        return (self.close_time - self.entry_time).total_seconds()

    def to_dashboard_row(self) -> dict:
        return {
            "Market": self.question,
            "Side": self.side,
            "Entry Price": round(self.entry_price, 4),
            "Exit Price": round(self.close_price, 4),
            "Profit %": self.profit_pct,
            "PnL": self.realized_pnl,
            "Result": self.result,
            "Exit Reason": self.exit_reason,
            "Close Time": self.close_time.strftime("%Y-%m-%d %H:%M:%S"),
        }
