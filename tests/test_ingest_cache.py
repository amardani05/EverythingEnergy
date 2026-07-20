"""--from-cache re-parse path: cached raw EDGAR JSON -> DuckDB without HTTP.

Regression context: the 2026-06-23 live ingest crashed mid-loop and lost the
whole submissions batch because rows were only inserted after the loop. The
hardened script flushes incrementally and can rebuild from the raw cache;
these tests drive that rebuild end-to-end on synthetic cache files.
"""

from __future__ import annotations

import importlib.util
import json
import os
from datetime import date, datetime
from pathlib import Path

import duckdb

from signal_engine.data import edgar

REPO_ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "ingest_edgar", REPO_ROOT / "scripts" / "ingest_edgar.py"
)
assert _spec is not None and _spec.loader is not None
ingest_edgar = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ingest_edgar)

CACHE_MTIME = datetime(2026, 6, 23, 12, 0, 0).timestamp()

CONCEPT_CHAINS = {
    "revenue": {"taxonomy": "us-gaap", "tags": ["Revenues"]},
}


def _write_cache(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload))
    os.utime(path, (CACHE_MTIME, CACHE_MTIME))


def _submissions_payload(cik: int, name: str) -> dict:
    return {
        "cik": cik,
        "name": name,
        "tickers": ["AAA", None],       # None entry: the 6/23 crash shape
        "exchanges": ["Nasdaq", None],
        "sic": "1311",
        "sicDescription": "Crude Petroleum and Natural Gas",
        "fiscalYearEnd": "1231",
    }


def _companyfacts_payload() -> dict:
    return {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {
                                "start": "2025-01-01", "end": "2025-12-31",
                                "fy": 2025, "fp": "FY", "form": "10-K",
                                "accn": "0001-25-000001", "filed": "2026-02-15",
                                "val": 1_000_000.0,
                            },
                            {
                                "start": "2024-01-01", "end": "2024-12-31",
                                "fy": 2024, "fp": "FY", "form": "10-K",
                                "accn": "0001-24-000001", "filed": "2025-02-14",
                                "val": 900_000.0,
                            },
                        ]
                    }
                }
            }
        }
    }


def test_iter_cached_payloads_skips_junk(tmp_path: Path) -> None:
    _write_cache(tmp_path / "submissions_0000000123.json", _submissions_payload(123, "Alpha"))
    (tmp_path / "submissions_notacik.json").write_text("{}")
    (tmp_path / "submissions_0000000456.json").write_text("{corrupt json")

    out = list(edgar.iter_cached_payloads(tmp_path, "submissions"))
    assert len(out) == 1
    cik, path, payload = out[0]
    assert cik == 123
    assert path.name == "submissions_0000000123.json"
    assert payload["name"] == "Alpha"


def test_reparse_from_cache_round_trip(tmp_db: Path, tmp_path: Path) -> None:
    raw_dir = tmp_path / "edgar"
    raw_dir.mkdir()
    _write_cache(raw_dir / "submissions_0000000123.json", _submissions_payload(123, "Alpha"))
    _write_cache(raw_dir / "submissions_0000000456.json", _submissions_payload(456, "Beta"))
    _write_cache(raw_dir / "companyfacts_0000000123.json", _companyfacts_payload())

    ingest_edgar.reparse_from_cache(tmp_db, raw_dir, CONCEPT_CHAINS)

    con = duckdb.connect(str(tmp_db))
    try:
        subs = con.execute(
            "SELECT cik, snapshot_date, tickers, exchanges FROM edgar_submissions ORDER BY cik"
        ).fetchall()
        assert [r[0] for r in subs] == [123, 456]
        # snapshot_date must be the cache file's mtime date, not today.
        assert all(r[1] == date(2026, 6, 23) for r in subs)
        # None entries filtered out of the pipe-joined arrays.
        assert subs[0][2] == "AAA" and subs[0][3] == "Nasdaq"

        facts = con.execute(
            "SELECT cik, concept, value, filed FROM edgar_facts ORDER BY filed"
        ).fetchall()
        assert len(facts) == 2
        assert all(f[0] == 123 and f[1] == "revenue" for f in facts)
        assert facts[1][2] == 1_000_000.0
    finally:
        con.close()


def test_reparse_from_cache_is_idempotent(tmp_db: Path, tmp_path: Path) -> None:
    raw_dir = tmp_path / "edgar"
    raw_dir.mkdir()
    _write_cache(raw_dir / "submissions_0000000123.json", _submissions_payload(123, "Alpha"))
    _write_cache(raw_dir / "companyfacts_0000000123.json", _companyfacts_payload())

    ingest_edgar.reparse_from_cache(tmp_db, raw_dir, CONCEPT_CHAINS)
    ingest_edgar.reparse_from_cache(tmp_db, raw_dir, CONCEPT_CHAINS)

    con = duckdb.connect(str(tmp_db))
    try:
        assert con.execute("SELECT count(*) FROM edgar_submissions").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM edgar_facts").fetchone()[0] == 2
    finally:
        con.close()


def test_flush_submissions_partial_batch_survives_bad_row(tmp_db: Path, tmp_path: Path) -> None:
    """A payload that fails to parse is skipped; the rest of the cache still lands."""
    raw_dir = tmp_path / "edgar"
    raw_dir.mkdir()
    _write_cache(raw_dir / "submissions_0000000123.json", _submissions_payload(123, "Alpha"))
    # `tickers` as a plain string will make submissions_row raise (not a list) - # simulates the next unknown payload mutation EDGAR throws at us.
    bad = _submissions_payload(456, "Beta")
    bad["tickers"] = 42
    _write_cache(raw_dir / "submissions_0000000456.json", bad)

    ingest_edgar.reparse_from_cache(tmp_db, raw_dir, CONCEPT_CHAINS)

    con = duckdb.connect(str(tmp_db))
    try:
        subs = con.execute("SELECT cik FROM edgar_submissions").fetchall()
        assert [r[0] for r in subs] == [123]
    finally:
        con.close()
