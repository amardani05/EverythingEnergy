"""Leakage canary - wired up now, exercised once the IC harness lands.

The contract: if anywhere in the pipeline a fact with `knowledge_date > t`
leaks into the t-indexed feature set, the forward-IC against a future-return
synthetic feature should explode (Spearman close to 1.0). If it does NOT
explode, the PIT plumbing has a hole.

This file holds the *infrastructure* (a known-future-return synthetic and a
helper that builds a fake panel). The full leakage test that runs the IC
harness on it lives in tests/test_validation_ic.py once validation/ exists.
Until then, this module is imported by other tests as a kit.

DO NOT delete this stub when the harness lands; refactor instead. The
existence of this file is a contract.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

import polars as pl


@dataclass
class FutureLeakSynthetic:
    """A feature whose value at date `t` equals the realized return from
    t -> t+horizon. Used as a positive control: any honest IC harness must
    flag this as IC ~= 1 at the matching horizon.
    """

    horizon_days: int

    def build(self, prices: pl.DataFrame) -> pl.DataFrame:
        """`prices` columns: ticker, date, close. Returns a long-form
        (ticker, date, leak_value) frame where leak_value is the realized
        forward return over `horizon_days`.
        """
        req = {"ticker", "date", "close"}
        missing = req - set(prices.columns)
        if missing:
            raise ValueError(f"prices missing columns: {missing}")
        return (
            prices.sort(["ticker", "date"])
            .with_columns(
                (pl.col("close").shift(-self.horizon_days).over("ticker") / pl.col("close") - 1.0)
                .alias("leak_value")
            )
            .select(["ticker", "date", "leak_value"])
            .drop_nulls("leak_value")
        )


def make_toy_prices(tickers: Iterable[str], start: date, days: int, seed: int = 0) -> pl.DataFrame:
    """Random-walk toy panel for synthetic-leakage tests. Not realistic;
    just enough rows to drive an IC harness through its paces."""
    import numpy as np

    rng = np.random.default_rng(seed)
    rows: list[dict[str, object]] = []
    for t in tickers:
        price = 100.0
        for i in range(days):
            d = start + timedelta(days=i)
            price *= 1.0 + rng.normal(0, 0.01)
            rows.append({"ticker": t, "date": d, "close": price})
    return pl.DataFrame(rows)


def test_leak_synthetic_is_perfect_predictor_of_forward_returns() -> None:
    """Sanity: by construction, leak_value over horizon H = forward H-day
    return. Spearman vs forward H-day return is exactly 1.0 within numeric
    precision. If THIS test ever fails, the kit itself is broken."""
    import numpy as np
    from scipy.stats import spearmanr

    prices = make_toy_prices(["A", "B", "C"], date(2024, 1, 1), days=120, seed=42)
    leak = FutureLeakSynthetic(horizon_days=5).build(prices)

    # Recompute the same forward return independently and correlate.
    fwd = (
        prices.sort(["ticker", "date"])
        .with_columns(
            (pl.col("close").shift(-5).over("ticker") / pl.col("close") - 1.0).alias("fwd5")
        )
        .select(["ticker", "date", "fwd5"])
        .drop_nulls("fwd5")
    )
    joined = leak.join(fwd, on=["ticker", "date"], how="inner")
    rho, _ = spearmanr(joined["leak_value"].to_numpy(), joined["fwd5"].to_numpy())
    assert np.isclose(rho, 1.0, atol=1e-9), f"leak synthetic must be perfect; got rho={rho}"
