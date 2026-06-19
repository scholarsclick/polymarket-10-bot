"""Spot price feed for the underlying assets (BTC, ETH ...).

Used by the dashboard's *Live Prices* section and by the opportunity scanner
to derive a momentum signal. Tries Binance first, then Coinbase, and finally
falls back to a simulated walk so the dashboard works offline.
"""

from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests

BINANCE_URL = "https://api.binance.com/api/v3"
COINBASE_URL = "https://api.coinbase.com/v2"
_SIM_BASE = {"BTC": 65000.0, "ETH": 3500.0, "SOL": 150.0, "XRP": 0.6}


@dataclass
class AssetTick:
    asset: str
    price: float = 0.0
    momentum_pct: float = 0.0  # short-term % change used as the signal
    history: deque = field(default_factory=lambda: deque(maxlen=12))


class SpotPriceFeed:
    """Maintains live spot prices + short-term momentum per asset."""

    def __init__(self, assets=("BTC", "ETH"), timeout: float = 6.0, seed: int | None = None):
        self.assets = [a.upper() for a in assets]
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "polymarket-10-bot/1.0"})
        self.ticks: dict[str, AssetTick] = {a: AssetTick(a) for a in self.assets}

        # Status surfaced in the debug panel.
        self.source: str = "—"
        self.binance_status: str = "unknown"
        self.coinbase_status: str = "unknown"
        self.last_updated: datetime | None = None

        self._rng = random.Random(seed)
        self._sim_prices = {a: _SIM_BASE.get(a, 100.0) for a in self.assets}

    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        """Refresh every asset, choosing the best available source."""
        binance = self._try_binance()
        if binance is not None:
            self.source = "Binance"
            self.binance_status = "ok"
            prices = binance
        else:
            coinbase = self._try_coinbase()
            if coinbase is not None:
                self.source = "Coinbase"
                self.coinbase_status = "ok"
                prices = coinbase
            else:
                self.source = "Simulated"
                prices = self._simulated()

        for asset, price in prices.items():
            tick = self.ticks[asset]
            tick.price = price
            tick.history.append(price)
            if len(tick.history) >= 2 and tick.history[0] > 0:
                tick.momentum_pct = round(
                    (tick.history[-1] / tick.history[0] - 1.0) * 100.0, 4
                )
        self.last_updated = datetime.now(timezone.utc)

    # ------------------------------------------------------------------ #
    def _try_binance(self) -> dict[str, float] | None:
        out: dict[str, float] = {}
        try:
            for asset in self.assets:
                r = self.session.get(
                    f"{BINANCE_URL}/ticker/price",
                    params={"symbol": f"{asset}USDT"},
                    timeout=self.timeout,
                )
                if r.status_code != 200:
                    self.binance_status = f"http {r.status_code}"
                    return None
                out[asset] = float(r.json()["price"])
            return out
        except (requests.RequestException, KeyError, ValueError) as exc:
            self.binance_status = f"error: {type(exc).__name__}"
            return None

    def _try_coinbase(self) -> dict[str, float] | None:
        out: dict[str, float] = {}
        try:
            for asset in self.assets:
                r = self.session.get(
                    f"{COINBASE_URL}/prices/{asset}-USD/spot", timeout=self.timeout
                )
                if r.status_code != 200:
                    self.coinbase_status = f"http {r.status_code}"
                    return None
                out[asset] = float(r.json()["data"]["amount"])
            return out
        except (requests.RequestException, KeyError, ValueError) as exc:
            self.coinbase_status = f"error: {type(exc).__name__}"
            return None

    def _simulated(self) -> dict[str, float]:
        if self.binance_status == "unknown":
            self.binance_status = "unreachable"
        if self.coinbase_status == "unknown":
            self.coinbase_status = "unreachable"
        out = {}
        for asset in self.assets:
            cur = self._sim_prices[asset]
            # Small random walk with occasional trend so momentum is meaningful.
            drift = self._rng.uniform(-0.0015, 0.0015)
            nxt = max(0.0001, cur * (1.0 + drift))
            self._sim_prices[asset] = nxt
            out[asset] = round(nxt, 2)
        return out

    # ------------------------------------------------------------------ #
    def price(self, asset: str) -> float:
        t = self.ticks.get(asset.upper())
        return t.price if t else 0.0

    def momentum(self, asset: str) -> float:
        t = self.ticks.get(asset.upper())
        return t.momentum_pct if t else 0.0

    @property
    def api_status(self) -> str:
        return f"{self.source} ({'live' if self.source != 'Simulated' else 'fallback'})"

    @property
    def last_updated_str(self) -> str:
        return self.last_updated.strftime("%H:%M:%S UTC") if self.last_updated else "never"
