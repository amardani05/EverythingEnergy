/* AskAtlas: ask questions about the terminal's data in plain English. Claude reads the
   published data files through read-only tools (api/ask.mjs). Access is by allowlist:
   an email plus a personal access code. */
"use strict";
(() => {
  const { esc } = T;
  const KEY = "ee.ask.auth";
  const store = { get() { for (const s of [sessionStorage, localStorage]) { try { const v = JSON.parse(s.getItem(KEY) || "null"); if (v) return v; } catch (e) { /* blocked */ } } return null; },
    set(v, remember) { try { (remember ? localStorage : sessionStorage).setItem(KEY, JSON.stringify(v)); } catch (e) { /* blocked */ } },
    clear() { for (const s of [sessionStorage, localStorage]) { try { s.removeItem(KEY); } catch (e) { /* blocked */ } } } };
  const SAMPLES = ["How tight is US distillate versus its 5-year range, and how many days of cover are there?", "Where will Lower 48 gas storage end the winter on a normal-weather path?",
    "Which countries dominate copper refining compared with copper mining?", "What moved in SHFE warehouse stocks this week?", "Which commodities have the most concentrated supply (HHI), and who leads each?"];

  /* small, safe markdown: everything is escaped first, then bold, code, headings, lists and pipe tables */
  function md(src) {
    const inline = (s) => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
    const out = [], lines = String(src).split("\n"); let i = 0;
    while (i < lines.length) { const l = lines[i];
      if (/^\s*\|.*\|\s*$/.test(l)) { const rows = []; while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) rows.push(lines[i++]);
        const cells = (r) => r.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim()), body = rows.filter((r) => !/^\s*\|[\s:|-]+\|\s*$/.test(r));
        out.push(`<table class="g ask-t"><thead><tr>${cells(body[0]).map((c) => `<th class="l">${inline(c)}</th>`).join("")}</tr></thead><tbody>${body.slice(1).map((r) => `<tr>${cells(r).map((c) => `<td class="l">${inline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`); continue; }
      if (/^\s*[-*] /.test(l)) { const items = []; while (i < lines.length && /^\s*[-*] /.test(lines[i])) items.push(lines[i++].replace(/^\s*[-*] /, "")); out.push(`<ul>${items.map((x) => `<li>${inline(x)}</li>`).join("")}</ul>`); continue; }
      if (/^#{1,4} /.test(l)) { out.push(`<h4>${inline(l.replace(/^#+ /, ""))}</h4>`); i++; continue; }
      if (l.trim()) out.push(`<p>${inline(l)}</p>`); i++; }
    return out.join("");
  }

  T.app("ask", { title: "AskAtlas", group: "Reference", w: 6, h: 14, open(win) {
    const bar = win.add(`<div class="toolbar ask-bar"></div>`), log = win.add(`<div class="fill ask-log"></div>`);
    const form = win.add(`<div class="ask-in"><textarea rows="2" placeholder="Ask about balances, stocks, supply chains, curves..."></textarea><button class="ask-go">Ask</button></div>`);
    const st = win.add(`<div class="status"><div>Claude reads the terminal's published data. Answers can be wrong: check the Sources line.</div></div>`).querySelector("div");
    const ta = form.querySelector("textarea"), go = form.querySelector(".ask-go"); let auth = store.get(), hist = [], busy = false;
    function header() {
      if (auth) { bar.innerHTML = `<span>Signed in as <b>${esc(auth.email)}</b></span><span style="flex:1"></span><button class="ask-new">New chat</button><button class="ask-out">Sign out</button>`;
        bar.querySelector(".ask-out").onclick = () => { store.clear(); auth = null; hist = []; header(); paint(); };
        bar.querySelector(".ask-new").onclick = () => { hist = []; paint(); }; form.style.display = ""; return; }
      bar.innerHTML = `<label>Email <input type="text" class="ask-e" autocomplete="username" style="width:170px"></label><label>Access code <input type="password" class="ask-c" autocomplete="current-password" style="width:120px"></label>
        <label><input type="checkbox" class="ask-r">remember</label><button class="ask-in-b">Sign in</button>`;
      form.style.display = "none";
      bar.querySelector(".ask-in-b").onclick = () => { const e = bar.querySelector(".ask-e").value.trim(), c = bar.querySelector(".ask-c").value; if (!e || !c) return;
        auth = { email: e, code: c }; store.set(auth, bar.querySelector(".ask-r").checked); header(); paint(); };
    }
    function paint(pending) {
      if (!auth) { log.innerHTML = `<div class="pad ask-intro"><b>AskAtlas</b> answers questions from the terminal's data: balances, exchange stocks, supply chains, curves, options and equity signals.<br><br>Access is limited to people on the list. Sign in with your email and the access code you were given.</div>`; return; }
      if (!hist.length) { log.innerHTML = `<div class="pad ask-intro">Try one of these:${SAMPLES.map((s) => `<div class="ask-sample">${esc(s)}</div>`).join("")}</div>`;
        log.querySelectorAll(".ask-sample").forEach((x) => { x.onclick = () => { ta.value = x.textContent; send(); }; }); return; }
      log.innerHTML = hist.map((m) => m.role === "user" ? `<div class="ask-q">${esc(m.content)}</div>` : `<div class="ask-a${m.error ? " err" : ""}">${m.error ? esc(m.content) : md(m.content)}${m.used && m.used.length ? `<div class="ask-used">read: ${m.used.filter((u) => u.tool !== "list_datasets").map((u) => esc(`${u.input.dataset || ""}${u.input.path ? " " + u.input.path : ""}`)).join(" · ") || "dataset list"}</div>` : ""}</div>`).join("")
        + (pending ? `<div class="ask-a mut">Reading the data...</div>` : "");
      log.scrollTop = log.scrollHeight;
    }
    async function send() {
      const q = ta.value.trim(); if (!q || busy || !auth) return; busy = true; go.disabled = true; ta.value = "";
      hist.push({ role: "user", content: q }); paint(true); const t0 = Date.now();
      try {
        const r = await fetch("api/ask", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email: auth.email, code: auth.code, messages: hist.filter((m) => !m.error).map(({ role, content }) => ({ role, content })) }) });
        const d = await r.json().catch(() => ({ error: r.status === 404 ? "AskAtlas runs as a Vercel function, so it only works on the deployed site (or under vercel dev), not on a plain local server." : `HTTP ${r.status}` }));
        if (r.status === 401) { store.clear(); auth = null; header(); }
        hist.push(d.answer ? { role: "assistant", content: d.answer + (d.truncated ? "\n\n(The answer was cut short. Ask a narrower question.)" : ""), used: d.used } : { role: "assistant", content: d.error || "No answer.", error: true });
        st.textContent = d.answer ? `Answered in ${((Date.now() - t0) / 1000).toFixed(0)}s by ${d.model || "Claude"}${d.usage ? `, ${d.usage.input + d.usage.output} tokens` : ""}. Check the Sources line.` : st.textContent;
      } catch (e) { hist.push({ role: "assistant", content: "Network error: " + e.message, error: true }); }
      busy = false; go.disabled = false; paint(); ta.focus();
    }
    go.onclick = send; ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
    header(); paint();
  } });
})();
