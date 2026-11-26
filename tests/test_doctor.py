import json
from datetime import date as D

import pytest

from signal_engine import doctor as X

T = D(2026, 11, 24)


def level(v, limit=3, **k):
    return X.fresh("x", v, T, limit, **k).level


def test_freshness_levels():
    assert level("2026-11-23", weekdays=True) == X.OK
    assert level("2026-05-06", weekdays=True) == X.FAIL
    assert level("2026-05-06", level=X.WARN) == X.WARN
    assert level("2026-11-25") == X.FAIL  # future
    assert level(None) == X.FAIL


def test_thanksgiving_weekend_is_not_stale():
    assert X.fresh("x", "2026-11-25", D(2026, 11, 30), 3, weekdays=True).level == X.OK


def series(n_bad, n=40):
    return {"series": [{"symbol": f"S{i}", "dates": ["2025-12-26" if i < n_bad else "2026-11-23"]} for i in range(n)]}


@pytest.mark.parametrize(("bad", "want"), [(0, None), (1, X.WARN), (5, X.FAIL)])
def test_one_dead_series_warns_many_fail(bad, want):
    got = [c.level for c in X.check_market(series(bad), T) if "stale" in c.name]
    assert got == ([want] if want else [])


def write(root, through="2026-11-23"):
    files = {
        "dashboard_data": {"meta": {"generated_at": "2026-11-24", "prices_through": through, "n_constituents": 205}},
        "signals_latest": {"meta": {"as_of": "2026-11-23", "n_ranked": 150}},
        "oil_balance": {"meta": {"steo_released": "2026-11-10", "us_week_ending": "2026-11-13"},
                        "products": {"summary": {"jet": {"date": "2026-11-13"}}},
                        "gas": {"storage": {"summary": {"l48": {"date": "2026-11-13"}}}}},
        "market_data": series(0),
        "options_data": {"meta": {"as_of": "2026-11-23"}, "underlyings": [{"symbol": "U", "expiries": [1]}] * 20},
        "flows_data": {"meta": {"compiled": "2026-11-10"}}, "commodities": {"meta": {"compiled": "2026-11-12"}},
    }
    for k, v in files.items():
        (root / f"{k}.json").write_text(json.dumps(v))


def fails(root):
    return [c.name for c in X.published(root, T) if c.level == X.FAIL]


def test_published_clean_then_the_may_bug(tmp_path):
    write(tmp_path)
    assert fails(tmp_path) == []
    write(tmp_path, through="2026-05-06")  # freshly generated, stale prices
    assert fails(tmp_path) == ["dashboard prices"]


def test_missing_file_and_bare_nan_fail(tmp_path):
    write(tmp_path)
    (tmp_path / "oil_balance.json").unlink()
    (tmp_path / "signals_latest.json").write_text('{"meta": {"as_of": "2026-11-23", "n_ranked": NaN}}')
    assert set(fails(tmp_path)) == {"oil_balance", "signals"}


def test_missing_local_stores_fail_not_skip(tmp_path):
    assert {c.level for c in X.local(tmp_path, T)} == {X.FAIL}
