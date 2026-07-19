# signal_engine — Improvement Roadmap

*Drafted 2026-07-16 against `main2` @ `02124ca`. Evidence cited as `file:line`.*

---

## 0. Where things stand

The repo holds two disjoint systems: the legacy **IMA Energy Atlas** dashboard (works, manual, print-based, May-era) and the new **`signal_engine`** (the future: bitemporal DuckDB store, PIT-correct factors, 39 green tests). The engine's factor layer is genuinely solid — but it currently **dead-ends in both directions**: the fundamentals data it needs was never loaded, and everything downstream of raw factor values doesn't exist yet.

| Layer | State | Evidence |
|---|---|---|
| PIT store (bitemporal DuckDB) | ✅ Designed & tested | `data/store.py:41-269`, `tests/test_store_pit.py` |
| Prices | ⚠️ Loaded but stale + flawed | 1.53M rows, yfinance-only, max date **2026-06-23**; dividends silently dropped (`data/prices.py:80`) |
| EDGAR fundamentals | ❌ **Empty** | `edgar_facts` = 0 rows. Ingest crashed at ~650/766 CIKs on 6/23; fix landed 6/25 (`02124ca`) but **never re-run**; 0 `companyfacts_*.json` on disk |
| Universe (IJR) | ⚠️ One snapshot (2026-06-12) | `ijr_pit` mode unusable; launchd job never installed (no `data_store/logs/`) |
| Macro (FRED/EIA), FINRA | ❌ Clients only / stub | No ingest script; `macro_series` = 0 rows; `supplements/finra.py:22` raises `NotImplementedError` |
| Factors (V/Q/M/PEAD) | ✅ Implemented, PIT-correct, unit-tested | `factors/*.py` — but called **only from tests** |
| Scoring / composite | ❌ Does not exist | `scoring/__init__.py` = 0 bytes; config spec at `config.yaml:37-44` is dead config |
| Validation | ⚠️ IC primitive only | `validation/ic.py`; `validation.backtest` referenced at `ic.py:17` but missing |
| Backtester | ❌ Does not exist | No portfolio construction, costs, turnover, or walk-forward anywhere |
| Output / reporting | ❌ Does not exist | `output/__init__.py` = 0 bytes; `data_store/snapshots/` never created |
| CLI / orchestration | ❌ None | No `[project.scripts]`, no Makefile, no runner — zero→signals is currently impossible |
| Docs / CI | ❌ README covers only the legacy dashboard; no CI | `README.md` never mentions the engine; no `.github/` |

**One-sentence diagnosis:** a well-built engine block with no fuel line (data), no transmission (scoring→backtest), and no steering wheel (CLI) — the roadmap is mostly *connecting* what exists, plus a handful of correctness fixes that are cheap now and expensive later.

---

## Phase 0 — Restore the data spine ✅ DONE (2026-07-17)

*Goal: every table populated, fresh, and refreshable by cron — because nothing downstream is testable against reality until this is done.*

- **0.1 Fix, then re-run the EDGAR ingest end-to-end.** ✅ Done. Hardened `scripts/ingest_edgar.py`: fetch+cache+parse now all inside the per-CIK `try`; incremental flushes every 50 CIKs; new `--from-cache` re-parse path (`edgar.iter_cached_payloads`) rebuilds from raw JSON with no HTTP, stamping `snapshot_date` from the cache file's mtime. Live re-run landed **1,325,958 fact rows across 756 CIKs = 98.4% of the 768 submitted CIKs** (4 CIKs 404'd at SEC — genuinely delisted/renamed). Core inputs present for 733–752 CIKs. Acceptance met.
- **0.2 Refresh prices — and stop dropping dividends while the pipes are open.** ✅ Done. `parse_yf_history` now returns `(prices, corporate_actions)`; new `corporate_actions` table (PK `ticker,date,kind,source`) in `store.py`. Full re-pull: prices current **through 2026-07-17** (769 tickers, 1.54M rows), **14,936 dividends across 517 tickers + 97 splits across 79 tickers**. Verified: XOM/CVX/KMI each 34 quarterly dividends since 2018. Total-return math unblocked.
- **0.3 Install the automation that already exists.** ✅ Done. Created `data_store/logs/`, added `com.signalengine.prices.plist` (nightly 21:00) and `com.signalengine.edgar.plist` (weekly Sun 09:00) alongside the existing IJR plist, and `launchctl load`ed all three — `launchctl list | grep signalengine` now shows all three staged. *(IJR still needs the manual browser CSV drop; automating detection is Phase 3.8.)*
- **0.4 Baseline IC scorecard on real data.** ✅ Done. New `scripts/baseline_ic.py` runs momentum (daily panel), value + quality (month-end grid from 2019), and PEAD (SUE broadcast 63d) through the IC harness and writes `docs/baselines.md`. See the report for numbers.
- **0.5 (emergent) Fix all-None SUE `Null`-dtype crash.** ✅ Done. The baseline surfaced a real pre-existing bug: `compute_sue_series` inferred a `Null` dtype for `sue` when a company's same-quarter EPS never varied (`pstdev == 0` → every `sue = None`), crashing `pl.concat` across tickers inside `compute_sue` with `SchemaError: type Float64 is incompatible with expected type Null`. Fixed at the source (cast to a declared `_SUE_SERIES_SCHEMA` on every return path); 2 regression tests added (`test_pead.py`). The synthetic fixtures never hit it because they always produced varied EPS.

