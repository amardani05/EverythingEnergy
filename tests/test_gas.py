from datetime import date, timedelta

import pytest

from signal_engine.macro.gas import REGIONS, STEO_GAS, build_monthly, build_storage, season_end


def _weekly(n=600, end=date(2026, 11, 20)):
    ds = [(end - timedelta(weeks=i)).isoformat() for i in range(n)][::-1]
    # same seasonal shape every year: +10/week Apr-Oct, -15/week otherwise
    s, out = 2000.0, {}
    for d in ds:
        m = int(d[5:7])
        s += 10 if 4 <= m <= 10 else -15
        out[d] = s
    return out


def test_season_end():
    assert season_end("2026-11-20") == "2027-03-26"
    assert season_end("2026-06-05") == "2026-10-30"
    assert season_end("2027-02-05") == "2027-03-26"


def test_storage_summary_and_projection():
    one = _weekly()
    out = build_storage({sid: one for sid in REGIONS.values()})
    s = out["summary"]["l48"]
    assert s["change"] == -15 and s["change_avg"] == pytest.approx(-15)
    e = out["end_of_season"]
    assert e["date"] == "2027-03-26" and e["weeks"] == 18
    assert e["projected"] == pytest.approx(s["stocks"] - 15 * 18)
    assert out["regions"]["east"]["dates"][0] >= "2020-01-01"


def test_monthly_balance_identity():
    ps = ["202601", "202602", "202603"]
    series = {sid: dict.fromkeys(ps, 1.0) for sid in STEO_GAS.values()}
    series[STEO_GAS["production"]] = dict.fromkeys(ps, 100.0)
    series[STEO_GAS["consumption"]] = dict.fromkeys(ps, 90.0)
    series[STEO_GAS["storage"]] = {"202601": 3000.0, "202602": 3000.0 + 28 * 8, "202603": 3000.0 + 28 * 8}
    m = build_monthly(series, {STEO_GAS["consumption"]: "202602"})
    assert m["status"] == ["hist", "hist", "fcst"]
    # supply 100+1+1+1, demand 90+1+1 -> implied +11
    assert m["implied_change"][1] == pytest.approx(11.0)
    assert m["reported_change"][1] == pytest.approx(8.0)
    assert m["balancing"][1] == pytest.approx(-3.0)
