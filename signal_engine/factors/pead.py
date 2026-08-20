"""PEAD / SUE - Post-Earnings Announcement Drift signal.

Per the spec (constraints 2, 3 and turn 6 SUE-method decision):

  SUE_t = (EPS_t - EPS_{t-4}) / stdev(YoY quarterly EPS change over
                                       trailing ~8 quarters)

  * EPS_t        = standalone quarterly diluted EPS for fiscal quarter t.
  * EPS_{t-4}    = same fiscal quarter, one year prior.
  * Denominator  = stdev of the 8 most recent YoY differences before t.
                   Requires >=5 valid pairs to fire (degrees of freedom).
  * KNOWLEDGE_DATE = the `filed` date of the 10-Q that REPORTED EPS_t.
                     This is the announcement date; SUE_t may only be
                     applied to dates >= signal_date.
  * Restatements (10-Q/A) do NOT rewrite history - we pull originals only
    via as_of_facts(..., originals_only=True). Hard contract; the
    SUE-`filed` test enforces it.

Output: one row per (cik, period_end), with `signal_date = filed`. The
caller broadcasts the signal forward (typical hold window: 21-63 trading
days post-announcement) by joining as_of_sue against a date grid.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import duckdb
import polars as pl

from signal_engine.factors.fundamentals import quarterly_eps_series

log = logging.getLogger(__name__)


MIN_YOY_PAIRS_FOR_DENOMINATOR = 5
DEFAULT_YOY_HISTORY_QUARTERS = 8

# One declared schema for every compute_sue_series return path. Applied even
# to the non-empty frame: a company whose same-quarter EPS never varies yields
# sd==0 -> sue=None for every row, which Polars would otherwise infer as a Null
# dtype, breaking the pl.concat over tickers in compute_sue.
_SUE_SERIES_SCHEMA: dict[str, type[pl.DataType]] = {
    "cik": pl.Int64, "period_end": pl.Date, "fy": pl.Int64,
    "fp": pl.Utf8, "filed": pl.Date, "eps_diluted": pl.Float64,
    "eps_yoy_diff": pl.Float64, "sue": pl.Float64,
}


@dataclass(frozen=True)
class SueConfig:
    history_quarters: int = DEFAULT_YOY_HISTORY_QUARTERS
    min_pairs: int = MIN_YOY_PAIRS_FOR_DENOMINATOR


def compute_sue_series(
    eps_quarterly: pl.DataFrame,
    cfg: SueConfig = SueConfig(),
) -> pl.DataFrame:
    """Seasonal-random-walk SUE for one company's quarterly EPS series.

    Input: cik, period_end, fy, fp, filed, eps_diluted (long form). Must
    be sorted ascending by period_end and contain only ORIGINALS (the
    caller already enforced originals_only).

    Output: cik, period_end, fy, fp, filed (= signal_date), eps_diluted,
            eps_yoy_diff, sue.

    The `filed` column equals the announcement date - the signal is known
    only on/after `filed`, never before. Any downstream join MUST gate on
    that.
    """
    if eps_quarterly.height == 0:
        return pl.DataFrame(schema=_SUE_SERIES_SCHEMA)

    df = eps_quarterly.sort("period_end").to_dicts()
    by_fp: dict[str, list[dict]] = {}
    for r in df:
        by_fp.setdefault(r["fp"], []).append(r)

    out_rows: list[dict] = []
    # For each fp (Q1/Q2/Q3), walk fiscal years in order, computing
    # YoY diff against the same fp one year prior.
    for fp, series in by_fp.items():
        # Sort by period_end ascending - should already be, defensive
        series.sort(key=lambda r: r["period_end"])
        for i, row in enumerate(series):
            if i == 0:
                continue
            prior = series[i - 1]
            # Step on the PERIOD's year, never on `fy`. EDGAR's `fy` is the
            # fiscal year of the REPORT, so a 10-Q carries fy=2026 for both
            # the current quarter and the prior-year comparative it restates;
            # differencing on fy compared a quarter against itself and
            # produced SUE = 0.0 across the book.
            if (row["period_end"].year - prior["period_end"].year) != 1:
                continue
            if row["eps_diluted"] is None or prior["eps_diluted"] is None:
                continue
            yoy = float(row["eps_diluted"]) - float(prior["eps_diluted"])
            # Denominator: trailing YoY diffs from this same fp's history
            # BEFORE this observation. Use the previous `history_quarters`
            # diffs in this fp series (capped by what's available).
            history = []
            for j in range(max(0, i - cfg.history_quarters), i):
                if j == 0:
                    continue
                pprev = series[j - 1]
                pcurr = series[j]
                if (pcurr["period_end"].year - pprev["period_end"].year) != 1:
                    continue
                if pcurr["eps_diluted"] is None or pprev["eps_diluted"] is None:
                    continue
                history.append(float(pcurr["eps_diluted"]) - float(pprev["eps_diluted"]))

            if len(history) < cfg.min_pairs:
                continue
            import statistics
            sd = statistics.pstdev(history)
            sue = (yoy / sd) if sd > 0 else None
            out_rows.append({
                "cik": row["cik"],
                "period_end": row["period_end"],
                "fy": row["fy"],
                "fp": fp,
                "filed": row["filed"],
                "eps_diluted": row["eps_diluted"],
                "eps_yoy_diff": yoy,
                "sue": sue,
            })

    if not out_rows:
        return pl.DataFrame(schema=_SUE_SERIES_SCHEMA)
    # Pass the schema explicitly so an all-None `sue` column is Float64, not Null.
    return pl.DataFrame(out_rows, schema=_SUE_SERIES_SCHEMA).sort(["cik", "period_end"])


def compute_sue(
    con: duckdb.DuckDBPyConnection,
    as_of: date,
    ticker_to_cik: dict[str, int],
    *,
    cfg: SueConfig = SueConfig(),
) -> pl.DataFrame:
    """Compute SUE for every ticker, using only ORIGINAL filings.

    Returns one row per (ticker, cik, period_end) with `signal_date` =
    `filed`. To convert to a daily signal panel, the caller forward-fills
    SUE by ticker, gated on `signal_date <= t <= signal_date + hold_window`.
    """
    rows: list[pl.DataFrame] = []
    for ticker, cik in ticker_to_cik.items():
        eps = quarterly_eps_series(con, as_of=as_of, cik=cik, originals_only=True)
        if eps.height == 0:
            continue
        sue = compute_sue_series(eps, cfg=cfg)
        if sue.height == 0:
            continue
        sue = sue.with_columns(pl.lit(ticker).alias("ticker"))
        rows.append(sue.rename({"filed": "signal_date"}))
    if not rows:
        return pl.DataFrame(schema={
            "ticker": pl.Utf8, "cik": pl.Int64, "period_end": pl.Date,
            "fy": pl.Int64, "fp": pl.Utf8, "signal_date": pl.Date,
            "eps_diluted": pl.Float64, "eps_yoy_diff": pl.Float64, "sue": pl.Float64,
        })
    return pl.concat(rows).select([
        "ticker", "cik", "period_end", "fy", "fp",
        "signal_date", "eps_diluted", "eps_yoy_diff", "sue",
    ])