**Phase 0 residue carried forward:** the value/quality baseline takes ~15 min each because `compute_value`/`compute_quality` issue per-ticker DuckDB queries (≈602 tickers × 91 grid dates × several queries). Vectorizing the snapshot fetch is folded into Phase 1 (the scoring layer needs a batched as-of fundamentals read anyway). Pre-existing repo-wide `ruff` debt (B008 on frozen-dataclass defaults, a few F541/I001 in older tests) is left for Phase 3.6's CI cleanup — this work added none.

---

## Phase 1 — Signal generation: wire the missing middle (≈1 week)

*Goal: `signals --as-of 2026-07-15` produces a ranked, sector-neutral composite cross-section — the config spec at `config.yaml:17-44` finally executes.*

- **1.1 Build `scoring/`** (currently 0 bytes) exactly to the spec already written in config: winsorize [1%, 99%] → cross-sectional z **within sector** → sign conventions (flip accruals) → equal-weight composite over enabled signals. Wire `data/sectors.py` (`sic_to_sector` exists but nothing calls it) via `edgar_submissions.sic`. Emit both raw and sector-relative variants (`emit_sector_relative: true`).
- **1.2 Make config real.** ✅ Done. `scoring/composite.py` reads `signals.selection` (enabled/weight/components), `scoring.winsorize_pct`, `emit_sector_relative`, and `universe.min_price_history_days`. Per baselines, config now enables **momentum + value(`ev_ebitda` only) + pead**; quality stays off pending neutralization evidence.
- **1.3 Known factor-correctness fixes:** ✅ Done (first two; third folded in).
  - **SUE Q4 derivation** — `quarterly_eps_series` now derives Q4 = FY − ΣQ1..Q3 from the 10-K's annual EPS, `filed` = the 10-K date, `derived=True` flag, partial years skipped. 3 PIT-shaped tests. Live effect: PEAD coverage in the composite rose 61 → 92 names.
  - **Total-return momentum** — `compute_momentum(dividends=...)` builds a per-ticker TR index from unadjusted closes + cash dividends (new `store.as_of_corporate_actions` PIT accessor); identical to price momentum when no dividends. **Bonus live-data bug found & fixed:** `momentum_as_of`'s panel window treated 252 *trading* days as 252 *calendar* days, so on real 5-day weeks the 252-row shift never filled and every name silently got null momentum — masked in tests by contiguous-calendar fixtures. Window now scales by 7/5; weekday-only regression test added.
  - `shares_outstanding` coalesce deferred: `latest_annual_snapshot` reads the dei chain; the us-gaap fallback merge moves to the Phase 2 vectorized fundamentals read.
- **1.4 Statistical hygiene:** ✅ Done. `newey_west_t` (Bartlett kernel, lag = horizon) in `validation/ic.py`; `IcSummary` now carries `t_stat_nw` beside the iid t, and the baseline report prints both. Test asserts NW cuts inflated |t| by >2× on a 21-day-overlap series.
- **1.5 Build `output/`:** ✅ Done. `output/snapshots.py`: parquet snapshot per run into `data_store/snapshots/`, diff vs the latest prior snapshot (top-decile entrants/exits, rank moves ≥ threshold) rendered to markdown.
- **1.6 Leakage canary through scoring:** ✅ Done. `tests/test_scoring.py::test_composite_pit_canary` plants absurd fundamentals filed *after* as_of and asserts the entire composite frame is byte-identical — PIT holds through winsorize/z/composite, not just at the store.
- **Acceptance:** ✅ Met. `scripts/signals.py [--as-of]` emits the ranked composite (381/600 ranked at 2026-07-17), writes snapshot + rendered diff, prints the family Spearman-correlation matrix (value↔momentum −0.12, momentum↔pead +0.23 — genuine diversification) and per-family coverage.

