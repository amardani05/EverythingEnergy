"""Directed commodity-flow graph over taxonomy baskets + propagation signals.

Graph propagation in one line: when a shock hits a basket, the baskets it
trades with tend to reprice LATER, because investors do not instantly chase
economic links (Cohen & Frazzini 2008 "Economic Links and Predictable
Returns"; Menzly & Ozbas 2010). The taxonomy's `feeds_into` edges encode
those links, so the signal is: a basket's trailing return, assigned to its
graph neighbors as a forecast.

Two directions per stock i in basket b at date t (PIT-safe by construction,
trailing returns only):

    neigh_up(i, t)   = mean over baskets a with a -> b of a's trailing
                       L-day return   (b's suppliers; shocks flow with the
                       commodity, downstream)
    neigh_down(i, t) = mean over baskets c with b -> c of c's trailing
                       L-day return   (b's customers; demand shocks travel
                       upstream)

The stock's OWN basket return is excluded from both. Without that exclusion
the signal is ordinary momentum wearing a costume.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import polars as pl

from signal_engine.atlas.clusters import load_taxonomy, ticker_to_basket

log = logging.getLogger(__name__)

DEFAULT_LOOKBACK_DAYS = 21


def flow_edges(path: Path | None = None) -> list[tuple[str, str]]:
    """Directed edges (from_basket, to_basket) from the taxonomy's
    `feeds_into` lists. Edges whose endpoints are not actual basket ids in
    the taxonomy (stale references) are dropped with a warning."""
    tax = load_taxonomy(path)
    basket_ids: set[str] = set(ticker_to_basket(path).values())

    edges: list[tuple[str, str]] = []
    dropped: list[tuple[str, str]] = []

    def record(nid: str, node: dict[str, Any]) -> None:
        feeds = node.get("feeds_into")
        if not isinstance(feeds, list):
            return
        for target in feeds:
            if isinstance(target, str):
                if nid in basket_ids and target in basket_ids:
                    edges.append((nid, target))
                else:
                    dropped.append((nid, target))

    def walk(node: Any, key_hint: str | None) -> None:
        if isinstance(node, dict):
            nid = node.get("id") or node.get("basket_id") or key_hint
            if nid and "feeds_into" in node:
                record(nid, node)
            for key, v in node.items():
                if isinstance(v, (dict, list)):
                    walk(v, key if isinstance(v, dict) else None)
        elif isinstance(node, list):
            for item in node:
                walk(item, None)

    walk(tax, None)
    if dropped:
        log.warning("[graph] dropped %d edges with unknown endpoints (sample: %s)",
                    len(dropped), dropped[:5])
    # Dedupe, stable order.
    seen: set[tuple[str, str]] = set()
    out = []
    for e in edges:
        if e not in seen:
            seen.add(e)
            out.append(e)
    return out


def compute_graph_momentum(
    prices: pl.DataFrame,
    t2b: dict[str, str],
    edges: list[tuple[str, str]],
    *,
    lookback: int = DEFAULT_LOOKBACK_DAYS,
) -> pl.DataFrame:
    """Neighbor-momentum panel for every mapped stock.

    prices: long panel (ticker, date, close). t2b: TICKER -> basket_id.
    Returns: ticker, date, basket, neigh_up, neigh_down. A stock whose
    basket has no in-edges gets null neigh_up (not 0: no information is
    not the same as zero signal); same for out-edges/neigh_down.
    """
    if not edges:
        raise ValueError("empty edge list: check the taxonomy's feeds_into fields")

    mapping = pl.DataFrame({
        "ticker": list(t2b.keys()),
        "basket": [t2b[k] for k in t2b],
    })
    panel = (
        prices.sort(["ticker", "date"])
        .with_columns(
            (pl.col("close") / pl.col("close").shift(lookback).over("ticker") - 1.0)
            .alias("_tr")
        )
        .join(mapping, on="ticker", how="inner")
        .drop_nulls("_tr")
    )
    if panel.height == 0:
        return pl.DataFrame(schema={
            "ticker": pl.Utf8, "date": pl.Date, "basket": pl.Utf8,
            "neigh_up": pl.Float64, "neigh_down": pl.Float64,
        })

    # Equal-weight basket trailing return per date.
    basket_ret = (
        panel.group_by(["basket", "date"])
        .agg(pl.col("_tr").mean().alias("basket_tr"))
    )

    # Edge table, one row per (basket, neighbor, direction). Direction is
    # from the BASKET's point of view: 'up' = the neighbor feeds into me.
    edge_rows = (
        [{"basket": b, "neighbor": a, "direction": "up"} for a, b in edges]
        + [{"basket": a, "neighbor": c, "direction": "down"} for a, c in edges]
    )
    edge_df = pl.DataFrame(edge_rows)

    neigh = (
        edge_df.join(basket_ret.rename({"basket": "neighbor", "basket_tr": "neigh_tr"}),
                     on="neighbor", how="inner")
        .group_by(["basket", "date", "direction"])
        .agg(pl.col("neigh_tr").mean())
        .pivot(on="direction", index=["basket", "date"], values="neigh_tr")
    )
    for col, name in (("up", "neigh_up"), ("down", "neigh_down")):
        if col in neigh.columns:
            neigh = neigh.rename({col: name})
        else:
            neigh = neigh.with_columns(pl.lit(None, dtype=pl.Float64).alias(name))
    neigh = neigh.select(["basket", "date", "neigh_up", "neigh_down"])

    return (
        panel.select(["ticker", "date", "basket"])
        .join(neigh, on=["basket", "date"], how="left")
    )
