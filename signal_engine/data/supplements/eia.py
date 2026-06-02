"""EIA energy series — free, env key EIA_API_KEY.

EIA's v2 API is paginated and supports vintage queries via `frequency` +
`facets`. We store one row per (series_id, period, knowledge_date).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import requests


@dataclass
class EiaClient:
    api_key: str | None = None
    base: str = "https://api.eia.gov/v2"

    def __post_init__(self) -> None:
        if self.api_key is None:
            self.api_key = os.environ.get("EIA_API_KEY")

    def series(self, route: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("EIA_API_KEY not set in environment")
        p = {"api_key": self.api_key, **(params or {})}
        r = requests.get(f"{self.base}/{route}", params=p, timeout=30)
        r.raise_for_status()
        return r.json()
