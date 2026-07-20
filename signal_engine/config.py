"""Config loader. One place that parses signal_engine/config.yaml; everything
else takes a Config instance (or sub-dict) so tests can inject overrides."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "signal_engine" / "config.yaml"


@dataclass(frozen=True)
class Config:
    raw: dict[str, Any]
    path: Path = field(default=DEFAULT_CONFIG_PATH)

    @classmethod
    def load(cls, path: Path | str | None = None) -> Config:
        p = Path(path) if path else DEFAULT_CONFIG_PATH
        with p.open("r") as f:
            data = yaml.safe_load(f)
        return cls(raw=data, path=p)

    # Convenience accessors - keep these thin; downstream modules can read raw[...] too.
    @property
    def duckdb_path(self) -> Path:
        return REPO_ROOT / self.raw["storage"]["duckdb_path"]

    @property
    def raw_dir(self) -> Path:
        return REPO_ROOT / self.raw["storage"]["raw_dir"]

    @property
    def edgar_user_agent(self) -> str:
        e = self.raw["edgar"]
        return f"{e['user_agent_name']} {e['user_agent_email']}"

    @property
    def edgar_rate_limit_per_sec(self) -> int:
        return int(self.raw["edgar"]["rate_limit_per_sec"])

    @property
    def edgar_concepts(self) -> dict[str, dict[str, Any]]:
        return self.raw["edgar"]["concepts"]
