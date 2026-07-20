"""Value factor - FCF yield + EV/EBITDA.

Both ratios are reciprocals-of-multiples; higher = cheaper. We surface them
as standalone raw values; the composite layer winsorizes and z-scores
within sector.

Per turn 6: EBITDA uses the street formula (NI + Int + Tax + D&A) - see
fundamentals.AnnualSnapshot.ebitda.

Market cap (and hence EV / FCF yield) requires a price at as_of. We use
the most-recent-as-of close from prices, multiplied by the snapshot's
shares_outstanding. Both are PIT - price is t-known-at-t, shares is the
most recent reported balance.

Negative-EBITDA companies get EV/EBITDA = None (sign-flipped multiples
break the cross-sectional ranking). FCF yield is allowed to be negative - some IJR names burn cash and the signal SHOULD penalize them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import duckdb
import polars as pl

from signal_engine.data.store import as_of_prices
from signal_engine.factors.fundamentals import AnnualSnapshot, latest_annual_snapshot

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ValueRaw:
    cik: int
    as_of: date
    market_cap: float | None
    enterprise_value: float | None
    fcf_yield: float | None      # FCF / market_cap; can be negative
    ev_ebitda: float | None      # EV / EBITDA; None if EBITDA <= 0


def _market_cap(con: duckdb.DuckDBPyConnection, as_of: date, ticker: str,
                shares_outstanding: float | None) -> float | None:
    if shares_outstanding is None or shares_outstanding <= 0:
        return None
    prices = as_of_prices(con, as_of=as_of, ticker=ticker)
    if prices.height == 0:
        return None
    latest = prices.sort("date", descending=True).row(0, named=True)
    close = latest["close"]
    if close is None or close <= 0:
        return None
    return float(close) * float(shares_outstanding)


def _enterprise_value(market_cap: float | None,
                      long_term_debt: float | None,
                      cash: float | None) -> float | None:
    if market_cap is None:
        return None
    # Treat missing debt/cash as zero - many IJR names truly have no LT debt
    # (see step 1 introspection on AAON). Document this so factor users know
    # EV = mcap whenever both are zero.
    debt = long_term_debt or 0.0
    c = cash or 0.0
    return market_cap + debt - c


def compute_value(
    con: duckdb.DuckDBPyConnection,
    as_of: date,
    ticker_to_cik: dict[str, int],
) -> pl.DataFrame:
    """Compute FCF yield + EV/EBITDA for every ticker with both a CIK and
    a price at `as_of`. Returns: ticker, cik, as_of, market_cap, ev,
    fcf_yield, ev_ebitda. NaN/None on either ratio means the input was
    missing or non-positive in the no-flip way."""
    rows: list[dict] = []
    for ticker, cik in ticker_to_cik.items():
        snap = latest_annual_snapshot(con, as_of=as_of, cik=cik)
        if snap is None:
            rows.append({"ticker": ticker, "cik": cik, "as_of": as_of,
                         "market_cap": None, "enterprise_value": None,
                         "fcf_yield": None, "ev_ebitda": None})
            continue
        v = _compute_one(con, as_of, ticker, snap)
        rows.append({
            "ticker": ticker, "cik": cik, "as_of": as_of,
            "market_cap": v.market_cap,
            "enterprise_value": v.enterprise_value,
            "fcf_yield": v.fcf_yield,
            "ev_ebitda": v.ev_ebitda,
        })
    return pl.DataFrame(rows)


def _compute_one(con: duckdb.DuckDBPyConnection, as_of: date, ticker: str,
                 snap: AnnualSnapshot) -> ValueRaw:
    mcap = _market_cap(con, as_of, ticker, snap.shares_outstanding)
    ev = _enterprise_value(mcap, snap.long_term_debt, snap.cash)

    fcf_yield: float | None = None
    if snap.fcf is not None and mcap is not None and mcap > 0:
        fcf_yield = snap.fcf / mcap

    ev_ebitda: float | None = None
    ebitda = snap.ebitda
    if ev is not None and ebitda is not None and ebitda > 0:
        ev_ebitda = ev / ebitda

    return ValueRaw(
        cik=snap.cik, as_of=as_of, market_cap=mcap,
        enterprise_value=ev, fcf_yield=fcf_yield, ev_ebitda=ev_ebitda,
    )
