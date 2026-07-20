"""Quality factor tests - ROIC + accruals + margin stability."""

from __future__ import annotations

from datetime import date

import duckdb
import pytest

from signal_engine.factors.quality import compute_quality
from tests.factor_fixtures import (
    insert_annual_bundle,
    standard_annual_values,
)


def test_roic_and_accruals_algebra(tmp_con: duckdb.DuckDBPyConnection) -> None:
    cik = 2000001
    vals = standard_annual_values()  # OpInc=150M, NI=100M, Tax=40M, OCF=180M, ...
    insert_annual_bundle(tmp_con, cik=cik, fy=2024, period_end=date(2024, 12, 31),
                         filed=date(2025, 2, 15), values=vals)

    df = compute_quality(tmp_con, as_of=date(2025, 6, 1), ticker_to_cik={"Q1": cik})
    row = df.row(0, named=True)

    # Effective tax = 40 / (100+40) = 0.2857; clamped to <=0.5 so unchanged.
    # NOPAT = 150M * (1 - 0.2857) = 107.14M
    # Invested = 800M + 300M = 1100M
    # ROIC = 107.14 / 1100 = 0.0974
    assert row["roic"] == pytest.approx(0.0974, abs=0.001)

    # Accruals = (NI - OCF) / Assets = (100M - 180M) / 2B = -0.04
    assert row["accruals"] == pytest.approx(-0.04, abs=0.001)


def test_margin_stability_requires_min_years(tmp_con: duckdb.DuckDBPyConnection) -> None:
    """1 year of history -> margin_stability is None."""
    cik = 2000002
    insert_annual_bundle(tmp_con, cik=cik, fy=2024, period_end=date(2024, 12, 31),
                         filed=date(2025, 2, 15),
                         values=standard_annual_values())
    df = compute_quality(tmp_con, as_of=date(2025, 6, 1), ticker_to_cik={"NEW": cik})
    assert df.row(0, named=True)["margin_stability"] is None


def test_margin_stability_higher_when_margins_more_consistent(tmp_con: duckdb.DuckDBPyConnection) -> None:
    """Two companies with same trailing-mean margin but different vol - the steadier one must score higher."""
    stable_cik, volatile_cik = 2000003, 2000004
    # Stable: opinc/revenue = 0.15 every year for 4 years
    for i, fy in enumerate(range(2021, 2025)):
        v = standard_annual_values(revenue=1_000_000_000 + 10_000_000 * i,
                                   operating_income=int((1_000_000_000 + 10_000_000 * i) * 0.15))
        insert_annual_bundle(tmp_con, cik=stable_cik, fy=fy,
                             period_end=date(fy, 12, 31),
                             filed=date(fy + 1, 2, 15), values=v,
                             accession_prefix="stable-")
    # Volatile: oscillates 0.05 / 0.25 / 0.05 / 0.25
    for i, fy in enumerate(range(2021, 2025)):
        margin = 0.05 if i % 2 == 0 else 0.25
        v = standard_annual_values(revenue=1_000_000_000,
                                   operating_income=int(1_000_000_000 * margin))
        insert_annual_bundle(tmp_con, cik=volatile_cik, fy=fy,
                             period_end=date(fy, 12, 31),
                             filed=date(fy + 1, 2, 15), values=v,
                             accession_prefix="vol-")

    df = compute_quality(tmp_con, as_of=date(2025, 6, 1),
                         ticker_to_cik={"STABLE": stable_cik, "VOL": volatile_cik})
    stable_row = df.filter(df["ticker"] == "STABLE").row(0, named=True)
    vol_row = df.filter(df["ticker"] == "VOL").row(0, named=True)
    assert stable_row["margin_stability"] is not None
    assert vol_row["margin_stability"] is not None
    assert stable_row["margin_stability"] > vol_row["margin_stability"]


def test_missing_snapshot_returns_all_none(tmp_con: duckdb.DuckDBPyConnection) -> None:
    df = compute_quality(tmp_con, as_of=date(2025, 6, 1), ticker_to_cik={"NONE": 2000005})
    row = df.row(0, named=True)
    assert row["roic"] is None
    assert row["accruals"] is None
    assert row["margin_stability"] is None
