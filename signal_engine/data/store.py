"""DuckDB bitemporal store + read API.

Schema decisions (see turn 5/7 design notes):

  * EDGAR XBRL is naturally long-form (concept-keyed). We store one row per
    (cik, concept, unit, period_end, accession). Restatements (amended forms)
    arrive on a later `filed` date with a new accession - never overwrite.

  * Prices (Stooq + yfinance fallback) are wide per (ticker, date, source).
    `source` is in the PK so we keep both feeds for cross-checks.

  * Corporate actions (dividends / splits) are stored raw per
    (ticker, date, kind, source) so total-return series are built explicitly
    from unadjusted closes + cash dividends, never trusted from a source's
    pre-adjusted close.

  * IJR holdings are snapshotted nightly: PK (snapshot_date, ticker).

  * EIA / FRED series are bitemporal because both publishers revise:
    PK (series_id, period, knowledge_date).

  * FINRA short interest: PK (ticker, settlement_date, knowledge_date) where
    knowledge_date is FINRA's publication date.

The PIT read API (`as_of_facts`, `as_of_series`, etc.) filters
`knowledge_date <= as_of` and resolves ties with `ROW_NUMBER() ... ORDER BY
knowledge_date DESC, ingest_ts DESC`. Factor code MUST go through these
helpers - never SELECT raw from the underlying tables. The leakage test
enforces this contract.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import duckdb
import polars as pl

# DDL is one string per logical table so we can call CREATE TABLE IF NOT EXISTS
# idempotently from any process. Keep the column order stable - downstream
# polars schemas mirror this.

DDL: dict[str, str] = {
    "edgar_facts": """
        CREATE TABLE IF NOT EXISTS edgar_facts (
            cik           BIGINT     NOT NULL,
            taxonomy      VARCHAR    NOT NULL,
            concept       VARCHAR    NOT NULL,   -- our canonical concept name (e.g. 'eps_diluted')
            concept_used  VARCHAR    NOT NULL,   -- which XBRL tag actually returned the value
            unit          VARCHAR    NOT NULL,   -- 'USD', 'USD/shares', 'shares', ...
            period_start  DATE,                  -- NULL for instant facts (e.g. shares outstanding)
            period_end    DATE       NOT NULL,
            fy            INTEGER,
            fp            VARCHAR,               -- 'Q1' | 'Q2' | 'Q3' | 'FY'
            form          VARCHAR,               -- '10-K' | '10-Q' | '10-K/A' | '10-Q/A' | '8-K' | ...
            is_amendment  BOOLEAN    NOT NULL DEFAULT FALSE,
            accession     VARCHAR    NOT NULL,
            filed         DATE       NOT NULL,   -- knowledge_date
            value         DOUBLE,
            ingest_ts     TIMESTAMP  NOT NULL DEFAULT now(),
            PRIMARY KEY (cik, concept, unit, period_end, accession)
        );
    """,
    "edgar_submissions": """
        CREATE TABLE IF NOT EXISTS edgar_submissions (
            cik             BIGINT  NOT NULL,
            snapshot_date   DATE    NOT NULL,
            name            VARCHAR,
            tickers         VARCHAR,             -- pipe-joined; one company can have multiple
            exchanges       VARCHAR,
            sic             VARCHAR,
            sic_description VARCHAR,
            fiscal_year_end VARCHAR,
            ingest_ts       TIMESTAMP NOT NULL DEFAULT now(),
            PRIMARY KEY (cik, snapshot_date)
        );
    """,
    "ticker_cik_map": """
        CREATE TABLE IF NOT EXISTS ticker_cik_map (
            snapshot_date DATE    NOT NULL,
            ticker        VARCHAR NOT NULL,
            cik           BIGINT  NOT NULL,
            title         VARCHAR,
            ingest_ts     TIMESTAMP NOT NULL DEFAULT now(),
            PRIMARY KEY (snapshot_date, ticker)
        );
    """,
    "prices": """
        CREATE TABLE IF NOT EXISTS prices (
            ticker      VARCHAR NOT NULL,
            date        DATE    NOT NULL,
            open        DOUBLE,
            high        DOUBLE,
            low         DOUBLE,
            close       DOUBLE,                  -- as-delivered close (may or may not be div-adjusted; see source flag)
            volume      DOUBLE,
            source      VARCHAR NOT NULL,        -- 'stooq' | 'yfinance'
            div_adjusted BOOLEAN,                -- TRUE if the close column already includes dividend reinvestment
            split_adjusted BOOLEAN,
            ingest_ts   TIMESTAMP NOT NULL DEFAULT now(),
            PRIMARY KEY (ticker, date, source)
        );
    """,
    "corporate_actions": """
        CREATE TABLE IF NOT EXISTS corporate_actions (
            ticker      VARCHAR NOT NULL,
            date        DATE    NOT NULL,     -- ex-date as delivered by the source
            kind        VARCHAR NOT NULL,     -- 'dividend' (cash per share) | 'split' (new/old ratio)
            value       DOUBLE  NOT NULL,
            source      VARCHAR NOT NULL,     -- 'yfinance' | 'stooq' | ...
            ingest_ts   TIMESTAMP NOT NULL DEFAULT now(),
            PRIMARY KEY (ticker, date, kind, source)
        );
    """,
    "ijr_holdings": """
        CREATE TABLE IF NOT EXISTS ijr_holdings (
            snapshot_date DATE    NOT NULL,
            ticker        VARCHAR NOT NULL,
            name          VARCHAR,
            weight        DOUBLE,
            shares        DOUBLE,
            market_value  DOUBLE,
            asset_class   VARCHAR,
            sector        VARCHAR,               -- iShares GICS - kept for cross-check vs our SIC mapping
            ingest_ts     TIMESTAMP NOT NULL DEFAULT now(),
            PRIMARY KEY (snapshot_date, ticker)
        );
    """,
    "finra_short_interest": """
        CREATE TABLE IF NOT EXISTS finra_short_interest (
            ticker          VARCHAR NOT NULL,
            settlement_date DATE    NOT NULL,
            knowledge_date  DATE    NOT NULL,    -- FINRA publication date
            short_interest  DOUBLE,
            avg_daily_vol   DOUBLE,
            days_to_cover   DOUBLE,
            market          VARCHAR,
            ingest_ts       TIMESTAMP NOT NULL DEFAULT now(),
            PRIMARY KEY (ticker, settlement_date, knowledge_date)
        );
    """,
    "macro_series": """
        CREATE TABLE IF NOT EXISTS macro_series (
            source         VARCHAR NOT NULL,     -- 'FRED' | 'EIA'
            series_id      VARCHAR NOT NULL,
            period         DATE    NOT NULL,     -- observation period end
            knowledge_date DATE    NOT NULL,     -- vintage / release date
            value          DOUBLE,
            ingest_ts      TIMESTAMP NOT NULL DEFAULT now(),
            PRIMARY KEY (source, series_id, period, knowledge_date)
        );
    """,
}


def init_db(db_path: Path) -> None:
    """Create the DuckDB file (if needed) and apply all DDL idempotently."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(db_path)) as con:
        for sql in DDL.values():
            con.execute(sql)


