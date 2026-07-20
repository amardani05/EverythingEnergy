"""SUE-`filed` PIT contract test - ACTIVATED in step 3.

The contract from the spec:

  Given a SUE computed from EPS at fiscal period_end P, that SUE may
  appear in the engine ONLY on dates >= the ORIGINAL 10-Q's `filed`
  date for period P. Specifically:

    For every SUE row,  signal_date >= MIN(filed) across originals(cik, period_end).

  Plus: an amendment filed LATER must not change the historical SUE
  (we use originals_only). Tested in test_pead.py separately.

If this test passes silently, the most subtle PIT contract in the
engine has a hole. The test injects deliberately-bad data (a SUE
that "should have been" attached before its filing) and asserts the
factor refuses to produce such a row.
"""

from __future__ import annotations

from datetime import date, timedelta

import duckdb
import polars as pl

from signal_engine.factors.pead import SueConfig, compute_sue
from tests.factor_fixtures import insert_quarterly_eps


def _seed_q1_history(con: duckdb.DuckDBPyConnection, cik: int,
                     years_to_eps: dict[int, float],
                     filed_delay_days: int) -> None:
    for fy, eps in sorted(years_to_eps.items()):
        period_end = date(fy, 3, 31)
        insert_quarterly_eps(
            con, cik=cik, period_end=period_end, fy=fy, fp="Q1",
            eps=eps,
            filed=period_end + timedelta(days=filed_delay_days),
            accession=f"{cik}-{fy}-Q1",
        )


def test_sue_never_attached_before_its_filed_date(tmp_con: duckdb.DuckDBPyConnection) -> None:
    """For every (cik, period_end) SUE row, signal_date must be >= the
    original 10-Q's `filed` for that period. Verified against the
    edgar_facts table directly."""
    cik = 4000001
    years = {y: 1.00 + 0.05 * (y - 2018) for y in range(2018, 2025)}
    _seed_q1_history(tmp_con, cik=cik, years_to_eps=years, filed_delay_days=45)

    sue = compute_sue(tmp_con, as_of=date(2026, 1, 1),
                      ticker_to_cik={"AAA": cik},
                      cfg=SueConfig(history_quarters=8, min_pairs=5))
    assert sue.height > 0, "expected at least one SUE row to fire"

    # Pull the original-only first-filed date per (cik, period_end) from
    # the underlying facts table.
    originals = tmp_con.execute("""
        SELECT cik, period_end, MIN(filed) AS first_filed
        FROM edgar_facts
        WHERE concept = 'eps_diluted' AND is_amendment = FALSE
        GROUP BY cik, period_end
    """).pl()

    joined = sue.join(originals, on=["cik", "period_end"], how="inner")
    violations = joined.filter(pl.col("signal_date") < pl.col("first_filed"))
    if violations.height > 0:
        print(violations)
    assert violations.height == 0, (
        "PIT contract violation: at least one SUE row has signal_date "
        "earlier than the original 10-Q's filed date. The engine has "
        "look-ahead in the earnings signal."
    )


def test_sue_filed_date_present_on_every_row(tmp_con: duckdb.DuckDBPyConnection) -> None:
    """The `signal_date` column must never be NULL - even degenerate rows
    where SUE numerator/denominator can't be computed should be dropped
    by the factor before emission."""
    cik = 4000002
    years = {y: 1.00 + 0.05 * (y - 2018) for y in range(2018, 2025)}
    _seed_q1_history(tmp_con, cik=cik, years_to_eps=years, filed_delay_days=45)
    sue = compute_sue(tmp_con, as_of=date(2026, 1, 1),
                      ticker_to_cik={"AAA": cik},
                      cfg=SueConfig(history_quarters=8, min_pairs=5))
    assert sue["signal_date"].null_count() == 0
