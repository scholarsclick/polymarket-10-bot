"""Headless paper-trading loop (no dashboard).

Usage:
    python run_bot.py --ticks 50 --interval 1

Defaults to Live Data Paper Trading with a 10% take-profit and 10% stop-loss.
Never places real orders.
"""

from __future__ import annotations

import argparse
import time

from bot.config import Settings
from bot.engine import TradingEngine


def main() -> None:
    parser = argparse.ArgumentParser(description="Polymarket 10% take-profit paper bot")
    parser.add_argument("--ticks", type=int, default=20, help="number of ticks to run")
    parser.add_argument("--interval", type=float, default=1.0, help="seconds between ticks")
    parser.add_argument("--take-profit", type=float, default=10.0)
    parser.add_argument("--stop-loss", type=float, default=10.0)
    parser.add_argument("--no-stop-loss", action="store_true")
    args = parser.parse_args()

    settings = Settings(
        take_profit_pct=args.take_profit,
        stop_loss_pct=args.stop_loss,
        stop_loss_enabled=not args.no_stop_loss,
    )
    engine = TradingEngine(settings)
    print(f"[feed] {engine.feed_message}")
    print(
        f"[config] TP={settings.take_profit_pct}% SL="
        f"{settings.stop_loss_pct if settings.stop_loss_enabled else 'off'}% "
        f"mode={settings.mode.value} real_orders={settings.real_orders_active}"
    )

    for i in range(args.ticks):
        result = engine.tick()
        for pos in result.opened:
            print(
                f"[open ] {pos.question[:50]!r} entry={pos.entry_price:.4f} "
                f"TP={pos.take_profit_price:.4f} SL={pos.stop_loss_price:.4f}"
            )
        for trade in result.closed:
            print(
                f"[close] {trade.question[:50]!r} {trade.exit_reason} "
                f"entry={trade.entry_price:.4f} close={trade.close_price:.4f} "
                f"profit={trade.profit_pct:+.2f}%"
            )
        print(
            f"[tick {i + 1:>3}] open={len(engine.positions)} "
            f"uPnL={engine.unrealized_pnl:+.2f} rPnL={engine.realized_pnl:+.2f}"
        )
        if i < args.ticks - 1:
            time.sleep(args.interval)

    print("\n=== Closed trades ===")
    for trade in engine.closed_trades:
        print(
            f"{trade.exit_reason:<24} entry={trade.entry_price:.4f} "
            f"close={trade.close_price:.4f} profit={trade.profit_pct:+.2f}% "
            f"held={trade.hold_seconds:.0f}s"
        )
    print(f"\nRealized PnL: {engine.realized_pnl:+.2f} {settings.base_currency}")


if __name__ == "__main__":
    main()
