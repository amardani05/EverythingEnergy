"""Graph-propagation tests: edge parsing, direction semantics, own-basket
exclusion, and a synthetic lead-lag world where propagation is true by
construction so the signal must find it."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest
import yaml

from signal_engine.atlas.graph import compute_graph_momentum, flow_edges

TINY_TAXONOMY = {
    "baskets": [
        {"id": "crude", "constituents": [{"ticker": "CRD1"}, {"ticker": "CRD2"}],
         "feeds_into": ["refining"]},
        {"id": "refining", "constituents": [{"ticker": "REF1"}, {"ticker": "REF2"}],
         "feeds_into": ["retail", "ghost_basket"]},   # ghost edge must be dropped
        {"id": "retail", "constituents": [{"ticker": "RET1"}],
         "feeds_into": []},
    ]
}


@pytest.fixture
def tiny_tax(tmp_path: Path) -> Path:
    p = tmp_path / "tax.yaml"
    p.write_text(yaml.safe_dump(TINY_TAXONOMY))
    return p


MAPPING_TAXONOMY = {
    "meta": {"version": "test"},
    # The real taxonomy shape: basket id IS the mapping key, no inner 'id'.
    "crude": {"display_name": "Crude", "feeds_into": ["refining"],
              "constituents": [{"ticker": "CRD1"}]},
    "refining": {"display_name": "Refining", "feeds_into": ["ghost"],
                 "constituents": [{"ticker": "REF1"}]},
}


def test_mapping_form_taxonomy_keys_are_basket_ids(tmp_path: Path) -> None:
    """Regression: the live taxonomy keys baskets by mapping key with no
    inner 'id'. The first graph run mapped 205 tickers to ONE basket and
    found zero edges because both walkers only looked for 'id' fields."""
    from signal_engine.atlas.clusters import ticker_to_basket

    p = tmp_path / "tax.yaml"
    p.write_text(yaml.safe_dump(MAPPING_TAXONOMY))
    t2b = ticker_to_basket(p)
    assert t2b == {"CRD1": "crude", "REF1": "refining"}
    edges = flow_edges(p)
    assert edges == [("crude", "refining")]  # ghost target dropped


def test_flow_edges_parses_and_drops_ghosts(tiny_tax: Path) -> None:
    edges = flow_edges(tiny_tax)
    assert ("crude", "refining") in edges
    assert ("refining", "retail") in edges
    assert all(t != "ghost_basket" for _, t in edges), "stale targets must be dropped"
    assert len(edges) == 2


def _panel(rows: list[tuple[str, date, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        {"ticker": [r[0] for r in rows], "date": [r[1] for r in rows],
         "close": [r[2] for r in rows]}
    )


def test_direction_semantics_and_own_basket_exclusion() -> None:
    """crude -> refining. Refiners' neigh_up must equal crude's trailing
    return; crude's neigh_down must equal refining's; and a basket's own
    move must never appear in its own neighbor signal."""
    start = date(2024, 1, 1)
    days = [start + timedelta(days=i) for i in range(10)]
    rows: list[tuple[str, date, float]] = []
    for i, d in enumerate(days):
        rows.append(("CRD1", d, 100.0 * (1.10 ** i)))   # crude rallies 10%/day
        rows.append(("REF1", d, 100.0))                  # refining flat
    t2b = {"CRD1": "crude", "REF1": "refining"}
    out = compute_graph_momentum(_panel(rows), t2b, [("crude", "refining")], lookback=5)

    last = days[-1]
    ref_row = out.filter((pl.col("ticker") == "REF1") & (pl.col("date") == last)).row(0, named=True)
    crd_row = out.filter((pl.col("ticker") == "CRD1") & (pl.col("date") == last)).row(0, named=True)

    crude_tr = 1.10 ** 5 - 1.0
    # Refiner sees its supplier's rally as upstream signal...
    assert ref_row["neigh_up"] == pytest.approx(crude_tr, rel=1e-9)
    # ...and has no downstream edge, so no information, so null (not 0).
    assert ref_row["neigh_down"] is None
    # Crude sees its customer's flat tape downstream; no upstream edge.
    assert crd_row["neigh_down"] == pytest.approx(0.0, abs=1e-12)
    assert crd_row["neigh_up"] is None
    # Own-basket exclusion: crude's own +61% rally must NOT leak into its
    # own neighbor columns.
    assert crd_row["neigh_down"] != pytest.approx(crude_tr, rel=1e-3)


def test_lead_lag_world_yields_positive_ic() -> None:
    """Construct propagation: refining's return today = crude's return
    5 days ago. Then neigh_up (crude trailing return) must positively
    predict refining's forward return: IC > 0.5 on this noiseless world."""
    import numpy as np


    rng = np.random.default_rng(3)
    n = 160
    start = date(2024, 1, 1)
    days = [start + timedelta(days=i) for i in range(n)]
    crude_daily = rng.normal(0.0, 0.02, size=n)
    lag = 5
    refining_daily = np.concatenate([np.zeros(lag), crude_daily[:-lag]])  # follows crude

    rows: list[tuple[str, date, float]] = []
    c_px, r_px = 100.0, 100.0
    for i, d in enumerate(days):
        c_px *= 1.0 + crude_daily[i]
        r_px *= 1.0 + refining_daily[i]
        rows.append(("CRD1", d, c_px))
        rows.append(("REF1", d, r_px))
    t2b = {"CRD1": "crude", "REF1": "refining"}
    panel = _panel(rows)
    out = compute_graph_momentum(panel, t2b, [("crude", "refining")], lookback=lag)

    signals = (
        out.filter(pl.col("ticker") == "REF1")
        .select(["ticker", "date", "neigh_up"])
        .drop_nulls("neigh_up")
        .rename({"neigh_up": "value"})
    )
    ref_prices = panel.filter(pl.col("ticker") == "REF1")
    # One name = no cross-section, so correlate signal vs forward return
    # through TIME (a per-date Spearman needs >= 2 names).
    from scipy.stats import spearmanr

    from signal_engine.validation.ic import forward_returns

    fwd = forward_returns(ref_prices, horizon=lag - 2)
    joined = signals.join(fwd, on=["ticker", "date"], how="inner").drop_nulls()
    assert joined.height > 100
    rho, _ = spearmanr(joined["value"].to_numpy(),
                       joined[f"fwd_{lag - 2}d"].to_numpy())
    assert rho > 0.5, f"propagation not detected: time-series rho={rho:.2f}"


def test_no_edges_raises() -> None:
    with pytest.raises(ValueError):
        compute_graph_momentum(
            _panel([("A", date(2024, 1, 1), 1.0)]), {"A": "x"}, [], lookback=5
        )
