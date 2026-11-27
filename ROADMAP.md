# Roadmap

*Rewritten 2026-11-24; plan updated 2026-11-26. Full history, specs and acceptance tests: [docs/roadmap_archive.md](docs/roadmap_archive.md) (older references to Phases, Tiers and item numbers point there).*

**Goal:** an energy and metals workstation that shows the physical market, prices it, and tests whether physical state predicts related equities.

## Where it stands

- **Terminal:** shipped as PhysicalsTerminal. Map, commodity and country sheets, quotes, options, strategies, oil, products and gas balances, exchange stocks, curves, workbench, methodology, four themes. AskAtlas v1 is built and waits on deployment settings.
- **Data:** free sources, nightly. Options are ETF proxies; metals lack demand and stocks; some infrastructure is 2003-2021 vintage.
- **Signals:** the generic factor composite is frozen. It failed on free data (gross Sharpe -0.36) and the survivorship-free rerun did not certify it. Value and PEAD are dropped as standalone signals; the flipped-momentum sleeve is cancelled. Every trial is in a ledger.
- **Operations:** `doctor` runs after every nightly refresh; CI and a daily freshness check run on GitHub.

## Key changes

1. **Physical state is the product, not the factor book.** Test balances, curves, cracks and the taxonomy graph. No more factor tuning.
2. **Separate research from live.** WRDS answers historical questions locally; free data runs the terminal. Paid data is optional and parked.
3. **Gate new signals.** Log every trial, test on survivorship-free data, then go live only on gross edge that survives costs.

## Plan

*Rewritten 2026-11-26 after a review of the live terminal. Steps 1 and 2 of the 2026-11-24 plan are done; its steps 3 to 5 continue below as tracks C to E.*

| | Build | Done when |
|---|---|---|
| **A. Fill the gaps users hit** | COMEX stocks source; energy reserves and processing; processing rows for every metal | No widget tab or map mode is empty without saying why, and the main ones have data |
| **B. AskAtlas** | Deploy v1; durable quotas and audit log; real sign-in; eval set; streaming; every new source registered as a dataset | Allowlisted users get sourced answers, spend is capped, and a 20-question eval passes before each change |
| **C. Physical depth** | Reproducible trade links; STEO forecast error; metals demand | Each main commodity has balance + curve + exposed baskets |
| **D. Options** | American pricing (binomial or BAW); IV and skew history from OptionMetrics, research only | Equity and ETF options stop using a European model |
| **E. Conditional signals** | Balance, curve and crack state to baskets; event studies; customer-link graph | One clears the gate, or a null is recorded |

Order: A and B first (they are what users see), then C; E needs C. D can go any time.

### A. Fill the gaps users hit

1. **COMEX copper and aluminum stocks.** CME refuses scripted downloads (HTTP 403) and no free feed carries base-metal stocks. Options, cheapest first: keep the manual file drop; test The Vault Report's free 30-day CSV (email sign-up, copper included); CME DataMine (price unknown); MetalCharts Pro ($99/month, over budget). Do not scrape around the block.
2. **Energy reserves and processing on the map.** Crude oil, gas, coal and uranium have no reserves or processing rows, so those map modes are greyed out. Wire EIA coal reserves (already in the free INTL bulk file); add crude and gas proved reserves and refinery capacity from the OPEC Annual Statistical Bulletin (free; check terms); uranium conversion and enrichment shares from WNA.
3. **Processing for every metal.** Cobalt, lithium, nickel, zinc, graphite, rare earths and others list only China. Add the next producers from USGS chapters and IEA critical minerals data.
4. **Map follows the theme.** The map keeps its own Light/Dark button; default it to the page theme.
5. **New widgets reach saved layouts.** Users with a saved layout only see new tabs that are pinned. Add a "What's new" note that offers to add new widgets.

### B. AskAtlas

v1 is built (`api/ask.mjs`, widget `terminal/ask.js`, tab AskAtlas): Claude Opus 5.5 reads the published data files through three read-only tools and must cite the dataset, path and as-of date. Access is an allowlist of emails, each with a personal access code stored only as a hash.

1. **Deploy.** Set `ANTHROPIC_API_KEY` and `ASKATLAS_USERS` in Vercel (entries from `node scripts/askatlas_user.mjs name@example.com`), then ask five known questions on the preview URL.
2. **Durable limits and audit log.** The hourly limit is per server instance only. Move it to a store (Vercel KV, Upstash or Supabase), add a monthly spend cap per user, and log question, tools read and tokens.
3. **Real sign-in.** Replace access codes with Sign in with Google (Auth.js or Clerk), keeping the email allowlist.
4. **Eval set.** 20 questions whose answers are fixed by the data files (distillate cover, L48 storage vs 5y, SHFE copper change, copper refining leaders...). Run it before changing the prompt, tools or model.
5. **Better answers.** Stream text as it arrives; make each Sources line a link that opens the widget at that dataset; add a computation tool (code execution) for regressions and spreads.
6. **Grows with the data.** Every new source gets a `DATASETS` entry with a one-line description, so AskAtlas can read it the day it lands. For long multi-step research, consider Claude Managed Agents with a sandbox that loads the files into Python.
7. **Match the reference.** The user pointed at "ima-research" as the model for this; it was not found locally, on GitHub or on Vercel on 2026-11-26. Compare once located.

*Done 2026-11-26:* themes (classic and modern, light and dark), retired classic pages kept in `retired/`, Methodology widget, PhysicalsTerminal name, resize from any side, no-outage default, import shares fixed (Comtrade months with no world weight), aluminum premium labelled, empty map modes greyed out, AskAtlas v1.

*Done earlier:* SHFE exchange stocks, US gas balance, US products balance, curve archive and Curves widget, `doctor` + CI, trial ledger, WRDS access test, CRSP/Compustat panel, composite rerun, Databento loader (offline only).

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

1. Deploy AskAtlas (B1): add the two Vercel environment variables and run five known questions.
2. Energy reserves and refinery capacity (A2): EIA coal reserves first, then the OPEC bulletin after a terms check.
3. Pick the COMEX stocks route (A1): test the free Vault Report CSV; otherwise keep the manual drop.
