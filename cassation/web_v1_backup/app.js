/* CASSATION courtroom board. Vanilla JS, no build step (PROTOCOL non-negotiables).
   Everything on screen is driven by Events from WS /ws; unknown types are ignored. */
"use strict";

// ------------------------------------------------------------------ helpers

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const tail = (s, n) => (s.length > n ? "…" + s.slice(-n) : s);

async function post(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) { /* non-json */ }
    throw new Error(detail);
  }
  return res.json();
}

let toastTimer = null;
function toast(msg, ms = 2800) {
  const el = $("toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, ms);
}

function fmtClock(sec) {
  const m = Math.floor(sec / 60), s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

// ------------------------------------------------------------------ state

const S = {
  runId: null,
  mode: "idle",
  claims: new Map(),      // id -> claim payload
  cards: new Map(),       // id -> {card, agents, evidence, vchip, evByStance:{}}
  agents: new Map(),      // agent_id -> {claimId, buf, tickEl, rowEl, statusEl}
  citations: new Map(),   // n -> citation
  synthesisBuf: "",
  judgeBuf: "",
  juryClaimId: null,
  struck: new Set(),      // approval-modal toggles
  maxT: 0,
  takeoverTimers: [],
  renderQueued: false,
};

function resetRun() {
  S.claims.clear(); S.cards.clear(); S.agents.clear(); S.citations.clear();
  S.synthesisBuf = ""; S.judgeBuf = ""; S.juryClaimId = null;
  S.struck.clear(); S.maxT = 0;
  S.takeoverTimers.forEach(clearTimeout); S.takeoverTimers = [];
  $("board").innerHTML = "";
  $("verdict-body").innerHTML = "";
  $("cites-list").innerHTML = "";
  $("brief-link").innerHTML = "";
  $("verdict-cites").hidden = true;
  $("verdict-panel").hidden = true;
  $("main").classList.remove("with-verdict");
  $("vp-status").textContent = "streaming…";
  $("judge-ticker").textContent = "";
  $("leaf-model").textContent = "";
  ["approval-overlay", "jury-overlay", "takeover", "docket-overlay"].forEach(
    (id) => { $(id).hidden = true; });
  $("cost-est").textContent = "£0.00";
  $("cost-sources").textContent = "0";
  $("cost-agents").textContent = "0 live · 0 done";
  $("cost-elapsed").textContent = "0:00";
  setPhase("—");
}

function setMode(mode, extra) {
  S.mode = mode;
  const chip = $("mode-chip");
  chip.className = `mode-chip mode-${mode}`;
  chip.textContent = (mode.toUpperCase() + (extra ? ` · ${extra}` : ""));
}

function setPhase(label) { $("phase").textContent = label; }

function judgeAppend(text) {
  S.judgeBuf += text;
  $("judge-ticker").textContent = tail(S.judgeBuf, 180);
}

// ------------------------------------------------------------------ claim cards

const STATE_BADGES = {
  excluded: "timed out — excluded",
  struck: "STRUCK",
  contested: "CONTESTED",
  refuted: "REFUTED",
};

function makeCard(claim) {
  const card = document.createElement("article");
  card.className = "card state-pending";
  card.id = `card-${claim.id}`;
  const deps = (claim.depends_on || []).length
    ? `<div class="deps">depends on ${claim.depends_on.map((d) => d.toUpperCase()).join(", ")}</div>` : "";
  card.innerHTML = `
    <div class="card-top">
      <span class="cid">${esc(claim.id.toUpperCase())}</span>
      <span class="branch">${esc(claim.branch || "")}</span>
      <span class="badge"></span>
    </div>
    <div class="claim-text">${esc(claim.text)}</div>
    ${deps}
    <div class="agents"></div>
    <div class="evidence"></div>
    <span class="vchip"></span>
    <span class="seal">⚖ human ruled</span>`;
  $("board").appendChild(card);
  S.cards.set(claim.id, {
    card,
    agents: card.querySelector(".agents"),
    evidence: card.querySelector(".evidence"),
    vchip: card.querySelector(".vchip"),
    badge: card.querySelector(".badge"),
    evByStance: {},
  });
}

function buildBoard(graph) {
  $("board").innerHTML = "";
  S.cards.clear();
  (graph.claims || []).forEach((c) => { S.claims.set(c.id, c); makeCard(c); });
  document.body.dataset.view = "board";
}

function setClaimState(claimId, state, note) {
  const entry = S.cards.get(claimId);
  if (!entry) return;
  entry.card.className = `card state-${state}`;
  const badgeText = STATE_BADGES[state];
  if (badgeText) {
    entry.badge.textContent = state === "struck" && note ? "STRUCK" : badgeText;
    entry.badge.style.display = "inline-block";
  } else {
    entry.badge.style.display = "none";
  }
  if (note) entry.card.title = note;
}

// ------------------------------------------------------------------ markdown

function mdToHtml(md) {
  const inline = (s) => esc(s)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*]+)\*(?!\*)/g, "$1<em>$2</em>")
    .replace(/\[(\d+)\]/g, (m, n) => {
      const c = S.citations.get(Number(n));
      return c && c.url
        ? `<a class="cite" href="${esc(c.url)}" target="_blank" rel="noopener" title="${esc(c.label || "")}">${n}</a>`
        : `<span class="cite">${n}</span>`;
    });
  const out = [];
  for (const block of md.trim().split(/\n\s*\n/)) {
    let lines = block.split("\n").filter((l) => l.trim());
    if (!lines.length) continue;
    const h = lines[0].match(/^(#{1,3}) (.*)$/);
    if (h) {
      out.push(`<h${h[1].length + 1}>${inline(h[2])}</h${h[1].length + 1}>`);
      lines = lines.slice(1);
      if (!lines.length) continue;
    }
    if (lines.every((l) => l.trim().startsWith("- "))) {
      out.push(`<ul>${lines.map((l) => `<li>${inline(l.trim().slice(2))}</li>`).join("")}</ul>`);
    } else {
      out.push(`<p>${inline(lines.join(" "))}</p>`);
    }
  }
  return out.join("\n");
}

function renderSynthesis() {
  if (S.renderQueued) return;
  S.renderQueued = true;
  requestAnimationFrame(() => {
    S.renderQueued = false;
    $("verdict-body").innerHTML = mdToHtml(S.synthesisBuf);
  });
}

// ------------------------------------------------------------------ overlays

function openApproval(graph) {
  S.struck.clear();
  const rows = $("approval-rows");
  rows.innerHTML = "";
  (graph.claims || []).forEach((c) => {
    const row = document.createElement("div");
    row.className = "arow";
    row.dataset.id = c.id;
    const deps = (c.depends_on || []).length
      ? `<span class="arow-deps">needs ${c.depends_on.map((d) => d.toUpperCase()).join(", ")}</span>` : "";
    row.innerHTML = `
      <span class="cid">${esc(c.id.toUpperCase())}</span>
      <span class="arow-text"><b>${esc(c.branch || "")}</b> — ${esc(c.text)}${deps}</span>
      <span class="arow-cost">£${(c.est_cost_gbp || 0).toFixed(2)}</span>
      <span class="arow-strike">strike</span>`;
    row.addEventListener("click", () => {
      row.classList.toggle("struck");
      if (row.classList.contains("struck")) S.struck.add(c.id);
      else S.struck.delete(c.id);
    });
    rows.appendChild(row);
  });
  $("approval-est").textContent =
    `~${graph.est_agents || "?"} agents · est £${(graph.est_total_cost_gbp || 0).toFixed(2)} (estimate)`;
  $("approval-overlay").hidden = false;
}

async function submitApproval() {
  $("approval-overlay").hidden = true;
  try {
    const r = await post("/api/approve", {
      run_id: S.runId, approved: true, struck_claim_ids: [...S.struck],
    });
    toast(r.resolved ? "Plan approved — empaneling agents"
                     : "Approval sent (no live gate — mock/replay)");
  } catch (e) {
    toast(`approve failed: ${e.message}`);
  }
}

function openJury(req) {
  S.juryClaimId = req.claim_id;
  $("jury-question").textContent = req.question || "";
  const box = $("jury-options");
  box.innerHTML = "";
  (req.options || []).forEach((opt) => {
    const el = document.createElement("div");
    el.className = "jury-opt";
    el.innerHTML = `
      <span class="opt-key">OPTION ${esc((opt.key || "").toUpperCase())}</span>
      <span class="opt-summary">${esc(opt.summary || "")}</span>
      <span class="opt-quote">“${esc(opt.quote || "")}”</span>
      <span class="opt-src">${opt.url
        ? `<a href="${esc(opt.url)}" target="_blank" rel="noopener">${esc(opt.source_label || opt.url)}</a>`
        : esc(opt.source_label || "")}</span>
      <button class="btn primary" type="button">RULE FOR THIS SOURCE</button>`;
    el.querySelector("button").addEventListener("click", async () => {
      el.classList.add("chosen");
      box.querySelectorAll("button").forEach((b) => { b.disabled = true; });
      try {
        const r = await post("/api/jury", {
          run_id: S.runId, claim_id: req.claim_id, choice_key: opt.key,
        });
        toast(r.resolved ? `Ruled for option ${opt.key.toUpperCase()} — stamped human-adjudicated`
                         : "Ruling sent (no live gate — mock/replay)");
      } catch (e) {
        toast(`jury failed: ${e.message}`);
      }
      setTimeout(() => { $("jury-overlay").hidden = true; }, 600);
    });
    box.appendChild(el);
  });
  $("jury-overlay").hidden = false;
}

function showTakeover(p) {
  const fill = (id, side) => {
    $(id).innerHTML = `“${esc(side.quote || "")}”<span class="src">${side.url
      ? `<a href="${esc(side.url)}" target="_blank" rel="noopener" style="color:inherit">${esc(side.source_label || side.url)}</a>`
      : esc(side.source_label || "")}</span>`;
  };
  fill("takeover-left", p.left || {});
  fill("takeover-right", p.right || {});
  const claim = S.claims.get(p.claim_id);
  $("takeover-claim").textContent = claim ? claim.text : (p.claim_id || "");
  const chip = $("tk-chip");
  chip.className = "tk-chip";
  chip.textContent = "SUPPORTED";
  S.takeoverTimers.forEach(clearTimeout);
  S.takeoverTimers = [
    setTimeout(() => { chip.className = "tk-chip amber"; chip.textContent = "CHALLENGED"; }, 900),
    setTimeout(() => { chip.className = "tk-chip red"; chip.textContent = "CONTESTED"; }, 1800),
  ];
  $("takeover").hidden = false;
}

// ------------------------------------------------------------------ docket

function verdictCountChips(counts) {
  const cls = { SUPPORTED: "g", REFUTED: "r", CONTESTED: "a", HUMAN_ADJUDICATED: "a",
                EXCLUDED: "x", STRUCK: "x" };
  return Object.entries(counts || {})
    .map(([k, v]) => `<span class="vc ${cls[k] || "x"}">${v} ${esc(k.toLowerCase().replace("_", "-"))}</span>`)
    .join(" ");
}

async function loadDocket(targetEl) {
  let runs = [];
  try { runs = await (await fetch("/api/runs")).json(); } catch (e) { /* server gone */ }
  targetEl.innerHTML = runs.length ? "" : `<p style="color:var(--muted)">No case files yet.</p>`;
  runs.forEach((r) => {
    const el = document.createElement("div");
    el.className = "case-file";
    const when = (r.started_at || "").slice(0, 16).replace("T", " ");
    el.innerHTML = `
      <div class="case-q">${esc(r.question || r.run_id)}</div>
      <div class="case-meta">
        <span>${esc(r.run_id)}</span><span>${esc(when)}</span>
        <span>${esc(r.status || "")}</span><span>${r.n_claims || 0} claims</span>
        <span>${fmtClock(r.duration_s || 0)}</span>
        ${verdictCountChips(r.verdict_counts)}
      </div>`;
    el.addEventListener("click", () => replayRun(r.run_id));
    targetEl.appendChild(el);
  });
}

async function replayRun(runId) {
  try {
    const r = await post("/api/replay", { run_id: runId || "" });
    $("docket-overlay").hidden = true;
    toast(`REPLAY — ${r.run_id}`);
  } catch (e) {
    toast(`replay failed: ${e.message}`);
  }
}

// ------------------------------------------------------------------ event handlers

const HANDLERS = {
  "run.start": (p) => {
    resetRun();
    S.runId = p.run_id;
    $("question").textContent = p.question || "";
    setMode(p.mode || "live");
    setPhase("GUARDRAIL");
    document.body.dataset.view = "board";
  },

  "replay.info": (p) => {
    const rec = (p.recorded_at_iso || "").slice(11, 16);
    setMode("replay", rec ? `rec ${rec}` : (p.original_run_id || ""));
  },

  "guardrail.result": (p) => {
    judgeAppend(p.ok ? "Guardrail: admissible. " : `Guardrail REFUSED: ${p.reason} `);
    setPhase("DECOMPOSE");
  },

  "plan.delta": (p) => { judgeAppend(p.text || ""); setPhase("DECOMPOSE"); },

  "plan.complete": (p) => { buildBoard(p); },

  "approval.request": (p) => {
    if (!S.cards.size) buildBoard(p);
    setPhase("AWAITING APPROVAL");
    openApproval(p);
  },

  "approval.result": (p) => {
    $("approval-overlay").hidden = true;
    (p.struck_claim_ids || []).forEach((id) => setClaimState(id, "struck", "struck at approval"));
    setPhase(p.approved ? "ADVOCATES" : "ABORTED");
  },

  "claim.state": (p) => { setClaimState(p.claim_id, p.state, p.note); },

  "agent.spawn": (p) => {
    const entry = p.claim_id ? S.cards.get(p.claim_id) : null;
    if (!entry) { judgeAppend(`[${p.label || p.agent_id}] `); return; }
    if (S.agents.has(p.agent_id)) return; // replayed backlog after reconnect
    const row = document.createElement("div");
    row.className = `agent-row role-${p.role || "advocate"}`;
    const tag = { advocate: "ADV", skeptic: "SKP", tiebreaker: "TIE" }[p.role] || (p.role || "?").toUpperCase();
    row.innerHTML = `<span class="agent-tag">${esc(tag)} ${esc(p.agent_id)}</span>
                     <span class="agent-tick"></span><span class="agent-status"></span>`;
    // provenance: every agent row names its model (tooltip + header legend)
    if (p.model) {
      row.title = `${p.role || "agent"} · ${p.model}`;
      $("leaf-model").textContent = `AGENTS · ${p.model}`;
    }
    entry.agents.appendChild(row);
    S.agents.set(p.agent_id, {
      claimId: p.claim_id, buf: "", rowEl: row,
      tickEl: row.querySelector(".agent-tick"),
      statusEl: row.querySelector(".agent-status"),
    });
    if (p.role === "advocate") setPhase("ADVOCATES");
    else if (p.role === "skeptic") setPhase("SKEPTICS");
    else if (p.role === "tiebreaker") setPhase("TIE-BREAKER");
  },

  "agent.delta": (p) => {
    const a = S.agents.get(p.agent_id);
    if (!a) { judgeAppend(p.text || ""); return; }
    a.buf += p.text || "";
    a.tickEl.textContent = tail(a.buf, 110);
  },

  "agent.evidence": (p) => {
    const entry = S.cards.get(p.claim_id);
    if (!entry) return;
    const stance = p.stance === "refute" ? "refute" : "support";
    let el = entry.evByStance[stance];
    if (!el) {
      el = document.createElement("div");
      entry.evidence.appendChild(el);
      entry.evByStance[stance] = el;
    }
    const src = p.source_label || p.url || "";
    const prov = p.provenance === "model-reported" ? " · model-reported" : "";
    el.className = `ev ${stance}`;
    el.innerHTML = `“${esc(p.quote || "")}”<span class="src">${p.url
      ? `<a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(src)}</a>` : esc(src)}${prov}</span>`;
  },

  "agent.done": (p) => {
    const a = S.agents.get(p.agent_id);
    if (!a) return;
    a.rowEl.classList.add("done");
    if (p.status === "ok") {
      a.statusEl.textContent = `✓ ${(p.elapsed_s ?? 0).toFixed ? p.elapsed_s.toFixed(1) : p.elapsed_s}s`;
    } else {
      a.statusEl.textContent = p.status.toUpperCase();
      a.statusEl.classList.add("err");
    }
  },

  "dispute.found": (p) => { setPhase("DISPUTES"); judgeAppend(`Dispute on ${(p.claim_id || "").toUpperCase()} (strength ${p.strength}). `); },

  "takeover.show": (p) => { showTakeover(p); },

  // flip timers are NOT cleared here: at high replay speeds hide can arrive
  // before the red beat lands, and the chip is invisible anyway once hidden
  "takeover.hide": () => { $("takeover").hidden = true; },

  "jury.request": (p) => { setPhase("JURY"); openJury(p); },

  "jury.ruling": (p) => {
    $("jury-overlay").hidden = true;
    setPhase("REPLAN");
    toast(`Jury ruled on ${(p.claim_id || "").toUpperCase()} — option ${(p.choice_key || "").toUpperCase()}`);
  },

  "replan.delta": (p) => { judgeAppend(p.text || ""); setPhase("REPLAN"); },

  "replan.complete": (p) => { judgeAppend(`Re-plan done: ${p.notes || ""} `); },

  "synthesis.delta": (p) => {
    setPhase("SYNTHESIS");
    if ($("verdict-panel").hidden) {
      $("verdict-panel").hidden = false;
      $("main").classList.add("with-verdict");
    }
    S.synthesisBuf += p.text || "";
    renderSynthesis();
  },

  "verdict.complete": (p) => {
    (p.citations || []).forEach((c) => S.citations.set(c.id, c));
    S.synthesisBuf = p.brief_markdown || S.synthesisBuf;
    $("verdict-body").innerHTML = mdToHtml(S.synthesisBuf);
    $("vp-status").textContent = "complete";
    // per-claim verdict chips on the cards
    const colors = { SUPPORTED: "var(--green)", REFUTED: "var(--red)", CONTESTED: "var(--amber)",
                     HUMAN_ADJUDICATED: "var(--gold)", EXCLUDED: "var(--grey)", STRUCK: "var(--grey)" };
    (p.verdicts || []).forEach((v) => {
      const entry = S.cards.get(v.claim_id);
      if (!entry) return;
      entry.vchip.textContent = `${v.verdict}${v.confidence ? ` · ${Math.round(v.confidence * 100)}%` : ""}`;
      entry.vchip.style.display = "inline-block";
      entry.vchip.style.borderColor = colors[v.verdict] || "var(--grey)";
      entry.vchip.style.color = colors[v.verdict] || "var(--grey)";
      if (v.summary) entry.card.title = v.summary;
    });
    // citations list + phone brief QR
    const list = $("cites-list");
    list.innerHTML = "";
    (p.citations || []).forEach((c) => {
      const li = document.createElement("li");
      li.value = c.id;
      li.innerHTML = `<a href="${esc(c.url || "#")}" target="_blank" rel="noopener">${esc(c.label || c.url)}</a>
        ${c.quote ? `<br><small>“${esc(c.quote)}”</small>` : ""}
        ${c.provenance === "model-reported" ? `<small> (model-reported)</small>` : ""}`;
      list.appendChild(li);
    });
    if (S.runId) {
      $("brief-link").innerHTML = `
        <img src="/api/qr/${encodeURIComponent(S.runId)}.png" alt="QR to the brief">
        <a href="/brief/${encodeURIComponent(S.runId)}" target="_blank" rel="noopener">
          Open the brief on your phone →</a>`;
    }
    $("verdict-cites").hidden = false;
  },

  "cost.update": (p) => {
    $("cost-est").textContent = `£${(p.est_gbp || 0).toFixed(2)}`;
    $("cost-sources").textContent = String(p.sources || 0);
    $("cost-agents").textContent = `${p.agents_live || 0} live · ${p.agents_done || 0} done`;
  },

  "run.complete": (p) => {
    setPhase(p.status === "ok" ? "DONE" : (p.status || "").toUpperCase());
    $("vp-status").textContent = "complete";
    toast(`Run complete (${p.status}) — ${fmtClock(p.elapsed_s || S.maxT)}`);
  },
};