**Phase 1 residue carried forward:** `compute_value`/`compute_quality` still query per-ticker (single-date composite ≈ 13s — fine for the daily runner; the walk-forward backtest needs the vectorized batched as-of read, now a Phase 2.1 prerequisite). Quality family stays disabled until sector-neutralized IC evidence says otherwise.

---

## Phase 2 — The backtester (≈2–3 weeks; the centerpiece)

*Goal: `validation/backtest.py` — the module `ic.py:17` promises — turns signal panels into net-of-cost walk-forward portfolio returns with a report you'd trust.*

- **2.1 Walk-forward driver.** Rebalance calendar from config (`rebalance_freq: B`, `min_train_days: 504`), PIT universe evaluated *at each rebalance date* (membership mode + `min_price_history_days`), signals recomputed strictly as-of. Every output row carries the `survivorship_clean` flag the config promises.
- **2.2 Portfolio construction.** Two standard forms first: (a) decile long-short (spread monotonicity is the sanity check), (b) long-only top-decile with the config's hysteresis (enter at top 10%, exit below top tertile — `enter_decile`/`exit_tertile` are specified and unread). Equal weight within bucket; sizing/liquidity stays out of v1 per the config note.
- **2.3 Turnover + cost model.** Per-rebalance turnover accounting; costs = `spread_bps + impact_bps_per_pct_adv` from config; report gross *and* net always. Then replace the placeholder 60bps flat spread with an OHLC-based estimator (Corwin–Schultz or high-low) — a flat spread on S&P 600 names will misrank strategies by turnover.
- **2.4 Metrics & report.** Net/gross CAGR, vol, Sharpe, max drawdown, hit rate; per-year table; decile-spread chart; IC-decay curve; factor exposure/attribution of the composite; benchmark vs equal-weight universe and IJR total return. Write to `data_store/snapshots/backtests/<run_id>/`.
- **2.5 Portfolio-level leakage canary.** Same poisoned-signal trick at the backtest level: a future-peeking signal must produce an absurd Sharpe, and a test asserts the harness detects/flags it. IC-level canaries don't protect portfolio plumbing (entry timing, rebalance joins).
- **2.6 Statistical guardrails baked into the report:** Newey–West on mean returns, IC t-stats per 1.4, and a standing multiple-testing note (count of configurations tried per family — see 4.6). The report should make it *hard* to fool yourself.
- **Acceptance:** momentum 12-1 decile backtest, net of costs, 2019→present, reproducible from one command; poisoned-signal test red-teams it; results within sanity range of published small-cap momentum.

---

## Phase 3 — Usability & automation (parallel with 1–2; ≈1 week of focused work)

*Goal: one CLI, one README, CI, and a data layer that maintains itself.*

