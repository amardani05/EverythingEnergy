// AskAtlas core: who may ask, which data Claude may read, and how it reads it.
// Kept apart from the HTTP handler (api/ask.mjs) so tests can run it with no network.
// Files under api/ that start with "_" are not deployed as their own endpoints.

import { createHash, timingSafeEqual } from "node:crypto";
import { readFile } from "node:fs/promises";
import path from "node:path";

/* ---------- datasets: everything the terminal publishes, nothing else ---------- */
export const DATASETS = {
  commodities: { file: "commodities.json", about: "World supply chains for 25 commodities: production, reserves, processing (refining/smelting) and consumption by country with world shares, HHI inputs, trade routes, key inputs, end uses. Latest published year (USGS MCS, EIA international, WNA)." },
  oil_balance: { file: "oil_balance.json", about: "Balances. Global liquids monthly (EIA STEO, mb/d, history + forecast, implied vs OECD builds); US crude weekly (WPSR); products (gasoline, distillate, jet: stocks, demand, days of cover, 5y norms); gas (storage by region vs 5y, end-of-season projection, STEO monthly balance, LNG, power burn)." },
  metals_stocks: { file: "metals_stocks.json", about: "Shanghai Futures Exchange weekly warehouse stocks, tonnes, for copper, aluminium, zinc, lead, nickel, tin: deliverable and on-warrant, weekly changes. Archive starts 2025-10-31." },
  us_metals: { file: "us_metals.json", about: "US monthly imports of refined copper (HS 7403) and unwrought aluminum (HS 7601) by origin from UN Comtrade, tonnes and USD; aluminum Midwest premium (CME AUP futures). COMEX stocks may be empty." },
  curves: { file: "curves.json", about: "Futures curves (settlements by contract) for energy and metals with 1 week / 1 month / 1 year comparisons, calendar spreads and 3-2-1 cracks." },
  market: { file: "market_data.json", about: "Daily price history and summary stats for futures, ETFs and indices (Yahoo, delayed closes): last, 1d/1m/YTD changes, realised vol." },
  options: { file: "options_data.json", about: "Delayed option chains for commodity ETFs and futures proxies: strikes, bids/asks, implied vols, open interest, by expiry." },
  flows: { file: "flows_data.json", about: "Curated trade routes and chokepoints for crude, products, LNG, pipeline gas, coal, uranium and metals, with volumes and shares." },
  signals: { file: "signals_latest.json", about: "Latest equity signal ranks for the energy universe: composite and family scores (momentum, value, earnings drift) by name and basket." },
  backtests: { file: "backtests.json", about: "Walk-forward backtest summaries for the equity signals (IC, returns, turnover). Pre-cost unless stated." },
  facilities: { file: "infra_facilities.geojson", about: "Mapped facilities (refineries, LNG terminals, mines, smelters, enrichment...) with country, capacity, operator and status. Use a path like features and filter with where." },
};

const MAX_CHARS = 14000;

/* ---------- access: an allowlist of emails, each with its own access code ----------
   ASKATLAS_USERS is JSON: {"name@example.com": "<sha256 hex of that person's code>"}.
   Codes never sit in the repo or the environment in plain text. */
export function loadUsers(env = process.env) {
  try { const u = JSON.parse(env.ASKATLAS_USERS || "{}"); return Object.fromEntries(Object.entries(u).map(([k, v]) => [k.trim().toLowerCase(), String(v).toLowerCase()])); }
  catch { return {}; }
}
export const sha256 = (s) => createHash("sha256").update(String(s), "utf8").digest("hex");
export function allowed(users, email, code) {
  const want = users[String(email || "").trim().toLowerCase()];
  if (!want || !code) return false;
  const a = Buffer.from(sha256(code), "hex"), b = Buffer.from(want, "hex");
  return a.length === b.length && timingSafeEqual(a, b);
}

/* ---------- data access ---------- */
export function makeLoader({ dir, origin, fetchImpl = fetch }) {
  const cache = new Map();
  return async (name) => {
    const d = DATASETS[name]; if (!d) throw new Error(`unknown dataset "${name}". Call list_datasets.`);
    if (!cache.has(name)) cache.set(name, (async () => {
      if (dir) return JSON.parse(await readFile(path.join(dir, d.file), "utf8"));
      const r = await fetchImpl(new URL("/" + d.file, origin)); if (!r.ok) throw new Error(`${d.file}: HTTP ${r.status}`); return r.json();
    })());
    return cache.get(name);
  };
}

/* Path language, kept small: dot-separated keys; on an array, a number picks an index and
   key=value picks the first element whose key equals value, e.g. commodities.id=copper.producers */
