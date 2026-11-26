import json
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

from signal_engine.data import databento_futures as F

JOB = F.Job("CL_statistics", ("CL.c.0",), "continuous", "statistics")
NOW = datetime(2026, 12, 5, tzinfo=UTC)


class Fake:
    def __init__(self, cost=1.0, fail=False):
        self.cost, self.fail, self.calls = cost, fail, 0
        self.metadata = SimpleNamespace(get_cost=lambda **k: self.cost)
        self.timeseries = SimpleNamespace(get_range=self._get)

    def _get(self, *, path, **k):
        self.calls += 1
        if self.fail:
            raise ConnectionError("drop")
        Path(path).write_bytes(b"x")


@pytest.fixture
def store(tmp_path):
    s = F.Store(tmp_path)
    return s


def req(a=date(2025, 1, 1), b=date(2026, 1, 1)):
    return F.Request(JOB, a, b)


def test_buy_records_and_is_not_requested_again(store):
    F.buy(Fake(), store, req(), "cash", NOW)
    assert store.spent("cash", "2026-12") == 1.0
    p = F.plan([JOB], store, date(2025, 1, 1), date(2026, 3, 1))
    assert [(r.start, r.end) for r in p] == [(date(2026, 1, 1), date(2026, 3, 1))]


def test_plan_splits_years(store):
    p = F.plan([JOB], store, date(2024, 6, 1), date(2026, 2, 1))
    assert [r.end for r in p] == [date(2025, 1, 1), date(2026, 1, 1), date(2026, 2, 1)]


@pytest.mark.parametrize("cost", [2.01, float("nan"), float("inf"), -1.0])
def test_bad_or_large_quote_never_downloads(store, cost):
    c = Fake(cost)
    with pytest.raises(F.BudgetError):
        F.buy(c, store, req(), "cash", NOW)
    assert c.calls == 0 and not store.events()


def test_monthly_cap_and_reset(store):
    c = Fake(2.0)
    for _ in range(5):
        F.buy(c, store, req(), "cash", NOW)
    with pytest.raises(F.BudgetError, match="cash cap"):
        F.buy(c, store, req(), "cash", NOW)
    assert c.calls == 5
    F.buy(c, store, req(), "cash", datetime(2027, 1, 1, tzinfo=UTC))


def test_credit_cap_is_lifetime(store):
    store.root.mkdir(exist_ok=True)
    store.record(event="reserve", usd=109.5, bucket="credit", at="2026-01-01")
    with pytest.raises(F.BudgetError, match="credit cap"):
        F.buy(Fake(1.0), store, req(), "credit", NOW)


def test_failed_download_still_counts_and_is_not_covered(store):
    with pytest.raises(ConnectionError):
        F.buy(Fake(fail=True), store, req(), "cash", NOW)
    assert store.spent("cash") == 1.0 and store.covered_to(JOB) is None


def test_corrupt_spend_ledger_refuses(store):
    store.ledger.write_text("{oops\n")
    c = Fake()
    with pytest.raises(F.BudgetError, match="corrupt"):
        F.buy(c, store, req(), "cash", NOW)
    assert c.calls == 0


def test_second_loader_refused(store):
    with store.lock(), pytest.raises(F.BudgetError), store.lock():
        pass


def test_changed_symbols_are_requested_again(store):
    F.buy(Fake(), store, req(), "cash", NOW)
    assert store.covered_to(F.Job(JOB.name, ("CL.c.0", "CL.c.1"), "continuous", "statistics")) is None


def ts(d, h):
    return datetime(2026, 11, d, h, tzinfo=UTC)


def stat(**k):
    return {"instrument_id": 1, "ts_recv": ts(2, 20), "ts_ref": ts(2, 0), "price": 60.0, "quantity": 0,
            "stat_type": 3, "stat_flags": 3, "update_action": 1, **k}


def test_final_settlement_beats_later_preliminary_and_intraday():
    s = pl.DataFrame([
        stat(), stat(price=61.0, stat_flags=2, ts_recv=ts(2, 22)),
        stat(price=62.0, stat_flags=11, ts_recv=ts(2, 23)), stat(price=63.0, update_action=2),
        stat(price=None, ts_recv=ts(2, 23)),
        stat(stat_type=9, price=None, quantity=500, ts_recv=ts(3, 2)),
    ])
    r = F.settlements(s)
    assert r.height == 1 and r["settle"][0] == 60.0 and r["final"][0] and r["open_interest"][0] == 500


def definition(i, sym, exp, cls="F"):
    return {"instrument_id": i, "ts_recv": ts(1, 0), "instrument_class": cls, "asset": "CL",
            "raw_symbol": sym, "expiration": exp}


def test_curve_ranks_by_expiry_and_requires_definitions():
    s = pl.DataFrame([stat(instrument_id=2, price=59.0), stat(instrument_id=1)])
    d = pl.DataFrame([definition(1, "CLZ6", datetime(2026, 11, 20, tzinfo=UTC)),
                      definition(2, "CLF7", datetime(2026, 12, 20, tzinfo=UTC))])
    assert F.curve(s, d)["raw_symbol"].to_list() == ["CLZ6", "CLF7"]
    with pytest.raises(ValueError, match="no definition"):
        F.curve(s, d.filter(pl.col("instrument_id") == 1))


def test_api_kwargs_accepted_by_real_client():
    db = pytest.importorskip("databento")
    import inspect
    h = db.Historical(key="db-" + "x" * 29)
    inspect.signature(h.metadata.get_cost).bind(**req().api())
    inspect.signature(h.timeseries.get_range).bind(**req().api(), path="p")
    import databento_dbn as dbn
    assert int(dbn.StatType.SETTLEMENT_PRICE) == F.SETTLE and int(dbn.StatType.OPEN_INTEREST) == F.OPEN_INTEREST
    assert int(dbn.StatUpdateAction.DELETE) == F.DELETE
    json.dumps(req().api())
