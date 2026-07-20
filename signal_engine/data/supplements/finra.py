"""FINRA short interest - semi-monthly publication, free.

URL pattern (subject to FINRA changes; verify on first connect):
    https://cdn.finra.org/equity/regsho/monthly/shrt{YYYYMM}{a|b}.txt
where `a` = first-half settlement, `b` = second-half. Publication is ~8
business days after the settlement date. We use the publication date as
`knowledge_date`, NOT settlement.

Stub - implemented when the short-interest signal lands (post-v1).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FinraDownloader:
    base_url: str = "https://cdn.finra.org/equity/regsho/monthly/"

    def download_file(self, year: int, month: int, half: str) -> str:
        raise NotImplementedError("FINRA short-interest ingestion: build when signal is added")
