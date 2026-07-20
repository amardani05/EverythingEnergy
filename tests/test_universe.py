"""IJR holdings parser tests - locks the iShares CSV format we ingest.

If iShares changes the columns/header again, these tests fail loudly
instead of silently dropping holdings into a malformed parquet.
"""

from __future__ import annotations

from datetime import date
from textwrap import dedent

import pytest

from signal_engine.data.universe import (
    parse_as_of_date,
    parse_holdings_csv,
)

CSV_2026_FORMAT = dedent("""\
    iShares Core S&P Small-Cap ETF
    Fund Holdings as of,"Jun 12, 2026"
    Inception Date,"May 22, 2000"
    Shares Outstanding,"747,500,000.00"
    Stock,"-"
    Bond,"-"
    Cash,"-"
    Other,"-"

    Ticker,Name,Type,Sector,Asset Class,Market Value,Notional Value,Quantity,Price,Location,Exchange,Currency,FX Rate,Market Currency,Accrual Date,Market Weight,Notional Weight
    "XTSLA","BLK CSH FND TREASURY SL AGENCY","STIF","Cash and/or Derivatives","Money Market","1,641,291,282.84","157,784,893.45","1,641,291,283.00","1.00","United States","-","USD","1.00","USD","-","1.53","0.15"
    "SMTC","SEMTECH CORP","EQUITY","Information Technology","Equity","932,489,884.35","932,489,884.35","5,593,485.00","166.71","United States","NASDAQ","USD","1.00","USD","-","0.87","0.87"
    "AAON","AAON INC","EQUITY","Industrials","Equity","300,000,000.00","300,000,000.00","2,000,000.00","150.00","United States","NASDAQ","USD","1.00","USD","-","0.28","0.28"
""")


def test_parse_as_of_date_quoted_form() -> None:
    """The 2026 format quotes the date with an internal comma."""
    assert parse_as_of_date('Fund Holdings as of,"Jun 12, 2026"') == date(2026, 6, 12)


def test_parse_as_of_date_unquoted_form() -> None:
    """Older snapshots used `Fund Holdings as of, Jun 12, 2026` (no quotes)."""
    assert parse_as_of_date("Fund Holdings as of, Jun 12, 2026") == date(2026, 6, 12)


def test_parse_holdings_csv_extracts_equity_rows_only() -> None:
    as_of, df = parse_holdings_csv(CSV_2026_FORMAT)
    assert as_of == date(2026, 6, 12)
    # XTSLA cash sweep must be filtered out - only equity asset class survives.
    assert df["ticker"].to_list() == ["SMTC", "AAON"]
    assert "XTSLA" not in df["ticker"].to_list()


def test_parse_holdings_csv_maps_2026_column_names() -> None:
    """Quantity -> shares, Market Weight -> weight."""
    _, df = parse_holdings_csv(CSV_2026_FORMAT)
    smtc = df.filter(df["ticker"] == "SMTC").to_dicts()[0]
    assert smtc["shares"] == pytest.approx(5_593_485.0)
    assert smtc["weight"] == pytest.approx(0.87)
    assert smtc["market_value"] == pytest.approx(932_489_884.35)
    assert smtc["sector"] == "Information Technology"


def test_parse_holdings_csv_rejects_html_response() -> None:
    """Akamai sometimes returns the product page HTML with a fake CSV
    Content-Type. Parser must fail loudly so a downstream ingest doesn't
    silently insert zero rows."""
    html = "<!DOCTYPE html><html><body>blocked</body></html>"
    with pytest.raises(ValueError, match="HTML instead of CSV"):
        parse_holdings_csv(html)