- **3.1 Single CLI** via `[project.scripts]` (none exists — no `entry_points.txt` in egg-info): `se ingest [prices|edgar|ijr|macro|all]`, `se signals --as-of`, `se backtest`, `se report`, `se doctor`. Kill the "five hand-typed `.venv/bin/python scripts/...` invocations" workflow.
- **3.2 `se doctor` — data QA in one command:** staleness per table (prices are 3.5 weeks old *right now* and nothing says so), per-concept EDGAR coverage, price sanity (zero/negative closes, gaps, zero-volume runs), IJR snapshot cadence gaps, skipped-ticker report persisted to a table instead of log lines (`data/prices.py:119-121`). Non-zero exit on failure so launchd/CI can alert.
- **3.3 Incremental ingest.** Everything is full-history re-pull today (`ingest_prices.py:80`; full `companyfacts` per CIK). Add watermarks (`max(date)` / `max(filed)` per entity) and pull deltas. Cuts the nightly window from ~20 min to seconds and reduces yfinance ban exposure.
- **3.4 Fix the price-revision trap.** `ON CONFLICT DO NOTHING` (`ingest_prices.py:66-67`) means a re-split/re-adjusted yfinance history can never correct stored rows. Either upsert-with-replace on re-pull windows, or add a proper knowledge_date to prices (consistent with the store's bitemporal philosophy). Decide explicitly; today's behavior is the worst default silently.
- **3.5 Retry/backoff on all HTTP clients** (none today — failures are caught and skipped: `prices.py:113-118`, `ingest_edgar.py:85-87,116-118`): exponential backoff, honor 429/`Retry-After`, then persist the failure to the skip table.
- **3.6 CI + pre-commit:** GitHub Actions running `ruff + mypy + pytest` (all three exist, all pass, nothing enforces them); extend mypy beyond `files=["signal_engine"]` to scripts and tests.
- **3.7 README rewrite.** Current README is 100% the legacy dashboard and its quickstart is broken (`pip install yaml` — the package is `pyyaml`, `README.md:13`). Document the two-pipeline reality, the engine quickstart (including the manual IJR download step and the cold-start "~20 min + one browser download" expectation), and move legacy dashboard docs to their own section/file.
- **3.8 De-risk the IJR manual loop.** Akamai blocks automation, so at minimum: `se doctor` flags snapshot gaps, launchd job complains loudly (notification) when no fresh CSV appeared in N days, and the drop-folder workflow is documented. Investigate alternate free S&P 600 membership sources for backfill/cross-check.
- **3.9 Config/env hygiene:** `.env.example` advertises `EDGAR_CONTACT_EMAIL` but config.yaml hardcodes the email and nothing reads `.env` (`.env.example` header admits this); pick one mechanism. Portable launchd plists (no hardcoded `/Users/amardani`).

---

## Phase 4 — Expansion & robustness (after the Phase 2 gate exists)

*Rule: no new signal enters until it passes the Phase 2 harness. That's the point of building the harness first.*

- **4.1 Macro/commodity-beta sleeve.** The missing `scripts/ingest_macro.py` (FRED/EIA clients exist, `macro_series` is empty), then the `commodity_beta` signal from `atlas/clusters.py` (the "step 4" residualization promised at `clusters.py:5`) — ranked as its own sleeve, not in the composite, per config.
- **4.2 FINRA short interest** — replace the stub (`supplements/finra.py:22`), semi-monthly bitemporal ingest, squeeze/crowding signal candidate.
- **4.3 Price-source redundancy.** Wire Stooq (or another source) as the cross-check `as_of_prices` was designed for (`store.py:228-234` has nothing to compare today); reconcile disagreements > tolerance into a QA table; verify split adjustments instead of trusting yfinance blindly.
- **4.4 Security master.** Prices are keyed by ticker string with a current-only ticker↔CIK map (`edgar.py:191-202`) — renames orphan history. Move to CIK-anchored identity with a ticker-history table; map sector-label mismatches (`InfoTech` vs iShares' `Information Technology`).
- **4.5 Survivorship: from flagged to fixed.** Accumulate IJR snapshots (0.3) until `ijr_pit` is usable going forward; investigate historical S&P 600 constituent backfill; add delisting handling (delisted names currently vanish silently — `prices.py:107-121`) with a terminal-return convention.
- **4.6 Experiment tracking.** Every backtest run writes a manifest: config hash, git SHA, data watermarks, and the family-wise count of variants tried. This is the cheap version of "don't p-hack yourself" and makes results citable later.
- **4.7 Fold the Energy Atlas in** (the pyproject description already promises this): pair/residual z-scores from the legacy pipeline become engine features; eventually the dashboard reads engine snapshots instead of its own parallel CSV pipeline. Until then: freeze the legacy code, don't refactor it.

---

## Sequencing logic

1. **Phase 0 before everything** — three of four factors have literally zero data; any signal work before the re-ingest is theater.
2. **Scoring before backtester** — the backtester consumes the composite; building it against raw single factors means rebuilding its input contract a week later.
3. **Correctness fixes (dividends, Q4-SUE, NW t-stats) before results accumulate** — every week they wait, more conclusions get anchored on biased numbers.
4. **Backtester before new signals** — the harness is the filter; adding FINRA/macro signals first just grows the pile of unvalidated ideas.
5. **Usability is parallel, not last** — the CLI/doctor/CI items are what make Phases 0–2 stick; do them as you touch each area.

## Explicitly deferred (don't build yet)

- Position sizing / liquidity floors (config says removed in v1; revisit only when a sizing layer needs spreads/ADV).
- Intraday anything, options, ML models — the cross-sectional daily spine isn't proven yet.
- Legacy dashboard refactor — freeze until 4.7.
- Stooq paid bulk access — only if 4.3 shows yfinance is materially wrong.
