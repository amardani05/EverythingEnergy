"""Massive Options Basic ($0): contracts and end-of-day bars, two years.

Basic has no open interest, quotes or implied vol, so this can check Yahoo closes
and build option history, but cannot replace the Yahoo chain.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import requests

BASE = "https://api.massive.com"
MIN_GAP = 12.5  # Basic allows 5 calls a minute


class Massive:
    def __init__(self, key: str | None = None, session: Any = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> None:
        self.key = key or os.environ.get("MASSIVE_API_KEY", "")
        if not self.key:
            raise RuntimeError("Set MASSIVE_API_KEY in your shell (never commit it).")
        self.s, self.sleep, self.clock = session or requests.Session(), sleep, clock
        self.last: float | None = None

    def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        p = urlsplit(url)
        if f"{p.scheme}://{p.netloc}" != BASE:  # next_url comes from the response; never send the key elsewhere
            raise ValueError(f"refusing to call {p.netloc}")
        if self.last is not None and (wait := MIN_GAP - (self.clock() - self.last)) > 0:
            self.sleep(wait)
        self.last = self.clock()
        r = self.s.get(url, params=params, headers={"Authorization": f"Bearer {self.key}"}, timeout=30,
                       allow_redirects=False)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code} from {p.path}")
        out: dict[str, Any] = r.json()
        return out

    def contracts(self, underlying: str, expiry: str, lo: float, hi: float) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        d = self._get(f"{BASE}/v3/reference/options/contracts", {
            "underlying_ticker": underlying, "expiration_date": expiry, "strike_price.gte": lo,
            "strike_price.lte": hi, "limit": 1000})
        while True:
            out += d.get("results") or []
            if not d.get("next_url"):
                return out
            d = self._get(d["next_url"])

    def bars(self, ticker: str, start: str, end: str) -> list[dict[str, Any]]:
        d = self._get(f"{BASE}/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}",
                      {"adjusted": "false", "sort": "asc", "limit": 5000})
        return list(d.get("results") or [])
