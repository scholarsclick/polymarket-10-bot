"""Market data providers.

``LivePolymarketProvider`` reads real markets/prices from Polymarket's public
APIs (Gamma for market discovery, CLOB for live mid prices). When those hosts
are not reachable — e.g. blocked by a network egress allowlist — the engine
falls back to ``SimulatedProvider`` so the dashboard keeps working offline.

Markets are modelled as binary UP/DOWN crypto markets: each ``MarketQuote``
carries both the UP and DOWN prices/token ids, the underlying asset, the
timeframe, and the expiry time.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import requests


# Keyword/pattern maps used to keep only BTC/ETH short-duration up/down markets.
ASSET_KEYWORDS = {
    "BTC": (r"\bbtc\b", r"\bbitcoin\b", r"\bxbt\b"),
    "ETH": (r"\beth\b", r"\bethereum\b", r"\bether\b"),
    "SOL": (r"\bsol\b", r"\bsolana\b"),
    "XRP": (r"\bxrp\b", r"\bripple\b"),
}

# \b before the leading digit prevents "15 min" from matching the 5m pattern.
TIMEFRAME_PATTERNS = {
    "5m": (r"\b5\s*-?\s*m(?:in(?:ute)?s?)?\b",),
    "15m": (r"\b15\s*-?\s*m(?:in(?:ute)?s?)?\b",),
    "1h": (r"\b1\s*-?\s*h(?:our|r)?s?\b", r"\b60\s*-?\s*m(?:in(?:ute)?s?)?\b"),
    "1d": (r"\b1\s*-?\s*d(?:ay)?s?\b", r"\bdaily\b"),
}

TIMEFRAME_MINUTES = {"5m": 5, "15m": 15, "1h": 60, "1d": 1440}

# Map an asset to the symbol used by spot exchanges (Binance/Coinbase).
ASSET_SPOT_SYMBOL = {"BTC": "BTC", "ETH": "ETH", "SOL": "SOL", "XRP": "XRP"}


def classify_asset(text: str) -> str | None:
    blob = (text or "").lower()
    for asset, patterns in ASSET_KEYWORDS.items():
        if any(re.search(p, blob) for p in patterns):
            return asset
    return None


def classify_timeframe_by_duration(minutes: float | None) -> str | None:
    """Map a market's window length (minutes) to a known timeframe bucket.

    This is far more robust than text matching: a 5-minute market has an
    end-minus-start window of ~5 minutes regardless of how its title is phrased.
    """
    if not minutes or minutes <= 0:
        return None
    # (label, low, high) tolerance bands.
    bands = [("5m", 3, 8), ("15m", 11, 20), ("1h", 45, 75), ("1d", 1200, 1560)]
    for label, lo, hi in bands:
        if lo <= minutes <= hi:
            return label
    return None


def classify_timeframe(text: str, duration_min: float | None = None) -> str | None:
    """Classify timeframe by window duration first, then fall back to text."""
    by_dur = classify_timeframe_by_duration(duration_min)
    if by_dur:
        return by_dur
    blob = (text or "").lower()
    for tf in ("5m", "15m", "1h", "1d"):
        if any(re.search(p, blob) for p in TIMEFRAME_PATTERNS[tf]):
            return tf
    return None


def matches_universe(text: str, assets, timeframes) -> bool:
    """True if ``text`` (question/slug) names one of the assets AND timeframes."""
    asset = classify_asset(text)
    if asset is None or asset not in {a.upper() for a in assets}:
        return False
    tf = classify_timeframe(text)
    return tf is not None and tf in {t.lower() for t in timeframes}


@dataclass
class MarketQuote:
    """A binary UP/DOWN crypto market with both sides priced."""

    market_id: str
    token_id: str          # UP token id (primary side, kept for back-compat)
    question: str
    outcome: str           # primary outcome label (UP)
    price: float           # UP price (kept for back-compat)
    slug: str = ""
    up_price: float = 0.0
    down_price: float = 0.0
    up_token_id: str = ""
    down_token_id: str = ""
    asset: str = ""
    timeframe: str = ""
    expiry: datetime | None = None
    duration_min: float | None = None

    def __post_init__(self) -> None:
        if not self.up_token_id:
            self.up_token_id = self.token_id
        if self.up_price == 0.0:
            self.up_price = self.price
        if self.down_price == 0.0:
            # Binary market: down ≈ 1 - up.
            self.down_price = round(max(0.0, 1.0 - self.up_price), 4)
        if not self.asset:
            self.asset = classify_asset(self.search_text) or ""
        if not self.timeframe:
            self.timeframe = classify_timeframe(self.search_text, self.duration_min) or ""

    def in_universe(self, assets, timeframes) -> bool:
        return (
            self.asset in {a.upper() for a in assets}
            and self.timeframe in {t.lower() for t in timeframes}
        )

    @property
    def search_text(self) -> str:
        """Combined text used for asset/timeframe matching."""
        return f"{self.question} {self.slug}"

    @property
    def spread(self) -> float:
        """Bid/ask-style spread proxy: 1 - (up + down). 0 = perfectly priced."""
        return round(1.0 - (self.up_price + self.down_price), 4)

    def price_for(self, side: str) -> float:
        return self.up_price if side.upper() == "UP" else self.down_price

    def token_for(self, side: str) -> str:
        return self.up_token_id if side.upper() == "UP" else self.down_token_id

    def expiry_str(self) -> str:
        return self.expiry.strftime("%Y-%m-%d %H:%M:%S") if self.expiry else "—"


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
        #: Sample of the most recent raw markets seen, for the debug panel.
        self.last_raw: list[dict] = []

    @property
    def name(self) -> str:
        return "Live Polymarket"

    def list_markets(self, limit: int) -> list[MarketQuote]:
        """Return currently active, tradable binary markets (paginated)."""
        url = f"{self.gamma_api_url}/markets"
        quotes: list[MarketQuote] = []
        raw: list[dict] = []
        page = min(100, limit)
        offset = 0
        first_status = None
        while offset < limit:
            params = {
                "active": "true",
                "closed": "false",
                "limit": str(page),
                "offset": str(offset),
                "order": "endDate",
                "ascending": "true",  # soonest-expiring first → short markets surface
            }
            resp = self.session.get(url, params=params, timeout=self.timeout)
            if resp.status_code != 200:
                if offset == 0:
                    raise MarketDataError(
                        f"Gamma API returned {resp.status_code}: {resp.text[:120]}"
                    )
                break
            batch = resp.json()
            if not batch:
                break
            for m in batch:
                quote = self._parse_market(m)
                if quote is not None:
                    quotes.append(quote)
                    raw.append((m, quote))
            offset += page

        # Build a diagnostic sample, prioritising crypto-looking markets so the
        # debug panel reveals exactly how real BTC/ETH markets are classified.
        def _is_crypto(pair):
            blob = (pair[1].question + " " + pair[1].slug).lower()
            return any(h in blob for h in ("bitcoin", "btc", "ethereum", "eth", "solana", "sol", "xrp"))

        raw.sort(key=lambda pair: not _is_crypto(pair))  # crypto first
        self.last_raw = [_diag_row(m, q) for m, q in raw[:30]]
        if not quotes:
            raise MarketDataError("Gamma API returned no tradable markets.")
        return quotes

    def _parse_market(self, m: dict) -> MarketQuote | None:
        try:
            token_ids = _maybe_json(m.get("clobTokenIds")) or []
            outcomes = _maybe_json(m.get("outcomes")) or []
            prices = _maybe_json(m.get("outcomePrices")) or []
            if len(token_ids) < 2 or len(outcomes) < 2 or len(prices) < 2:
                return None
            up_idx = 0
            for i, o in enumerate(outcomes):
                if str(o).strip().lower() in {"up", "yes"}:
                    up_idx = i
                    break
            down_idx = 1 - up_idx if len(outcomes) == 2 else (up_idx + 1) % len(outcomes)
            end = _parse_dt(m.get("endDate") or m.get("end_date_iso"))
            duration = _window_minutes(m, end)
            return MarketQuote(
                market_id=str(m.get("id")),
                token_id=str(token_ids[up_idx]),
                question=str(m.get("question", "")),
                outcome=str(outcomes[up_idx]),
                price=float(prices[up_idx]),
                slug=str(m.get("slug", "")),
                up_price=float(prices[up_idx]),
                down_price=float(prices[down_idx]),
                up_token_id=str(token_ids[up_idx]),
                down_token_id=str(token_ids[down_idx]),
                expiry=end,
                duration_min=duration,
            )
        except (ValueError, IndexError, TypeError):
            return None

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

    def __init__(self, seed: int | None = None, drift: float = 0.0, vol: float = 0.02):
        self._rng = random.Random(seed)
        self._drift = drift
        self._vol = vol
        self._prices: dict[str, float] = {}
        self._tf: dict[str, str] = {}  # market_id -> timeframe
        self.last_raw: list[dict] = []
        self._catalog = self._build_catalog()

    @property
    def name(self) -> str:
        return "Simulated (offline)"

    def _build_catalog(self) -> list[MarketQuote]:
        now = datetime.now(timezone.utc)
        # (question, slug, up_price, timeframe)
        seeds = [
            ("Bitcoin Up or Down — 5 minute", "bitcoin-up-or-down-5m", 0.51, "5m"),
            ("Bitcoin Up or Down — 15 minute", "bitcoin-up-or-down-15m", 0.48, "15m"),
            ("Ethereum Up or Down — 5 minute", "ethereum-up-or-down-5m", 0.53, "5m"),
            ("Ethereum Up or Down — 15 minute", "ethereum-up-or-down-15m", 0.46, "15m"),
            ("Bitcoin Up or Down — 5 minute (next)", "bitcoin-up-or-down-5m-2", 0.55, "5m"),
            ("Ethereum Up or Down — 15 minute (next)", "ethereum-up-or-down-15m-2", 0.44, "15m"),
            ("Bitcoin Up or Down — 15 minute (next)", "bitcoin-up-or-down-15m-2", 0.49, "15m"),
            ("Ethereum Up or Down — 5 minute (next)", "ethereum-up-or-down-5m-2", 0.50, "5m"),
        ]
        catalog = []
        for i, (q, slug, up, tf) in enumerate(seeds):
            up_tid, down_tid = f"sim-{i}-up", f"sim-{i}-down"
            self._prices[up_tid] = up
            self._prices[down_tid] = round(1.0 - up, 4)
            self._tf[f"sim-{i}"] = tf
            catalog.append(
                MarketQuote(
                    market_id=f"sim-{i}",
                    token_id=up_tid,
                    question=q,
                    outcome="Up",
                    price=up,
                    slug=slug,
                    up_price=up,
                    down_price=round(1.0 - up, 4),
                    up_token_id=up_tid,
                    down_token_id=down_tid,
                    expiry=now + timedelta(minutes=TIMEFRAME_MINUTES[tf]),
                    timeframe=tf,
                    duration_min=TIMEFRAME_MINUTES[tf],
                )
            )
        return catalog

    def list_markets(self, limit: int) -> list[MarketQuote]:
        now = datetime.now(timezone.utc)
        out, raw = [], []
        for q in self._catalog[:limit]:
            tf = self._tf[q.market_id]
            # Roll the expiry forward so the countdown always looks live.
            secs_into = int(now.timestamp()) % (TIMEFRAME_MINUTES[tf] * 60)
            expiry = now + timedelta(seconds=TIMEFRAME_MINUTES[tf] * 60 - secs_into)
            out.append(
                MarketQuote(
                    market_id=q.market_id,
                    token_id=q.up_token_id,
                    question=q.question,
                    outcome="Up",
                    price=self._prices[q.up_token_id],
                    slug=q.slug,
                    up_price=self._prices[q.up_token_id],
                    down_price=self._prices[q.down_token_id],
                    up_token_id=q.up_token_id,
                    down_token_id=q.down_token_id,
                    expiry=expiry,
                    timeframe=tf,
                    duration_min=TIMEFRAME_MINUTES[tf],
                )
            )
            raw.append({"title": q.question[:60], "asset": q.asset, "tf": tf,
                        "dur_min": TIMEFRAME_MINUTES[tf]})
        self.last_raw = raw
        return out

    def get_price(self, token_id: str) -> float:
        cur = self._prices.get(token_id)
        if cur is None:
            cur = self._rng.uniform(0.3, 0.7)
        step = self._drift + self._rng.gauss(0.0, self._vol)
        nxt = min(0.99, max(0.01, cur + step))
        self._prices[token_id] = nxt
        # Keep the paired side roughly complementary so spreads stay realistic.
        for tid in self._prices:
            if tid != token_id and tid.rsplit("-", 1)[0] == token_id.rsplit("-", 1)[0]:
                self._prices[tid] = round(1.0 - nxt, 4)
        return round(nxt, 4)


def _diag_row(m: dict, quote: "MarketQuote") -> dict:
    """A compact, copyable diagnostic row for one market."""
    dates = {k: m.get(k) for k in
             ("startDate", "endDate", "gameStartTime", "acceptingOrdersTimestamp")
             if m.get(k)}
    return {
        "title": quote.question[:70],
        "slug": quote.slug[:50],
        "asset": quote.asset or "?",
        "tf": quote.timeframe or "?",
        "dur_min": quote.duration_min,
        "dates": dates,
    }


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


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


# Candidate "window start" fields, in order of how likely they mark the actual
# trading window (not the listing date).
_START_FIELDS = (
    "gameStartTime", "eventStartTime", "startTime", "acceptingOrdersTimestamp",
    "startDate", "start_date_iso",
)


def _window_minutes(m: dict, end: datetime | None) -> float | None:
    """Best estimate of a market's window length, in minutes.

    Polymarket's ``startDate`` is often the *listing* date (days before
    resolution), which would massively overstate the window for a 5/15-minute
    market. We therefore take the **smallest positive** duration across all
    available start fields, which picks the true short window when a
    ``gameStartTime``-style field is present.
    """
    if end is None:
        return None
    candidates = []
    for field_name in _START_FIELDS:
        start = _parse_dt(m.get(field_name))
        if start is None:
            continue
        minutes = (end - start).total_seconds() / 60.0
        if minutes > 0:
            candidates.append(minutes)
    return round(min(candidates), 4) if candidates else None


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
