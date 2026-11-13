// node tests/test_bs.mjs  (also run by tests/test_bs_js.py under pytest)
import { createRequire } from "module";
import assert from "assert";
const BS = createRequire(import.meta.url)("../terminal/bs.js");
const near = (a, b, tol, msg) => assert.ok(Math.abs(a - b) <= tol, `${msg}: ${a} vs ${b}`);

// Hull, Options Futures and Other Derivatives: S=42 K=40 r=10% sigma=20% T=0.5 -> c=4.76 p=0.81
near(BS.price(42, 40, 0.5, 0.10, 0, 0.20, "c"), 4.7594, 1e-3, "Hull call");
near(BS.price(42, 40, 0.5, 0.10, 0, 0.20, "p"), 0.8086, 1e-3, "Hull put");
// cdf reference values
near(BS.cdf(0), 0.5, 1e-15, "cdf0"); near(BS.cdf(1.96), 0.9750021048517795, 1e-12, "cdf1.96"); near(BS.cdf(-3), 0.0013498980316301, 1e-13, "cdf-3");

// put-call parity with a dividend yield, across a grid
for (const S of [50, 100, 150]) for (const K of [60, 100, 140]) for (const T of [0.02, 0.5, 2]) for (const s of [0.1, 0.5, 1.2]) {
  const r = 0.04, q = 0.02, c = BS.price(S, K, T, r, q, s, "c"), p = BS.price(S, K, T, r, q, s, "p");
  near(c - p, S * Math.exp(-q * T) - K * Math.exp(-r * T), 1e-9, `parity S${S} K${K} T${T} s${s}`);
}

// greeks against central finite differences
{ const [S, K, T, r, q, s] = [100, 105, 0.4, 0.04, 0.01, 0.35];
  for (const ty of ["c", "p"]) { const g = BS.greeks(S, K, T, r, q, s, ty), f = (S_, s_, T_) => BS.price(S_, K, T_, r, q, s_, ty), h = 1e-3;
    near(g.delta, (f(S + h, s, T) - f(S - h, s, T)) / (2 * h), 1e-6, ty + " delta");
    near(g.gamma, (f(S + h, s, T) - 2 * f(S, s, T) + f(S - h, s, T)) / (h * h), 1e-5, ty + " gamma");
    near(g.vega, (f(S, s + h, T) - f(S, s - h, T)) / (2 * h), 1e-5, ty + " vega");
    near(g.theta, -(f(S, s, T + h) - f(S, s, T - h)) / (2 * h), 1e-5, ty + " theta");
    const d = (s_) => BS.greeks(S, K, T, r, q, s_, ty).delta, v = (s_) => BS.greeks(S, K, T, r, q, s_, ty).vega;
    near(g.vanna, (d(s + h) - d(s - h)) / (2 * h), 1e-5, ty + " vanna");
    near(g.volga, (v(s + h) - v(s - h)) / (2 * h), 1e-4, ty + " volga"); } }

// implied vol round-trips, including wings and short dates; out-of-band prices return null
for (const s of [0.08, 0.3, 0.9, 2.5]) for (const K of [70, 100, 140]) for (const T of [5 / 365, 0.25, 1.5]) for (const ty of ["c", "p"]) {
  const px = BS.price(100, K, T, 0.04, 0.0, s, ty);
  if (px < 1e-6) continue;                       // no information in a sub-cent price
  const iv = BS.impliedVol(px, 100, K, T, 0.04, 0.0, ty);
  if (BS.greeks(100, K, T, 0.04, 0, s, ty).vega < 1e-4) continue;
  assert.ok(iv != null, `iv null s${s} K${K} T${T} ${ty}`); near(iv, s, 1e-5, `iv s${s} K${K} T${T} ${ty}`);
}
assert.strictEqual(BS.impliedVol(0.5, 100, 90, 0.25, 0.04, 0, "c"), null, "below intrinsic -> null");
assert.strictEqual(BS.impliedVol(101, 100, 90, 0.25, 0.04, 0, "c"), null, "above spot -> null");
assert.strictEqual(BS.impliedVol(0, 100, 90, 0.25, 0.04, 0, "c"), null);

