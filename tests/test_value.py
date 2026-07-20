"""Value factor tests - FCF yield + EV/EBITDA algebra and PIT contract."""

from __future__ import annotations

from datetime import date

import duckdb
import pytest

from signal_engine.factors.value import compute_value
from tests.factor_fixtures import (
    insert_annual_bundle,
    insert_price,
    standard_annual_values,
)


def test_fcf_yield_and_ev_ebitda_match_hand_numbers(tmp_con: duckdb.DuckDBPyConnection) -> None:
    """Insert a known company-year + price, compute, verify ratios."""
    cik = 1000001
    period_end = date(2024, 12, 31)
    filed = date(2025, 2, 15)
    vals = standard_annual_values()  # see fixtures.py for the numbers
    insert_annual_bundle(tmp_con, cik=cik, fy=2024, period_end=period_end,
                         filed=filed, values=vals)
    insert_price(tmp_con, ticker="ABC", d=date(2025, 6, 1), close=20.00)

    df = compute_value(tmp_con, as_of=date(2025, 6, 1), ticker_to_cik={"ABC": cik})
    row = df.row(0, named=True)

    # market_cap = 20 * 50,000,000 = 1,000,000,000
    assert row["market_cap"] == pytest.approx(1_000_000_000)

    # EV = mcap + lt_debt - cash = 1B + 300M - 100M = 1.2B
    assert row["enterprise_value"] == pytest.approx(1_200_000_000)

    # FCF = OCF - Capex = 180M - 60M = 120M
    # FCF yield = 120M / 1B = 0.12
    assert row["fcf_yield"] == pytest.approx(0.12)

    # EBITDA (street) = NI + Int + Tax + D&A = 100 + 10 + 40 + 50 = 200M
    # EV / EBITDA = 1.2B / 200M = 6.0
    assert row["ev_ebitda"] == pytest.approx(6.0)


def test_negative_ebitda_returns_none(tmp_con: duckdb.DuckDBPyConnection) -> None:
    """Sign-flipped EV/EBITDA breaks ranking - emit None instead."""
    cik = 1000002
    # Push values so EBITDA is negative: big NI loss, tiny everything else
    vals = standard_annual_values(net_income=-500_000_000, interest=10_000_000,
                                  tax=0, da=10_000_000)
    insert_annual_bundle(tmp_con, cik=cik, fy=2024, period_end=date(2024, 12, 31),
                         filed=date(2025, 2, 15), values=vals)
    insert_price(tmp_con, ticker="LOSS", d=date(2025, 6, 1), close=10.0)

    df = compute_value(tmp_con, as_of=date(2025, 6, 1), ticker_to_cik={"LOSS": cik})
    row = df.row(0, named=True)
    assert row["ev_ebitda"] is None
    # FCF yield is allowed to be negative - see docstring
    assert row["fcf_yield"] is not None


def test_missing_price_returns_none_mcap(tmp_con: duckdb.DuckDBPyConnection) -> None:
    cik = 1000003
    insert_annual_bundle(tmp_con, cik=cik, fy=2024, period_end=date(2024, 12, 31),
                         filed=date(2025, 2, 15),
                         values=standard_annual_values())
    # No price inserted.
    df = compute_value(tmp_con, as_of=date(2025, 6, 1), ticker_to_cik={"NOPX": cik})
    row = df.row(0, named=True)
    assert row["market_cap"] is None
    assert row["enterprise_value"] is None
    assert row["fcf_yield"] is None
    assert row["ev_ebitda"] is None


def test_value_does_not_see_facts_filed_after_as_of(tmp_con: duckdb.DuckDBPyConnection) -> None:
    """PIT canary: a 10-K filed AFTER as_of must be invisible."""
    cik = 1000004
    insert_annual_bundle(tmp_con, cik=cik, fy=2024, period_end=date(2024, 12, 31),
                         filed=date(2025, 7, 1),   # AFTER our as_of
                         values=standard_annual_values())
    insert_price(tmp_con, ticker="FUT", d=date(2025, 6, 1), close=20.0)

    df = compute_value(tmp_con, as_of=date(2025, 6, 1), ticker_to_cik={"FUT": cik})
    row = df.row(0, named=True)
    # Snapshot returns None -> all derived values are None
    assert row["market_cap"] is None
    assert row["fcf_yield"] is None
    assert row["ev_ebitda"] is None
