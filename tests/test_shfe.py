from datetime import date, timedelta

import pytest

from signal_engine.macro.shfe import LABELS, METALS, build, parse_weekly


def _html(day="2026-11-20", copper=("27642", "2270", "30846", "6053", "3204", "3783")):
    rows = [f"<tr><td>Issue No.(45),2026 DATE:{day}</td></tr>"]
    for raw in METALS:
        vals = copper if raw == "COPPER" else ("100", "50", "110", "40", "10", "-10")
        rows += [f"<tr><td>{raw}</td><td>Unit\uff1aTonne</td></tr>",
                 "<tr><td>Shanghai</td><td>Some warehouse</td><td>1</td><td>1</td><td>1</td><td>1</td><td>0</td><td>0</td></tr>",
                 "<tr><td>Total(Bonded)</td>" + "<td>0</td>" * 6 + "</tr>",
                 "<tr><td>Total</td>" + "".join(f"<td>{v}</td>" for v in vals) + "<td>9</td><td>9</td><td>0</td></tr>"]
    rows += ["<tr><td>FUEL OIL</td><td>Unit\uff1aTonne</td></tr>", "<tr><td>Total</td>" + "<td>1</td>" * 9 + "</tr>"]
    return "<table>" + "".join(rows) + "</table>"


def test_parse_totals():
    r = parse_weekly(_html())
    assert r["date"] == "2026-11-20"
    assert r["metals"]["copper"] == {"deliverable": 30846.0, "warrant": 6053.0}
    assert set(r["metals"]) == set(LABELS)


def test_parse_refuses_unreconciled():
    with pytest.raises(ValueError, match="reconcile"):
        parse_weekly(_html(copper=("100", "50", "200", "40", "10", "-10")))


def test_parse_refuses_missing_date():
    with pytest.raises(ValueError, match="DATE"):
        parse_weekly(_html().replace("DATE:", "D:"))


def test_build_changes_and_year_ago():
    start = date(2025, 10, 31)
    reports = []
    for i in range(60):
        d = (start + timedelta(weeks=i)).isoformat()
        reports.append({"date": d, "metals": {m: {"deliverable": 1000.0 + i * 10, "warrant": 500.0} for m in LABELS}})
    out = build(reports)
    s = out["summary"]["copper"]
    assert s["chg_1w"] == 10 and s["chg_4w"] == 40
    assert s["vs_1y"] == 520  # 52 weeks x 10
    assert s["warrant_share"] == pytest.approx(500 / 1590, abs=1e-3)
    assert s["vs_5y"] is None  # not enough history yet
