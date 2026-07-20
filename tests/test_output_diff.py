"""Output layer - snapshot round-trip + diff semantics."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

from signal_engine.output import (
    DiffReport,
    diff_snapshots,
    latest_prior_snapshot,
    render_diff_markdown,
    write_snapshot,
)


def _frame(rows: list[tuple[str, float | None]]) -> pl.DataFrame:
    """(ticker, pctl) -> a minimal ranked cross-section."""
    return pl.DataFrame({
        "ticker": [t for t, _ in rows],
        "sector": ["Industrials"] * len(rows),
        "pctl": [p for _, p in rows],
        "composite": [1.0 - (p or 1.0) for _, p in rows],
        "n_families": [3] * len(rows),
    })


def test_diff_detects_entrants_exits_and_jumps() -> None:
    prev = _frame([("AAA", 0.05), ("BBB", 0.50), ("CCC", 0.08), ("DDD", 0.90)])
    curr = _frame([("AAA", 0.06), ("BBB", 0.04), ("CCC", 0.40), ("DDD", 0.20)])
    diff = diff_snapshots(prev, curr, as_of=date(2026, 7, 17),
                          prev_as_of=date(2026, 7, 16),
                          new_entrant_decile=0.10, rank_jump_threshold=50.0)
    # BBB entered the top decile (0.50 -> 0.04); AAA stayed; CCC left.
    assert diff.new_entrants["ticker"].to_list() == ["BBB"]
    assert diff.exits["ticker"].to_list() == ["CCC"]
    # Jumps >= 50 pts: BBB +46 pts? (0.50-0.04)*100 = 46 -> NO; DDD 70 pts -> yes.
    assert diff.rank_jumps["ticker"].to_list() == ["DDD"]
    assert diff.rank_jumps["pctl_gain"].to_list()[0] == 70.0


def test_diff_handles_new_and_dropped_tickers() -> None:
    """A ticker only present on one side must not crash the join; a brand-new
    top-decile name counts as an entrant."""
    prev = _frame([("AAA", 0.05)])
    curr = _frame([("AAA", 0.06), ("NEWB", 0.02)])
    diff = diff_snapshots(prev, curr, as_of=date(2026, 7, 17),
                          prev_as_of=date(2026, 7, 16))
    assert diff.new_entrants["ticker"].to_list() == ["NEWB"]
    assert diff.exits.height == 0


def test_snapshot_roundtrip_and_prior_lookup(tmp_path: Path) -> None:
    d1, d2, d3 = date(2026, 7, 15), date(2026, 7, 16), date(2026, 7, 17)
    for d in (d1, d2):
        write_snapshot(_frame([("AAA", 0.05)]), d, tmp_path)
    # Prior to d3 is d2, not d1; prior to d1 is None.
    found = latest_prior_snapshot(tmp_path, before=d3)
    assert found is not None and found[0] == d2
    assert latest_prior_snapshot(tmp_path, before=d1) is None
    # Same-day re-run overwrites, not duplicates.
    write_snapshot(_frame([("AAA", 0.01), ("BBB", 0.02)]), d2, tmp_path)
    found2 = latest_prior_snapshot(tmp_path, before=d3)
    assert found2 is not None and found2[1].height == 2


def test_render_markdown_sections() -> None:
    prev = _frame([("AAA", 0.05), ("CCC", 0.08)])
    curr = _frame([("AAA", 0.06), ("BBB", 0.04)])
    diff = diff_snapshots(prev, curr, as_of=date(2026, 7, 17),
                          prev_as_of=date(2026, 7, 16))
    md = render_diff_markdown(diff)
    assert "New top-decile entrants (1)" in md
    assert "BBB" in md
    assert "Top-decile exits (1)" in md
    first = DiffReport(as_of=date(2026, 7, 17), prev_as_of=None)
    assert "First snapshot" in render_diff_markdown(first)
