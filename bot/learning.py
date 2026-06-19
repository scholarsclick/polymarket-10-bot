"""Adaptive learning layer.

Keeps an online win-rate estimate for each (asset, timeframe, direction)
"bucket" using a Beta-Bernoulli model, learning from every closed trade. The
learned win rate is fed back into the scanner to (a) adjust confidence and
(b) veto buckets that have proven unprofitable. State is persisted to disk so
the bot keeps learning across restarts.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


def bucket_key(asset: str, timeframe: str, direction: str) -> str:
    return f"{(asset or '?').upper()}|{(timeframe or '?').lower()}|{(direction or '?').upper()}"


@dataclass
class BucketStat:
    wins: int = 0
    losses: int = 0

    @property
    def n(self) -> int:
        return self.wins + self.losses


class LearningModel:
    def __init__(
        self,
        path: str | None = None,
        prior_alpha: float = 2.0,
        prior_beta: float = 2.0,
        min_samples: int = 6,
        winrate_floor: float = 0.45,
    ):
        self.path = path
        self.prior_alpha = prior_alpha
        self.prior_beta = prior_beta
        self.min_samples = min_samples
        self.winrate_floor = winrate_floor
        self.buckets: dict[str, BucketStat] = {}
        if path:
            self.load()

    # ------------------------------------------------------------------ #
    def record(self, asset: str, timeframe: str, direction: str, won: bool) -> None:
        key = bucket_key(asset, timeframe, direction)
        stat = self.buckets.setdefault(key, BucketStat())
        if won:
            stat.wins += 1
        else:
            stat.losses += 1

    def record_trade(self, trade) -> None:
        """Record a ClosedTrade outcome (win = positive realized PnL)."""
        self.record(
            getattr(trade, "asset", "") or _asset_from_trade(trade),
            getattr(trade, "timeframe", "") or "",
            trade.side,
            trade.realized_pnl > 0,
        )

    # ------------------------------------------------------------------ #
    def winrate(self, asset: str, timeframe: str, direction: str) -> float:
        """Posterior-mean win rate (Beta prior + observed wins/losses)."""
        stat = self.buckets.get(bucket_key(asset, timeframe, direction))
        wins = stat.wins if stat else 0
        n = stat.n if stat else 0
        return (self.prior_alpha + wins) / (self.prior_alpha + self.prior_beta + n)

    def samples(self, asset: str, timeframe: str, direction: str) -> int:
        stat = self.buckets.get(bucket_key(asset, timeframe, direction))
        return stat.n if stat else 0

    def should_trade(self, asset: str, timeframe: str, direction: str) -> tuple[bool, str]:
        """Veto a bucket only once it has enough samples and a poor win rate."""
        n = self.samples(asset, timeframe, direction)
        wr = self.winrate(asset, timeframe, direction)
        if n >= self.min_samples and wr < self.winrate_floor:
            return False, f"learned win rate {wr*100:.0f}% < floor {self.winrate_floor*100:.0f}% (n={n})"
        return True, ""

    def confidence_multiplier(self, asset: str, timeframe: str, direction: str) -> float:
        """Scale base confidence by learned edge: 1.0 at 50% win rate, up/down
        toward the extremes, bounded to [0.5, 1.5]."""
        wr = self.winrate(asset, timeframe, direction)
        return max(0.5, min(1.5, 0.5 + wr))

    # ------------------------------------------------------------------ #
    def stats_rows(self) -> list[dict]:
        rows = []
        for key, stat in sorted(self.buckets.items()):
            asset, tf, direction = key.split("|")
            rows.append({
                "Asset": asset, "TF": tf, "Dir": direction,
                "Trades": stat.n, "Wins": stat.wins, "Losses": stat.losses,
                "Win rate": f"{stat.wins / stat.n * 100:.0f}%" if stat.n else "—",
                "Learned WR": f"{self.winrate(asset, tf, direction)*100:.0f}%",
            })
        return rows

    # ------------------------------------------------------------------ #
    def load(self) -> None:
        try:
            with open(self.path, "r") as fh:
                data = json.load(fh)
            self.buckets = {
                k: BucketStat(int(v.get("wins", 0)), int(v.get("losses", 0)))
                for k, v in data.get("buckets", {}).items()
            }
        except (FileNotFoundError, json.JSONDecodeError, ValueError, TypeError):
            self.buckets = {}

    def save(self) -> None:
        if not self.path:
            return
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        data = {"buckets": {k: {"wins": v.wins, "losses": v.losses}
                            for k, v in self.buckets.items()}}
        tmp = f"{self.path}.tmp"
        with open(tmp, "w") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, self.path)


def _asset_from_trade(trade) -> str:
    from .market_data import classify_asset
    return classify_asset(trade.question) or ""
