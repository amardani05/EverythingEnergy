"""SEC EDGAR XBRL client - fundamentals spine.

Endpoints used (all under https://data.sec.gov/):
  * submissions/CIK{10pad}.json
        Per-company filing index + SIC, name, tickers, exchanges, fye.
  * api/xbrl/companyfacts/CIK{10pad}.json
        All XBRL facts a company has ever filed. The workhorse - one call
        returns every concept's full history with `accn` and `filed`.
  * api/xbrl/companyconcept/CIK{10pad}/{taxonomy}/{concept}.json
        Single concept for one company. Used as a fallback if companyfacts
        is incomplete for a specific tag.
  * files/company_tickers.json
        Current ticker -> CIK map (no history). Snapshotted daily.

Knowledge-date convention: the XBRL `filed` field on each fact = the date
the filing became public. Amendments (form ends in '/A') get a new accession
and a later `filed` date; we store them as new rows, never overwriting.

Rate limit: SEC fair-use is ~10 req/s with a real User-Agent. Config caps us
at 8 to leave headroom. A simple monotonic-clock throttle is enforced
per-process - if you parallelize, each worker must call `EdgarClient.throttle()`
or share a Limiter.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://data.sec.gov"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


@dataclass
class EdgarClient:
    user_agent: str
    rate_limit_per_sec: int = 8
    session: requests.Session | None = None
    _last_call_ts: float = 0.0
    _min_interval: float = 0.0

    def __post_init__(self) -> None:
        if self.session is None:
            self.session = requests.Session()
        # SEC requires Host, Accept-Encoding, and a contact-bearing UA.
        assert self.session is not None  # for type narrowing
        self.session.headers.update({
            "User-Agent": self.user_agent,
            "Accept-Encoding": "gzip, deflate",
            "Accept": "application/json",
            "Host": "data.sec.gov",
        })
        self._min_interval = 1.0 / max(self.rate_limit_per_sec, 1)

    def throttle(self) -> None:
        """Block until at least 1/rate_limit_per_sec has elapsed since last call."""
        now = time.monotonic()
        wait = self._min_interval - (now - self._last_call_ts)
        if wait > 0:
            time.sleep(wait)
        self._last_call_ts = time.monotonic()

    def _get_json(self, url: str, *, host_override: str | None = None) -> dict[str, Any]:
        self.throttle()
        assert self.session is not None
        headers = {"Host": host_override} if host_override else {}
        # SEC returns 403 with no body if UA is missing; surface that clearly.
        r = self.session.get(url, headers=headers, timeout=30)
        if r.status_code == 403:
            raise RuntimeError(
                f"EDGAR 403 for {url}. Check User-Agent (currently {self.session.headers['User-Agent']!r}); "
                "SEC requires a real contact email."
            )
        r.raise_for_status()
        return r.json()

    # ---------- endpoints ----------

    def ticker_cik_map(self) -> dict[str, dict[str, Any]]:
        """Current ticker -> {cik_str, ticker, title} map. Snapshot this nightly."""
        # company_tickers.json lives on www.sec.gov, not data.sec.gov.
        return self._get_json(TICKERS_URL, host_override="www.sec.gov")

    def submissions(self, cik: int) -> dict[str, Any]:
        return self._get_json(f"{BASE_URL}/submissions/CIK{cik:010d}.json")

    def company_facts(self, cik: int) -> dict[str, Any]:
        return self._get_json(f"{BASE_URL}/api/xbrl/companyfacts/CIK{cik:010d}.json")

    def company_concept(self, cik: int, taxonomy: str, concept: str) -> dict[str, Any]:
        return self._get_json(
            f"{BASE_URL}/api/xbrl/companyconcept/CIK{cik:010d}/{taxonomy}/{concept}.json"
        )

    # ---------- normalization ----------

    @staticmethod
    def extract_facts(
        company_facts: dict[str, Any],
        cik: int,
        concept_chains: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Walk a companyfacts payload and produce one row per fact, using the
        first tag in each fallback chain that has data.

        `concept_chains` is the parsed `edgar.concepts` block from config.yaml.

        Returns rows ready for direct INSERT into edgar_facts (schema in store.py).
        """
        rows: list[dict[str, Any]] = []
        facts_root = company_facts.get("facts", {})

        for canonical_name, spec in concept_chains.items():
            taxonomy = spec["taxonomy"]
            tags: list[str] = spec["tags"]
            tax_block = facts_root.get(taxonomy, {})

            chosen_tag: str | None = None
            for tag in tags:
                if tag in tax_block:
                    chosen_tag = tag
                    break
            if chosen_tag is None:
                continue

            concept_block = tax_block[chosen_tag]
            units = concept_block.get("units", {})
            for unit_name, fact_list in units.items():
                for f in fact_list:
                    form = f.get("form") or ""
                    rows.append({
                        "cik": cik,
                        "taxonomy": taxonomy,
                        "concept": canonical_name,
                        "concept_used": chosen_tag,
                        "unit": unit_name,
                        "period_start": f.get("start"),  # may be None for instant facts
                        "period_end": f["end"],
                        "fy": f.get("fy"),
                        "fp": f.get("fp"),
                        "form": form,
                        "is_amendment": form.endswith("/A"),
                        "accession": f["accn"],
                        "filed": f["filed"],
                        "value": f.get("val"),
                    })
        return rows

    @staticmethod
    def submissions_row(payload: dict[str, Any], snapshot_date: str) -> dict[str, Any]:
        """Flatten one submissions.json response into a single edgar_submissions row.

        Some filers carry `null` entries inside their `tickers` / `exchanges`
        arrays (multi-class shares where one class isn't ticker-listed, OTC
        dual-listings where one venue is null, etc.). Filter those out
        before joining - leaving them in raises TypeError on str.join.
        """
        tickers_raw = payload.get("tickers") or []
        exchanges_raw = payload.get("exchanges") or []
        tickers = [t for t in tickers_raw if isinstance(t, str) and t]
        exchanges = [e for e in exchanges_raw if isinstance(e, str) and e]
        return {
            "cik": int(payload.get("cik", 0)),
            "snapshot_date": snapshot_date,
            "name": payload.get("name"),
            "tickers": "|".join(tickers) if tickers else None,
            "exchanges": "|".join(exchanges) if exchanges else None,
            "sic": payload.get("sic"),
            "sic_description": payload.get("sicDescription"),
            "fiscal_year_end": payload.get("fiscalYearEnd"),
        }


