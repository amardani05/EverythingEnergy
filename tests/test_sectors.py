"""Sector mapping sanity — first matching range wins, energy SIC codes map
to Energy, garbage input is Unclassified."""

from __future__ import annotations

from signal_engine.data.sectors import UNCLASSIFIED, load_sic_ranges, sic_to_sector


def test_oil_gas_extraction_is_energy() -> None:
    ranges = load_sic_ranges()
    assert sic_to_sector("1311", ranges) == "Energy"      # Crude Petroleum and Natural Gas
    assert sic_to_sector("1381", ranges) == "Energy"      # Drilling Oil and Gas Wells
    assert sic_to_sector("2911", ranges) == "Energy"      # Petroleum Refining
    assert sic_to_sector("4924", ranges) == "Utilities"   # Natural Gas Distribution


def test_unknown_or_missing_is_unclassified() -> None:
    ranges = load_sic_ranges()
    assert sic_to_sector(None, ranges) == UNCLASSIFIED
    assert sic_to_sector("", ranges) == UNCLASSIFIED
    assert sic_to_sector("not-a-number", ranges) == UNCLASSIFIED


def test_first_matching_row_wins() -> None:
    """SIC 2834 (Pharma Prep) sits inside both a generic 2830-2839 row
    (Pharma-adjacent) and the 2834-2836 specific row (Pharma). The CSV
    orders them so the more specific row appears after — but our resolver
    is first-match-wins, so the order in the CSV is what matters. Verify
    that whichever row is *first* in the file is the one that resolves."""
    ranges = load_sic_ranges()
    # Find the first range that contains 2834 — that's what sic_to_sector picks.
    expected = next(r.sector for r in ranges if r.low <= 2834 <= r.high)
    assert sic_to_sector("2834", ranges) == expected
