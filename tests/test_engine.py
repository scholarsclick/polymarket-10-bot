"""Tests for the universe filter, signal scanner, TP/SL exits, and performance."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import Settings, TradingMode
from bot.engine import TradingEngine
from bot.market_data import MarketQuote, SimulatedProvider, matches_universe
from bot.models import ClosedTrade, Position
from bot.performance import compute_performance, daily_stats
from bot import scanner


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #
class _StubSpot:
    """Deterministic spot feed returning a fixed momentum for every asset."""

    def __init__(self, momentum=0.5):
        self._m = momentum
        self.assets = ["BTC", "ETH"]
        self.source = "stub"
        self.binance_status = self.coinbase_status = "stub"
        self.last_updated = None
        self.last_updated_str = "—"

    def refresh(self):
        pass

    def price(self, asset):
        return 100.0

    def momentum(self, asset):
        return self._m


class _StubProvider:
    """Provider whose mark price we control to force exits."""

    def __init__(self, price):
        self.name = "stub"
        self._price = price
        self._quote = MarketQuote("m1", "tok1", "Bitcoin Up or Down — 5 minute",
                                  "Up", 0.50, "btc-5m")

    def list_markets(self, limit):
        return [self._quote]

    def get_price(self, token_id):
        return self._price


def _engine(price=0.50, momentum=0.5, **kw):
    settings = Settings(order_size_usdc=100, entry_price_min=0.4, entry_price_max=0.6, **kw)
    return TradingEngine(settings, provider=_StubProvider(price), spot_feed=_StubSpot(momentum))


# --------------------------------------------------------------------------- #
# Market universe filter (BTC/ETH 5m & 15m)
# --------------------------------------------------------------------------- #
def test_matches_btc_eth_5m_15m():
    assets, tfs = ("BTC", "ETH"), ("5m", "15m")
    assert matches_universe("Bitcoin Up or Down — 5 minute", assets, tfs)
    assert matches_universe("Ethereum Up or Down — 15 minute", assets, tfs)
    assert matches_universe("btc-up-or-down-5m", assets, tfs)


def test_rejects_other_assets_and_timeframes():
    assets, tfs = ("BTC", "ETH"), ("5m", "15m")
    assert not matches_universe("Solana Up or Down — 5 minute", assets, tfs)
    assert not matches_universe("Bitcoin Up or Down — 1 hour", assets, tfs)
    assert not matches_universe("Will the Fed cut rates?", assets, tfs)


def test_5m_pattern_does_not_match_15m_text():
    assert not matches_universe("Bitcoin Up or Down — 15 minute", ("BTC",), ("5m",))
    assert matches_universe("Bitcoin Up or Down — 15 minute", ("BTC",), ("15m",))


def test_simulated_catalog_is_all_btc_eth_short_markets():
    quotes = SimulatedProvider().list_markets(50)
    assert quotes
    for q in quotes:
        assert matches_universe(q.search_text, ("BTC", "ETH"), ("5m", "15m"))
        assert q.asset in ("BTC", "ETH")
        assert q.timeframe in ("5m", "15m")
        assert q.up_token_id and q.down_token_id and q.expiry is not None


def test_engine_only_enters_filtered_markets():
    class MixedProvider:
        name = "mixed"
        def __init__(self):
            self._q = [
                MarketQuote("1", "t1", "Bitcoin Up or Down — 5 minute", "Up", 0.50, "btc-5m"),
                MarketQuote("2", "t2", "Will the Fed cut rates?", "Yes", 0.50, "fed"),
                MarketQuote("3", "t3", "Solana Up or Down — 5 minute", "Up", 0.50, "sol-5m"),
            ]
        def list_markets(self, limit): return self._q
        def get_price(self, token_id): return 0.50

    settings = Settings(entry_price_min=0.4, entry_price_max=0.6)
    engine = TradingEngine(settings, provider=MixedProvider(), spot_feed=_StubSpot(0.5))
    engine.tick()
    assert [p.token_id for p in engine.positions.values()] == ["t1"]


# --------------------------------------------------------------------------- #
# Signal scanner
# --------------------------------------------------------------------------- #
def test_scanner_picks_up_on_positive_momentum():
    q = MarketQuote("1", "t1", "Bitcoin Up or Down — 5 minute", "Up", 0.50, "btc-5m")
    opp = scanner.evaluate(q, _StubSpot(0.5), Settings(), held=set())
    assert opp.signal == "UP" and opp.will_trade and opp.side == "UP"
    assert opp.confidence > 50


def test_scanner_picks_down_on_negative_momentum():
    q = MarketQuote("1", "t1", "Bitcoin Up or Down — 5 minute", "Up", 0.50, "btc-5m")
    opp = scanner.evaluate(q, _StubSpot(-0.5), Settings(), held=set())
    assert opp.signal == "DOWN" and opp.side == "DOWN"


def test_scanner_neutral_when_flat_gives_no_trade_reason():
    q = MarketQuote("1", "t1", "Bitcoin Up or Down — 5 minute", "Up", 0.50, "btc-5m")
    opp = scanner.evaluate(q, _StubSpot(0.0), Settings(), held=set())
    assert opp.signal == "NEUTRAL" and not opp.will_trade
    assert "flat momentum" in opp.reason_no_trade


def test_scanner_rejects_price_outside_band():
    q = MarketQuote("1", "t1", "Bitcoin Up or Down — 5 minute", "Up", 0.95, "btc-5m")
    opp = scanner.evaluate(q, _StubSpot(0.5), Settings(entry_price_max=0.7), held=set())
    assert not opp.will_trade and "outside entry band" in opp.reason_no_trade


# --------------------------------------------------------------------------- #
# Price formulas
# --------------------------------------------------------------------------- #
def test_take_profit_price_formula():
    s = Settings(take_profit_pct=10.0)
    assert s.take_profit_price(0.50) == 0.55
    assert s.take_profit_price(0.60) == 0.66


def test_stop_loss_price_formula():
    s = Settings(stop_loss_pct=10.0)
    assert s.stop_loss_price(0.50) == 0.45
    assert s.stop_loss_price(0.60) == 0.54


def test_exit_reason_labels_default_to_10_percent():
    s = Settings()
    assert s.take_profit_reason == "TAKE_PROFIT_10_PERCENT"
    assert s.stop_loss_reason == "STOP_LOSS_10_PERCENT"


def test_exit_reason_labels_track_custom_percent():
    s = Settings(take_profit_pct=15, stop_loss_pct=5)
    assert s.take_profit_reason == "TAKE_PROFIT_15_PERCENT"
    assert s.stop_loss_reason == "STOP_LOSS_5_PERCENT"


# --------------------------------------------------------------------------- #
# Position metrics
# --------------------------------------------------------------------------- #
def _pos(**kw):
    base = dict(market_id="m", token_id="t", question="q", outcome="Up", side="UP",
                entry_price=0.50, size=100, take_profit_price=0.55, stop_loss_price=0.45)
    base.update(kw)
    return Position(**base)


def test_unrealized_pnl_and_pct():
    pos = _pos(size=200)
    pos.mark_price = 0.55
    assert round(pos.unrealized_pnl, 4) == 10.0
    assert pos.unrealized_pnl_pct == 10.0


def test_hit_take_profit_and_stop_loss():
    pos = _pos()
    pos.mark_price = 0.55
    assert pos.hit_take_profit()
    pos.mark_price = 0.45
    assert pos.hit_stop_loss()
    pos.mark_price = 0.50
    assert not pos.hit_take_profit() and not pos.hit_stop_loss()


def test_stop_loss_can_be_disabled():
    pos = _pos(stop_loss_enabled=False)
    pos.mark_price = 0.30
    assert not pos.hit_stop_loss()


# --------------------------------------------------------------------------- #
# Engine exit behaviour
# --------------------------------------------------------------------------- #
def test_engine_closes_at_take_profit():
    engine = _engine(0.50)
    engine.tick()
    assert len(engine.positions) == 1
    engine.provider._price = 0.55  # +10% -> take profit
    result = engine.tick(allow_entries=False)
    assert len(result.closed) == 1
    trade = result.closed[0]
    assert trade.exit_reason == "TAKE_PROFIT_10_PERCENT"
    assert round(trade.profit_pct, 2) == 10.0
    assert trade.result == "WIN"
    assert len(engine.positions) == 0


def test_engine_closes_at_stop_loss():
    engine = _engine(0.50)
    engine.tick()
    engine.provider._price = 0.45  # -10% -> stop loss
    result = engine.tick(allow_entries=False)
    assert len(result.closed) == 1
    assert result.closed[0].exit_reason == "STOP_LOSS_10_PERCENT"
    assert round(result.closed[0].profit_pct, 2) == -10.0
    assert result.closed[0].result == "LOSS"


def test_engine_does_not_close_before_threshold():
    engine = _engine(0.50)
    engine.tick()
    engine.provider._price = 0.54  # +8%, below TP
    result = engine.tick(allow_entries=False)
    assert len(result.closed) == 0
    assert len(engine.positions) == 1


def test_scan_report_records_counts_and_skips():
    engine = _engine(0.50, momentum=0.0)  # neutral -> entry skipped with reason
    engine.tick()
    report = engine.last_report
    assert report.markets_returned == 1
    assert report.markets_accepted == 1
    assert report.skipped and "flat momentum" in report.skipped[0][1]


# --------------------------------------------------------------------------- #
# Performance & daily stats
# --------------------------------------------------------------------------- #
def _trade(pnl_pct, when):
    entry = 0.50
    close = round(entry * (1 + pnl_pct / 100), 4)
    return ClosedTrade(
        market_id="m", question="q", outcome="Up", side="UP",
        entry_time=when, close_time=when, entry_price=entry, close_price=close,
        size=100, exit_reason="TAKE_PROFIT_10_PERCENT" if pnl_pct > 0 else "STOP_LOSS_10_PERCENT",
    )


def test_performance_metrics():
    now = datetime.now(timezone.utc)
    trades = [_trade(10, now), _trade(10, now), _trade(-10, now)]  # 2 wins, 1 loss
    p = compute_performance(trades)
    assert p["total_trades"] == 3 and p["wins"] == 2 and p["losses"] == 1
    assert p["win_rate"] == round(2 / 3 * 100, 1)
    # gross profit 2*5=10, gross loss 5 -> profit factor 2.0
    assert p["profit_factor"] == 2.0
    assert p["avg_win"] == 5.0 and p["avg_loss"] == -5.0
    assert p["net_pnl"] == 5.0


def test_performance_empty():
    p = compute_performance([])
    assert p["total_trades"] == 0 and p["win_rate"] == 0.0


def test_daily_stats_filters_today():
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)
    trades = [_trade(10, now), _trade(-10, now), _trade(10, yesterday)]
    d = daily_stats(trades, now)
    assert d["trades_today"] == 2
    assert d["win_rate_today"] == 50.0


# --------------------------------------------------------------------------- #
# Safety: defaults & no real orders
# --------------------------------------------------------------------------- #
def test_defaults_are_paper_and_safe():
    s = Settings()
    assert s.mode == TradingMode.LIVE_DATA_PAPER
    assert s.live_trading_enabled is False
    assert s.real_orders_active is False


def test_default_universe_is_btc_eth_5m_15m():
    s = Settings()
    assert s.assets == ("BTC", "ETH")
    assert s.timeframes == ("5m", "15m")
    assert s.restrict_to_crypto_shortterm is True


def test_real_orders_only_when_live_and_enabled():
    assert Settings(mode=TradingMode.LIVE, live_trading_enabled=False).real_orders_active is False
    assert Settings(mode=TradingMode.LIVE, live_trading_enabled=True).real_orders_active is True
    assert Settings(mode=TradingMode.LIVE_DATA_PAPER, live_trading_enabled=True).real_orders_active is False


def test_paper_engine_never_submits_real_orders():
    engine = _engine(0.50)
    engine.tick()
    engine.provider._price = 0.55
    engine.tick(allow_entries=False)  # would raise if the real-order path were hit
