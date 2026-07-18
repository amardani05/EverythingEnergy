"""submissions_row regression tests — locks the None-handling that broke
the live ingest around 2026-06-23 (some filers ship null entries inside
their `tickers` / `exchanges` arrays)."""

from __future__ import annotations

from signal_engine.data.edgar import EdgarClient


def test_submissions_row_filters_none_from_exchanges() -> None:
    payload = {
        "cik": 1234567,
        "name": "Multi-listed Co.",
        "tickers": ["FOO", "FOO.B"],
        "exchanges": ["Nasdaq", None],   # the case that crashed live ingest
        "sic": "3585",
        "sicDescription": "Air-Cond Equip",
        "fiscalYearEnd": "1231",
    }
    row = EdgarClient.submissions_row(payload, snapshot_date="2026-06-23")
    assert row["exchanges"] == "Nasdaq"
    assert row["tickers"] == "FOO|FOO.B"


def test_submissions_row_all_none_arrays() -> None:
    """If every entry is None, output is None (not empty string)."""
    payload = {"cik": 999, "tickers": [None, None], "exchanges": [None]}
    row = EdgarClient.submissions_row(payload, "2026-06-23")
    assert row["tickers"] is None
    assert row["exchanges"] is None


def test_submissions_row_empty_arrays() -> None:
    payload = {"cik": 999, "tickers": [], "exchanges": []}
    row = EdgarClient.submissions_row(payload, "2026-06-23")
    assert row["tickers"] is None
    assert row["exchanges"] is None


def test_submissions_row_missing_keys() -> None:
    """Some old filings have no tickers/exchanges key at all."""
    payload = {"cik": 999, "name": "Old Co.", "sic": "1234"}
    row = EdgarClient.submissions_row(payload, "2026-06-23")
    assert row["tickers"] is None
    assert row["exchanges"] is None
    assert row["name"] == "Old Co."
