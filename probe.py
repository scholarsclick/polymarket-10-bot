"""Diagnostic: dump what Polymarket's live API actually returns.

Run this in an environment with network access to Polymarket to see which
markets come back and how the bot classifies them. Use it to confirm whether
BTC/ETH 5m/15m markets exist and how their timeframe is detected.

    python probe.py
    python probe.py --limit 1000 --show 60
"""

from __future__ import annotations

import argparse
from collections import Counter

from bot.config import Settings
from bot.market_data import LivePolymarketProvider


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=500, help="max markets to scan")
    ap.add_argument("--show", type=int, default=40, help="rows to print")
    args = ap.parse_args()

    s = Settings()
    provider = LivePolymarketProvider(s.gamma_api_url, s.clob_api_url)
    try:
        quotes = provider.list_markets(args.limit)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR contacting Polymarket: {type(exc).__name__}: {exc}")
        print("If this is a host-allowlist error, allowlist gamma-api.polymarket.com "
              "and clob.polymarket.com in your network egress settings.")
        return

    print(f"Fetched {len(quotes)} parsed markets.\n")

    asset_counts = Counter(q.asset or "?" for q in quotes)
    tf_counts = Counter(q.timeframe or "?" for q in quotes)
    print("By asset:    ", dict(asset_counts))
    print("By timeframe:", dict(tf_counts))

    in_universe = [q for q in quotes if q.in_universe(s.assets, s.timeframes)]
    print(f"\nMatching BTC/ETH 5m·15m universe: {len(in_universe)}\n")

    print(f"{'asset':6} {'tf':4} {'dur_min':8} {'up':6} {'down':6}  title")
    print("-" * 90)
    for q in quotes[:args.show]:
        dur = f"{q.duration_min:.1f}" if q.duration_min else "—"
        print(f"{(q.asset or '?'):6} {(q.timeframe or '?'):4} {dur:8} "
              f"{q.up_price:<6.3f} {q.down_price:<6.3f}  {q.question[:60]}")

    if not in_universe:
        print("\nNo 5m/15m BTC/ETH markets detected. Paste this output back so the "
              "asset/timeframe detection can be tuned to the real titles/durations.")


if __name__ == "__main__":
    main()
