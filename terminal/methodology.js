/* Methodology: how every number in the terminal is built, where it comes from,
   how fresh it is and what it cannot tell you. Replaces the retired
   methodology.html (kept in retired/). Text only, no data file. */
"use strict";
(() => {
  const S = [
    ["scope", "Scope", `
      <p>Everything Energy is a research workstation for physical energy and metals. It answers four questions with free or low-cost public data:
      where a commodity is produced, processed and moved; how tight its balance is against the seasonal norm; what the futures and options
      market is pricing; and which listed energy names screen as rich or cheap.</p>
      <p>Every widget is descriptive unless it says otherwise. The only part held to a predictive standard is the equity signal engine
      (see <a data-go="signals">Equity signals</a>), and even that has thin evidence. Nothing here is investment advice.</p>
      <table class="g"><thead><tr><th class="l">Area</th><th class="l">Widgets</th><th class="l">Unit of analysis</th></tr></thead><tbody>
      <tr><td class="l">Physical map</td><td class="l">World Map, Commodity, Country, Physicals board, Concentration</td><td class="l">country x commodity, latest year</td></tr>
      <tr><td class="l">Flows</td><td class="l">Routes, Chokepoints, US Metals</td><td class="l">route or trade partner</td></tr>
      <tr><td class="l">Balances</td><td class="l">Oil Balance, Products Balance, Gas Balance, Exchange Stocks</td><td class="l">week or month vs 5-year norm</td></tr>
      <tr><td class="l">Markets</td><td class="l">Quotes, Chart, Movers, Curves, Options, Vol monitor</td><td class="l">contract or ticker, daily close</td></tr>
      <tr><td class="l">Equities</td><td class="l">Data grid: Equity signals</td><td class="l">taxonomy basket</td></tr></tbody></table>`],
    ["sources", "Data and freshness", `
      <p>Each data file carries a <code>generated_at</code> stamp and the footer shows the as-of date of what is loaded. A nightly job
      (<code>scripts/refresh_atlas.sh</code>) rebuilds the files, then <code>doctor</code> runs about 40 checks (dates advanced, series
      reconcile, no empty metals) and stops publication if one fails.</p>
      <table class="g"><thead><tr><th class="l">Source</th><th class="l">Feeds</th><th class="l">Cadence</th><th class="l">Notes</th></tr></thead><tbody>
      <tr><td class="l">EIA Weekly Petroleum Status Report</td><td class="l">US crude and products balances</td><td class="l">weekly, Wed</td><td class="l">dnav history workbooks, no key</td></tr>
      <tr><td class="l">EIA Weekly Natural Gas Storage</td><td class="l">Gas storage, 6 regions</td><td class="l">weekly, Thu</td><td class="l">Bcf, working gas</td></tr>
      <tr><td class="l">EIA Short-Term Energy Outlook</td><td class="l">Global liquids, US gas monthly balance</td><td class="l">monthly</td><td class="l">history plus forecast, flagged per month</td></tr>
      <tr><td class="l">Shanghai Futures Exchange</td><td class="l">Exchange Stocks</td><td class="l">weekly, Fri</td><td class="l">public HTML from 2025-10-31, archived once and never rewritten</td></tr>
      <tr><td class="l">UN Comtrade, USGS, COMEX</td><td class="l">US Metals</td><td class="l">monthly / daily</td><td class="l">China data lags to 2024-12</td></tr>
      <tr><td class="l">USGS MCS, EIA, WNA</td><td class="l">Physicals, map shading, concentration</td><td class="l">annual</td><td class="l">latest published year per commodity</td></tr>
      <tr><td class="l">Yahoo Finance</td><td class="l">Quotes, charts, curves, options chains</td><td class="l">daily close, delayed</td><td class="l">not point-in-time</td></tr>
      <tr><td class="l">SEC EDGAR XBRL</td><td class="l">Equity fundamentals</td><td class="l">weekly</td><td class="l">filed date = knowledge date</td></tr></tbody></table>
      <p class="mut">Licensed research data (WRDS) is used only on the local machine for back-tests. No WRDS data, or anything derived from it, is published here.</p>
      <p>Not covered by free data, so not shown: LME warehouse stocks (behind a bot challenge, not scraped), weekly LNG feedgas, and real-time prices.</p>`],
    ["norms", "Seasonal norms", `
      <p>Every "5y" figure uses the <b>same ISO week (or month) over the prior five years, current year excluded</b>. Week 53 is folded into week 52.
      A norm is shown only when at least three of the five years exist; otherwise the cell is blank rather than a guess.</p>
      <ul><li><b>vs 5y</b> = value minus the 5-year average for that week.</li>
      <li><b>5y range</b> = position between the 5-year low (0%) and high (100%); "below 5y low" and "above 5y high" mark breaks.</li>
      <li><b>vs 1y</b> = value minus the same week 364 days earlier (within 4 days for irregular weekly series).</li></ul>
      <p>Chinese series follow the Lunar New Year, which moves between late January and mid February, so ISO-week norms are loose in weeks 3 to 10.</p>`],
    ["oil", "Oil balance", `
      <p>Two balances, both reducing to <b>stock build = supply - demand + unexplained</b>.</p>
      <p><b>Global liquids (monthly, mb/d, STEO).</b> Supply and demand are split into blocs; the implied build is supply minus demand.
      The only timely observed stocks are OECD commercial inventories, so the gap between the implied global build and the observed OECD build
      is the "missing barrels" term: non-OECD stocks, oil on water and measurement error. A one-variable OLS of observed on implied shows how much
      of an implied build historically reaches OECD tanks; its residual sigma is the error band carried over the forecast months.</p>
      <p><b>US crude (weekly, mb/d, WPSR).</b> Production + imports - exports - refinery inputs is the modelled build. What is left against the
      reported stock change is EIA's adjustment (unaccounted-for crude). Watch its trend, not its level.</p>`],
    ["products", "Products balance", `
      <p>Gasoline, distillate and jet fuel from the WPSR: stocks, product supplied (implied demand), production, imports and exports, plus refinery utilisation.</p>
      <ul><li><b>Days of cover</b> = stocks / 4-week average product supplied.</li>
      <li><b>Balance bridge</b>: production + imports - exports - product supplied, against the reported stock change. The residual is blending and
      reclassification. Gasoline carries a structural residual of about -0.4 mb/d because ethanol and blending components enter outside refinery output; this is expected.</li>
      <li>Product supplied is a disappearance measure, not retail sales, so single weeks are noisy. Use the 4-week average.</li></ul>`],
    ["gas", "Gas balance", `
      <p><b>Storage.</b> Lower 48 and five regions, Bcf. The end-of-season projection adds the 5-year average weekly change for every week left in the season
      (to the last Friday on or before 31 March, or 31 October) and compares the result with the 5-year average level for that week. It assumes normal weather
      and is meant as a reference path, not a forecast.</p>
      <p><b>Monthly balance (Bcf/d, STEO).</b> Supply = dry production + supplemental + pipeline and LNG imports. Demand = consumption + LNG and pipeline exports.
      The implied change is supply minus demand; the balancing item is the reported working-gas change minus the implied change.</p>
      <p>LNG is monthly only: weekly feedgas is not in free data.</p>`],
    ["metals", "Metals and exchange stocks", `
      <p><b>SHFE weekly stocks</b>: copper, aluminium, zinc, lead, nickel and tin, tonnes. For each metal the report's Total row gives deliverable stocks and stocks
      on warrant (registered for delivery against futures). A report is archived only if this week minus last week equals the reported change for both columns;
      a report that does not reconcile is refused. Holiday weeks with no report are recorded and not retried.</p>
      <ul><li><b>Warrant share</b> = on warrant / deliverable. Sharp falls in warrants are the usual early sign of a squeeze.</li>
      <li>The archive starts 2025-10-31, so 5-year norms fill in over time.</li></ul>
      <p><b>US metals</b>: imports by origin (Comtrade; when Comtrade gives a month no world weight, the total is the sum of countries and is marked estimated), COMEX warehouse stocks (no free automated source yet: CME blocks scripted downloads) and the aluminum Midwest premium (CME AUP futures).</p>`],
    ["physicals", "Physical atlas", `
      <p>Production by country for each commodity, latest published year, from USGS Mineral Commodity Summaries, EIA and the World Nuclear Association.
      Processing (refining, smelting) is tracked separately from mining where the source splits it, because concentration often sits downstream.</p>
      <ul><li><b>Share</b> = country output / world output, both in the source unit.</li>
      <li><b>Concentration</b>: top-3 share and the Herfindahl index (sum of squared shares, 0 to 10,000). Above 2,500 is highly concentrated.</li>
      <li>"OTH" rows are the source's rest-of-world line, not a country, and are excluded from rankings.</li></ul>
      <p>Pipelines, facilities, routes and chokepoints are hand-curated reference layers, not live flows.</p>`],
    ["curves", "Futures curves", `
      <p>Each curve is the settlement of every listed contract on one date. Comparisons with the past (1 week, 1 month, 1 year ago) hold the <b>contract</b> fixed:
      no continuous front month is rebuilt, so roll gaps never appear as price moves. Curves are archived daily so history accrues from the first run.</p>
      <ul><li><b>Spread</b> = near contract minus far contract. Positive is backwardation (prompt tightness), negative is contango.</li>
      <li><b>Cracks</b>: the 3-2-1 (3 barrels of WTI into 2 of RBOB gasoline and 1 of ULSD) and the single-product cracks, in $/bbl, shown only when all three settle on the same session.</li></ul>`],
    ["options", "Options", `
      <p>All pricing goes through Black-Scholes-Merton. Time is in years, rates and dividend yield are continuously compounded, volatility is annualised.
      Implied volatility is solved from the mid price, falling back to the last trade when there is no two-sided quote. Vega is per 1.00 of vol (divide by 100 for a vol point), theta per year.</p>
      <ul><li><b>Expected move</b> = spot x IV x sqrt(T).</li>
      <li>The strategy builder values each leg with BSM at the chosen vol and date and sums them; the scenario grid shifts spot and vol together.</li>
      <li>Chains are delayed and American exercise is ignored. Treat fair values as a guide, not a quote.</li></ul>`],
    ["signals", "Equity signals", `
      <p>A point-in-time, basket-neutral composite over about 190 hand-curated energy names in 23 sub-industry baskets (<code>energy_taxonomy.yaml</code>).</p>
      <ul><li><b>Momentum 12-1</b>: total-return index TR[t-21] / TR[t-252] - 1, built from unadjusted closes plus cash dividends.</li>
      <li><b>Value</b>: EV/EBITDA from the latest 10-K, lower is better; negative EBITDA names are excluded rather than sign-flipped.</li>
      <li><b>Earnings drift (SUE)</b>: surprise against a seasonal random walk, live for about 63 trading days after filing.</li></ul>
      <p>Each component is winsorised at the 1st/99th percentile and z-scored within its basket; baskets thinner than 8 names fall back to a super-basket, then to
      the whole universe. Families are averaged, and the composite is the weight-renormalised mean of the families each name has.</p>
      <p>Fundamentals enter on their SEC filed date and restatements never rewrite history. A leakage test plants a future-dated filing and checks that no output changes.
      Every trial run is recorded in a ledger so the number of things tried is known when judging a result.</p>`],
    ["askatlas", "AskAtlas", `
      <p>AskAtlas answers questions with Claude (Opus 5.5), which reads the same published data files as the widgets through three read-only tools: list the datasets,
      describe one, and read a path inside it with filters. It cannot see anything that is not published, and it is told to cite the dataset, path and as-of date
      and to say when the data does not cover a question.</p>
      <ul><li>Access is limited to an allowlist of emails, each with a personal access code; the server stores only a hash of each code.</li>
      <li>Answers can still be wrong. Check the figures against the widget named in the Sources line.</li>
      <li>Questions and the data read are sent to Anthropic's API to produce the answer.</li></ul>`],
    ["limits", "Limitations", `
      <ul><li><b>Survivorship.</b> The equity universe is a fixed list applied to all history, so delisted names are missing and back-tested returns are biased up.</li>
      <li><b>Revisions.</b> EIA weekly data and STEO history are revised; the terminal shows the latest vintage, not what was known at the time.</li>
      <li><b>Coverage.</b> No LME stocks, no weekly LNG feedgas, Chinese trade data a year behind.</li>
      <li><b>Prices</b> are delayed daily closes from Yahoo and may have gaps or stale prints.</li>
      <li><b>Short history.</b> SHFE and the curve archive start in late 2025, so their norms are incomplete.</li>
      <li>Annual production data is one or two years old by construction.</li></ul>`],
    ["cadence", "Update cadence", `
      <table class="g"><thead><tr><th class="l">File</th><th class="l">Built by</th><th class="l">Refresh</th></tr></thead><tbody>
      <tr><td class="l">oil_balance.json (oil, products, gas)</td><td class="l">scripts/oil_balance.py</td><td class="l">nightly; new data Wed / Thu / STEO release</td></tr>
      <tr><td class="l">metals_stocks.json</td><td class="l">scripts/shfe_stocks.py</td><td class="l">nightly; new data Fri</td></tr>
      <tr><td class="l">us_metals.json</td><td class="l">scripts/us_metals.py</td><td class="l">nightly</td></tr>
      <tr><td class="l">curves.json</td><td class="l">scripts/curve_archive.py</td><td class="l">nightly, archived</td></tr>
      <tr><td class="l">market_data.json, options_data.json</td><td class="l">scripts/market_snapshot.py</td><td class="l">nightly</td></tr>
      <tr><td class="l">commodities.json</td><td class="l">scripts/build_commodities.py</td><td class="l">when a source publishes</td></tr>
      <tr><td class="l">signals_latest.json</td><td class="l">scripts/signals.py</td><td class="l">daily</td></tr></tbody></table>
      <p>Model code and this methodology are versioned together in git. The retired classic pages, including the earlier methodology, are kept in <code>retired/</code>.</p>`],
  ];
  T.app("methodology", { title: "Methodology", group: "Reference", w: 8, h: 16, open(win, arg) {
    win.add(`<div class="split" style="flex:1;min-height:0"><div class="col" style="flex:none;width:170px;background:var(--bar);overflow:auto"><div class="md-nav"></div></div>
      <div class="fill md-body" style="padding:10px 14px 24px;background:var(--paper);line-height:1.5;font-size:12px;max-width:none"></div></div>`);
    const nav = win.el.querySelector(".md-nav"), body = win.el.querySelector(".md-body");
    nav.innerHTML = S.map(([k, t], i) => `<span class="it md-it" data-k="${k}"><span class="mut">${i + 1}.</span> ${t}</span>`).join("");
    body.innerHTML = S.map(([k, t, h], i) => `<section id="md-${k}"><h3>${i + 1}. ${t}</h3>${h}</section>`).join("");
    const go = (k) => { const sec = body.querySelector(`#md-${k}`); if (!sec) return; body.scrollTop = sec.offsetTop - body.offsetTop - 4; mark(k); win.setArg(k); };
    const mark = (k) => nav.querySelectorAll(".md-it").forEach((x) => x.classList.toggle("on", x.dataset.k === k));
    nav.onclick = (e) => { const it = e.target.closest("[data-k]"); if (it) go(it.dataset.k); };
    body.onclick = (e) => { const a = e.target.closest("[data-go]"); if (a) { e.preventDefault(); go(a.dataset.go); } };
    body.onscroll = () => { let cur = S[0][0]; for (const [k] of S) { const sec = body.querySelector(`#md-${k}`); if (sec.offsetTop - body.offsetTop - 30 <= body.scrollTop) cur = k; } mark(cur); };
    win.onArg = go; setTimeout(() => (arg ? go(arg) : mark(S[0][0])), 0);
  } });
})();
