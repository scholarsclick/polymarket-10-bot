"""Market data providers.

``LivePolymarketProvider`` reads real markets/prices from Polymarket's public
APIs (Gamma for market discovery, CLOB for live mid prices). When those hosts
are not reachable — e.g. blocked by a network egress allowlist — the engine
falls back to ``SimulatedProvider`` so the dashboard keeps working offline.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field

import requests


@dataclass
class MarketQuote:
    """A tradable outcome with its current price."""

    market_id: str
    token_id: str
    question: str
    outcome: str
    price: float


class MarketDataError(RuntimeError):
    pass


class LivePolymarketProvider:
    """Reads live data from Polymarket's public Gamma + CLOB APIs."""

    def __init__(self, gamma_api_url: str, clob_api_url: str, timeout: float = 8.0):
        self.gamma_api_url = gamma_api_url.rstrip("/")
        self.clob_api_url = clob_api_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "polymarket-10-bot/1.0"})

    @property
    def name(self) -> str:
        return "Live Polymarket"

    def list_markets(self, limit: int) -> list[MarketQuote]:
        """Return currently active, tradable outcomes."""
        url = f"{self.gamma_api_url}/markets"
        params = {
            "active": "true",
            "closed": "false",
            "limit": str(limit),
            "order": "volume24hr",
            "ascending": "false",
        }
        resp = self.session.get(url, params=params, timeout=self.timeout)
        if resp.status_code != 200:
            raise MarketDataError(
                f"Gamma API returned {resp.status_code}: {resp.text[:120]}"
            )
        quotes: list[MarketQuote] = []
        for m in resp.json():
            try:
                token_ids = _maybe_json(m.get("clobTokenIds"))
                outcomes = _maybe_json(m.get("outcomes"))
                prices = _maybe_json(m.get("outcomePrices"))
                if not token_ids or not outcomes:
                    continue
                # Use the first outcome (typically "Yes") as the tradable token.
                price = float(prices[0]) if prices else 0.0
                quotes.append(
                    MarketQuote(
                        market_id=str(m.get("id")),
                        token_id=str(token_ids[0]),
                        question=str(m.get("question", "")),
                        outcome=str(outcomes[0]),
                        price=price,
                    )
                )
            except (ValueError, IndexError, TypeError):
                continue
        if not quotes:
            raise MarketDataError("Gamma API returned no tradable markets.")
        return quotes

    def get_price(self, token_id: str) -> float:
        """Return the live mid price for a CLOB token id."""
        url = f"{self.clob_api_url}/midpoint"
        resp = self.session.get(url, params={"token_id": token_id}, timeout=self.timeout)
        if resp.status_code != 200:
            raise MarketDataError(
                f"CLOB API returned {resp.status_code}: {resp.text[:120]}"
            )
        return float(resp.json().get("mid", 0.0))


class SimulatedProvider:
    """Offline price feed using a bounded random walk.

    Prices drift and diffuse within (0, 1) so paper positions eventually reach
    take-profit or stop-loss, which makes the dashboard demonstrable without
    network access.
    """

    def __init__(self, seed: int | None = None, drift: float = 0.004, vol: float = 0.012):
        self._rng = random.Random(seed)
        self._drift = drift
        self._vol = vol
        self._prices: dict[str, float] = {}
        self._catalog = self._build_catalog()

    @property
    def name(self) -> str:
        return "Simulated (offline)"

    def _build_catalog(self) -> list[MarketQuote]:
        seeds = [
            ("Will BTC close above $100k this month?", "Yes", 0.52),
            ("Will the Fed cut rates at the next meeting?", "Yes", 0.45),
            ("Will Team A win the championship?", "Yes", 0.61),
            ("Will it rain in NYC on election day?", "Yes", 0.38),
            ("Will ETH flip $5k before year end?", "Yes", 0.43),
            ("Will the incumbent win re-election?", "Yes", 0.55),
            ("Will the new film gross >$1B?", "Yes", 0.49),
            ("Will SpaceX launch succeed this week?", "Yes", 0.66),
        ]
        catalog = []
        for i, (q, outcome, p) in enumerate(seeds):
            tid = f"sim-token-{i}"
            self._prices[tid] = p
            catalog.append(
                MarketQuote(
                    market_id=f"sim-{i}",
                    token_id=tid,
                    question=q,
                    outcome=outcome,
                    price=p,
                )
            )
        return catalog

    def list_markets(self, limit: int) -> list[MarketQuote]:
        out = []
        for q in self._catalog[:limit]:
            out.append(
                MarketQuote(q.market_id, q.token_id, q.question, q.outcome, self._prices[q.token_id])
            )
        return out

    def get_price(self, token_id: str) -> float:
        cur = self._prices.get(token_id)
        if cur is None:
            cur = self._rng.uniform(0.3, 0.7)
        # Mean-reverting-ish random walk kept inside (0.01, 0.99).
        step = self._drift + self._rng.gauss(0.0, self._vol)
        nxt = min(0.99, max(0.01, cur + step))
        self._prices[token_id] = nxt
        return round(nxt, 4)


def _maybe_json(value):
    """Gamma returns some array fields as JSON-encoded strings."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return None


def build_provider(settings, force_simulated: bool = False):
    """Pick a provider, falling back to simulated data when live is unreachable.

    Returns ``(provider, used_fallback, message)``.
    """
    from .config import TradingMode

    if force_simulated or settings.mode == TradingMode.SIMULATED_PAPER:
        return SimulatedProvider(), True, "Using simulated price feed."

    live = LivePolymarketProvider(settings.gamma_api_url, settings.clob_api_url)
    try:
        live.list_markets(limit=1)
        return live, False, "Connected to live Polymarket data."
    except (MarketDataError, requests.RequestException) as exc:
        return (
            SimulatedProvider(),
            True,
            f"Live data unavailable ({type(exc).__name__}); fell back to simulated feed.",
        )
