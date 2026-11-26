from datetime import date as D
from datetime import timedelta

import polars as pl
import pytest

from signal_engine.data import wrds_signals as S

DAYS = [d for i in range(120) if (d := D(2020, 1, 1) + timedelta(days=i)).weekday() < 5]
GRID = [D(2020, 1, 31), D(2020, 2, 28), D(2020, 3, 31)]


def rows(p, days, **k):
    return [{"permno": p, "date": d, "ret": 0.0, "delist": "N", "conditionaltype": "RW", **k} for d in days]


def build(extra, stress=False):
    return S.total_return_index(pl.DataFrame(rows(9, DAYS) + extra), GRID, stress=stress)


def ret(px, t="1"):
    x = px.filter(pl.col("ticker") == t).sort("date")["close"].to_list()
    return x[1] / x[0] - 1


CUT = [d for d in DAYS if d <= D(2020, 2, 12)]


def test_entry_is_next_day_and_calendar_complete():
    px = build([])
    assert px.filter(pl.col("ticker") == "9")["date"].to_list() == [D(2020, 2, 3), D(2020, 3, 2), D(2020, 4, 1)]
    assert px.filter(pl.col("ticker") == S.CALENDAR_TICKER).height == len(DAYS)


@pytest.mark.parametrize("when", [D(2020, 2, 13), D(2020, 4, 20)])
def test_delisting_loss_is_held_even_when_dated_late(when):
    px = build(rows(1, CUT) + rows(1, [when], ret=-0.8, delist="Y", conditionaltype="N/A"))
    assert ret(px) == pytest.approx(-0.8)


def test_missing_delist_base_and_stress():
    e = rows(1, CUT) + rows(1, [D(2020, 2, 13)], ret=None, delist="Y", conditionaltype="N/A")
    assert ret(build(e)) == 0
    assert ret(build(e, True)) == pytest.approx(-1, abs=1e-5)


def test_suspension_base_and_stress():
    e = rows(1, CUT) + rows(1, [d for d in DAYS if d > CUT[-1]], ret=None, conditionaltype="NT")
    assert ret(build(e)) == 0
    assert ret(build(e, True)) == pytest.approx(-1, abs=1e-5)


def test_floor_universe_and_stale_sue():
    b = dict(group="upstream", siccd=1311, prc=5.0, mom_12_1=0.1, date=D(2020, 1, 31), sue=1.0,
             eps_avail=D(2019, 12, 1))
    p = pl.DataFrame([b, {**b, "prc": 0.99}, {**b, "group": "mining", "siccd": 1040},
                      {**b, "group": "mining", "siccd": 1094}, {**b, "eps_avail": D(2019, 9, 1)}])
    e = S.eligible(p)
    assert e.height == 3 and e["sue"].to_list() == [1.0, 1.0, None]
