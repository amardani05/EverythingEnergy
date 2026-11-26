import ast
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from signal_engine.validation import ledger as L
from signal_engine.validation.backtest import walk_forward_ls
from tests.test_backtest import _world

KW = dict(signal="s", dataset="d", hypothesis="h", min_names=20, n_perm=199)


@pytest.fixture
def path(tmp_path):
    return tmp_path / "x" / "ledger.jsonl"


def test_distinct_configs_counted_once(path):
    sig, px = _world()
    for cost in (10.0, 20, 20.0):
        L.run_trial(sig, px, path=path, cost_bps=cost, **KW)
    L.run_trial(sig, px, path=path, cost_bps=20, n_quantiles=5, **KW)  # explicit default
    ts = L.trials(path)
    assert [t["runs"] for t in ts] == [1, 3]
    L.run_trial(sig, px, path=path, cost_bps=20, version="2", **KW)
    assert len(L.trials(path)) == 3


def test_extending_the_grid_is_the_same_trial():
    g = [date(2020, 1, 31) + timedelta(days=30 * i) for i in range(12)]
    a, b = L.resolve_params({"rebalance_dates": g[:9]}), L.resolve_params({"rebalance_dates": g})
    weekly = L.resolve_params({"rebalance_dates": [g[0] + timedelta(days=7 * i) for i in range(9)]})
    assert a == b != weekly


def test_failed_trial_still_counts(path):
    sig, px = _world()
    with pytest.raises(ValueError):
        L.run_trial(sig, px, path=path, **{**KW, "min_names": 500})
    t = L.trials(path)
    assert len(t) == 1 and t[0]["status"] == "error"


def test_corrupt_ledger_raises(path):
    L.append_event({"key": "a"}, path)
    path.write_text(path.read_text() + "{oops\n")
    with pytest.raises(ValueError, match="corrupt"):
        L.read_events(path)


def test_backfill_idempotent_and_matches_live_key(path):
    sig, px = _world()
    kw = dict(signal="s", dataset="d", hypothesis="h", at="2026-01-01", summary={"sharpe_net": 1.0},
              note="n", path=path, min_names=20)
    assert L.backfill(**kw) and not L.backfill(**kw)
    L.run_trial(sig, px, path=path, **KW)
    assert len(L.trials(path)) == 1 and L.trials(path)[0]["runs"] == 2


def test_poisoned_signal_significant():
    sig, px = _world(alpha_frac=1.0)
    assert L.permutation_pvalue(walk_forward_ls(sig, px, min_names=20)) < 0.01


def test_noise_pvalues_uniform():
    _, px = _world(n_days=80)
    dates, tick = sorted(px["date"].unique()), sorted(px["ticker"].unique())
    ps = []
    for seed in range(200):
        rng = np.random.default_rng(1000 + seed)
        sig = pl.DataFrame({"ticker": tick * len(dates), "date": [d for d in dates for _ in tick],
                            "value": rng.normal(size=len(dates) * len(tick))})
        ps.append(L.permutation_pvalue(walk_forward_ls(sig, px, min_names=20), n_perm=99, seed=seed))
    assert 0.4 < np.mean(ps) < 0.6
    assert np.mean(np.array(ps) > 0.05) >= 0.9


def test_name_returns_reproduce_books():
    sig, px = _world()
    r = walk_forward_ls(sig, px, min_names=20)
    assert [len(x) for x in r.name_returns] == r.period_returns["n_ranked"].to_list()


def test_scripts_cannot_bypass_the_ledger():
    root = Path(__file__).resolve().parents[1]
    files = [*root.glob("scripts/*.py"), *root.glob("*.py"), *root.glob("signal_engine/**/*.py")]
    allowed = {"ledger.py", "backtest.py"}
    bad = [f.name for f in files if f.name not in allowed
           for n in ast.walk(ast.parse(f.read_text()))
           if (isinstance(n, ast.Attribute) and n.attr == "walk_forward_ls")
           or (isinstance(n, ast.Name) and n.id == "walk_forward_ls")
           or (isinstance(n, ast.alias) and n.name == "walk_forward_ls")]
    assert not bad
