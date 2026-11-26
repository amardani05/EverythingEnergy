import math
from datetime import date as D
from datetime import timedelta

import polars as pl
import pytest

from signal_engine.data import curve_view as V

ASOF = D(2026, 11, 24)


def frame(root, prices, vols=None, days=45, last=ASOF, drift=0.0):
    out = []
    for k, (dl, p) in enumerate(prices.items()):
        for i in range(days):
            out.append({"symbol": f"{root}{dl}", "root": root, "delivery": dl, "date": last - timedelta(days=i),
                        "close": p - drift * i, "volume": (vols or [100.0] * 20)[k]})
    return pl.DataFrame(out)


P = {"2027-01": 80.0, "2027-02": 79.0, "2027-03": 78.0, "2028-01": 70.0}


def test_spreads_roll_and_fixed_contract_comparison():
    r = V.root_view(frame("CL", P, drift=0.1), "CL")
    c = r["curve"][0]
    assert r["asof"] == "2026-11-24" and r["front"] == 0
    assert c["spread"] == pytest.approx(1) and c["roll"] == pytest.approx(math.log(80 / 79) * 12)
    assert c["w1"] == pytest.approx(79.3) and c["m1"] == pytest.approx(77) and c["y1"] is None
    assert r["year"]["to"] == "2028-01" and r["year"]["spread"] == pytest.approx(10)
    assert r["curve"][-1]["spread"] is None


def test_roll_uses_actual_month_gap_and_handles_negative():
    assert V.roll_yield(80, 70, 10) == pytest.approx(math.log(80 / 70) * 1.2)
    assert V.roll_yield(-37, 20, 1) is None


def test_metals_front_is_most_active_and_next_skips_illiquid():
    r = V.root_view(frame("HG", P, vols=[1.0, 500.0, 2.0, 300.0]), "HG")
    assert r["front"] == 1 and r["next"]["to"] == "2028-01"


def test_partial_session_does_not_define_the_curve():
    x = pl.concat([frame("CL", P), frame("CL", {"2027-01": 99.0}, days=1, last=D(2026, 11, 25))])
    r = V.root_view(x, "CL")
    assert r["asof"] == "2026-11-24" and len(r["curve"]) == 4
    assert r["history"]["dates"][-1] == "2026-11-24"


def test_cracks_by_delivery_and_units():
    x = pl.concat([frame("CL", P), frame("RB", {"2027-01": 2.5, "2027-02": 2.4}), frame("HO", {"2027-01": 3.0})])
    c = V.build(x)["cracks"]["rows"]
    assert [r["delivery"] for r in c] == ["2027-01"]
    assert c[0]["rbob"] == pytest.approx(25) and c[0]["ulsd"] == pytest.approx(46)
    assert c[0]["c321"] == pytest.approx((2 * 25 + 46) / 3)


def test_cracks_refuse_mismatched_sessions():
    x = pl.concat([frame("CL", P), frame("RB", {"2027-01": 2.5}, last=D(2026, 11, 23)), frame("HO", {"2027-01": 3.0})])
    assert V.build(x)["cracks"] is None


def test_midwest_premium_skips_the_averaging_month():
    r = V.root_view(frame("AUP", {"2026-11": 1.06, "2026-12": 0.94, "2027-01": 0.90}), "AUP")
    assert r["curve"][r["front"]]["delivery"] == "2026-12" and r["next"]["to"] == "2027-01"
