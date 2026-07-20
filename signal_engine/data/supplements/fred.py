"""FRED macro series - free, env key FRED_API_KEY.

FRED revises series (esp. macro aggregates), so we store every vintage
with `knowledge_date` = release date, keyed (series_id, period, knowledge_date).
The `observations` endpoint with `realtime_start`/`realtime_end` gives us
the full vintage history; default behavior pulls just the current vintage.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import requests

log = logging.getLogger(__name__)


@dataclass
class FredClient:
    api_key: str | None = None
    base: str = "https://api.stlouisfed.org/fred/series/observations"

    def __post_init__(self) -> None:
        if self.api_key is None:
            self.api_key = os.environ.get("FRED_API_KEY")

    def observations(
        self,
        series_id: str,
        *,
        realtime_start: str = "1776-07-04",
        realtime_end: str = "9999-12-31",
    ) -> dict[str, Any]:
        """Pull all vintages of a series. Default realtime span fetches the
        complete revision history. Returns the raw FRED payload."""
        if not self.api_key:
            raise RuntimeError("FRED_API_KEY not set in environment")
        params = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "realtime_start": realtime_start,
            "realtime_end": realtime_end,
        }
        r = requests.get(self.base, params=params, timeout=30)
        r.raise_for_status()
        return r.json()
