"""The paper-trading engine: signal-driven entries plus 10% TP / SL exits.

Key behaviours required by the spec:

* Trades only BTC/ETH 5m & 15m up/down markets (configurable).
* Positions are closed as soon as +take_profit% is reached on the token price
  (default +10%), without waiting for market resolution.
* An optional stop-loss closes positions at -stop_loss% (default -10%).
* The engine NEVER places real orders unless ``settings.real_orders_active``
  (mode == LIVE *and* live_trading_enabled). Default mode is Live Data Paper
  Trading, so fills are simulated locally.
* An adaptive ``LearningModel`` records every closed trade's outcome and feeds
  a learned win rate back into the scanner.
* Work is split so the dashboard can refresh prices/PnL fast while scanning for
  new markets on a slower cadence:
    - ``refresh_prices()`` → spot feed, marks, TP/SL exits  (fast loop)
    - ``scan()``           → discover markets + open trades  (slow loop)
    - ``tick()``           → both (headless/back-compat)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .config import Settings, TradingMode
from .learning import LearningModel
from .market_data import MarketQuote, build_provider
from .models import ClosedTrade, Position
from .price_feed import SpotPriceFeed
from . import scanner


@dataclass
class ScanReport:
    """Diagnostics for one scan, surfaced in the debug panel."""

    time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    markets_returned: int = 0
    markets_accepted: int = 0
    opportunities: list = field(default_factory=list)  # list[Opportunity]
    skipped: list = field(default_factory=list)  # list[(title, reason)]
    raw_sample: list = field(default_factory=list)  # list[dict] of raw markets

    @property
    def time_str(self) -> str:
        return self.time.strftime("%H:%M:%S UTC")


@dataclass
class TickResult:
    opened: list = field(default_factory=list)
    closed: list = field(default_factory=list)
    report: ScanReport | None = None


class TradingEngine:
    def __init__(self, settings: Settings, provider=None, spot_feed=None, learner=None):
        self.settings = settings
        self.positions: dict[str, Position] = {}  # token_id -> Position
        self.closed_trades: list[ClosedTrade] = []
        self.realized_pnl: float = 0.0
        self.feed_message: str = ""
        self.using_fallback: bool = False
        self.last_report: ScanReport = ScanReport()
        self.equity_curve: list[float] = []  # cumulative realized PnL over time

        if provider is None:
            provider, fallback, msg = build_provider(settings)
            self.using_fallback = fallback
            self.feed_message = msg
        self.provider = provider

        self.spot_feed = spot_feed or SpotPriceFeed(assets=settings.assets)

        if learner is None and settings.learning_enabled:
            path = os.path.join(settings.state_dir, "learning.json")
            learner = LearningModel(
                path=path,
                prior_alpha=settings.learn_prior_alpha,
                prior_beta=settings.learn_prior_beta,
                min_samples=settings.learn_min_samples,
                winrate_floor=settings.learn_winrate_floor,
            )
        self.learner = learner

    # ------------------------------------------------------------------ #
    # Entries
    # ------------------------------------------------------------------ #
    def _build_position(self, quote: MarketQuote, opp) -> Position:
        entry = opp.entry_price
        size = self.settings.order_size_usdc / entry if entry > 0 else 0.0
        return Position(
            market_id=quote.market_id,
            token_id=opp.token_id,
            question=quote.question,
            outcome=opp.side.title(),
            side=opp.side,
            entry_price=entry,
            size=size,
            expiry=quote.expiry,
            asset=quote.asset,
            timeframe=quote.timeframe,
            take_profit_price=self.settings.take_profit_price(entry),
            stop_loss_price=self.settings.stop_loss_price(entry),
            stop_loss_enabled=self.settings.stop_loss_enabled,
            mark_price=entry,
        )

    def scan(self) -> tuple[list[Position], ScanReport]:
        """Discover markets, evaluate signals, and open new trades."""
        report = ScanReport()
        opened: list[Position] = []
        try:
            quotes = self.provider.list_markets(self.settings.market_scan_limit)
        except Exception:
            report.raw_sample = getattr(self.provider, "last_raw", [])
            self.last_report = report
            return opened, report
        report.markets_returned = len(quotes)
        report.raw_sample = getattr(self.provider, "last_raw", [])

        held = set(self.positions.keys())
        for q in quotes:
            if self.settings.restrict_to_crypto_shortterm and not q.in_universe(
                self.settings.assets, self.settings.timeframes
            ):
                continue  # silent: too many off-universe markets to list
            report.markets_accepted += 1

            opp = scanner.evaluate(q, self.spot_feed, self.settings, held, self.learner)
            report.opportunities.append(opp)

            if not opp.will_trade:
                report.skipped.append((q.question, opp.reason_no_trade))
                continue
            if len(self.positions) >= self.settings.max_open_positions:
                report.skipped.append((q.question, "max open positions reached"))
                continue
            if opp.token_id in self.positions:
                report.skipped.append((q.question, "already holding this side"))
                continue

            pos = self._open(q, opp)
            if pos is not None:
                opened.append(pos)
                held.add(pos.token_id)

        self.last_report = report
        return opened, report

    def _open(self, quote: MarketQuote, opp) -> Position | None:
        pos = self._build_position(quote, opp)
        if pos.size <= 0:
            return None
        if self.settings.real_orders_active:
            self._submit_real_order(pos, side="BUY")
        self.positions[pos.token_id] = pos
        return pos

    # ------------------------------------------------------------------ #
    # Marks & exits
    # ------------------------------------------------------------------ #
    def _update_marks(self) -> None:
        for pos in self.positions.values():
            try:
                pos.mark_price = self.provider.get_price(pos.token_id)
            except Exception:
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
            side=pos.side,
            entry_time=pos.entry_time,
            close_time=datetime.now(timezone.utc),
            entry_price=pos.entry_price,
            close_price=pos.mark_price,
            size=pos.size,
            exit_reason=reason,
            asset=pos.asset,
            timeframe=pos.timeframe,
        )
        self.realized_pnl += trade.realized_pnl
        self.closed_trades.append(trade)
        self.equity_curve.append(round(self.realized_pnl, 4))
        del self.positions[token_id]

        # Learn from the outcome and persist.
        if self.learner is not None:
            self.learner.record_trade(trade)
            try:
                self.learner.save()
            except OSError:
                pass
        return trade

    def close_all(self, reason: str = "MANUAL_CLOSE") -> list[ClosedTrade]:
        self._update_marks()
        return [
            self._close_position(tid, pos, reason)
            for tid, pos in list(self.positions.items())
        ]

    # ------------------------------------------------------------------ #
    def _submit_real_order(self, pos: Position, side: str) -> None:
        raise NotImplementedError(
            "Real order submission is not wired up. Provide CLOB credentials "
            "and implement _submit_real_order before enabling live trading."
        )

    # ------------------------------------------------------------------ #
    # Public loops
    # ------------------------------------------------------------------ #
    def refresh_prices(self) -> list[ClosedTrade]:
        """Fast loop: refresh spot + marks, then run TP/SL exits."""
        self.spot_feed.refresh()
        self._update_marks()
        return self._check_exits()

    def tick(self, allow_entries: bool = True) -> TickResult:
        """Run both loops once (headless / tests / back-compat)."""
        closed = self.refresh_prices()
        opened, report = ([], self.last_report)
        if allow_entries:
            opened, report = self.scan()
        self._update_marks()
        return TickResult(opened=opened, closed=closed, report=report)

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
