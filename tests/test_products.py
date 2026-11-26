from datetime import date, timedelta

import pytest

from signal_engine.macro.products import PRODUCTS, UTILIZATION, build_product, build_products, summary


def _weeks(n, end=date(2026, 11, 20)):
    return [(end - timedelta(weeks=i)).isoformat() for i in range(n)][::-1]


def _raw(n=400, stock=100_000.0, supplied=4_000.0):
    ds = _weeks(n)
    raw = {"stocks": {}, "production": {}, "imports": {}, "exports": {}, "supplied": {}}
    s = stock
    for d in ds:
        raw["production"][d], raw["imports"][d], raw["exports"][d], raw["supplied"][d] = 5000.0, 100.0, 1000.0, supplied
        s += (5000 + 100 - 1000 - supplied) * 7  # build of 100 kb/d
        raw["stocks"][d] = s
    return raw


def test_identity_and_cover():
    p = build_product(_raw(), start="2020-01-01")
    assert p["build"][-1] == pytest.approx(0.1)
    assert p["modelled"][-1] == pytest.approx(0.1)
    assert p["adjustment"][-1] == pytest.approx(0.0, abs=1e-9)
    assert p["cover"][-1] == pytest.approx(p["stocks"][-1] / 4.0, abs=1e-3)


def test_norms_exclude_current_year():
    p = build_product(_raw(), start="2020-01-01")
    # stocks rise every week, so the same week in prior years is always lower
    assert p["stocks_max"][-1] < p["stocks"][-1]
    s = summary(p)
    assert s["stocks_in_range"] > 1 and s["stocks_vs_5y"] > 0


def test_build_products_all():
    one = _raw()
    raw = {sid: one[f] for ids in PRODUCTS.values() for f, sid in ids.items()}
    raw[UTILIZATION] = {d: 90.0 for d in one["stocks"]}
    out = build_products(raw)
    assert set(out["summary"]) == set(PRODUCTS)
    assert out["utilization"]["value"][-1] == 90.0
    assert out["products"]["jet"]["dates"][0] >= "2020-01-01"
