"""Tests for take-profit / stop-loss math and exit behaviour."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import Settings, TradingMode
from bot.engine import TradingEngine
from bot.market_data import MarketQuote, SimulatedProvider, matches_universe
from bot.models import Position


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
    # right timeframe, wrong asset
    assert not matches_universe("Solana Up or Down — 5 minute", assets, tfs)
    # right asset, wrong timeframe
    assert not matches_universe("Bitcoin Up or Down — 1 hour", assets, tfs)
    # not a crypto up/down market at all
    assert not matches_universe("Will the Fed cut rates?", assets, tfs)


def test_5m_pattern_does_not_match_15m_text():
    # "15 minute" must not be caught by the 5m pattern.
    assert not matches_universe("Bitcoin Up or Down — 15 minute", ("BTC",), ("5m",))
    assert matches_universe("Bitcoin Up or Down — 15 minute", ("BTC",), ("15m",))


def test_simulated_catalog_is_all_btc_eth_short_markets():
    quotes = SimulatedProvider().list_markets(50)
    assert quotes
    for q in quotes:
        assert matches_universe(q.search_text, ("BTC", "ETH"), ("5m", "15m"))


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
    engine = TradingEngine(settings, provider=MixedProvider())
    engine.tick()
    # Only the BTC 5m market should have been entered.
    assert [p.token_id for p in engine.positions.values()] == ["t1"]


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
def test_unrealized_pnl_and_pct():
    pos = Position(
        market_id="m", token_id="t", question="q", outcome="Yes",
        entry_price=0.50, size=200, take_profit_price=0.55, stop_loss_price=0.45,
    )
    pos.mark_price = 0.55
    assert round(pos.unrealized_pnl, 4) == 10.0  # (0.55-0.50)*200
    assert pos.unrealized_pnl_pct == 10.0


def test_hit_take_profit_and_stop_loss():
    pos = Position(
        market_id="m", token_id="t", question="q", outcome="Yes",
        entry_price=0.50, size=100, take_profit_price=0.55, stop_loss_price=0.45,
    )
    pos.mark_price = 0.55
    assert pos.hit_take_profit()
    pos.mark_price = 0.45
    assert pos.hit_stop_loss()
    pos.mark_price = 0.50
    assert not pos.hit_take_profit() and not pos.hit_stop_loss()


def test_stop_loss_can_be_disabled():
    pos = Position(
        market_id="m", token_id="t", question="q", outcome="Yes",
        entry_price=0.50, size=100, take_profit_price=0.55, stop_loss_price=0.45,
        stop_loss_enabled=False,
    )
    pos.mark_price = 0.30
    assert not pos.hit_stop_loss()


# --------------------------------------------------------------------------- #
# Engine exit behaviour
# --------------------------------------------------------------------------- #
class _StubProvider:
    """Provider whose price we control to force exits."""

    def __init__(self, price):
        self.name = "stub"
        self._price = price
        self._quote = MarketQuote("m1", "tok1", "Will X happen?", "Yes", 0.50)

    def list_markets(self, limit):
        return [self._quote]

    def get_price(self, token_id):
        return self._price


def test_engine_closes_at_take_profit():
    settings = Settings(order_size_usdc=100, entry_price_min=0.4, entry_price_max=0.6,
                        restrict_to_crypto_shortterm=False)
    engine = TradingEngine(settings, provider=_StubProvider(0.50))
    engine.tick()  # opens at 0.50
    assert len(engine.positions) == 1

    engine.provider._price = 0.55  # +10% -> take profit
    result = engine.tick(allow_entries=False)
    assert len(result.closed) == 1
    trade = result.closed[0]
    assert trade.exit_reason == "TAKE_PROFIT_10_PERCENT"
    assert round(trade.profit_pct, 2) == 10.0
    assert len(engine.positions) == 0


def test_engine_closes_at_stop_loss():
    settings = Settings(order_size_usdc=100, entry_price_min=0.4, entry_price_max=0.6,
                        restrict_to_crypto_shortterm=False)
    engine = TradingEngine(settings, provider=_StubProvider(0.50))
    engine.tick()
    engine.provider._price = 0.45  # -10% -> stop loss
    result = engine.tick(allow_entries=False)
    assert len(result.closed) == 1
    assert result.closed[0].exit_reason == "STOP_LOSS_10_PERCENT"
    assert round(result.closed[0].profit_pct, 2) == -10.0


def test_engine_does_not_close_before_threshold():
    settings = Settings(order_size_usdc=100, entry_price_min=0.4, entry_price_max=0.6,
                        restrict_to_crypto_shortterm=False)
    engine = TradingEngine(settings, provider=_StubProvider(0.50))
    engine.tick()
    engine.provider._price = 0.54  # +8%, below TP
    result = engine.tick(allow_entries=False)
    assert len(result.closed) == 0
    assert len(engine.positions) == 1


# --------------------------------------------------------------------------- #
# Safety: defaults & no real orders
# --------------------------------------------------------------------------- #
def test_defaults_are_paper_and_safe():
    s = Settings()
    assert s.mode == TradingMode.LIVE_DATA_PAPER
    assert s.live_trading_enabled is False
    assert s.real_orders_active is False


def test_real_orders_only_when_live_and_enabled():
    assert Settings(mode=TradingMode.LIVE, live_trading_enabled=False).real_orders_active is False
    assert Settings(mode=TradingMode.LIVE, live_trading_enabled=True).real_orders_active is True
    assert Settings(mode=TradingMode.LIVE_DATA_PAPER, live_trading_enabled=True).real_orders_active is False


def test_paper_engine_never_submits_real_orders():
    """Opening/closing in paper mode must not raise from the real-order guard."""
    settings = Settings(order_size_usdc=100, entry_price_min=0.4, entry_price_max=0.6,
                        restrict_to_crypto_shortterm=False)
    engine = TradingEngine(settings, provider=_StubProvider(0.50))
    engine.tick()
    engine.provider._price = 0.55
    engine.tick(allow_entries=False)  # would raise if real order path were hit
