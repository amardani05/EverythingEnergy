"""Quality factor — composite of ROIC, accruals (lower better), trailing
margin stability.

Three raw values per ticker. Higher = better quality across all three
after sign flips, so the composite is just the equal-weight z of the three.

ROIC (return on invested capital):
    nopat = operating_income * (1 - effective_tax_rate)
    invested_capital = stockholders_equity + long_term_debt
    ROIC = nopat / invested_capital
  Effective tax rate clamped to [0, 0.5] to avoid pre-tax-loss companies
  blowing up the numerator. Falls back to 0.21 (statutory) when tax info
  is missing.

Accruals (Sloan, cash-flow form):
    accruals = (net_income - operating_cash_flow) / total_assets
  Negative accruals = cash exceeds reported earnings = high quality. The
  factor sign-flips this so higher = better, consistent with the others.

Margin stability:
    operating_margin_history = OpInc / Revenue across the trailing N annual
    snapshots. Stability = 1 / stdev(operating_margin_history). Higher
    stability = more predictable business. Requires >=3 annual filings
    in history; else None.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import duckdb
import polars as pl

from signal_engine.data.store import as_of_facts
from signal_engine.factors.fundamentals import (
    ANNUAL_MAX_DAYS,
    ANNUAL_MIN_DAYS,
    AnnualSnapshot,
    latest_annual_snapshot,
)

log = logging.getLogger(__name__)

# Effective-tax bounds. Pre-tax losses produce tax_expense / pre_tax = nonsense
# (sometimes negative, sometimes >>1). Clamp to a sensible range and fall back
# to the statutory rate when info is missing.
TAX_RATE_LOWER = 0.0
TAX_RATE_UPPER = 0.50
STATUTORY_FALLBACK = 0.21

MARGIN_HISTORY_MIN_YEARS = 3
MARGIN_HISTORY_MAX_YEARS = 8


@dataclass(frozen=True)
class QualityRaw:
    cik: int
    as_of: date
    roic: float | None
    accruals: float | None         # negative = good
    margin_stability: float | None # higher = better


def _effective_tax_rate(snap: AnnualSnapshot) -> float:
    """Tax expense / pre-tax income, clamped. Falls back to statutory."""
    if snap.net_income is None or snap.tax_expense is None:
        return STATUTORY_FALLBACK
    pretax = snap.net_income + snap.tax_expense
    if pretax <= 0:
        return STATUTORY_FALLBACK
    rate = snap.tax_expense / pretax
    return max(TAX_RATE_LOWER, min(TAX_RATE_UPPER, rate))


def _roic(snap: AnnualSnapshot) -> float | None:
    if snap.operating_income is None:
        return None
    invested = (snap.stockholders_equity or 0.0) + (snap.long_term_debt or 0.0)
    if invested <= 0:
        return None
    nopat = snap.operating_income * (1 - _effective_tax_rate(snap))
    return nopat / invested


def _accruals(snap: AnnualSnapshot) -> float | None:
    if (snap.net_income is None or snap.operating_cash_flow is None
            or snap.total_assets is None or snap.total_assets <= 0):
        return None
    return (snap.net_income - snap.operating_cash_flow) / snap.total_assets


def _margin_history(
    con: duckdb.DuckDBPyConnection, as_of: date, cik: int, max_years: int = MARGIN_HISTORY_MAX_YEARS,
) -> list[float]:
    """Trailing operating-margin time series, most-recent-first, up to max_years.
    Returns [] if any year is missing or revenue is non-positive."""
    facts = as_of_facts(con, as_of=as_of, cik=cik)
    if facts.height == 0:
        return []
    rows = facts.with_columns(
        ((pl.col("period_end") - pl.col("period_start")).dt.total_days())
        .alias("_period_days")
    )
    annuals = rows.filter(
        pl.col("_period_days").is_between(ANNUAL_MIN_DAYS, ANNUAL_MAX_DAYS)
    )
    rev = (annuals.filter(pl.col("concept") == "revenue")
           .select(["period_end", "value"]).rename({"value": "revenue"}))
    opinc = (annuals.filter(pl.col("concept") == "operating_income")
             .select(["period_end", "value"]).rename({"value": "operating_income"}))
    joined = (rev.join(opinc, on="period_end", how="inner")
              .sort("period_end", descending=True)
              .head(max_years))
    out: list[float] = []
    for r in joined.iter_rows(named=True):
        rev_v, op_v = r["revenue"], r["operating_income"]
        if rev_v is None or op_v is None or rev_v <= 0:
            continue
        out.append(float(op_v) / float(rev_v))
    return out


def _margin_stability(margins: list[float]) -> float | None:
    if len(margins) < MARGIN_HISTORY_MIN_YEARS:
        return None
    import statistics
    sd = statistics.pstdev(margins) if len(margins) > 1 else 0.0
    # Avoid divide-by-zero blowing the cross-section. A perfectly constant
    # margin (stdev=0) is "infinitely stable"; cap at a sentinel that's
    # safely above any real-world value (real corporate stdev rarely < 1e-4).
    if sd <= 1e-6:
        return 1e6
    return 1.0 / sd


def compute_quality(
    con: duckdb.DuckDBPyConnection,
    as_of: date,
    ticker_to_cik: dict[str, int],
) -> pl.DataFrame:
    """Compute ROIC, accruals, and margin stability for every ticker.

    Returns: ticker, cik, as_of, roic, accruals, margin_stability.

    Composite z-scoring + sign flips happen in scoring/ (step 5); the raw
    values flow out unmodified.
    """
    rows: list[dict] = []
    for ticker, cik in ticker_to_cik.items():
        snap = latest_annual_snapshot(con, as_of=as_of, cik=cik)
        if snap is None:
            rows.append({"ticker": ticker, "cik": cik, "as_of": as_of,
                         "roic": None, "accruals": None, "margin_stability": None})
            continue
        margins = _margin_history(con, as_of, cik)
        rows.append({
            "ticker": ticker, "cik": cik, "as_of": as_of,
            "roic": _roic(snap),
            "accruals": _accruals(snap),
            "margin_stability": _margin_stability(margins),
        })
    return pl.DataFrame(rows)
