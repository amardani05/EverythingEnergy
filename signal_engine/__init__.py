"""Cross-sectional, point-in-time signal engine on a free-data spine.

Architecture: see signal_engine/config.yaml and README.
Build sequence: data -> momentum factor -> value/quality -> PEAD -> atlas -> scoring -> validation -> output.
"""

from __future__ import annotations

__version__ = "0.1.0"
