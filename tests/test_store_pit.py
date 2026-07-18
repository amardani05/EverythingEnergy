"""Bitemporal read-API tests — the PIT plumbing must not be violable.

Three things we assert here:
  1. Facts filed AFTER as_of are invisible (the basic PIT cut).
  2. Restatements (amended forms) supersede originals on the *as-of side*
     of their filing date, but originals remain visible BEFORE the
     amendment was filed.
  3. `originals_only=True` (the PEAD path) NEVER returns amendments,
     regardless of as_of.
"""

from __future__ import annotations

from datetime import date

import duckdb

from signal_engine.data import store


def _insert(con: duckdb.DuckDBPyConnection, **fact: object) -> None:
    cols = ",".join(fact.keys())
    placeholders = ",".join("?" for _ in fact)
    con.execute(
        f"INSERT INTO edgar_facts ({cols}) VALUES ({placeholders})",
        list(fact.values()),
    )


def _make_row(*, accession: str, filed: str, value: float, is_amendment: bool = False) -> dict[str, object]:
    return {
        "cik": 1234567,
        "taxonomy": "us-gaap",
        "concept": "eps_diluted",
        "concept_used": "EarningsPerShareDiluted",
        "unit": "USD/shares",
        "period_start": None,
        "period_end": date(2024, 3, 31),
        "fy": 2024,
        "fp": "Q1",
        "form": "10-Q/A" if is_amendment else "10-Q",
        "is_amendment": is_amendment,
        "accession": accession,
        "filed": filed,
        "value": value,
    }


def test_facts_filed_after_as_of_are_invisible(tmp_con: duckdb.DuckDBPyConnection) -> None:
    _insert(tmp_con, **_make_row(accession="0001-25-001", filed="2024-05-01", value=1.10))
    visible_before = store.as_of_facts(tmp_con, as_of=date(2024, 4, 30), concept="eps_diluted")
    visible_after = store.as_of_facts(tmp_con, as_of=date(2024, 5, 1), concept="eps_diluted")
    assert visible_before.height == 0, "fact filed 2024-05-01 must be invisible on 2024-04-30"
    assert visible_after.height == 1


def test_restatement_supersedes_only_after_its_filing(tmp_con: duckdb.DuckDBPyConnection) -> None:
    # Original 10-Q filed May 1 reports EPS = 1.10
    _insert(tmp_con, **_make_row(accession="0001-25-001", filed="2024-05-01", value=1.10))
    # 10-Q/A filed Aug 1 restates EPS = 1.05
    _insert(tmp_con, **_make_row(accession="0001-25-099", filed="2024-08-01", value=1.05, is_amendment=True))

    # Before the amendment: original value is what was known.
    v_before = store.as_of_facts(tmp_con, as_of=date(2024, 7, 31), concept="eps_diluted")
    assert v_before.height == 1
    assert v_before["value"].item() == 1.10

    # On/after the amendment: restated value wins.
    v_after = store.as_of_facts(tmp_con, as_of=date(2024, 8, 1), concept="eps_diluted")
    assert v_after.height == 1
    assert v_after["value"].item() == 1.05


def test_originals_only_never_returns_amendments(tmp_con: duckdb.DuckDBPyConnection) -> None:
    """PEAD/SUE path: even after a restatement is filed, originals_only
    should return the original EPS so that historical surprise values are
    not retroactively rewritten."""
    _insert(tmp_con, **_make_row(accession="0001-25-001", filed="2024-05-01", value=1.10))
    _insert(tmp_con, **_make_row(accession="0001-25-099", filed="2024-08-01", value=1.05, is_amendment=True))

    v = store.as_of_facts(tmp_con, as_of=date(2025, 1, 1), concept="eps_diluted", originals_only=True)
    assert v.height == 1, "exactly one original row expected"
    assert v["value"].item() == 1.10
    assert v["is_amendment"].item() is False
