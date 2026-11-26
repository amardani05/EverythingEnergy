import sys
from pathlib import Path

import duckdb
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ingest_prices import write_prices

from signal_engine.data import store


def frame(close):
    return pl.DataFrame({"ticker": ["X"], "date": ["2026-11-19"], "open": [1.0], "high": [1.0], "low": [1.0],
                         "close": [close], "volume": [1.0], "source": ["yfinance"], "div_adjusted": [False],
                         "split_adjusted": [True]}, schema_overrides={"close": pl.Float64}
                        ).with_columns(pl.col("date").str.to_date())


def closes(db):
    with duckdb.connect(str(db)) as con:
        return [r[0] for r in con.execute("select close from prices").fetchall()]


def test_blank_close_not_stored_repaired_and_good_close_kept(tmp_path):
    db = tmp_path / "t.duckdb"
    store.init_db(db)
    for bad in (None, float("nan"), 0.0):
        write_prices(db, frame(bad))
    assert closes(db) == []
    write_prices(db, frame(10.0))
    with store.connect(db) as con:
        con.execute("update prices set close = null")  # as left by the old insert
    write_prices(db, frame(11.0))
    assert closes(db) == [11.0]
    write_prices(db, frame(99.0))
    assert closes(db) == [11.0]
