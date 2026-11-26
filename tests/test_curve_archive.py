from datetime import date as D
from datetime import datetime as DT

import polars as pl
import pytest

from signal_engine.data import curve_archive as A

NIGHT = DT(2026, 11, 25, 21, 40)


def obs(*rows):
    return pl.DataFrame([{"symbol": s, "root": "CL", "delivery": m, "date": d, "close": c, "volume": 1.0}
                         for s, m, d, c in rows])


Z, F = ("CLZ26.NYM", "2026-12"), ("CLF27.NYM", "2027-01")


def save(root, rows, now=NIGHT):
    new = A.delta(A.clean(obs(*rows), now), A.read(root))
    A.write(root, new, now)
    return new.height


def test_contract_list():
    c = A.contracts(D(2026, 11, 25), 3)
    assert [x[0] for x in c if x[1] == "CL"] == ["CLX26.NYM", "CLZ26.NYM", "CLF27.NYM"]
    assert ("HGX26.CMX", "HG", "2026-11") in c


def test_unsettled_session_not_saved():
    rows = [(*F, D(2026, 11, 24), 82.0), (*F, D(2026, 11, 25), 83.0), (*F, D(2026, 11, 26), 84.0)]
    assert A.clean(obs(*rows), DT(2026, 11, 25, 12, 0))["date"].to_list() == [D(2026, 11, 24)]
    assert A.clean(obs(*rows), NIGHT)["date"].max() == D(2026, 11, 25)


def test_bad_closes_dropped_negative_kept():
    r = A.clean(obs((*Z, D(2026, 11, 23), 0.0), (*Z, D(2026, 11, 24), float("nan")), (*Z, D(2026, 11, 25), -37.6)), NIGHT)
    assert r["close"].to_list() == [-37.6]


def test_rerun_saves_nothing_and_revision_keeps_both(tmp_path):
    d = D(2026, 11, 25)
    assert save(tmp_path, [(*F, d, 83.0)]) == 1
    assert save(tmp_path, [(*F, d, 83.0)], DT(2026, 11, 25, 22)) == 0
    assert save(tmp_path, [(*F, d, 83.5)], DT(2026, 11, 26, 21)) == 1
    assert A.latest(A.read(tmp_path))["close"].to_list() == [83.5]
    assert A.latest(A.read(tmp_path, asof=DT(2026, 11, 25, 23)))["close"].to_list() == [83.0]
    r = A.latest(A.read(tmp_path))
    assert r["revised"][0] and r["first_seen"][0] == NIGHT


def test_expired_contract_survives(tmp_path):
    save(tmp_path, [(*Z, D(2026, 11, 25), 80.0), (*F, D(2026, 11, 25), 83.0)])
    save(tmp_path, [(*F, D(2026, 11, 26), 84.0)], DT(2026, 11, 26, 21))
    assert A.read(tmp_path).filter(pl.col("symbol") == Z[0]).height == 1


def test_curve_ranks_live_rows_and_refuses_backfill(tmp_path):
    save(tmp_path, [(*F, D(2025, 1, 2), 70.0), (*F, D(2026, 11, 25), 83.0), (*Z, D(2026, 11, 25), 80.0)])
    c = A.curve(A.read(tmp_path))
    assert c["date"].unique().to_list() == [D(2026, 11, 25)] and c["delivery"].to_list() == ["2026-12", "2027-01"]
    assert A.curve(A.read(tmp_path), allow_backfill=True).height == 3


def test_files_are_never_overwritten(tmp_path):
    save(tmp_path, [(*F, D(2026, 11, 25), 83.0)])
    with pytest.raises(FileExistsError):
        A.write(tmp_path, A.clean(obs((*F, D(2026, 11, 24), 1.0)), NIGHT), NIGHT)


def test_coverage_ignores_stale(tmp_path):
    o = A.clean(obs((*F, D(2026, 11, 25), 83.0), (*Z, D(2026, 10, 1), 80.0)), NIGHT)
    assert A.coverage(o, NIGHT)["CL"] == 1 and A.coverage(o, NIGHT)["NG"] == 0


def test_nightly_requests_known_contracts_only(tmp_path):
    save(tmp_path, [(*F, D(2026, 11, 25), 83.0)])
    k = A.read(tmp_path)
    ask = lambda now: [s for s, r, _ in A.to_request(now, k) if r == "CL"]  # noqa: E731
    assert ask(NIGHT) == [F[0]]
    assert len(ask(DT(2026, 11, 30, 21))) == A.MONTHS_AHEAD  # Monday scan
    assert any(r == "NG" for _, r, _ in A.to_request(NIGHT, k))  # empty root still scanned
