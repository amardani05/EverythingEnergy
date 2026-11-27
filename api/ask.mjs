// POST /api/ask: AskAtlas. Answers a question about the terminal's data with Claude,
// which reads the published data files through three read-only tools.
// Only emails listed in ASKATLAS_USERS, each with its own access code, can use it.
//
// Environment (Vercel project settings, never the repo):
//   ANTHROPIC_API_KEY   Claude API key
//   ASKATLAS_USERS      {"name@example.com": "<sha256 of their access code>"}
//                       make an entry with: node scripts/askatlas_user.mjs name@example.com
//   ASKATLAS_HOURLY     optional, questions per user per hour per server instance (default 30)

import Anthropic from "@anthropic-ai/sdk";
import { betaZodTool } from "@anthropic-ai/sdk/helpers/beta/zod";
import { z } from "zod";
import { DATASETS, SYSTEM, allowed, loadUsers, makeLoader, tools } from "./_lib/atlas.mjs";

const MODEL = "claude-opus-5-5";
const names = Object.keys(DATASETS);
const recent = new Map(); // email -> timestamps; best effort, per warm instance only

function throttled(email, perHour) {
  const now = Date.now(), t = (recent.get(email) || []).filter((x) => now - x < 3600e3);
  if (t.length >= perHour) return true;
  t.push(now); recent.set(email, t); return false;
}

function cleanHistory(msgs) {
  if (!Array.isArray(msgs) || !msgs.length) return null;
  const out = msgs.slice(-12).map((m) => ({ role: m.role === "assistant" ? "assistant" : "user", content: String(m.content || "").slice(0, 6000) }))
    .filter((m) => m.content.trim());
  while (out.length && out[0].role !== "user") out.shift();
  return out.length && out[out.length - 1].role === "user" ? out : null;
}

export default async function handler(req, res) {
  if (req.method !== "POST") { res.setHeader("Allow", "POST"); return res.status(405).json({ error: "POST only" }); }
  const body = typeof req.body === "string" ? JSON.parse(req.body || "{}") : req.body || {};
  const email = String(body.email || "").trim().toLowerCase();
  if (!allowed(loadUsers(), email, body.code)) return res.status(401).json({ error: "This email and access code are not on the AskAtlas list." });
  if (throttled(email, +process.env.ASKATLAS_HOURLY || 30)) return res.status(429).json({ error: "Hourly question limit reached. Try again later." });
  const messages = cleanHistory(body.messages);
  if (!messages) return res.status(400).json({ error: "Send messages ending with a user question." });
  if (!process.env.ANTHROPIC_API_KEY) return res.status(503).json({ error: "AskAtlas is not configured: ANTHROPIC_API_KEY is missing." });

  const host = req.headers["x-forwarded-host"] || req.headers.host;
  const load = makeLoader({ dir: process.env.ASKATLAS_DATA_DIR, origin: `${host?.startsWith("localhost") ? "http" : "https"}://${host}` });
  const fn = tools(load), used = [];
  const wrap = (name, run) => async (input) => { used.push({ tool: name, input }); try { return await run(input); } catch (e) { return `Error: ${e.message}`; } };
  const ds = z.enum(names).describe("Dataset name from list_datasets");
  const pathArg = z.string().optional().describe('Dot path into the JSON. On an array, a number picks an index and key=value picks the first match, e.g. "commodities.id=copper.producers" or "gas.storage.summary.l48"');

  const client = new Anthropic();
  try {
    const final = await client.beta.messages.toolRunner({
      model: MODEL,
      max_tokens: 16000,
      max_iterations: 10,
      output_config: { effort: "medium" },
      betas: ["server-side-fallback-2026-07-01"],
      fallbacks: "default",
      system: [{ type: "text", text: SYSTEM, cache_control: { type: "ephemeral" } }],
      tools: [
        betaZodTool({ name: "list_datasets", description: "List the datasets AskAtlas can read, with what each contains.", inputSchema: z.object({}), run: wrap("list_datasets", fn.list_datasets) }),
        betaZodTool({ name: "describe_dataset", description: "Show the shape of a dataset or of one path inside it (keys, types, array lengths) and its meta block (dates, sources). Use before reading.", inputSchema: z.object({ dataset: ds, path: pathArg }), run: wrap("describe_dataset", fn.describe_dataset) }),
        betaZodTool({
          name: "read_dataset",
          description: "Read values at a path. Arrays of rows can be filtered with where (exact match on fields), trimmed to fields, and limited to the last (default) or first n rows. Large results come back as an outline instead; narrow the path.",
          inputSchema: z.object({
            dataset: ds, path: pathArg,
            where: z.record(z.string(), z.union([z.string(), z.number()])).optional().describe('Exact-match filter on array rows, e.g. {"iso3": "CHL"}'),
            fields: z.array(z.string()).optional().describe("Keep only these fields of each row"),
            limit: z.number().int().optional().describe("Rows to return, default 50, max 500"),
            from: z.enum(["end", "start"]).optional().describe("Take rows from the end (latest, default) or the start"),
          }),
          run: wrap("read_dataset", fn.read_dataset),
        }),
      ],
      messages,
    });
    if (final.stop_reason === "refusal") return res.status(200).json({ answer: "AskAtlas could not answer that question.", used, model: final.model });
    const answer = final.content.filter((b) => b.type === "text").map((b) => b.text).join("\n").trim();
    return res.status(200).json({
      answer: answer || "No answer was produced. Try asking more narrowly.",
      used, model: final.model,
      truncated: final.stop_reason === "max_tokens" || final.stop_reason === "tool_use",
      usage: { input: final.usage?.input_tokens, output: final.usage?.output_tokens },
    });
  } catch (e) {
    if (e instanceof Anthropic.RateLimitError) return res.status(429).json({ error: "Claude is rate limited right now. Try again in a minute." });
    if (e instanceof Anthropic.AuthenticationError) return res.status(503).json({ error: "AskAtlas is misconfigured: the Claude API key was rejected." });
    if (e instanceof Anthropic.APIError) return res.status(502).json({ error: `Claude API error ${e.status}` });
    console.error(e);
    return res.status(500).json({ error: "AskAtlas failed. The error is in the server log." });
  }
}
