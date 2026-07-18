"""Fundamentals helper — assembles per-(ticker, as_of) snapshots from
EDGAR XBRL facts. All Value/Quality factors read through this so the PIT
contract is implemented in one place.

v1 simplifications (documented; revisit in v1.5):
  * Value/Quality use the most recent ANNUAL (10-K, fp='FY', period >= 350d)
    filing whose `filed` <= as_of. TTM rolling-sum across the 4 most recent
    quarters is a more responsive alternative but materially more complex
    to PIT correctly (varying quarter lengths, Q4 derivation from FY -
    sum(Q1..Q3)). Annual is one observation per year per name, but every
    name in the engine updates within ~3 months of its 10-K filing.
  * PEAD uses standalone quarterly EPS from 10-Qs (Q1/Q2/Q3 directly;
    Q4 EPS derived from FY net income - sum(Q1..Q3 NI), divided by FY
    weighted-average shares). See pead.py for the SUE specifics.
  * Restatements: as_of_facts returns the latest knowledge_date <= as_of,
    per the bitemporal store contract. The PEAD path uses originals_only
    so a 10-Q/A doesn't retroactively change a historical surprise.

Every helper here takes (con, as_of, cik) — never a "current" implicit time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import duckdb
import polars as pl

from signal_engine.data.store import as_of_facts

log = logging.getLogger(__name__)


# Minimum period length (days) to qualify as an annual filing. 10-K periods
# are nominally 365 days but transitional years can run 350-380. Anything
# shorter is YTD or quarterly.
ANNUAL_MIN_DAYS = 350
ANNUAL_MAX_DAYS = 380

# Quarterly period acceptance window (standalone Q1/Q2/Q3 in 10-Qs).
QUARTERLY_MIN_DAYS = 60
QUARTERLY_MAX_DAYS = 100


@dataclass(frozen=True)
class AnnualSnapshot:
    """Most-recent-as-of-`as_of` annual values for one company.

    Stock items (assets, equity, debt, cash, shares) are period-end balances.
    Flow items (revenue, ni, opinc, ebitda, fcf, ...) are full-year sums.
    All values are denominated in USD except `shares_outstanding` (count)
    and `eps` (USD/share).

    `period_end` is the FY end the values describe; `filed` is the
    knowledge_date of the 10-K.
    """
    cik: int
    period_end: date
    filed: date
    fy: int | None

    # Flow items (annual)
    revenue: float | None
    net_income: float | None
    operating_income: float | None
    gross_profit: float | None
    depreciation_amortization: float | None
    interest_expense: float | None
    tax_expense: float | None
    operating_cash_flow: float | None
    capex: float | None

    # Stock items (period end)
    total_assets: float | None
    stockholders_equity: float | None
    long_term_debt: float | None
    cash: float | None
    shares_outstanding: float | None

    # Per-share
    eps_diluted: float | None

    # ----- derived (street formulas, per turn 6) -----
    @property
    def ebitda(self) -> float | None:
        """Street EBITDA = NI + Int + Tax + D&A (per turn 6 decision)."""
        parts = [self.net_income, self.interest_expense, self.tax_expense,
                 self.depreciation_amortization]
        if any(p is None for p in parts):
            return None
        return sum(parts)  # type: ignore[arg-type]

    @property
    def fcf(self) -> float | None:
        """FCF = OCF - Capex."""
        if self.operating_cash_flow is None or self.capex is None:
            return None
        return self.operating_cash_flow - self.capex


def latest_annual_snapshot(
    con: duckdb.DuckDBPyConnection,
    as_of: date,
    cik: int,
) -> AnnualSnapshot | None:
    """Build the most-recent-annual snapshot for one CIK as of `as_of`.

    Algorithm:
      1. Pull all facts for this CIK with filed <= as_of (latest-knowledge
         already applied by the read API).
      2. For each concept, take the row whose `period_end` is the most
         recent annual (period length 350-380d) — that's the latest 10-K's
         annual value.
      3. Bundle into AnnualSnapshot; missing concepts -> None.
    """
    facts = as_of_facts(con, as_of=as_of, cik=cik)
    if facts.height == 0:
        return None

    # Annual rows only: period length within the annual window. period_start
    # may be None for instant facts (stock items); for those we still want
    # the most recent observation (latest period_end <= as_of's most recent FY).
    annuals = facts.with_columns(
        ((pl.col("period_end") - pl.col("period_start")).dt.total_days())
        .alias("_period_days")
    )

    flow_concepts = {
        "revenue", "net_income", "operating_income", "gross_profit",
        "depreciation_amortization", "interest_expense", "tax_expense",
        "operating_cash_flow", "capex", "eps_diluted",
    }
    stock_concepts = {
        "total_assets", "stockholders_equity", "long_term_debt", "cash",
        "shares_outstanding",
    }

    # Flow: annual rows only (~365d period). Stock: any instant or end-of-period
    # observation; pick most recent.
    def _latest_flow(concept: str) -> tuple[float | None, date | None, int | None]:
        sub = annuals.filter(
            (pl.col("concept") == concept)
            & pl.col("_period_days").is_between(ANNUAL_MIN_DAYS, ANNUAL_MAX_DAYS)
        ).sort("period_end", descending=True)
        if sub.height == 0:
            return None, None, None
        row = sub.row(0, named=True)
        return float(row["value"]) if row["value"] is not None else None, row["period_end"], row["fy"]

    def _latest_stock(concept: str) -> float | None:
        sub = annuals.filter(pl.col("concept") == concept).sort("period_end", descending=True)
        if sub.height == 0:
            return None
        row = sub.row(0, named=True)
        return float(row["value"]) if row["value"] is not None else None

    # Resolve annual fiscal anchor from revenue (most reliable annual flow).
    flow_vals: dict[str, float | None] = {}
    period_end: date | None = None
    fy: int | None = None
    filed: date | None = None
    for c in flow_concepts:
        v, pe, _fy = _latest_flow(c)
        flow_vals[c] = v
        if c == "revenue":
            period_end = pe
            fy = _fy

    # Filed date = the 10-K filed for the chosen period_end (max(filed) among
    # facts for that period_end on this CIK).
    if period_end is not None:
        filed_row = annuals.filter(
            (pl.col("period_end") == period_end)
            & pl.col("_period_days").is_between(ANNUAL_MIN_DAYS, ANNUAL_MAX_DAYS)
        ).sort("filed", descending=True)
        if filed_row.height > 0:
            filed = filed_row.row(0, named=True)["filed"]

    if period_end is None or filed is None:
        return None

    stock_vals = {c: _latest_stock(c) for c in stock_concepts}

    return AnnualSnapshot(
        cik=cik,
        period_end=period_end,
        filed=filed,
        fy=fy,
        revenue=flow_vals.get("revenue"),
        net_income=flow_vals.get("net_income"),
        operating_income=flow_vals.get("operating_income"),
        gross_profit=flow_vals.get("gross_profit"),
        depreciation_amortization=flow_vals.get("depreciation_amortization"),
        interest_expense=flow_vals.get("interest_expense"),
        tax_expense=flow_vals.get("tax_expense"),
        operating_cash_flow=flow_vals.get("operating_cash_flow"),
        capex=flow_vals.get("capex"),
        total_assets=stock_vals.get("total_assets"),
        stockholders_equity=stock_vals.get("stockholders_equity"),
        long_term_debt=stock_vals.get("long_term_debt"),
        cash=stock_vals.get("cash"),
        shares_outstanding=stock_vals.get("shares_outstanding"),
        eps_diluted=flow_vals.get("eps_diluted"),
    )


def quarterly_eps_series(
    con: duckdb.DuckDBPyConnection,
    as_of: date,
    cik: int,
    *,
    originals_only: bool = True,
) -> pl.DataFrame:
    """Standalone quarterly diluted EPS series for one CIK as of `as_of`.

    Returns: cik, period_end, fy, fp, filed, eps_diluted.

    Q1/Q2/Q3 are pulled directly from 10-Q filings (standalone period
    length 60-100 days). Q4 is DERIVED from the 10-K:
        eps_Q4 ≈ NI_FY / shares_FY - (eps_Q1 + eps_Q2 + eps_Q3)
    This is an approximation that ignores intra-year changes in share
    count; for SUE purposes (a YoY-difference signal) the bias is largely
    constant across companies and washes out cross-sectionally. Use only
    Q1/Q2/Q3 for production SUE if you want zero approximation.

    `originals_only=True` (default) excludes amended filings — restatements
    don't rewrite history. The PEAD/SUE path relies on this.
    """
    facts = as_of_facts(
        con, as_of=as_of, cik=cik, originals_only=originals_only,
    )
    if facts.height == 0:
        return pl.DataFrame(schema={
            "cik": pl.Int64, "period_end": pl.Date, "fy": pl.Int64,
            "fp": pl.Utf8, "filed": pl.Date, "eps_diluted": pl.Float64,
        })

    facts_with_len = facts.with_columns(
        ((pl.col("period_end") - pl.col("period_start")).dt.total_days())
        .alias("_period_days")
    )

    # Standalone quarterly EPS: period_days in [60, 100], fp in Q1/Q2/Q3,
    # concept = eps_diluted. Latest knowledge per period already applied
    # by as_of_facts.
    quarterly = (
        facts_with_len
        .filter(
            (pl.col("concept") == "eps_diluted")
            & pl.col("_period_days").is_between(QUARTERLY_MIN_DAYS, QUARTERLY_MAX_DAYS)
            & pl.col("fp").is_in(["Q1", "Q2", "Q3"])
        )
        .select(["cik", "period_end", "fy", "fp", "filed", "value"])
        .rename({"value": "eps_diluted"})
        .sort("period_end")
    )
    return quarterly
