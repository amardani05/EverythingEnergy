"""Scoring layer tests - sign conventions, composite algebra, family floor,
and the PIT canary THROUGH the composite (a fact filed after as_of must not
move any score).

Fixture world: six names in one sector (below MIN_SECTOR_N, so global z - deterministic expectations), ~440 weekday prices each, annual fundamentals
for five, and a long Q1 EPS history + fresh earnings shock for one.
"""

from __future__ import annotations

from datetime import date, timedelta

import duckdb
import polars as pl
import pytest
from polars.testing import assert_frame_equal

from signal_engine.config import Config
from signal_engine.scoring import build_composite, family_correlation
from signal_engine.scoring.composite import winsorize_series, zscore_expr
from tests.factor_fixtures import insert_annual_bundle, insert_quarterly_eps

AS_OF = date(2026, 7, 17)
PRICE_START = date(2024, 11, 1)
# The composite's selection universe is now the energy taxonomy; tests inject
# their own ticker set via build_composite(..., universe=TEST_UNIVERSE).
TEST_UNIVERSE = ["MOMHI", "MOMLO", "CHEAP", "EXPNS", "PEADP", "NOFUN"]


def _test_cfg() -> Config:
    return Config(raw={
        "universe": {"membership_mode": "energy_taxonomy", "min_price_history_days": 200},
        "scoring": {"winsorize_pct": [0.01, 0.99], "emit_sector_relative": True},
        "signals": {"selection": [
            {"name": "value", "enabled": True, "weight": 1.0, "components": ["ev_ebitda"]},
            {"name": "quality", "enabled": False, "weight": 1.0},
            {"name": "momentum", "enabled": True, "weight": 1.0},
            {"name": "pead", "enabled": True, "weight": 1.0},
        ]},
    })


