"""Opportunity scanner.

Turns each candidate market into an ``Opportunity`` with a signal direction
(UP/DOWN/NEUTRAL), a confidence score, and human-readable reasons for trading
or skipping. The signal is a transparent momentum rule on the underlying spot
price: if spot is rising the UP side is favoured, if falling the DOWN side is.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class Opportunity:
    market_id: str
    title: str
    asset: str
    timeframe: str
    expiry: datetime | None
    up_price: float
    down_price: float
    spread: float
    signal: str          # "UP" | "DOWN" | "NEUTRAL"
    confidence: float    # 0..100
    will_trade: bool
    side: str            # chosen side if will_trade, else signal
    entry_price: float   # price of the chosen side
    reason_trade: str
    reason_no_trade: str
    token_id: str = ""
    learned_winrate: float = 0.0  # historical win rate for this bucket (0..1)
    learned_samples: int = 0

    def time_left_str(self) -> str:
        return _time_left(self.expiry)

    def to_dashboard_row(self) -> dict:
        return {
            "Market": self.title,
            "Asset": self.asset,
            "TF": self.timeframe,
            "Expiry": self.expiry.strftime("%H:%M:%S") if self.expiry else "—",
            "UP": round(self.up_price, 3),
            "DOWN": round(self.down_price, 3),
            "Spread": round(self.spread, 3),
            "Signal": self.signal,
            "Confidence": f"{self.confidence:.0f}%",
            "Hist WR": f"{self.learned_winrate*100:.0f}% (n={self.learned_samples})",
            "Trade?": "✅" if self.will_trade else "—",
            "Reason (trade)": self.reason_trade or "",
            "Reason (no trade)": self.reason_no_trade or "",
        }


def evaluate(quote, spot_feed, settings, held: set[str], learner=None) -> Opportunity:
    """Build an Opportunity for a single market quote.

    The ``learner`` (optional) supplies a historical win rate per
    (asset, timeframe, direction) bucket, which adjusts confidence and can veto
    buckets that have proven unprofitable.
    """
    asset = quote.asset or "?"
    timeframe = quote.timeframe or "?"
    momentum = spot_feed.momentum(asset)
    thr = settings.momentum_threshold_pct

    # 1) Direction from spot momentum.
    if momentum >= thr:
        signal = "UP"
    elif momentum <= -thr:
        signal = "DOWN"
    else:
        signal = "NEUTRAL"

    side = signal if signal != "NEUTRAL" else "UP"
    entry_price = quote.price_for(side)

    # 2) Base confidence from momentum strength, bounded to [50, 95].
    if signal == "NEUTRAL":
        confidence = round(min(49.0, 50.0 * abs(momentum) / thr if thr else 0.0), 1)
    else:
        over = (abs(momentum) - thr) / thr if thr else 0.0
        confidence = round(min(95.0, 55.0 + 40.0 * min(1.0, over)), 1)

    # 3) Learned adjustment: blend in the historical win rate for this bucket.
    learned_wr = 0.0
    learned_n = 0
    learn_veto = ""
    if learner is not None and signal != "NEUTRAL":
        learned_wr = learner.winrate(asset, timeframe, side)
        learned_n = learner.samples(asset, timeframe, side)
        confidence = round(min(98.0, confidence * learner.confidence_multiplier(asset, timeframe, side)), 1)
        ok, learn_veto = learner.should_trade(asset, timeframe, side)

    # 4) Trade gates → reasons.
    reason_trade = ""
    reason_no_trade = ""
    will_trade = True

    if signal == "NEUTRAL":
        will_trade = False
        reason_no_trade = f"flat momentum ({momentum:+.3f}% < ±{thr:.3f}%)"
    elif quote.token_id and quote.token_for(side) in held:
        will_trade = False
        reason_no_trade = "already holding this side"
    elif not (settings.entry_price_min <= entry_price <= settings.entry_price_max):
        will_trade = False
        reason_no_trade = (
            f"{side} priced {entry_price:.2f} outside entry band "
            f"{settings.entry_price_min:.2f}–{settings.entry_price_max:.2f}"
        )
    elif quote.spread > settings.max_spread:
        will_trade = False
        reason_no_trade = f"spread {quote.spread:.3f} > max {settings.max_spread:.3f}"
    elif learn_veto:
        will_trade = False
        reason_no_trade = learn_veto
    else:
        wr_note = f", hist WR {learned_wr*100:.0f}% (n={learned_n})" if learned_n else ""
        reason_trade = (
            f"{asset} spot {momentum:+.3f}% → {side}; "
            f"{side} @ {entry_price:.2f} in band, conf {confidence:.0f}%{wr_note}"
        )

    return Opportunity(
        market_id=quote.market_id,
        title=quote.question,
        asset=asset,
        timeframe=timeframe,
        expiry=quote.expiry,
        up_price=quote.up_price,
        down_price=quote.down_price,
        spread=quote.spread,
        signal=signal,
        confidence=confidence,
        will_trade=will_trade,
        side=side,
        entry_price=entry_price,
        reason_trade=reason_trade,
        reason_no_trade=reason_no_trade,
        token_id=quote.token_for(side),
        learned_winrate=learned_wr,
        learned_samples=learned_n,
    )


def _time_left(expiry: datetime | None) -> str:
    if expiry is None:
        return "—"
    now = datetime.now(timezone.utc)
    delta = (expiry - now).total_seconds()
    if delta <= 0:
        return "expired"
    m, s = divmod(int(delta), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m{s:02d}s"
