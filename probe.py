"""Diagnostic: inspect what Polymarket's live API actually returns.

Run this in an environment with network access to Polymarket. It prints the
real field shapes so the bot's asset/timeframe detection can be matched to
reality — especially the date fields that define a market's window.

    python probe.py
    python probe.py --limit 1000 --crypto 40
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime

import requests

from bot.config import Settings
from bot.market_data import (
    LivePolymarketProvider, classify_asset, classify_timeframe, _maybe_json, _parse_dt,
)

CRYPTO_HINT = ("bitcoin", "btc", "ethereum", "eth", "solana", "sol", "xrp")
DATE_FIELDS = (
    "startDate", "start_date_iso", "endDate", "end_date_iso", "gameStartTime",
    "acceptingOrdersTimestamp", "createdAt", "closedTime",
)


def _raw_markets(s: Settings, limit: int) -> list[dict]:
    url = f"{s.gamma_api_url}/markets"
    out: list[dict] = []
    offset, page = 0, min(100, limit)
    sess = requests.Session()
    sess.headers.update({"User-Agent": "polymarket-10-bot/1.0"})
    while offset < limit:
        r = sess.get(url, params={
            "active": "true", "closed": "false", "limit": str(page),
            "offset": str(offset), "order": "endDate", "ascending": "true",
        }, timeout=10)
        if r.status_code != 200:
            raise RuntimeError(f"{r.status_code}: {r.text[:160]}")
        batch = r.json()
        if not batch:
            break
        out.extend(batch)
        offset += page
    return out


def _dur_min(m: dict) -> float | None:
    start = _parse_dt(m.get("startDate") or m.get("start_date_iso")
                      or m.get("acceptingOrdersTimestamp"))
    end = _parse_dt(m.get("endDate") or m.get("end_date_iso"))
    return (end - start).total_seconds() / 60.0 if (start and end) else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--crypto", type=int, default=30, help="crypto rows to print")
    args = ap.parse_args()

    s = Settings()
    try:
        markets = _raw_markets(s, args.limit)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR contacting Polymarket: {exc}")
        print("If this is a host-allowlist error, allowlist gamma-api.polymarket.com.")
        return

    print(f"=== Fetched {len(markets)} raw markets ===\n")

    # 1) Field names available on a market object.
    if markets:
        print("Sample market field keys:")
        print(sorted(markets[0].keys()))
        print()

    # 2) Crypto markets with ALL their date fields + detected asset/timeframe.
    crypto = [m for m in markets
              if any(h in (str(m.get("question", "")) + str(m.get("slug", ""))).lower()
                     for h in CRYPTO_HINT)]
    print(f"=== Crypto-looking markets: {len(crypto)} ===\n")
    for m in crypto[:args.crypto]:
        q = str(m.get("question", ""))[:55]
        slug = str(m.get("slug", ""))[:40]
        dur = _dur_min(m)
        asset = classify_asset(q + " " + slug)
        tf = classify_timeframe(q + " " + slug, dur)
        dates = {k: m.get(k) for k in DATE_FIELDS if m.get(k)}
        print(f"- {q}")
        print(f"    slug={slug}")
        print(f"    asset={asset} tf={tf} duration_min={round(dur,1) if dur else None}")
        print(f"    dates={dates}")

    # 3) One full crypto market as raw JSON, so any nonstandard fields are visible.
    if crypto:
        print("\n=== Full raw JSON of first crypto market ===")
        print(json.dumps(crypto[0], indent=2, default=str)[:2500])

    # 4) Summary counts.
    print("\n=== Summary ===")
    print("crypto by detected asset:",
          dict(Counter(classify_asset(str(m.get("question", "")) + " " + str(m.get("slug", "")))
                       or "?" for m in crypto)))
    print("crypto by detected timeframe:",
          dict(Counter(classify_timeframe(str(m.get("question", "")) + " " + str(m.get("slug", "")),
                                          _dur_min(m)) or "?" for m in crypto)))


if __name__ == "__main__":
    main()