// expiry: price collapses to intrinsic, greeks stay finite
near(BS.price(110, 100, 0, 0.04, 0, 0.3, "c"), 10, 1e-12, "expiry call"); near(BS.price(110, 100, 0, 0.04, 0, 0.3, "p"), 0, 1e-12, "expiry put");
assert.ok(Object.values(BS.greeks(110, 100, 0, 0.04, 0, 0.3, "c")).every(Number.isFinite));

// strategies
const o = { r: 0.04, q: 0, days: 0 }, T = 30 / 365, iv = 0.4, S0 = 100;
const leg = (type, k, qty, t = T) => ({ type, k, T: t, qty, iv, mult: 100, entry: type === "u" ? S0 : BS.price(S0, k, t, o.r, o.q, iv, type) });
{ // long straddle: zero P&L at entry, V shape at expiry, breakevens at K +/- premium, positive gamma, negative theta
  const st = [leg("c", 100, 1), leg("p", 100, 1)], prem = st[0].entry + st[1].entry;
  near(BS.pnl(st, S0, o), 0, 1e-9, "straddle entry pnl");
  const exp = { ...o, days: BS.horizonDays(st) }; assert.strictEqual(exp.days, 30);
  near(BS.pnl(st, 100, exp), -100 * prem, 1e-6, "straddle max loss"); near(BS.pnl(st, 130, exp), 100 * (30 - prem), 1e-6, "straddle wing");
  const be = BS.breakevens(BS.curve(st, exp, 60, 140, 801)); assert.strictEqual(be.length, 2);
  near(be[0], 100 - prem, 0.02, "be lo"); near(be[1], 100 + prem, 0.02, "be hi");
  const g = BS.bookGreeks(st, S0, o); assert.ok(g.gamma > 0 && g.theta < 0 && g.vega > 0);
  assert.ok(BS.tails(st, o, S0).upSlope > 0); }
{ // call spread is bounded both sides; covered call = short put by parity at expiry
  const cs = [leg("c", 100, 1), leg("c", 110, -1)], exp = { ...o, days: 30 }, debit = cs[0].entry - cs[1].entry;
  near(BS.pnl(cs, 200, exp), 100 * (10 - debit), 1e-6, "call spread max"); near(BS.pnl(cs, 50, exp), -100 * debit, 1e-6, "call spread min");
  assert.strictEqual(BS.tails(cs, o, S0).upSlope, 0);
  const cc = [{ type: "u", qty: 1, mult: 100, entry: S0 }, leg("c", 105, -1)], sp = [leg("p", 105, -1)];
  for (const S of [80, 105, 130]) near(BS.pnl(cc, S, exp) - BS.pnl(sp, S, exp), BS.pnl(cc, 80, exp) - BS.pnl(sp, 80, exp), 1e-6, "covered call ~ short put"); }
{ // calendar: at front expiry the back leg is still alive and valued by model
  const cal = [leg("c", 100, -1, 30 / 365), leg("c", 100, 1, 90 / 365)], exp = { ...o, days: BS.horizonDays(cal) };
  assert.strictEqual(exp.days, 30);
  const peak = BS.pnl(cal, 100, exp), wing = BS.pnl(cal, 140, exp); assert.ok(peak > 0 && wing < 0 && peak > wing, "calendar peaks at strike");
  near(BS.legValue(cal[1], 100, exp), BS.price(100, 100, 60 / 365, o.r, 0, iv, "c"), 1e-9, "back leg has 60d left"); }
{ // vol shift: +5 points on a long straddle gains about vega * 0.05
  const st = [leg("c", 100, 1), leg("p", 100, 1)], v = BS.bookGreeks(st, S0, o).vega;
  near(BS.pnl(st, S0, { ...o, volAdd: 0.05 }), v * 0.05, Math.abs(v * 0.05) * 0.03, "vega pnl"); }
console.log("bs.js: all checks passed");
