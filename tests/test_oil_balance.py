"""Oil balance model: identities must reconcile exactly, and the calibration
must recover a planted relationship."""

from __future__ import annotations

import random
from datetime import date, timedelta
from itertools import pairwise

import pytest

from signal_engine.macro.oil_balance import (
    DEMAND_BLOCS,
    SUPPLY_BLOCS,
    SteoBundle,
    build_global_balance,
    build_us_weekly,
    days_in_month,
    fit_stock_model,
    project_oecd_stocks,
    steo_series_ids,
    variance_bridge,
)


def _bundle(n_months: int = 30, hist_through: int = 20) -> SteoBundle:
    """Synthetic STEO: every id gets a deterministic, distinct series; world
    totals are built so the bloc residuals are well defined."""
    rng = random.Random(7)
    periods = []
    y, m = 2024, 1
    for _ in range(n_months):
        periods.append(f"{y}{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    series = {sid: {p: 5 + rng.random() for p in periods} for sid in steo_series_ids()}
    # make the aggregates comfortably larger than the parts they net against
    for agg in ("PAPR_NONOPEC", "PATC_OECD", "PATC_NON_OECD"):
        series[agg] = {p: 60 + rng.random() for p in periods}
    for p in periods:
        series["PAPR_WORLD"][p] = series["COPR_OPEC"][p] + series["OPEC_NC"][p] + series["PAPR_NONOPEC"][p]
        series["PATC_WORLD"][p] = series["PATC_OECD"][p] + series["PATC_NON_OECD"][p]
    level = 2800.0
    for p in periods:
        level += rng.uniform(-20, 20)
        series["PASC_OECD_T3"][p] = level
    last = periods[hist_through]
    return SteoBundle(series=series, last_hist={k: last for k in series}, released="2026-11-10")


def test_blocs_sum_to_world_totals():
    bal = build_global_balance(_bundle(), start="202401")
    for i in range(len(bal["periods"])):
        sup = sum(bal["supply"][k][i] for k, _, _, _ in SUPPLY_BLOCS)
        dem = sum(bal["demand"][k][i] for k, _, _, _ in DEMAND_BLOCS)
        assert sup == pytest.approx(bal["supply_total"][i], abs=0.01)
        assert dem == pytest.approx(bal["demand_total"][i], abs=0.01)
        assert bal["implied_build"][i] == pytest.approx(sup - dem, abs=0.01)


def test_status_flags_and_first_month_has_no_stock_change():
    b = _bundle(hist_through=20)
    b.last_hist["PATC_WORLD"] = "202406"
    bal = build_global_balance(b, start="202401")
    assert bal["oecd_build"][0] is None and bal["unexplained"][0] is None
    assert bal["status"][5] == "hist" and bal["status"][6] == "est"
    assert bal["status"][20] == "est" and bal["status"][21] == "fcst"


def test_unexplained_closes_the_identity():
    bal = build_global_balance(_bundle(), start="202401")
    for imp, obs, unx in zip(bal["implied_build"][1:], bal["oecd_build"][1:], bal["unexplained"][1:], strict=True):
        assert imp == pytest.approx(obs + unx, abs=0.002)


def test_fit_recovers_planted_relationship():
    rng = random.Random(1)
    x = [rng.uniform(-3, 3) for _ in range(400)]
    y = [0.2 + 0.4 * v + rng.gauss(0, 0.5) for v in x]
    m = fit_stock_model(x, y)
    assert m.alpha == pytest.approx(0.2, abs=0.08)
    assert m.beta == pytest.approx(0.4, abs=0.05)
    assert m.sigma == pytest.approx(0.5, abs=0.06)
    assert m.n == 400


def test_fit_skips_missing_pairs_and_rejects_tiny_samples():
    m = fit_stock_model([1, None, 2, 3, 4], [1, 5, None, 3, 4])
    assert m.n == 3
    with pytest.raises(ValueError):
        fit_stock_model([1, 2], [1, 2])


def test_projection_band_widens_and_is_centred():
    bal = build_global_balance(_bundle(hist_through=20), start="202401")
    idx = range(1, 21)
    model = fit_stock_model([bal["implied_build"][i] for i in idx], [bal["oecd_build"][i] for i in idx])
    proj = project_oecd_stocks(bal, model)
    assert proj["anchor"] == bal["periods"][20]
    assert proj["periods"] == bal["periods"][21:]
    widths = [hi - lo for lo, hi in zip(proj["lo"], proj["hi"], strict=True)]
    assert all(b > a for a, b in pairwise(widths))
    first = proj["periods"][0]
    assert widths[0] == pytest.approx(2 * model.sigma * days_in_month(first), abs=0.2)


def test_variance_bridge_reconciles_exactly():
    bal = build_global_balance(_bundle(), start="202401")
    br = variance_bridge(bal, idx_now=[12, 13, 14], idx_base=[0, 1, 2])
    assert abs(br["check"]) < 0.01
    assert br["base"] + sum(s["delta"] for s in br["steps"]) == pytest.approx(br["now"], abs=0.01)
    assert {s["side"] for s in br["steps"]} == {"supply", "demand"}


def test_variance_bridge_signs():
    bal = build_global_balance(_bundle(), start="202401")
    bal["supply"]["us"][5] = bal["supply"]["us"][4] + 1.0       # +1 supply -> +1 build
    bal["demand"]["china"][5] = bal["demand"]["china"][4] + 1.0  # +1 demand -> -1 build
    steps = {(s["side"], s["key"]): s["delta"] for s in variance_bridge(bal, [5], [4])["steps"]}
    assert steps[("supply", "us")] == pytest.approx(1.0)
    assert steps[("demand", "china")] == pytest.approx(-1.0)


def _weekly_raw(n_weeks: int = 330, planted_adj: float = 250.0):
    """kb / kb/d fixture where the true unaccounted crude is a constant."""
    d0 = date(2020, 1, 3)
    raw = {k: {} for k in ("stocks_total", "stocks_comm", "stocks_spr", "production", "imports", "exports", "refinery_inputs")}
    total, spr = 1_000_000.0, 400_000.0
    for i in range(n_weeks):
        d = (d0 + timedelta(days=7 * i)).isoformat()
        prod, imp, exp, runs = 12_000 + i, 6_000, 3_500, 15_800 + i * 2
        if i:
            total += 7 * (prod + imp - exp - runs + planted_adj)
        raw["production"][d], raw["imports"][d] = prod, imp
        raw["exports"][d], raw["refinery_inputs"][d] = exp, runs
        raw["stocks_total"][d], raw["stocks_spr"][d] = total, spr
        raw["stocks_comm"][d] = total - spr
    return raw


def test_us_weekly_adjustment_is_the_residual():
    us = build_us_weekly(_weekly_raw(), start="2020-01-01")
    assert all(a == pytest.approx(0.25, abs=0.002) for a in us["adjustment"])
    for b, m, a in zip(us["build"], us["modelled_build"], us["adjustment"], strict=True):
        assert b == pytest.approx(m + a, abs=0.002)
    assert us["adjustment_stats"]["sigma"] == pytest.approx(0.0, abs=0.002)
    assert all(v == pytest.approx(0.0, abs=0.002) for v in us["build_spr"])


def test_us_weekly_norm_uses_prior_years_only():
    us = build_us_weekly(_weekly_raw(), start="2020-01-01")
    # the first ~3 years cannot have a 3-observation seasonal norm
    assert us["refinery_inputs_norm"][10] is None
    last = us["refinery_inputs_norm"][-1]
    assert last is not None and last != pytest.approx(us["refinery_inputs"][-1], abs=1e-9)