export function navigate(root, p) {
  let cur = root; const trail = [];
  for (const seg of String(p || "").split(".").filter(Boolean)) {
    if (Array.isArray(cur)) {
      const m = seg.match(/^([^=]+)=(.*)$/);
      if (m) { const hit = cur.find((x) => x && String(x[m[1]]) === m[2]); if (!hit) throw new Error(`no element with ${m[1]}=${m[2]} at ${trail.join(".") || "(root)"}`); cur = hit; }
      else if (/^-?\d+$/.test(seg)) { const i = +seg < 0 ? cur.length + +seg : +seg; if (!(i in cur)) throw new Error(`index ${seg} out of range at ${trail.join(".") || "(root)"} (length ${cur.length})`); cur = cur[i]; }
      else throw new Error(`"${seg}" applied to an array at ${trail.join(".") || "(root)"}: use an index or key=value`);
    } else if (cur && typeof cur === "object") {
      if (!(seg in cur)) throw new Error(`no key "${seg}" at ${trail.join(".") || "(root)"}; keys: ${Object.keys(cur).slice(0, 40).join(", ")}`);
      cur = cur[seg];
    } else throw new Error(`cannot go into ${typeof cur} at ${trail.join(".")}`);
    trail.push(seg);
  }
  return cur;
}

/* outline of a value: keys, types and lengths, so Claude can drill down instead of reading everything */
export function outline(v, depth = 2) {
  if (Array.isArray(v)) return v.length && depth > 0 && v[0] && typeof v[0] === "object" ? { array: v.length, first: outline(v[0], depth - 1) } : `array(${v.length})`;
  if (v && typeof v === "object") { if (depth <= 0) return `object(${Object.keys(v).length} keys)`; return Object.fromEntries(Object.entries(v).slice(0, 60).map(([k, x]) => [k, outline(x, depth - 1)])); }
  return v === null ? "null" : typeof v === "string" && v.length > 80 ? `string(${v.length})` : v;
}

/* project an array of rows: filter with where, keep fields, take the last or first n */
export function select(v, { where, fields, limit = 50, from = "end" } = {}) {
  if (!Array.isArray(v)) return v;
  let rows = v;
  if (where && typeof where === "object") rows = rows.filter((r) => r && Object.entries(where).every(([k, x]) => String(r[k] ?? (r.properties || {})[k]) === String(x)));
  const total = rows.length; rows = from === "start" ? rows.slice(0, limit) : rows.slice(-limit);
  if (fields && fields.length) rows = rows.map((r) => (r && typeof r === "object" ? Object.fromEntries(fields.map((f) => [f, r[f] ?? (r.properties || {})[f]])) : r));
  return total > rows.length ? { total, returned: rows.length, from, rows } : rows;
}

export function clip(v) {
  const s = JSON.stringify(v);
  if (s.length <= MAX_CHARS) return s;
  return JSON.stringify({ note: `Too large to return whole (${s.length} chars). Here is its outline; read a narrower path, or use where/fields/limit.`, outline: outline(v, 3) }).slice(0, MAX_CHARS);
}

/* the tools, as plain functions; api/ask.mjs wraps them for the SDK's tool runner */
export function tools(load) {
  return {
    async list_datasets() {
      return JSON.stringify(Object.entries(DATASETS).map(([name, d]) => ({ name, about: d.about })));
    },
    async describe_dataset({ dataset, path: p = "" }) {
      const v = navigate(await load(dataset), p);
      const meta = (await load(dataset)).meta;
      return clip({ dataset, path: p || "(root)", meta: p ? undefined : meta, outline: outline(v, 3) });
    },
    async read_dataset({ dataset, path: p = "", where, fields, limit, from }) {
      const v = navigate(await load(dataset), p);
      return clip(select(v, { where, fields, limit: Math.min(Math.max(+limit || 50, 1), 500), from }));
    },
  };
}

export const SYSTEM = `You are AskAtlas, the research assistant inside PhysicalsTerminal, a workstation for physical energy and metals markets.

Answer from the terminal's own datasets, which you read with the tools. Start with list_datasets if you are unsure where something lives, use describe_dataset to see a dataset's shape, then read_dataset with a narrow path. Read before you state a number.

How to answer:
- Lead with the answer, then the numbers behind it. Be dense and specific: units, dates, and the as-of date of the data you used.
- Say which dataset and path each figure came from, in a short "Sources" line at the end (for example: oil_balance gas.storage.summary.l48, as of 2026-11-20).
- If the data does not cover the question, say so plainly and name what source would. Never fill a gap from memory and present it as data; label any general background as such.
- Compare with the 5-year norm or a year ago when the data has it; that is how the terminal reads every balance.
- Markdown is fine: short paragraphs, bullet lists, small tables.
- This is a research tool. Do not give personalised investment advice or tell anyone to buy or sell.

Conventions in the data: 5y norms are the same ISO week (or month) over the prior five years, current year excluded. Oil balances are in mb/d, gas storage in Bcf, gas balance in Bcf/d, SHFE stocks in tonnes. Commodity shares are fractions (0.23 = 23%). "OTH" is the source's rest-of-world line, not a country.`;
