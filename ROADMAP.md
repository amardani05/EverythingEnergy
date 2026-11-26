# Roadmap

*Rewritten 2026-11-24. Full history, specs and acceptance tests: [docs/roadmap_archive.md](docs/roadmap_archive.md) (older references to Phases, Tiers and item numbers point there).*

**Goal:** an energy and metals workstation that shows the physical market, prices it, and tests whether physical state predicts related equities.

## Where it stands

- **Terminal:** shipped. Map, commodity and country sheets, quotes, options, strategies, oil balance, workbench.
- **Data:** free sources, nightly. Options are ETF proxies; metals lack demand and stocks; some infrastructure is 2003-2021 vintage.
- **Signals:** the generic factor composite is frozen. It failed on free data (gross Sharpe -0.36) and the survivorship-free rerun did not certify it. Value and PEAD are dropped as standalone signals; the flipped-momentum sleeve is cancelled. Every trial is in a ledger.
- **Operations:** `doctor` runs after every nightly refresh; CI and a daily freshness check are written and start once pushed.

## Key changes

1. **Physical state is the product, not the factor book.** Test balances, curves, cracks and the taxonomy graph. No more factor tuning.
2. **Separate research from live.** WRDS answers historical questions locally; free data runs the terminal. Paid data is optional and parked.
3. **Gate new signals.** Log every trial, test on survivorship-free data, then go live only on gross edge that survives costs.

## Plan

| | Build | Done when |
|---|---|---|
| **1. Trust** | `doctor` (staleness per artifact, non-zero exit in the nightly job); CI | Stale data fails loudly; every push is tested |
| **2. Curves** | Free per-contract curves for CL, NG, RB, HO, GC, SI, HG; calendar spreads, cracks, roll yield; archive every night | The terminal shows each curve and its history since archiving began |
| **3. Physical depth** | Products, then gas/LNG, then metals demand and exchange stocks; reproducible trade links; STEO forecast error | Each main commodity has balance + curve + exposed baskets |
| **4. Options** | American pricing (binomial or BAW); implied vol and skew history from OptionMetrics, research only | Equity and ETF options stop using a European model |
| **5. Conditional signals** | Balance, curve and crack state to baskets; event studies; customer-link graph | One clears the gate, or a null is recorded |

Do 1 first. Then 2 and 3; 5 needs both. 4 can go any time. Nothing here needs paid data.

*Done:* SHFE exchange stocks (step 3: six base metals, weekly archive from 2025-10-31, Exchange Stocks widget), US gas balance (step 3: storage by region vs 5-year range, end-of-season projection, STEO monthly balance with LNG and power burn, Gas Balance widget), US products balance (step 3: gasoline, distillate, jet vs 5-year range, Products Balance widget), curve archive and Curves widget (step 2), `doctor` + CI (first run pending push), trial ledger, WRDS access test, CRSP/Compustat panel, composite rerun, Databento loader (offline only).

**Eventual additions:** saved positions and P&L; event calendar; disruption overlay (Hormuz first); power and spark spreads; COT positioning; workbench joins and regressions; Global Energy Monitor infrastructure.

**Not doing:** paid real-time feeds, intraday, ML, an execution simulator, or more weighting of the failed composite.

## WRDS: what each dataset is for

*Read access tested 2026-11-24. Table listings overstate access; only a real SELECT counts.*

| Dataset | Access | Use |
|---|---|---|
| CRSP annual (`crsp.dsf_v2`) + delistings | Yes, through 2025-12-31 (old `crsp.dsf` stops 2024) | Survivorship-free universe and returns |
| Compustat NA + CRSP link | Yes, current through 2026-09/10 quarters | Historical fundamentals; EDGAR `filed` dates remain the point-in-time source |
| IBES + CRSP link | Yes, summary through 2026-08 | Analyst-based earnings surprise |
| Compustat customer segments, `seglink` | Yes, through 2026-09 | Firm-to-firm edges for the graph |
| OptionMetrics US + CRSP link | Yes, through 2025-08-29 | Historical IV, skew, variance premium |
| TAQ millisecond, 2017 | Yes (2009-16 listed, not tested) | Spread calibration; nothing for 2025 |
| Datastream futures, Compustat Snapshot, FactSet Revere, FINRA short interest, Markit CDS, CRSP monthly/quarterly | **Denied** | Do not plan on these |

No IvyDB Futures schema found, so WRDS covers no futures. CRSP ends 2025: label the 2026 yfinance extension separately.

Pull order: CRSP, Compustat, IBES, customer links, OptionMetrics. Filter on the server to energy and mining, and cache vintage-stamped parquet locally.

**Rules.** Academic, non-commercial use only. This repo is public and pushes nightly, so WRDS data never enters git, the site or live JSON; raw files stay in gitignored `data_store/wrds/`. Publish no WRDS-derived output until the university confirms the license allows it. Query through the Postgres service or WRDS Cloud, never by scripting the website. Cite WRDS and delete data when affiliation ends. The live pipeline must work without it.

## The $10/month: parked

Free data covers live curves: Yahoo serves individual contract months (10 of 10 tested on 2026-11-24, one year of daily closes each). It lacks dependable expired-contract history, open interest and futures options.

**When resumed:** Databento pay-as-you-go. `scripts/databento_futures.py` quotes by default and caps spend ($2 per request, $10 per month, $110 of the $125 credit). It is untested live, and sign-up likely starts the credit's six-month clock.

**Resume if** step 5 needs curve history from before the nightly archive, or step 4 needs futures options. Check prices, redistribution terms and publication lag first.

**Rejected:** EODHD ($19.99), Tiingo Power ($30), Massive Options Starter ($29), Databento Standard (reported $199). Massive Options Basic ($0) is the trial source (`scripts/massive_check.py`): end-of-day bars only, no open interest, quotes or IV. Buy Databento only if the trial and Yahoo leave a gap.

**Keep free:** EIA, FRED, EDGAR, CFTC, FINRA, USGS, JODI, AGSI, Comtrade, Yahoo.

## Next three actions

1. Metals demand (step 3): no timely free source. Comtrade China imports lag about two years; LME stocks are behind a bot wall. Weekly LNG feedgas likewise needs a paid source; monthly STEO stands in.
2. Reproducible trade links and STEO forecast error (finish step 3), then step 5.

*US metals added 2026-11-25:* imports by origin (Comtrade), Midwest premium (AUP futures), COMEX stocks (manual file drop: CME blocks automated downloads). Review Comtrade terms before committing `us_metals.json`.
