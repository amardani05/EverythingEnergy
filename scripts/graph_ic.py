#!/usr/bin/env python3
"""Graph-propagation IC scorecard on real data (roadmap 4.8a, first read).

Universe: energy taxonomy tickers with prices (~190 names). Signals:
  * neigh_up   - trailing return of the baskets that FEED INTO mine
  * neigh_down - trailing return of the baskets MINE FEEDS INTO
  * basket_mom - my OWN basket's trailing return (control: if neighbor
    signals only work as well as own-basket momentum, the graph adds
    nothing beyond sector co-movement)

Writes docs/graph_baselines.md. Read t (NW), not t (iid).

Usage:
  .venv/bin/python scripts/graph_ic.py
  .venv/bin/python scripts/graph_ic.py --lookback 21
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from datetime import date
from pathlib import Path

import polars as pl

from signal_engine.atlas.clusters import ticker_to_basket
from signal_engine.atlas.graph import compute_graph_momentum, flow_edges
from signal_engine.config import Config
from signal_engine.data.store import as_of_prices, connect
from signal_engine.validation.ic import IcSummary, ic_scorecard

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
log = logging.getLogger("graph_ic")


def scorecard_lines(name: str, summaries: list[IcSummary]) -> list[str]:
    lines = [f"### {name}", "",
             "| horizon | n dates | mean IC | std | t (iid) | t (NW) | hit rate | avg breadth |",
             "|---|---|---|---|---|---|---|---|"]
    for s in summaries:
        lines.append(
            f"| {s.horizon}d | {s.n_dates} | {s.mean:+.4f} | {s.std:.4f} "
            f"| {s.t_stat:+.2f} | {s.t_stat_nw:+.2f} | {s.hit_rate:.2f} "
            f"| {s.sample_size_mean:.0f} |"
        )
    lines.append("")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lookback", type=int, default=21)
    parser.add_argument("--out", default="docs/graph_baselines.md")
    args = parser.parse_args()

    cfg = Config.load()
    t2b = ticker_to_basket()
    edges = flow_edges()
    log.info("[graph] %d tickers mapped to %d baskets, %d directed edges",
             len(t2b), len(set(t2b.values())), len(edges))

    with connect(cfg.duckdb_path, read_only=True) as con:
        today = date.today()
        panel = (
            as_of_prices(con, as_of=today)
            .filter(pl.col("ticker").is_in(list(t2b.keys())))
            .select(["ticker", "date", "close"])
        )
    log.info("[prices] %d rows, %d tickers with data",
             panel.height, panel["ticker"].n_unique())

    graph = compute_graph_momentum(panel, t2b, edges, lookback=args.lookback)

    # Control: own-basket trailing return (same lookback, same construction).
    mapping = pl.DataFrame({"ticker": list(t2b.keys()),
                            "basket": [t2b[k] for k in t2b]})
    own = (
        panel.sort(["ticker", "date"])
        .with_columns(
            (pl.col("close") / pl.col("close").shift(args.lookback).over("ticker") - 1.0)
            .alias("_tr"))
        .join(mapping, on="ticker", how="inner")
        .drop_nulls("_tr")
    )
    basket_tr = own.group_by(["basket", "date"]).agg(pl.col("_tr").mean().alias("value"))
    own_signal = (
        own.select(["ticker", "date", "basket"])
        .join(basket_tr, on=["basket", "date"], how="inner")
        .select(["ticker", "date", "value"])
    )

    results: dict[str, list[IcSummary]] = {}
    for col, label in (("neigh_up", "graph/neigh_up (suppliers' trailing ret)"),
                       ("neigh_down", "graph/neigh_down (customers' trailing ret)")):
        sig = graph.select(["ticker", "date", col]).drop_nulls(col).rename({col: "value"})
        results[label] = ic_scorecard(sig, panel)
        log.info("[%s] %d signal rows", col, sig.height)
    results["control/own_basket_mom (same lookback)"] = ic_scorecard(own_signal, panel)

    git_sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, check=False).stdout.strip()
    report = [
        "# Graph-propagation baseline (roadmap 4.8a)",
        "",
        f"*Generated {today} on `{git_sha}`. Universe: energy taxonomy "
        f"({panel['ticker'].n_unique()} names with prices, "
        f"{len(set(t2b.values()))} baskets, {len(edges)} directed flow edges). "
        f"Signal lookback {args.lookback}d. Spearman IC, forward return "
        "t+1 -> t+H+1. Read t (NW).*",
        "",
        "The control row matters: neighbor signals must beat own-basket momentum "
        "to prove the GRAPH is adding information beyond sector co-movement.",
        "",
    ]
    for name, summaries in results.items():
        for s in summaries:
            log.info("[%s] %s", name, s)
        report += scorecard_lines(name, summaries)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(report) + "\n")
    log.info("[report] wrote %s", out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
