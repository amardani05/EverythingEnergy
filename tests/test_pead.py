"""PEAD/SUE tests — algebra + originals-only contract."""

from __future__ import annotations

from datetime import date

import duckdb
import pytest

from signal_engine.factors.fundamentals import quarterly_eps_series
from signal_engine.factors.pead import SueConfig, compute_sue, compute_sue_series
from tests.factor_fixtures import insert_quarterly_eps


def _q1(year: int) -> date:
    return date(year, 3, 31)


def _seed_q1_history(con: duckdb.DuckDBPyConnection, cik: int, *,
                     years_to_eps: dict[int, float],
                     filed_delay_days: int = 45) -> None:
    """Plant a Q1 series for the given (fy -> eps) map. `filed` = period_end + delay."""
    from datetime import timedelta
    for fy, eps in sorted(years_to_eps.items()):
        period_end = _q1(fy)
        insert_quarterly_eps(
            con, cik=cik, period_end=period_end, fy=fy, fp="Q1",
            eps=eps, filed=period_end + timedelta(days=filed_delay_days),
            accession=f"{cik:07d}-{fy}-Q1",
        )


def test_sue_requires_min_history_pairs(tmp_con: duckdb.DuckDBPyConnection) -> None:
    """SUE shouldn't fire with only 2 quarters of history (min_pairs=5)."""
    cik = 3000001
    _seed_q1_history(tmp_con, cik, years_to_eps={2020: 1.00, 2021: 1.10, 2022: 1.20})
    eps = quarterly_eps_series(tmp_con, as_of=date(2026, 1, 1), cik=cik)
    sue_df = compute_sue_series(eps)
    assert sue_df.height == 0


def test_sue_algebra_on_synthetic_series(tmp_con: duckdb.DuckDBPyConnection) -> None:
    """Plant a Q1 series with KNOWN YoY differences so the SUE numerator
    and denominator are both checkable. 2024 has a deliberate shock so
    stdev > 0 and SUE is a well-defined number."""
    cik = 3000003
    insert_quarterly_eps(
        tmp_con, cik=cik, period_end=_q1(2018), fy=2018, fp="Q1", eps=1.00,
        filed=_q1(2018), accession="A0",
    )
    # YoY diffs across 2019..2023: 0.05, 0.05, 0.05, -0.05, 0.10.
    # 2024 shocks: YoY = 0.80
    yoy_pairs = [(2019, 1.05), (2020, 1.10), (2021, 1.15), (2022, 1.10),
                 (2023, 1.20), (2024, 2.00)]
    for fy, eps_val in yoy_pairs:
        insert_quarterly_eps(
            tmp_con, cik=cik, period_end=_q1(fy), fy=fy, fp="Q1", eps=eps_val,
            filed=_q1(fy), accession=f"A-{fy}",
        )
    eps = quarterly_eps_series(tmp_con, as_of=date(2026, 1, 1), cik=cik)
    sue_df = compute_sue_series(eps, SueConfig(history_quarters=8, min_pairs=5))
    spike = sue_df.filter(sue_df["fy"] == 2024).row(0, named=True)

    # Numerator: 2.00 - 1.20 = 0.80
    assert spike["eps_yoy_diff"] == pytest.approx(0.80, abs=1e-9)
    # Denominator: pstdev([0.05, 0.05, 0.05, -0.05, 0.10]) ≈ 0.05385
    # SUE ≈ 0.80 / 0.05385 ≈ 14.86
    import statistics
    expected_sd = statistics.pstdev([0.05, 0.05, 0.05, -0.05, 0.10])
    assert spike["sue"] == pytest.approx(0.80 / expected_sd, rel=0.01)


def test_originals_only_excludes_amendments(tmp_con: duckdb.DuckDBPyConnection) -> None:
    """If a 10-Q/A is filed later with a different EPS, the original
    EPS is what SUE uses — restatements never rewrite history."""
    cik = 3000004
    # Plant a long enough Q1 history so SUE can fire
    years = {y: 1.00 + 0.05 * (y - 2018) for y in range(2018, 2024)}
    _seed_q1_history(tmp_con, cik, years_to_eps=years)
    # Plant the original Q1 2024
    insert_quarterly_eps(
        tmp_con, cik=cik, period_end=_q1(2024), fy=2024, fp="Q1", eps=2.00,
        filed=date(2024, 5, 15), accession=f"{cik}-2024Q1-orig",
    )
    # Plant a restatement 6 months later with a wildly different value
    insert_quarterly_eps(
        tmp_con, cik=cik, period_end=_q1(2024), fy=2024, fp="Q1", eps=0.10,
        filed=date(2024, 11, 1), accession=f"{cik}-2024Q1-A1",
        is_amendment=True,
    )
    eps = quarterly_eps_series(tmp_con, as_of=date(2026, 1, 1), cik=cik,
                               originals_only=True)
    row_2024 = eps.filter(eps["fy"] == 2024).row(0, named=True)
    assert row_2024["eps_diluted"] == pytest.approx(2.00), \
        "originals_only must return the ORIGINAL 2.00, not the amended 0.10"


def test_compute_sue_attaches_signal_date_eq_filed(tmp_con: duckdb.DuckDBPyConnection) -> None:
    """The signal_date on every SUE row must equal the original 10-Q's
    filed date — NEVER period_end, NEVER ingest_ts. This is the spec
    contract for 'no look-ahead in the earnings signal'."""
    cik = 3000005
    years = {y: 1.00 + 0.05 * (y - 2018) for y in range(2018, 2025)}
    # Plant with filed delays so signal_date is visibly distinct from period_end
    from datetime import timedelta
    for fy, eps in years.items():
        period_end = _q1(fy)
        filed = period_end + timedelta(days=45)
        insert_quarterly_eps(
            tmp_con, cik=cik, period_end=period_end, fy=fy, fp="Q1", eps=eps,
            filed=filed, accession=f"{cik}-{fy}-Q1",
        )

    sue = compute_sue(tmp_con, as_of=date(2026, 1, 1),
                      ticker_to_cik={"SIG": cik},
                      cfg=SueConfig(history_quarters=8, min_pairs=5))
    for r in sue.iter_rows(named=True):
        from datetime import timedelta as td
        assert r["signal_date"] == r["period_end"] + td(days=45)
        # Specifically: signal_date is NEVER strictly less than period_end
        assert r["signal_date"] > r["period_end"]
