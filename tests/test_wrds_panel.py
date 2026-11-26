from datetime import date, timedelta

import polars as pl
import pytest

from signal_engine.data import wrds_panel as w

D = date


def daily(rows):
    base = dict(permco=1, ticker="X", siccd=1311, sharetype="NS", securitytype="EQTY",
                securitysubtype="COM", primaryexch="N", conditionaltype="RW",
                tradingstatusflg="A", delist="N", prc=10.0, cap=1000.0, ret=0.0, vol=1.0)
    return pl.DataFrame([{**base, **r} for r in rows])


def test_groups_first_match():
    df = pl.DataFrame({"siccd": [1311, 1381, 1221, 4911, 4922, 4953, 7372]}).with_columns(w.group_expr())
    assert df["group"].to_list() == ["upstream", "oilfield_services", "coal", "power", "pipelines", None, None]


def test_delisting_return_reaches_last_holder():
    d = daily([
        dict(permno=1, date=D(2020, 1, 31), ret=0.1),
        dict(permno=1, date=D(2020, 4, 15), ret=-0.9, delist="Y", primaryexch="X", siccd=0),
        dict(permno=2, permco=2, date=D(2020, 1, 31)), dict(permno=2, permco=2, date=D(2020, 2, 28)),
        dict(permno=2, permco=2, date=D(2020, 4, 15)),
    ])
    p = w.add_momentum_and_forward(w.month_end_members(d), w.monthly_returns(d))
    r = p.filter((pl.col("permno") == 1) & (pl.col("date") == D(2020, 1, 31))).row(0, named=True)
    assert r["fwd_ret_1m"] == pytest.approx(-0.9) and r["fwd_delisted"]


def test_missing_delisting_return_is_null_and_flagged():
    d = daily([dict(permno=1, date=D(2020, 1, 31)),
               dict(permno=1, date=D(2020, 2, 3), ret=None, delist="Y")])
    r = w.monthly_returns(d).filter(pl.col("month") == D(2020, 2, 1)).row(0, named=True)
    assert r["ret_1m"] is None and r["missing_delist_ret"]


def test_exchange_exit_without_delisting_is_flagged():
    d = daily([dict(permno=1, date=D(2020, 1, 31)), dict(permno=1, date=D(2020, 2, 3), ret=-0.5),
               dict(permno=1, date=D(2020, 2, 4), ret=None, primaryexch="X", conditionaltype="NT")])
    p = w.add_momentum_and_forward(w.month_end_members(d), w.monthly_returns(d))
    r = p.filter(pl.col("date") == D(2020, 1, 31)).row(0, named=True)
    assert r["fwd_suspended"] and r["fwd_ret_1m"] == pytest.approx(-0.5)


def test_membership_uses_sic_of_the_day():
    d = daily([dict(permno=1, date=D(2020, 1, 31), siccd=1311),
               dict(permno=1, date=D(2020, 2, 28), siccd=7372)])
    assert w.month_end_members(d)["date"].to_list() == [D(2020, 1, 31)]


def test_momentum_needs_all_months_and_skips_latest():
    ms = [D(2020, m, 1) for m in range(1, 13)]
    rets = pl.DataFrame({"permno": [1] * 12, "month": ms, "ret_1m": [0.01] * 11 + [5.0],
                         "delisted": [False] * 12, "suspended": [False] * 12, "missing_delist_ret": [False] * 12})
    m = pl.DataFrame({"permno": [1], "month": [D(2020, 12, 1)]})
    assert w.add_momentum_and_forward(m, rets)["mom_12_1"][0] == pytest.approx(1.01**11 - 1)
    gap = rets.filter(pl.col("month") != D(2020, 5, 1))
    assert w.add_momentum_and_forward(m, gap)["mom_12_1"][0] is None


def fund(n=16, last_eps=None):
    q = [D(2016 + i // 4, *[(3, 31), (6, 30), (9, 30), (12, 31)][i % 4]) for i in range(n)]
    return pl.DataFrame({
        "gvkey": ["1"] * n, "datadate": q, "fyearq": [d.year for d in q],
        "fqtr": [i % 4 + 1 for i in range(n)], "curcdq": ["USD"] * n,
        "rdq": [d + timedelta(days=30) for d in q],
        "epspxq": [1 + (i % 3) * 0.1 + i * 0.02 for i in range(n - 1)] + [last_eps or 9.0],
        "ajexq": [1.0] * n, "oibdpq": [25.0] * n, "dlttq": [100.0] * n, "dlcq": [0.0] * n,
        "cheq": [50.0] * n, "pstkq": [0.0] * n, "mibq": [0.0] * n})


def test_fundamentals_not_visible_before_available():
    f = w.prepare_fundamentals(fund())
    p = pl.DataFrame({"permno": [1, 1, 1], "gvkey": ["1"] * 3, "company_mcap_mm": [950.0] * 3,
                      "sharetype": ["NS", "NS", "AD"],
                      "date": [D(2020, 1, 30), D(2020, 1, 31), D(2020, 3, 31)]})
    o = w.attach_fundamentals(p, f).sort("date")
    q3, q4 = D(2019, 9, 30), D(2019, 12, 31)
    assert o["eps_period"].to_list() == [q3, q4, q4]       # rdq 2020-01-30 is not usable same day
    assert o["stmt_period"].to_list() == [q3, q3, q4]      # fiscal Q4 waits 90 days
    assert o["ebitda_ev"][0] == pytest.approx(100 / 1000)
    assert o["ebitda_ev"][2] is None                       # ADR market cap is incomplete


def test_ttm_null_across_missing_quarter():
    f = w.prepare_fundamentals(fund().filter(pl.col("datadate") != D(2019, 6, 30)))
    assert f.filter(pl.col("datadate") == D(2019, 12, 31))["ebitda_ttm"][0] is None


def test_expired_link_not_used():
    m = pl.DataFrame({"permno": [1, 1], "date": [D(2010, 1, 29), D(2020, 1, 31)]})
    ln = pl.DataFrame({"gvkey": ["a"], "permno": [1], "linktype": ["LC"], "linkprim": ["P"],
                       "linkdt": [D(2005, 1, 1)], "linkenddt": [D(2015, 1, 1)]})
    assert w.attach_links(m, ln).sort("date")["gvkey"].to_list() == ["a", None]
