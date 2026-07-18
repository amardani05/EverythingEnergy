"""PEAD/SUE tests — algebra + originals-only contract."""

from __future__ import annotations

from datetime import date

import duckdb
import pytest

from signal_engine.factors.fundamentals import quarterly_eps_series
from signal_engine.factors.pead import SueConfig, compute_sue, compute_sue_series
from tests.factor_fixtures import insert_annual_bundle, insert_quarterly_eps


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


def _seed_full_year(con: duckdb.DuckDBPyConnection, cik: int, fy: int,
                    q_eps: tuple[float, float, float], fy_eps: float,
                    tenk_filed: date) -> None:
    """Q1/Q2/Q3 10-Qs plus the FY annual EPS row from the 10-K."""
    from datetime import timedelta
    for fp, month_day, eps in [("Q1", (3, 31), q_eps[0]), ("Q2", (6, 30), q_eps[1]),
                               ("Q3", (9, 30), q_eps[2])]:
        pe = date(fy, *month_day)
        insert_quarterly_eps(con, cik=cik, period_end=pe, fy=fy, fp=fp, eps=eps,
                             filed=pe + timedelta(days=40), accession=f"{cik}-{fy}-{fp}")
    insert_annual_bundle(con, cik=cik, fy=fy, period_end=date(fy, 12, 31),
                         filed=tenk_filed, values={"eps_diluted": fy_eps},
                         accession_prefix=f"{cik}-FY-")


def test_q4_derived_from_fy_minus_quarters(tmp_con: duckdb.DuckDBPyConnection) -> None:
    """Q4 EPS = FY EPS - (Q1+Q2+Q3), filed on the 10-K date, derived=True."""
    cik = 3000020
    _seed_full_year(tmp_con, cik, 2023, q_eps=(0.30, 0.25, 0.20), fy_eps=1.00,
                    tenk_filed=date(2024, 2, 15))
    eps = quarterly_eps_series(tmp_con, as_of=date(2026, 1, 1), cik=cik)
    q4 = eps.filter(eps["fp"] == "Q4")
    assert q4.height == 1
    row = q4.row(0, named=True)
    assert row["eps_diluted"] == pytest.approx(0.25)   # 1.00 - 0.75
    assert row["filed"] == date(2024, 2, 15)           # knowable only at the 10-K
    assert row["period_end"] == date(2023, 12, 31)
    assert row["derived"] is True
    # Q1-Q3 are direct, not derived.
    assert eps.filter(eps["fp"] != "Q4")["derived"].any() is False
    # Opt-out path returns the zero-approximation series.
    eps_no_q4 = quarterly_eps_series(tmp_con, as_of=date(2026, 1, 1), cik=cik,
                                     derive_q4=False)
    assert eps_no_q4.filter(eps_no_q4["fp"] == "Q4").height == 0


def test_q4_not_derived_when_a_quarter_is_missing(tmp_con: duckdb.DuckDBPyConnection) -> None:
    """Partial years are skipped, never guessed."""
    from datetime import timedelta
    cik = 3000021
    for fp, month_day, eps in [("Q1", (3, 31), 0.30), ("Q2", (6, 30), 0.25)]:
        pe = date(2023, *month_day)
        insert_quarterly_eps(tmp_con, cik=cik, period_end=pe, fy=2023, fp=fp, eps=eps,
                             filed=pe + timedelta(days=40), accession=f"{cik}-2023-{fp}")
    insert_annual_bundle(tmp_con, cik=cik, fy=2023, period_end=date(2023, 12, 31),
                         filed=date(2024, 2, 15), values={"eps_diluted": 1.00})
    eps = quarterly_eps_series(tmp_con, as_of=date(2026, 1, 1), cik=cik)
    assert eps.filter(eps["fp"] == "Q4").height == 0


def test_q4_invisible_before_tenk_filed(tmp_con: duckdb.DuckDBPyConnection) -> None:
    """PIT: the derived Q4 must not exist at an as_of before the 10-K filed,
    even though all three quarters are already public."""
    cik = 3000022
    _seed_full_year(tmp_con, cik, 2023, q_eps=(0.30, 0.25, 0.20), fy_eps=1.00,
                    tenk_filed=date(2024, 2, 15))
    eps = quarterly_eps_series(tmp_con, as_of=date(2024, 1, 31), cik=cik)
    assert eps.filter(eps["fp"] == "Q4").height == 0
    assert eps.filter(eps["fp"] == "Q3").height == 1   # quarters themselves visible


def test_sue_series_all_none_keeps_float_dtype(tmp_con: duckdb.DuckDBPyConnection) -> None:
    """A company whose Q1 EPS grows by a constant step has zero-variance YoY
    diffs -> pstdev == 0 -> sue == None on every emitted row. The frame must
    still type `sue` as Float64 (not Null), or the pl.concat over tickers in
    compute_sue explodes. Regression for the 2026-07 live-baseline crash."""
    import polars as pl
    cik = 3000010
    # Perfectly flat EPS 2012..2024: every YoY diff is EXACTLY 0.0, so
    # pstdev == 0.0 and sue == None on every emitted row (not a float epsilon).
    _seed_q1_history(tmp_con, cik,
                     years_to_eps={y: 1.00 for y in range(2012, 2025)})
    eps = quarterly_eps_series(tmp_con, as_of=date(2026, 1, 1), cik=cik)
    sue_df = compute_sue_series(eps, SueConfig(history_quarters=8, min_pairs=5))
    assert sue_df.height > 0, "rows should still be emitted with sue=None"
    assert sue_df["sue"].dtype == pl.Float64
    assert sue_df["sue"].null_count() == sue_df.height, "every sue must be None here"


def test_compute_sue_mixes_all_none_and_valued_tickers(tmp_con: duckdb.DuckDBPyConnection) -> None:
    """compute_sue must vstack a flat-EPS ticker (all sue None) with a
    shocked ticker (real Float64 sue) without a SchemaError."""
    import polars as pl
    flat_cik, shock_cik = 3000011, 3000012
    # Perfectly flat EPS -> pstdev exactly 0 -> all-None sue (Null dtype pre-fix).
    _seed_q1_history(tmp_con, flat_cik,
                     years_to_eps={y: 2.00 for y in range(2012, 2025)})
    # Shocked series -> at least one real sue.
    insert_quarterly_eps(tmp_con, cik=shock_cik, period_end=_q1(2018), fy=2018,
                         fp="Q1", eps=1.00, filed=_q1(2018), accession="S0")
    for fy, eps_val in [(2019, 1.05), (2020, 1.10), (2021, 1.15), (2022, 1.10),
                        (2023, 1.20), (2024, 2.00)]:
        insert_quarterly_eps(tmp_con, cik=shock_cik, period_end=_q1(fy), fy=fy,
                             fp="Q1", eps=eps_val, filed=_q1(fy), accession=f"S-{fy}")

    out = compute_sue(tmp_con, as_of=date(2026, 1, 1),
                      ticker_to_cik={"FLAT": flat_cik, "SHOCK": shock_cik},
                      cfg=SueConfig(history_quarters=8, min_pairs=5))
    assert out["sue"].dtype == pl.Float64
    assert {"FLAT", "SHOCK"} <= set(out["ticker"].to_list())
    assert out.filter(out["ticker"] == "SHOCK")["sue"].drop_nulls().len() > 0


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
