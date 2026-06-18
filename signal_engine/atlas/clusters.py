"""Atlas cluster + ticker loader. Reads the existing /energy_taxonomy.yaml
in the repo root — the same file the Energy Atlas dashboard uses — so the
signal engine doesn't fork a second source of truth.

In step 4 this same module gains commodity-beta residualization. In step 2
we just need:
  * the ticker universe (energy_universe_tickers())
  * the cluster membership (ticker -> basket id)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from signal_engine.config import REPO_ROOT

log = logging.getLogger(__name__)


def load_taxonomy(path: Path | None = None) -> dict[str, Any]:
    p = path or (REPO_ROOT / "energy_taxonomy.yaml")
    with p.open("r") as f:
        return yaml.safe_load(f)


def _walk_constituents(node: Any, basket_id: str | None, out: dict[str, str]) -> None:
    """Recursive walk that records ticker -> basket_id. The taxonomy nests
    baskets a few levels deep; the most recent enclosing basket id (the
    closest dict with 'id' or 'basket_id') is what wins."""
    if isinstance(node, dict):
        new_basket = node.get("id") or node.get("basket_id") or basket_id
        constituents = node.get("constituents")
        if isinstance(constituents, list):
            for c in constituents:
                if isinstance(c, dict) and "ticker" in c:
                    out[c["ticker"].upper()] = new_basket or "uncategorized"
                elif isinstance(c, str):
                    out[c.upper()] = new_basket or "uncategorized"
        for v in node.values():
            if isinstance(v, (dict, list)):
                _walk_constituents(v, new_basket, out)
    elif isinstance(node, list):
        for item in node:
            _walk_constituents(item, basket_id, out)


def ticker_to_basket(path: Path | None = None) -> dict[str, str]:
    """Returns {TICKER: basket_id} for every constituent in the taxonomy."""
    tax = load_taxonomy(path)
    out: dict[str, str] = {}
    _walk_constituents(tax, None, out)
    return out


def energy_universe_tickers(path: Path | None = None) -> list[str]:
    """Sorted list of all unique tickers in the energy taxonomy."""
    return sorted(ticker_to_basket(path).keys())