@contextmanager
def connect(db_path: Path, *, read_only: bool = False) -> Iterator[duckdb.DuckDBPyConnection]:
    """Yield a DuckDB connection. Use a fresh connection per task - DuckDB
    handles concurrency via the file lock; long-lived connections are not
    needed here."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path), read_only=read_only)
    try:
        yield con
    finally:
        con.close()


# ---------- Point-in-time read API ----------
#
# Every factor reads ONLY through these helpers. Direct SELECTs from
# edgar_facts/prices/macro_series in factor code are a leakage risk; the
# leakage test (tests/test_leakage.py) asserts this by introspecting
# imports / usage.

def as_of_facts(
    con: duckdb.DuckDBPyConnection,
    as_of: date,
    *,
    concept: str | None = None,
    cik: int | None = None,
    originals_only: bool = False,
) -> pl.DataFrame:
    """Latest-known fact per (cik, concept, period_end) as of `as_of`.

    Set `originals_only=True` for the PEAD/SUE signal - restatements (forms
    ending in '/A') must NOT retroactively change a surprise that was
    computed at the original announcement date.
    """
    where_parts = ["filed <= ?"]
    params: list[object] = [as_of]
    if concept is not None:
        where_parts.append("concept = ?")
        params.append(concept)
    if cik is not None:
        where_parts.append("cik = ?")
        params.append(cik)
    if originals_only:
        where_parts.append("is_amendment = FALSE")
    where = " AND ".join(where_parts)
    sql = f"""
        SELECT * EXCLUDE _rn FROM (
            SELECT *,
              row_number() OVER (
                PARTITION BY cik, concept, unit, period_end
                ORDER BY filed DESC, ingest_ts DESC
              ) AS _rn
            FROM edgar_facts
            WHERE {where}
        )
        WHERE _rn = 1
    """
    return con.execute(sql, params).pl()


def as_of_prices(
    con: duckdb.DuckDBPyConnection,
    as_of: date,
    *,
    ticker: str | None = None,
) -> pl.DataFrame:
    """All price rows with trade `date <= as_of`. Prefers stooq; falls back
    to yfinance on misses for a given (ticker, date)."""
    where_parts = ["date <= ?"]
    params: list[object] = [as_of]
    if ticker is not None:
        where_parts.append("ticker = ?")
        params.append(ticker)
    where = " AND ".join(where_parts)
    sql = f"""
        SELECT * EXCLUDE _rn FROM (
            SELECT *,
              row_number() OVER (
                PARTITION BY ticker, date
                ORDER BY CASE source WHEN 'stooq' THEN 0 WHEN 'yfinance' THEN 1 ELSE 2 END
              ) AS _rn
            FROM prices
            WHERE {where}
        )
        WHERE _rn = 1
    """
    return con.execute(sql, params).pl()


def as_of_corporate_actions(
    con: duckdb.DuckDBPyConnection,
    as_of: date,
    *,
    ticker: str | None = None,
    kind: str | None = None,
) -> pl.DataFrame:
    """All corporate-action rows with `date <= as_of`.

    A dividend/split is public knowledge on its ex-date, so the trade date
    is the knowledge date - no separate vintage axis needed. Factor code
    reads dividends ONLY through this helper (leakage contract)."""
    where_parts = ["date <= ?"]
    params: list[object] = [as_of]
    if ticker is not None:
        where_parts.append("ticker = ?")
        params.append(ticker)
    if kind is not None:
        where_parts.append("kind = ?")
        params.append(kind)
    where = " AND ".join(where_parts)
    sql = f"SELECT ticker, date, kind, value, source FROM corporate_actions WHERE {where}"
    return con.execute(sql, params).pl()


def as_of_macro(
    con: duckdb.DuckDBPyConnection,
    as_of: date,
    *,
    source: str | None = None,
    series_id: str | None = None,
) -> pl.DataFrame:
    """Latest vintage per (source, series_id, period) as of `as_of`."""
    where_parts = ["knowledge_date <= ?"]
    params: list[object] = [as_of]
    if source is not None:
        where_parts.append("source = ?")
        params.append(source)
    if series_id is not None:
        where_parts.append("series_id = ?")
        params.append(series_id)
    where = " AND ".join(where_parts)
    sql = f"""
        SELECT * EXCLUDE _rn FROM (
            SELECT *,
              row_number() OVER (
                PARTITION BY source, series_id, period
                ORDER BY knowledge_date DESC, ingest_ts DESC
              ) AS _rn
            FROM macro_series
            WHERE {where}
        )
        WHERE _rn = 1
    """
    return con.execute(sql, params).pl()
