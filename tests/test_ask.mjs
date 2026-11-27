// node --test tests/test_ask.mjs  (no network, no Claude calls)
import assert from "node:assert/strict";
import { test } from "node:test";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { allowed, clip, loadUsers, makeLoader, navigate, select, sha256, tools } from "../api/_lib/atlas.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("allowlist: right email and code only", () => {
  const users = loadUsers({ ASKATLAS_USERS: JSON.stringify({ "A@x.com": sha256("s3cret") }) });
  assert.equal(allowed(users, "a@x.com", "s3cret"), true);
  assert.equal(allowed(users, " A@X.com ", "s3cret"), true);
  assert.equal(allowed(users, "a@x.com", "wrong"), false);
  assert.equal(allowed(users, "b@x.com", "s3cret"), false);
  assert.equal(allowed(users, "a@x.com", ""), false);
  assert.deepEqual(loadUsers({ ASKATLAS_USERS: "not json" }), {});
});

test("navigate: keys, indexes, key=value, helpful errors", () => {
  const d = { a: [{ id: "x", v: 1 }, { id: "y", v: 2 }], b: { c: 3 } };
  assert.equal(navigate(d, "a.id=y.v"), 2);
  assert.equal(navigate(d, "a.-1.v"), 2);
  assert.equal(navigate(d, "b.c"), 3);
  assert.throws(() => navigate(d, "b.z"), /no key "z".*keys: c/);
  assert.throws(() => navigate(d, "a.id=q"), /no element/);
});

test("select and clip keep answers small", () => {
  const rows = Array.from({ length: 100 }, (_, i) => ({ i, k: i % 2 ? "odd" : "even", big: "x".repeat(10) }));
  const s = select(rows, { where: { k: "odd" }, fields: ["i"], limit: 3 });
  assert.equal(s.total, 50); assert.deepEqual(s.rows, [{ i: 95 }, { i: 97 }, { i: 99 }]);
  const huge = { series: Array.from({ length: 5000 }, (_, i) => i * 1.2345) };
  const c = JSON.parse(clip(huge));
  assert.match(c.note, /Too large/); assert.equal(c.outline.series, "array(5000)");
});

test("tools read the real published files", async () => {
  const fn = tools(makeLoader({ dir: root }));
  const list = JSON.parse(await fn.list_datasets());
  assert.ok(list.some((d) => d.name === "oil_balance"));
  const cu = JSON.parse(await fn.read_dataset({ dataset: "commodities", path: "commodities.id=copper.producers", where: { iso3: "CHL" }, fields: ["iso3", "share"] }));
  assert.equal(cu[0].iso3, "CHL"); assert.ok(cu[0].share > 0.1);
  const desc = JSON.parse(await fn.describe_dataset({ dataset: "metals_stocks" }));
  assert.ok(desc.outline.metals);
});

test("handler refuses anyone not on the list", async () => {
  process.env.ASKATLAS_USERS = JSON.stringify({ "a@x.com": sha256("ok") });
  const { default: handler } = await import("../api/ask.mjs");
  const res = { code: 0, body: null, headers: {}, setHeader(k, v) { this.headers[k] = v; }, status(c) { this.code = c; return this; }, json(b) { this.body = b; return this; } };
  await handler({ method: "POST", headers: {}, body: { email: "b@x.com", code: "ok", messages: [{ role: "user", content: "hi" }] } }, res);
  assert.equal(res.code, 401);
  await handler({ method: "POST", headers: {}, body: { email: "a@x.com", code: "ok", messages: [] } }, res);
  assert.equal(res.code, 400);
  await handler({ method: "GET", headers: {} }, res);
  assert.equal(res.code, 405);
});