# ---------- helpers callers will reach for ----------

def cache_raw(path: Path, payload: dict[str, Any]) -> None:
    """Persist a raw API response as JSON next to the parsed rows - useful
    for debugging and for re-parsing without re-pulling."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def iter_cached_payloads(raw_dir: Path, prefix: str) -> Iterable[tuple[int, Path, dict[str, Any]]]:
    """Yield (cik, path, payload) for every '{prefix}_{cik:010d}.json' under
    `raw_dir` - the re-parse-without-re-pull counterpart to `cache_raw`.

    Files whose name doesn't parse to a CIK or whose JSON is corrupt are
    logged and skipped, never fatal: one bad cache file must not kill a
    bulk re-parse.
    """
    for path in sorted(raw_dir.glob(f"{prefix}_*.json")):
        stem_suffix = path.stem.removeprefix(f"{prefix}_")
        try:
            cik = int(stem_suffix)
        except ValueError:
            log.warning("[cache] skipping %s: unparseable CIK %r", path.name, stem_suffix)
            continue
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            log.warning("[cache] skipping %s: %s", path.name, e)
            continue
        yield cik, path, payload


def cik_for_ticker(ticker_cik_payload: dict[str, Any], ticker: str) -> int | None:
    """Look up a CIK from a `ticker_cik_map()` response.

    The payload is a dict keyed by string index whose values are
    {cik_str, ticker, title}. Tickers are case-sensitive in the file but we
    normalize to upper here.
    """
    ticker = ticker.upper()
    for entry in ticker_cik_payload.values():
        if entry.get("ticker", "").upper() == ticker:
            return int(entry["cik_str"])
    return None


def iter_cik_ticker_pairs(payload: dict[str, Any]) -> Iterable[tuple[int, str, str]]:
    """Yield (cik, ticker, title) from a `ticker_cik_map()` response."""
    for entry in payload.values():
        yield int(entry["cik_str"]), entry["ticker"].upper(), entry.get("title", "")
