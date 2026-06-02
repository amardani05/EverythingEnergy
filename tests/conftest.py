"""Shared pytest fixtures.

`tmp_db` gives every test its own DuckDB file under pytest's tmp_path, so
ingestion / read-API tests run in isolation without touching the real
data_store.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from signal_engine.data import store


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    db = tmp_path / "test.duckdb"
    store.init_db(db)
    return db


@pytest.fixture
def tmp_con(tmp_db: Path):
    con = duckdb.connect(str(tmp_db))
    try:
        yield con
    finally:
        con.close()