function dispatch(ev) {
  const h = HANDLERS[ev.type];
  if (h) {
    try { h(ev.payload || {}, ev); }
    catch (err) { console.error("handler failed", ev.type, err); }
  } // unknown event types: ignored by contract
  if (typeof ev.t === "number" && ev.t >= S.maxT) {
    S.maxT = ev.t;
    $("cost-elapsed").textContent = fmtClock(S.maxT);
  }
}

// ------------------------------------------------------------------ websocket

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => { $("conn-dot").classList.remove("off"); };
  ws.onmessage = (msg) => {
    let ev;
    try { ev = JSON.parse(msg.data); } catch (e) { return; }
    dispatch(ev);
  };
  ws.onclose = () => {
    $("conn-dot").classList.add("off");
    setTimeout(connect, 1200);
  };
  ws.onerror = () => { ws.close(); };
}

// ------------------------------------------------------------------ wiring

$("approve-btn").addEventListener("click", submitApproval);
$("docket-btn").addEventListener("click", async () => {
  await loadDocket($("docket-list"));
  $("docket-overlay").hidden = false;
});
$("docket-close").addEventListener("click", () => { $("docket-overlay").hidden = true; });

$("run-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("question-input").value.trim();
  if (!q) return;
  $("run-error").textContent = "";
  try {
    await post("/api/run", { question: q });
  } catch (err) {
    $("run-error").textContent = err.message;
  }
});

document.addEventListener("keydown", (e) => {
  const typing = /^(INPUT|TEXTAREA)$/.test(document.activeElement?.tagName || "");
  if (typing) return;
  if (e.key === "r" || e.key === "R") {
    replayRun("");           // server resolves "" to the newest recorded run
  } else if (e.key === "Escape") {
    $("docket-overlay").hidden = true;
  }
});

loadDocket($("idle-docket"));
connect();
