"""Shared helpers for factor tests - synthetic edgar_facts and prices
injection so we can drive Value/Quality/PEAD in isolation without hitting
the live ingester."""

from __future__ import annotations

from datetime import date
from typing import Any

import duckdb


def insert_fact(
    con: duckdb.DuckDBPyConnection,
    *,
    cik: int,
    concept: str,
    value: float,
    period_start: date,
    period_end: date,
    filed: date,
    accession: str,
    fy: int | None = None,
    fp: str | None = None,
    form: str = "10-K",
    unit: str = "USD",
    taxonomy: str = "us-gaap",
    is_amendment: bool = False,
    concept_used: str | None = None,
) -> None:
    con.execute(
        """
        INSERT INTO edgar_facts
          (cik, taxonomy, concept, concept_used, unit, period_start, period_end,
           fy, fp, form, is_amendment, accession, filed, value)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [cik, taxonomy, concept, concept_used or concept, unit,
         period_start, period_end, fy, fp, form, is_amendment, accession,
         filed, value],
    )


def insert_price(
    con: duckdb.DuckDBPyConnection,
    *,
    ticker: str,
    d: date,
    close: float,
    source: str = "yfinance",
) -> None:
    con.execute(
        """
        INSERT INTO prices
          (ticker, date, open, high, low, close, volume, source, div_adjusted, split_adjusted)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT DO NOTHING
        """,
        [ticker, d, close, close, close, close, 1000.0, source, False, True],
    )


def insert_annual_bundle(
    con: duckdb.DuckDBPyConnection,
    *,
    cik: int,
    fy: int,
    period_end: date,
    filed: date,
    values: dict[str, float],
    accession_prefix: str = "0000-",
) -> None:
    """Insert a full annual fact set for one company-year.

    `values` maps canonical concept names -> values (USD; eps in USD/share;
    shares in count). Missing keys are simply not inserted.
    """
    period_start = date(period_end.year, 1, 1)
    for concept, val in values.items():
        unit = ("USD/shares" if concept.startswith("eps")
                else "shares" if concept == "shares_outstanding"
                else "USD")
        insert_fact(
            con, cik=cik, concept=concept, value=val,
            period_start=period_start, period_end=period_end, filed=filed,
            accession=f"{accession_prefix}{fy:04d}", fy=fy, fp="FY",
            form="10-K", unit=unit,
        )


def insert_quarterly_eps(
    con: duckdb.DuckDBPyConnection,
    *,
    cik: int,
    period_end: date,
    fy: int,
    fp: str,
    eps: float,
    filed: date,
    accession: str,
    is_amendment: bool = False,
) -> None:
    """Insert a standalone quarterly EPS row (90-day period)."""
    if fp == "Q1":
        period_start = date(period_end.year, 1, 1)
    elif fp == "Q2":
        period_start = date(period_end.year, 4, 1)
    elif fp == "Q3":
        period_start = date(period_end.year, 7, 1)
    else:
        raise ValueError(f"unsupported fp: {fp}")
    insert_fact(
        con, cik=cik, concept="eps_diluted", value=eps,
        period_start=period_start, period_end=period_end, filed=filed,
        accession=accession, fy=fy, fp=fp,
        form=("10-Q/A" if is_amendment else "10-Q"),
        unit="USD/shares", is_amendment=is_amendment,
    )


def standard_annual_values(
    *,
    revenue: float = 1_000_000_000,
    net_income: float = 100_000_000,
    operating_income: float = 150_000_000,
    gross_profit: float = 400_000_000,
    da: float = 50_000_000,
    interest: float = 10_000_000,
    tax: float = 40_000_000,
    ocf: float = 180_000_000,
    capex: float = 60_000_000,
    assets: float = 2_000_000_000,
    equity: float = 800_000_000,
    lt_debt: float = 300_000_000,
    cash: float = 100_000_000,
    shares: float = 50_000_000,
    eps_diluted: float = 2.00,
) -> dict[str, Any]:
    return {
        "revenue": revenue,
        "net_income": net_income,
        "operating_income": operating_income,
        "gross_profit": gross_profit,
        "depreciation_amortization": da,
        "interest_expense": interest,
        "tax_expense": tax,
        "operating_cash_flow": ocf,
        "capex": capex,
        "total_assets": assets,
        "stockholders_equity": equity,
        "long_term_debt": lt_debt,
        "cash": cash,
        "shares_outstanding": shares,
        "eps_diluted": eps_diluted,
    }
