"""The paper-trading engine: entries plus 10% take-profit / stop-loss exits.

Key behaviours required by the spec:

* Positions are closed as soon as +take_profit% is reached on the token price
  (default +10%), without waiting for market resolution.
* An optional stop-loss closes positions at -stop_loss% (default -10%).
* The engine NEVER places real orders unless ``settings.real_orders_active``
  is true (mode == LIVE *and* live_trading_enabled). The default mode is
  Live Data Paper Trading, so all fills are simulated locally.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .config import Settings, TradingMode
from .market_data import MarketQuote, build_provider, matches_universe
from .models import ClosedTrade, Position


@dataclass
class TickResult:
    """Summary of one engine tick, handy for surfacing in the dashboard."""

    opened: list[Position] = field(default_factory=list)
    closed: list[ClosedTrade] = field(default_factory=list)
    feed_message: str = ""
    using_fallback: bool = False


class TradingEngine:
    """Holds state and drives paper trading on each ``tick``."""

    def __init__(self, settings: Settings, provider=None):
        self.settings = settings
        self.positions: dict[str, Position] = {}  # token_id -> Position
        self.closed_trades: list[ClosedTrade] = []
        self.realized_pnl: float = 0.0
        self.feed_message: str = ""
        self.using_fallback: bool = False

        if provider is None:
            provider, fallback, msg = build_provider(settings)
            self.using_fallback = fallback
            self.feed_message = msg
        self.provider = provider

    # ------------------------------------------------------------------ #
    # Position sizing & entries
    # ------------------------------------------------------------------ #
    def _build_position(self, quote: MarketQuote) -> Position:
        size = self.settings.order_size_usdc / quote.price if quote.price > 0 else 0.0
        return Position(
            market_id=quote.market_id,
            token_id=quote.token_id,
            question=quote.question,
            outcome=quote.outcome,
            entry_price=quote.price,
            size=size,
            take_profit_price=self.settings.take_profit_price(quote.price),
            stop_loss_price=self.settings.stop_loss_price(quote.price),
            stop_loss_enabled=self.settings.stop_loss_enabled,
            mark_price=quote.price,
        )

    def open_position(self, quote: MarketQuote) -> Position | None:
        """Open a paper (or, if enabled, real) position for ``quote``."""
        if quote.token_id in self.positions:
            return None
        if len(self.positions) >= self.settings.max_open_positions:
            return None
        if quote.price <= 0:
            return None
        pos = self._build_position(quote)
        if pos.size <= 0:
            return None
        if self.settings.real_orders_active:
            self._submit_real_order(pos, side="BUY")
        self.positions[quote.token_id] = pos
        return pos

    def _auto_entries(self) -> list[Position]:
        """Simple, transparent entry rule: buy outcomes priced inside the
        configured band that we don't already hold, up to max positions."""
        opened: list[Position] = []
        if len(self.positions) >= self.settings.max_open_positions:
            return opened
        try:
            quotes = self.provider.list_markets(self.settings.market_scan_limit)
        except Exception:  # data hiccup — skip entries this tick
            return opened
        for q in quotes:
            if len(self.positions) >= self.settings.max_open_positions:
                break
            if q.token_id in self.positions:
                continue
            if self.settings.restrict_to_crypto_shortterm and not matches_universe(
                q.search_text, self.settings.assets, self.settings.timeframes
            ):
                continue
            if not (self.settings.entry_price_min <= q.price <= self.settings.entry_price_max):
                continue
            pos = self.open_position(q)
            if pos is not None:
                opened.append(pos)
        return opened

    # ------------------------------------------------------------------ #
    # Marks & exits
    # ------------------------------------------------------------------ #
    def _update_marks(self) -> None:
        for pos in self.positions.values():
            try:
                pos.mark_price = self.provider.get_price(pos.token_id)
            except Exception:
                # Keep the last known mark if the feed momentarily fails.
                continue

    def _check_exits(self) -> list[ClosedTrade]:
        closed: list[ClosedTrade] = []
        for token_id, pos in list(self.positions.items()):
            reason = None
            if pos.hit_take_profit():
                reason = self.settings.take_profit_reason
            elif pos.hit_stop_loss():
                reason = self.settings.stop_loss_reason
            if reason is not None:
                closed.append(self._close_position(token_id, pos, reason))
        return closed

    def _close_position(self, token_id: str, pos: Position, reason: str) -> ClosedTrade:
        if self.settings.real_orders_active:
            self._submit_real_order(pos, side="SELL")
        trade = ClosedTrade(
            market_id=pos.market_id,
            question=pos.question,
            outcome=pos.outcome,
            entry_time=pos.entry_time,
            close_time=datetime.now(timezone.utc),
            entry_price=pos.entry_price,
            close_price=pos.mark_price,
            size=pos.size,
            exit_reason=reason,
        )
        self.realized_pnl += trade.realized_pnl
        self.closed_trades.append(trade)
        del self.positions[token_id]
        return trade

    def close_all(self, reason: str = "MANUAL_CLOSE") -> list[ClosedTrade]:
        """Manually flatten every open position (used by the dashboard)."""
        self._update_marks()
        return [
            self._close_position(tid, pos, reason)
            for tid, pos in list(self.positions.items())
        ]

    # ------------------------------------------------------------------ #
    # Real-order guard
    # ------------------------------------------------------------------ #
    def _submit_real_order(self, pos: Position, side: str) -> None:
        """Placeholder for real CLOB order submission.

        Intentionally left unimplemented: wiring real signed orders requires
        API credentials and is only ever reached when the operator has both
        switched mode to LIVE and toggled live_trading_enabled on. Paper modes
        never call this.
        """
        raise NotImplementedError(
            "Real order submission is not wired up. Provide CLOB credentials "
            "and implement _submit_real_order before enabling live trading."
        )

    # ------------------------------------------------------------------ #
    # Public tick
    # ------------------------------------------------------------------ #
    def tick(self, allow_entries: bool = True) -> TickResult:
        """Advance the engine one step: refresh marks, run exits, then entries."""
        self._update_marks()
        closed = self._check_exits()
        opened = self._auto_entries() if allow_entries else []
        # Refresh marks for freshly opened positions so the dashboard shows them.
        self._update_marks()
        return TickResult(
            opened=opened,
            closed=closed,
            feed_message=self.feed_message,
            using_fallback=self.using_fallback,
        )

    # ------------------------------------------------------------------ #
    # Reporting helpers
    # ------------------------------------------------------------------ #
    @property
    def unrealized_pnl(self) -> float:
        return round(sum(p.unrealized_pnl for p in self.positions.values()), 6)

    @property
    def total_pnl(self) -> float:
        return round(self.realized_pnl + self.unrealized_pnl, 6)

    def open_rows(self) -> list[dict]:
        return [p.to_dashboard_row() for p in self.positions.values()]

    def closed_rows(self) -> list[dict]:
        return [t.to_dashboard_row() for t in reversed(self.closed_trades)]
