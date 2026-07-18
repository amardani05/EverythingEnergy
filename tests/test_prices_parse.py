"""parse_yf_history: yfinance frame -> (prices, corporate_actions).

Regression context: the original parser fetched dividends (`actions=True`)
then silently dropped them in the final column select, making total-return
math impossible from the store. These tests pin the parse contract with a
synthetic yfinance-shaped frame — no network.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

from signal_engine.data.prices import ACTIONS_SCHEMA, PRICES_SCHEMA, parse_yf_history


def _yf_frame(*, with_actions: bool = True) -> pd.DataFrame:
    idx = pd.DatetimeIndex(
        pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]),
        name="Date",
    ).tz_localize("America/New_York")
    data = {
        "Open":   [10.0, 10.5, 11.0, 5.6],
        "High":   [10.6, 11.0, 11.4, 5.8],
        "Low":    [9.9, 10.4, 10.9, 5.5],
        "Close":  [10.5, 10.9, 11.2, 5.7],
        "Volume": [1000, 1100, 900, 2500],
    }
    if with_actions:
        data["Dividends"] = [0.0, 0.25, 0.0, 0.0]
        data["Stock Splits"] = [0.0, 0.0, 0.0, 2.0]
    return pd.DataFrame(data, index=idx)


def test_parse_prices_schema_and_flags() -> None:
    prices, _ = parse_yf_history(_yf_frame(), "aaon")
    assert prices.height == 4
    assert list(prices.columns) == list(PRICES_SCHEMA.keys())
    assert prices["ticker"].unique().to_list() == ["AAON"]
    assert prices["source"].unique().to_list() == ["yfinance"]
    assert prices["div_adjusted"].unique().to_list() == [False]
    assert prices["split_adjusted"].unique().to_list() == [True]
    assert prices["date"].to_list()[0] == date(2026, 1, 5)


def test_parse_extracts_dividends_and_splits() -> None:
    _, actions = parse_yf_history(_yf_frame(), "AAON")
    assert list(actions.columns) == list(ACTIONS_SCHEMA.keys())
    divs = actions.filter(actions["kind"] == "dividend")
    splits = actions.filter(actions["kind"] == "split")
    assert divs.height == 1
    assert divs["date"].to_list() == [date(2026, 1, 6)]
    assert divs["value"].to_list() == [0.25]
    assert splits.height == 1
    assert splits["date"].to_list() == [date(2026, 1, 8)]
    assert splits["value"].to_list() == [2.0]


def test_parse_without_action_columns_yields_empty_actions() -> None:
    prices, actions = parse_yf_history(_yf_frame(with_actions=False), "AAON")
    assert prices.height == 4
    assert actions.height == 0


def test_parse_empty_frame() -> None:
    prices, actions = parse_yf_history(pd.DataFrame(), "AAON")
    assert prices.height == 0
    assert actions.height == 0


def test_corporate_actions_table_pk_dedupes(tmp_db: Path) -> None:
    """store.init_db creates corporate_actions; the PK makes re-ingest a no-op."""
    _, actions = parse_yf_history(_yf_frame(), "AAON")
    con = duckdb.connect(str(tmp_db))
    try:
        for _ in range(2):
            con.register("incoming_actions", actions)
            con.execute("""
                INSERT INTO corporate_actions (ticker, date, kind, value, source)
                SELECT ticker, date, kind, value, source FROM incoming_actions
                ON CONFLICT DO NOTHING;
            """)
        assert con.execute("SELECT count(*) FROM corporate_actions").fetchone()[0] == 2
    finally:
        con.close()
