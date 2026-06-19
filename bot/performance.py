"""Performance and daily statistics computed from closed trades."""

from __future__ import annotations

from datetime import datetime, timezone


def compute_performance(closed_trades) -> dict:
    """Aggregate performance metrics over all closed trades."""
    n = len(closed_trades)
    if n == 0:
        return {
            "total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "net_pnl": 0.0, "roi_pct": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
            "profit_factor": 0.0, "max_drawdown": 0.0,
        }

    wins = [t for t in closed_trades if t.realized_pnl > 0]
    losses = [t for t in closed_trades if t.realized_pnl < 0]
    gross_profit = sum(t.realized_pnl for t in wins)
    gross_loss = sum(t.realized_pnl for t in losses)  # negative
    net_pnl = sum(t.realized_pnl for t in closed_trades)
    invested = sum(t.cost_basis for t in closed_trades) or 1.0

    profit_factor = (gross_profit / abs(gross_loss)) if gross_loss != 0 else (
        float("inf") if gross_profit > 0 else 0.0
    )

    return {
        "total_trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / n * 100.0, 1),
        "net_pnl": round(net_pnl, 2),
        "roi_pct": round(net_pnl / invested * 100.0, 2),
        "avg_win": round(gross_profit / len(wins), 2) if wins else 0.0,
        "avg_loss": round(gross_loss / len(losses), 2) if losses else 0.0,
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else float("inf"),
        "max_drawdown": round(_max_drawdown(closed_trades), 2),
    }


def _max_drawdown(closed_trades) -> float:
    """Largest peak-to-trough drop of the cumulative PnL curve (in currency)."""
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in sorted(closed_trades, key=lambda x: x.close_time):
        cumulative += t.realized_pnl
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)
    return max_dd


def daily_stats(closed_trades, day: datetime | None = None) -> dict:
    """Stats restricted to trades closed on ``day`` (UTC, default today)."""
    day = (day or datetime.now(timezone.utc)).date()
    todays = [t for t in closed_trades if t.close_time.date() == day]
    n = len(todays)
    wins = [t for t in todays if t.realized_pnl > 0]
    return {
        "trades_today": n,
        "win_rate_today": round(len(wins) / n * 100.0, 1) if n else 0.0,
        "pnl_today": round(sum(t.realized_pnl for t in todays), 2),
    }
