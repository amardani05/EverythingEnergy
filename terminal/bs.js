/* Black-Scholes-Merton pricing, greeks, implied vol and multi-leg valuation.
   Pure functions, no DOM: loaded by the terminal and by tests/test_bs.mjs.
   Conventions: T in years, r and q continuously compounded, sigma annualised.
   vega is per 1.00 of vol (divide by 100 for a vol point), theta per year. */
"use strict";
(function (root, factory) { const api = factory(); if (typeof module === "object" && module.exports) module.exports = api; else root.BS = api; })(typeof self !== "undefined" ? self : this, function () {
  const SQRT2PI = Math.sqrt(2 * Math.PI);
  const pdf = (x) => Math.exp(-0.5 * x * x) / SQRT2PI;
  /* Abramowitz-Stegun 26.2.17 would give 7e-8; this is West's (2005) double
     precision algorithm, accurate to about 1e-15, which matters for IV on
     far wings. */
  function cdf(x) {
    const a = Math.abs(x); let c;
    if (a > 37) c = 0;
    else { const e = Math.exp(-a * a / 2);
      if (a < 7.07106781186547) { let n = 3.52624965998911e-02 * a + 0.700383064443688; n = n * a + 6.37396220353165; n = n * a + 33.912866078383; n = n * a + 112.079291497871; n = n * a + 221.213596169931; n = n * a + 220.206867912376;
        let d = 8.83883476483184e-02 * a + 1.75566716318264; d = d * a + 16.064177579207; d = d * a + 86.7807322029461; d = d * a + 296.564248779674; d = d * a + 637.333633378831; d = d * a + 793.826512519948; d = d * a + 440.413735824752; c = e * n / d; }
      else { let b = a + 0.65; b = a + 4 / b; b = a + 3 / b; b = a + 2 / b; b = a + 1 / b; c = e / b / 2.506628274631; } }
    return x > 0 ? 1 - c : c;
  }
  const d1d2 = (S, K, T, r, q, s) => { const v = s * Math.sqrt(T), d1 = (Math.log(S / K) + (r - q + 0.5 * s * s) * T) / v; return [d1, d1 - v]; };
  const intrinsic = (S, K, type) => Math.max(0, type === "c" ? S - K : K - S);

  function price(S, K, T, r, q, s, type) {
    if (!(T > 0) || !(s > 0)) return intrinsic(S * Math.exp(-q * Math.max(T, 0)), K * Math.exp(-r * Math.max(T, 0)), type);
    const [d1, d2] = d1d2(S, K, T, r, q, s), df = Math.exp(-r * T), dq = Math.exp(-q * T);
    return type === "c" ? S * dq * cdf(d1) - K * df * cdf(d2) : K * df * cdf(-d2) - S * dq * cdf(-d1);
  }
  function greeks(S, K, T, r, q, s, type) {
    if (!(T > 0) || !(s > 0)) { const itm = type === "c" ? S > K : S < K; return { delta: itm ? (type === "c" ? 1 : -1) : 0, gamma: 0, vega: 0, theta: 0, rho: 0, vanna: 0, volga: 0 }; }
    const [d1, d2] = d1d2(S, K, T, r, q, s), df = Math.exp(-r * T), dq = Math.exp(-q * T), sq = Math.sqrt(T), n1 = pdf(d1);
    const c = type === "c";
    return {
      delta: c ? dq * cdf(d1) : -dq * cdf(-d1),
      gamma: dq * n1 / (S * s * sq),
      vega: S * dq * n1 * sq,
      theta: -S * dq * n1 * s / (2 * sq) + (c ? -r * K * df * cdf(d2) + q * S * dq * cdf(d1) : r * K * df * cdf(-d2) - q * S * dq * cdf(-d1)),
      rho: c ? K * T * df * cdf(d2) : -K * T * df * cdf(-d2),
      vanna: -dq * n1 * d2 / s,                 // d delta / d vol
      volga: S * dq * n1 * sq * d1 * d2 / s,    // d vega / d vol
    };
  }
  /* Implied vol by bracketed Newton. Returns null when the price is outside
     the no-arbitrage band (below discounted intrinsic or above the forward
     bound), which on wide ETF wings is common and must not become a number. */
  function impliedVol(px, S, K, T, r, q, type) {
    if (!(px > 0) || !(T > 0)) return null;
    const df = Math.exp(-r * T), dq = Math.exp(-q * T);
    const lo0 = intrinsic(S * dq, K * df, type), hi0 = type === "c" ? S * dq : K * df;
    if (px <= lo0 + 1e-10 || px >= hi0) return null;
    let lo = 1e-4, hi = 6, s = 0.4;
    for (let i = 0; i < 80; i++) {
      const diff = price(S, K, T, r, q, s, type) - px;
      if (Math.abs(diff) < 1e-8) return s;
      if (diff > 0) hi = s; else lo = s;
      const v = greeks(S, K, T, r, q, s, type).vega;
      const nx = v > 1e-10 ? s - diff / v : NaN;
      s = nx > lo && nx < hi ? nx : 0.5 * (lo + hi);
      if (hi - lo < 1e-9) return s;
    }
    return s;
  }

  /* ---- strategies ----
     leg: {type: 'c'|'p'|'u', k, T (years to expiry today), qty (signed), iv, entry (per unit), mult}
     'u' is the underlying itself (entry = price paid). */
  function legValue(leg, S, o) {
    if (leg.type === "u") return S;
    const T = Math.max(0, leg.T - (o.days || 0) / 365), s = Math.max(1e-4, leg.iv * (1 + (o.volMul || 0)) + (o.volAdd || 0));
    return price(S, leg.k, T, o.r, o.q, s, leg.type);
  }
  function legGreeks(leg, S, o) {
    if (leg.type === "u") return { delta: 1, gamma: 0, vega: 0, theta: 0, rho: 0, vanna: 0, volga: 0 };
    const T = Math.max(0, leg.T - (o.days || 0) / 365), s = Math.max(1e-4, leg.iv * (1 + (o.volMul || 0)) + (o.volAdd || 0));
    return greeks(S, leg.k, T, o.r, o.q, s, leg.type);
  }
  const m = (leg) => leg.qty * (leg.mult || 1);
  /* P&L of the whole book versus entry prices. */
  function pnl(legs, S, o) { let v = 0; for (const l of legs) v += m(l) * (legValue(l, S, o) - l.entry); return v; }
  function bookGreeks(legs, S, o) {
    const g = { delta: 0, gamma: 0, vega: 0, theta: 0, rho: 0, vanna: 0, volga: 0 };
    for (const l of legs) { const x = legGreeks(l, S, o); for (const k in g) g[k] += m(l) * x[k]; }
    return g;
  }
  /* Expiry payoff of the nearest-dated leg: later legs are still alive there
     and are valued by model, so calendars come out right. */
  function horizonDays(legs) { const t = legs.filter((l) => l.type !== "u").map((l) => l.T); return t.length ? Math.round(Math.min(...t) * 365) : 0; }
  function curve(legs, o, lo, hi, n = 161) { const out = []; for (let i = 0; i < n; i++) { const S = lo + (hi - lo) * i / (n - 1); out.push([S, pnl(legs, S, o)]); } return out; }
  function breakevens(pts) { const out = []; for (let i = 1; i < pts.length; i++) { const [x0, y0] = pts[i - 1], [x1, y1] = pts[i];
    if (y0 === 0) out.push(x0); else if (y0 * y1 < 0) out.push(x0 - y0 * (x1 - x0) / (y1 - y0)); } return out; }
  /* Are the tails bounded? Compare the slope at the far edges: the underlying
     and naked options give an unbounded side. */
  function tails(legs, o, S0) {
    const far = S0 * 50, d = S0 * 0.01, oo = { ...o, days: horizonDays(legs) };
    const up = (pnl(legs, far + d, oo) - pnl(legs, far, oo)) / d, dn = pnl(legs, 1e-6, oo);
    return { upSlope: Math.abs(up) < 1e-6 ? 0 : up, atZero: dn };
  }
  return { pdf, cdf, price, greeks, impliedVol, intrinsic, legValue, legGreeks, pnl, bookGreeks, horizonDays, curve, breakevens, tails };
});
