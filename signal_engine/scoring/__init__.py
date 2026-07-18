"""Scoring — raw factor values -> sector-neutral z -> weighted composite."""

from signal_engine.scoring.composite import (
    FAMILY_COMPONENTS,
    MIN_FAMILIES,
    MIN_SECTOR_N,
    SIGN_CONVENTIONS,
    SignalConfig,
    build_composite,
    family_correlation,
    selection_signals,
)

__all__ = [
    "FAMILY_COMPONENTS",
    "MIN_FAMILIES",
    "MIN_SECTOR_N",
    "SIGN_CONVENTIONS",
    "SignalConfig",
    "build_composite",
    "family_correlation",
    "selection_signals",
]
