"""Adapter: WRDS panel -> (signals, prices) for the walk-forward harness.

Spec fixed 2026-11-24, before any result was seen:
  * Energy only: every panel group except mining and smelting, plus uranium (SIC 1094).
  * Close at the signal date >= $1 and a full 12-1 momentum history.
  * Same scoring as the live composite: winsorize 1/99, z within group (global when a
    group has < 8 names), families weighted equally, at least 2 families.
  * SUE counts only if known within PEAD_HOLD_CALENDAR_DAYS.
  * Month-end grid from 2005-01-31, terciles, 60 bps, min 45 names (30 for one family).
  * No extreme-return filter: CRSP returns are adjusted, unlike the free data.
  * Base: delistings use the CRSP return, then cash; a missing delisting return or an
    off-exchange suspension earns 0. Stress: both of those earn -100%.
"""

from __future__ import annotations

from datetime import date

import polars as pl

from signal_engine.scoring.composite import (
    MIN_FAMILIES,
    MIN_SECTOR_N,
    PEAD_HOLD_CALENDAR_DAYS,
    winsorize_series,
    zscore_expr,
)

PRICE_FLOOR = 1.0
GRID_START = date(2005, 1, 31)
CALENDAR_TICKER = "_CALENDAR"  # carries every market date so annualization stays right
CARRY_DAYS = 75
MIN_SUSPENSION_DAYS = 5
LOSS = -0.999999
FAMILIES = {"score_momentum": "mom_12_1", "score_value": "ebitda_ev", "score_pead": "sue"}


def eligible(panel: pl.DataFrame) -> pl.DataFrame:
    energy = ~pl.col("group").is_in(["mining", "smelting"]) | (pl.col("siccd") == 1094)
    return panel.filter(
        energy & (pl.col("prc") >= PRICE_FLOOR) & pl.col("mom_12_1").is_not_null()
        & (pl.col("date") >= GRID_START)
    ).with_columns(
        pl.when((pl.col("date") - pl.col("eps_avail")).dt.total_days() < PEAD_HOLD_CALENDAR_DAYS)
        .then(pl.col("sue")).alias("sue")
    )


def score(panel: pl.DataFrame) -> pl.DataFrame:
    """ticker, date, three family scores and the composite."""
    out = []
    for _, g in eligible(panel).group_by("date", maintain_order=True):
        g = g.with_columns(pl.len().over("group").alias("_n"))
        for fam, col in FAMILIES.items():
            g = g.with_columns(winsorize_series(g[col], 0.01, 0.99).alias("_w")).with_columns(
                pl.when(pl.col("_n") >= MIN_SECTOR_N)
                .then(zscore_expr("_w", over="group")).otherwise(zscore_expr("_w")).alias(fam)
            )
        fams = list(FAMILIES)
        n = pl.sum_horizontal(pl.col(f).is_not_null().cast(pl.Int32) for f in fams)
        out.append(g.select(
            pl.col("permno").cast(pl.Utf8).alias("ticker"), "date", *fams,
            pl.when(n >= MIN_FAMILIES).then(pl.mean_horizontal(fams)).alias("composite"),
        ))
    return pl.concat(out)


def total_return_index(daily: pl.DataFrame, grid: list[date], *, stress: bool) -> pl.DataFrame:
    """(ticker, date, close) total-return index at each entry date (grid date + 1).

    Delisted names are carried as cash so the harness keeps, rather than drops, the loss.
    """
    market = sorted(daily.filter(pl.col("delist") != "Y")["date"].unique().to_list())
    cal = pl.DataFrame({"date": market}).with_columns(pl.col("date").shift(-1).alias("_next"))
    d = daily.sort("permno", "date").select("permno", "date", "ret", "delist", "conditionaltype")
    # A delisting return dated long after the last trade belongs to the last holder.
    d = (
        d.with_columns(pl.col("date").shift(1).over("permno").alias("_prev"))
        .join(cal.rename({"date": "_prev", "_next": "_after"}), on="_prev", how="left")
        .with_columns(
            pl.when(pl.col("delist") == "Y").then(pl.min_horizontal("date", "_after"))
            .otherwise(pl.col("date")).alias("date")
        )
    )
    nt = pl.col("conditionaltype") == "NT"
    first_nt = nt & ~nt.shift(1).over("permno").fill_null(False)
    d = d.with_columns(
        pl.when(nt).then(pl.col("conditionaltype").rle_id().over("permno")).alias("_spell")
    ).with_columns(pl.len().over("permno", "_spell").alias("_len"))
    missing_delist = (pl.col("delist") == "Y") & pl.col("ret").is_null()
    off_exchange = first_nt & (pl.col("_len") >= MIN_SUSPENSION_DAYS)
    ret = pl.col("ret").fill_null(0.0)
    if stress:
        ret = pl.when(missing_delist | off_exchange).then(pl.lit(-1.0)).otherwise(ret)
    d = d.with_columns(ret.clip(lower_bound=LOSS).alias("_r")).with_columns(
        (1 + pl.col("_r")).cum_prod().over("permno").alias("close")
    )
    nxt = dict(cal.iter_rows())
    entries = pl.DataFrame({"date": sorted({nxt[g] for g in grid if nxt.get(g)})}).with_columns(
        pl.col("date").set_sorted()
    )
    names = d.select("permno").unique()
    px = (
        names.join(entries, how="cross").sort("date")
        .join_asof(d.select("permno", "date", "close", pl.col("date").alias("_seen")).sort("date"),
                   on="date", by="permno", strategy="backward", check_sortedness=False)
        .filter(pl.col("close").is_not_null()
                & ((pl.col("date") - pl.col("_seen")).dt.total_days() <= CARRY_DAYS))
        .select(pl.col("permno").cast(pl.Utf8).alias("ticker"), "date", "close")
    )
    calendar = pl.DataFrame({"ticker": [CALENDAR_TICKER] * len(market), "date": market,
                             "close": [1.0] * len(market)})
    return pl.concat([px, calendar])