def _weekdays(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _seed_prices(con: duckdb.DuckDBPyConnection, ticker: str,
                 start_close: float, end_close: float) -> None:
    days = _weekdays(PRICE_START, AS_OF)
    n = len(days)
    closes = [start_close + (end_close - start_close) * i / (n - 1) for i in range(n)]
    frame = pl.DataFrame({
        "ticker": [ticker] * n, "date": days,
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": [1000.0] * n, "source": ["yfinance"] * n,
        "div_adjusted": [False] * n, "split_adjusted": [True] * n,
    })
    con.register("seed_px", frame)
    con.execute("INSERT INTO prices SELECT *, now() FROM seed_px ON CONFLICT DO NOTHING")


def _seed_universe(con: duckdb.DuckDBPyConnection, tickers: dict[str, int]) -> None:
    for ticker, cik in tickers.items():
        con.execute(
            "INSERT INTO ticker_cik_map (snapshot_date, ticker, cik) VALUES (?, ?, ?)",
            [date(2026, 7, 1), ticker, cik],
        )
        con.execute(
            "INSERT INTO edgar_submissions (cik, snapshot_date, sic) VALUES (?, ?, '1311')",
            [cik, date(2026, 7, 1)],
        )


def _seed_fundamentals(con: duckdb.DuckDBPyConnection, cik: int, *, ebitda: float) -> None:
    """Annual bundle giving EV/EBITDA = mcap/ebitda (no debt, no cash).
    mcap = close(~100-200) * 1_000_000 shares."""
    insert_annual_bundle(
        con, cik=cik, fy=2025, period_end=date(2025, 12, 31), filed=date(2026, 2, 15),
        values={
            "revenue": 1e8, "net_income": ebitda, "interest_expense": 0.0,
            "tax_expense": 0.0, "depreciation_amortization": 0.0,
            "operating_cash_flow": ebitda, "capex": 0.0,
            "shares_outstanding": 1_000_000.0,
        },
        accession_prefix=f"{cik}-",
    )


@pytest.fixture
def seeded(tmp_con: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    tickers = {"MOMHI": 1, "MOMLO": 2, "CHEAP": 3, "EXPNS": 4, "PEADP": 5, "NOFUN": 6}
    _seed_universe(tmp_con, tickers)
    _seed_prices(tmp_con, "MOMHI", 100.0, 200.0)   # strong up
    _seed_prices(tmp_con, "MOMLO", 200.0, 100.0)   # strong down
    for t in ("CHEAP", "EXPNS", "PEADP"):
        _seed_prices(tmp_con, t, 100.0, 100.0)     # flat
    _seed_prices(tmp_con, "NOFUN", 100.0, 150.0)   # up, but no fundamentals

    _seed_fundamentals(tmp_con, 1, ebitda=1e7)     # EV/EBITDA ~ 15-20
    _seed_fundamentals(tmp_con, 2, ebitda=1e7)
    _seed_fundamentals(tmp_con, 3, ebitda=5e7)     # ~2  (cheap)
    _seed_fundamentals(tmp_con, 4, ebitda=2e6)     # ~50 (expensive)
    _seed_fundamentals(tmp_con, 5, ebitda=1e7)

    # PEADP: 8 years of Q1 EPS with variance, then a POSITIVE shock filed
    # inside the 92-day hold window before AS_OF. CHEAP gets the same
    # history with a NEGATIVE surprise - a one-name SUE cross-section would
    # z-score to a flat 0.0, so ordering needs at least two events.
    eps_by_fy = {2018: 1.00, 2019: 1.05, 2020: 1.10, 2021: 1.15,
                 2022: 1.10, 2023: 1.20, 2024: 1.25, 2025: 1.30}
    for cik in (5, 3):
        for fy, eps in eps_by_fy.items():
            pe = date(fy, 3, 31)
            insert_quarterly_eps(tmp_con, cik=cik, period_end=pe, fy=fy, fp="Q1", eps=eps,
                                 filed=pe + timedelta(days=40), accession=f"{cik}-{fy}-Q1")
    insert_quarterly_eps(tmp_con, cik=5, period_end=date(2026, 3, 31), fy=2026,
                         fp="Q1", eps=3.00, filed=date(2026, 6, 20), accession="5-2026-Q1")
    insert_quarterly_eps(tmp_con, cik=3, period_end=date(2026, 3, 31), fy=2026,
                         fp="Q1", eps=0.80, filed=date(2026, 6, 20), accession="3-2026-Q1")
    return tmp_con


def test_composite_orders_momentum_and_value(seeded: duckdb.DuckDBPyConnection) -> None:
    df = build_composite(seeded, AS_OF, _test_cfg(), universe=TEST_UNIVERSE)
    by = {r["ticker"]: r for r in df.iter_rows(named=True)}

    # Momentum ordering flows into the composite (same value profile).
    assert by["MOMHI"]["composite"] > by["MOMLO"]["composite"]
    # Lower EV/EBITDA -> higher value score (sign convention).
    assert by["CHEAP"]["score_value"] > by["EXPNS"]["score_value"]
    # PEAD fires only for names with a recent earnings event, ordered by
    # surprise: positive shock above negative shock.
    assert by["PEADP"]["score_pead"] > 0 > by["CHEAP"]["score_pead"]
    for t in ("MOMHI", "MOMLO", "EXPNS"):
        assert by[t]["score_pead"] is None


def test_min_families_floor(seeded: duckdb.DuckDBPyConnection) -> None:
    """A momentum-only name (no fundamentals at all) must not be ranked."""
    df = build_composite(seeded, AS_OF, _test_cfg(), universe=TEST_UNIVERSE)
    row = df.filter(pl.col("ticker") == "NOFUN").row(0, named=True)
    assert row["n_families"] == 1
    assert row["composite"] is None
    assert row["rank"] is None


def test_composite_pit_canary(seeded: duckdb.DuckDBPyConnection) -> None:
    """THE contract: a fundamental filed AFTER as_of must not move a single
    number in the composite. Extends the leakage canary through scoring."""
    before = build_composite(seeded, AS_OF, _test_cfg(), universe=TEST_UNIVERSE)
    # Absurd fundamentals for EXPNS filed six weeks after as_of.
    insert_annual_bundle(
        seeded, cik=4, fy=2026, period_end=date(2026, 6, 30), filed=date(2026, 8, 30),
        values={"revenue": 9e9, "net_income": 5e9, "interest_expense": 0.0,
                "tax_expense": 0.0, "depreciation_amortization": 0.0,
                "operating_cash_flow": 5e9, "capex": 0.0,
                "shares_outstanding": 1_000_000.0},
        accession_prefix="future-",
    )
    after = build_composite(seeded, AS_OF, _test_cfg(), universe=TEST_UNIVERSE)
    assert_frame_equal(before, after)


def test_family_correlation_shape(seeded: duckdb.DuckDBPyConnection) -> None:
    df = build_composite(seeded, AS_OF, _test_cfg(), universe=TEST_UNIVERSE)
    corr = family_correlation(df)
    fams = corr["family"].to_list()
    assert set(fams) == {"value", "momentum", "pead"}
    for f in fams:
        assert corr.filter(pl.col("family") == f)[f].to_list() == [1.0]


# ---------- primitives ----------

def test_winsorize_clips_extremes_only() -> None:
    s = pl.Series("x", [float(i) for i in range(1, 100)] + [10_000.0])
    w = winsorize_series(s, 0.01, 0.99)
    assert float(w.max()) < 10_000.0        # outlier clipped
    assert float(w[50]) == s[50]            # middle untouched
    n = pl.Series("x", [1.0, None, 3.0, 2.0, 5.0])
    assert winsorize_series(n, 0.01, 0.99).null_count() == 1


def test_zscore_within_group() -> None:
    df = pl.DataFrame({
        "g": ["a", "a", "a", "b", "b", "b"],
        "x": [1.0, 2.0, 3.0, 10.0, 20.0, 30.0],
    }).with_columns([
        zscore_expr("x").alias("zg"),
        zscore_expr("x", over="g").alias("zs"),
    ])
    # Within each group the middle value must be exactly z=0.
    assert df["zs"].to_list()[1] == pytest.approx(0.0)
    assert df["zs"].to_list()[4] == pytest.approx(0.0)
    # Flat group -> 0.0, not division blow-up; null passes through.
    flat = pl.DataFrame({"x": [5.0, 5.0, None]}).with_columns(zscore_expr("x").alias("z"))
    assert flat["z"].to_list() == [0.0, 0.0, None]


# ---------- basket-relative neutralization ----------

def test_basket_shock_is_neutralized(tmp_con: duckdb.DuckDBPyConnection) -> None:
    """THE acceptance test: lifting one whole basket's raw momentum by a
    constant must NOT hand that basket the top of the book. Under the old
    sector-z (which did nothing in an all-energy universe) it would have.

    Two real baskets, each large enough to clear MIN_SECTOR_N so both get
    their own basket-level z: upstream_oil_eandp and ofs_onshore.
    """
    from signal_engine.atlas.clusters import ticker_to_basket

    t2b = ticker_to_basket()
    oil = [t for t, b in t2b.items() if b == "upstream_oil_eandp"][:12]
    ofs = [t for t, b in t2b.items() if b == "ofs_onshore"][:12]
    assert len(oil) >= 10 and len(ofs) >= 10

    # The oil basket rallies hard; OFS is flat. EBITDA is scaled with the
    # ending price so EV/EBITDA is IDENTICAL across both baskets: without
    # that, the rally inflates oil's market cap, worsens its value score, and
    # the two effects cancel, letting this test pass even with the bug.
    # Momentum must be the only thing that differs.
    for i, t in enumerate(oil):
        _seed_prices(tmp_con, t, 100.0, 300.0 + i)      # huge basket-wide move
    for i, t in enumerate(ofs):
        _seed_prices(tmp_con, t, 100.0, 100.0 + i)      # flat basket
    universe = oil + ofs
    ciks = {t: 100 + i for i, t in enumerate(universe)}
    _seed_universe(tmp_con, ciks)
    for i, t in enumerate(oil):
        _seed_fundamentals(tmp_con, ciks[t], ebitda=1e7 * (300.0 + i) / 100.0)
    for i, t in enumerate(ofs):
        _seed_fundamentals(tmp_con, ciks[t], ebitda=1e7 * (100.0 + i) / 100.0)

    cfg = Config(raw={
        "universe": {"membership_mode": "energy_taxonomy", "min_price_history_days": 200},
        "scoring": {"winsorize_pct": [0.01, 0.99], "emit_sector_relative": True},
        "signals": {"selection": [
            {"name": "value", "enabled": True, "weight": 1.0, "components": ["ev_ebitda"]},
            {"name": "momentum", "enabled": True, "weight": 1.0},
            {"name": "pead", "enabled": False, "weight": 1.0},
        ]},
    })
    df = build_composite(tmp_con, AS_OF, cfg, universe=universe)
    ranked = df.filter(pl.col("composite").is_not_null())
    assert ranked.height >= 20

    # Both baskets got their own basket-level z (they clear MIN_SECTOR_N).
    assert set(ranked["neutralization_level"].unique().to_list()) == {"basket"}

    # The decisive check: the rallying basket must NOT sweep the top half.
    # Under global-z neutralization it would take essentially every top slot.
    top_half = ranked.head(ranked.height // 2)
    oil_share = top_half.filter(pl.col("basket") == "upstream_oil_eandp").height / top_half.height
    assert 0.3 <= oil_share <= 0.7, (
        f"basket shock leaked into ranks: oil holds {oil_share:.0%} of the top half"
    )

    # And the basket means of the momentum z must be ~equal (that is what
    # neutralization means), even though raw momentum differs enormously.
    means = (ranked.group_by("basket")
             .agg(pl.col("z_momentum").mean().alias("m"))
             .sort("basket"))
    spread = float(means["m"].max() - means["m"].min())
    assert abs(spread) < 0.15, f"basket z means differ by {spread:.3f}"


def test_thin_basket_falls_back_to_super_basket(tmp_con: duckdb.DuckDBPyConnection) -> None:
    """A basket below MIN_SECTOR_N must not mint its own z; it falls back to
    the super-basket (commodity-chain group), and only then to global."""
    from signal_engine.atlas.clusters import ticker_to_basket

    t2b = ticker_to_basket()
    # coal (3 names) and uranium (2) are both 'fuel_minerals': 5 total, still
    # under MIN_SECTOR_N, so these must land on 'global'.
    tiny = [t for t, b in t2b.items() if b in ("coal", "uranium_nuclear_fuel")]
    # ofs_onshore is large enough to keep its own basket z.
    big = [t for t, b in t2b.items() if b == "ofs_onshore"][:12]
    universe = tiny + big
    for i, t in enumerate(universe):
        _seed_prices(tmp_con, t, 100.0, 120.0 + i)
    ciks = {t: 200 + i for i, t in enumerate(universe)}
    _seed_universe(tmp_con, ciks)
    for cik in ciks.values():
        _seed_fundamentals(tmp_con, cik, ebitda=1e7)

    cfg = Config(raw={
        "universe": {"membership_mode": "energy_taxonomy", "min_price_history_days": 200},
        "scoring": {"winsorize_pct": [0.01, 0.99], "emit_sector_relative": True},
        "signals": {"selection": [
            {"name": "value", "enabled": True, "weight": 1.0, "components": ["ev_ebitda"]},
            {"name": "momentum", "enabled": True, "weight": 1.0},
            {"name": "pead", "enabled": False, "weight": 1.0},
        ]},
    })
    df = build_composite(tmp_con, AS_OF, cfg, universe=universe)
    lvl = {r["ticker"]: r["neutralization_level"] for r in df.iter_rows(named=True)}
    for t in tiny:
        assert lvl[t] == "global", f"{t} should fall through to global, got {lvl[t]}"
    for t in big:
        assert lvl[t] == "basket", f"{t} should keep its basket z, got {lvl[t]}"
