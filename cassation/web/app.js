/* CASSATION — mission-control courtroom UI. Vanilla JS, no build step.
   Everything on screen is driven by Events from WS /ws (backlog then tail);
   unknown event types are ignored by contract (cassation/PROTOCOL.md). */
"use strict";

// ------------------------------------------------------------------ helpers

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const tail = (s, n) => (s.length > n ? "…" + s.slice(-n) : s);

/* fit text into a one-line element, truncating at a word boundary — never
   mid-word (spec hard rule). Binary-searches the word count that fits. */
function setFittedLine(span, text, wrap) {
  const [pre, post] = wrap ? ["“", "”"] : ["", ""];
  span.textContent = pre + text + post;
  if (span.scrollWidth <= span.clientWidth + 1) return;
  const words = String(text).split(/\s+/);
  let lo = 1, hi = words.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    span.textContent = pre + words.slice(0, mid).join(" ") + "…" + post;
    if (span.scrollWidth <= span.clientWidth + 1) lo = mid; else hi = mid - 1;
  }
  const kept = words.slice(0, lo).join(" ").replace(/[\s,;:.!?—-]+$/, "");
  span.textContent = pre + kept + "…" + post;
  /* pathological case: a single unbreakable token (e.g. a long URL) wider than
     the line — char-slice it down so the ellipsis is ours, never a hard clip */
  if (span.scrollWidth > span.clientWidth + 1 && kept.length > 1) {
    let a = 1, b = kept.length - 1;
    while (a < b) {
      const mid = (a + b + 1) >> 1;
      span.textContent = pre + kept.slice(0, mid) + "…" + post;
      if (span.scrollWidth <= span.clientWidth + 1) a = mid; else b = mid - 1;
    }
    span.textContent = pre + kept.slice(0, a).replace(/[\s,;:.!?—-]+$/, "") + "…" + post;
  }
}

/* word-boundary truncation — never cuts mid-word (spec hard rule) */
function truncWord(s, n) {
  s = String(s ?? "").trim();
  if (s.length <= n) return s;
  let cut = s.slice(0, n + 1);
  const sp = cut.lastIndexOf(" ");
  if (sp > n * 0.5) cut = cut.slice(0, sp);
  else cut = s.slice(0, n);
  return cut.replace(/[\s,;:.!?—-]+$/, "") + "…";
}

function fmtClock(sec) {
  const m = Math.floor(sec / 60), s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

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

/* smooth number ticking for the cost counter */
const tweens = new Map();
function tweenMoney(el, target) {
  const from = tweens.get(el) ?? 0;
  tweens.set(el, target);
  const t0 = performance.now(), dur = 350;
  function frame(now) {
    if (tweens.get(el) !== target) return;        // superseded
    const k = Math.min(1, (now - t0) / dur);
    el.textContent = `£${(from + (target - from) * k).toFixed(2)}`;
    if (k < 1) requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

// ------------------------------------------------------------------ state

const S = {
  runId: null,
  mode: "idle",
  lastT: 0,
  maxT: 0,
  frozen: false,
  claims: new Map(),        // id -> claim payload
  claimOrder: [],
  cards: new Map(),         // id -> {card, pill, live, slots:{adv,skp,tie}, slim}
  agents: new Map(),        // agent_id -> {claimId, role, buf, status, elapsed, model}
  agentsByClaim: new Map(), // claim_id -> [agent_id]
  evByClaim: new Map(),     // claim_id -> [evidence payload]
  citations: new Map(),     // n -> citation
  verdicts: new Map(),      // claim_id -> ClaimVerdict
  synthesisBuf: "",
  grow: { plan: null, replan: null, synth: null },
  steps: [],
  subs: [],
  roleStats: { advocate: { spawned: 0, done: 0 }, skeptic: { spawned: 0, done: 0 },
               tiebreaker: { spawned: 0, done: 0 } },
  struck: new Set(),        // approval-sheet toggles
  approvalGraph: null,
  dependents: new Map(),    // claim_id -> [ids that need it]
  juryClaimId: null,
  drawerClaimId: null,
  lastCost: { est_gbp: 0, sources: 0, agents_live: 0, agents_done: 0 },
  scrolledUp: false,
  autoSwitched: false,
  renderQueued: false,
  takeoverTimers: [],
  burst: null,              // coalesces same-kind record rows arriving in a burst
  draining: false,          // WS backlog is replaying after (re)connect
  pendingGate: null,        // {kind:"approval"|"jury", p} deferred until drain ends
  pendingTakeover: null,    // takeover.show payload deferred until drain ends
  replayPending: false,     // replay POST accepted; run.start must paint REPLAY
};

// ------------------------------------------------------------------ stepper

const STEP = { PLAN: 0, APPROVAL: 1, ADVOCATES: 2, SKEPTICS: 3, DISPUTE: 4,
               RULING: 5, REPLAN: 6, VERDICT: 7 };
const STEPS = [
  { full: "Plan",          short: "Plan" },
  { full: "Your approval", short: "Approve", human: true },
  { full: "Advocates",     short: "Advocates" },
  { full: "Skeptics",      short: "Skeptics" },
  { full: "Dispute check", short: "Dispute" },
  { full: "Your ruling",   short: "Rule", human: true },
  { full: "Re-plan",       short: "Re-plan" },
  { full: "Verdict",       short: "Verdict" },
];

function buildStepper() {
  const ol = $("stepper");
  ol.innerHTML = "";
  STEPS.forEach((st, i) => {
    const li = document.createElement("li");
    li.className = `step${st.human ? " human" : ""} pending`;
    li.id = `step-${i}`;
    li.innerHTML = `
      ${st.human ? '<span class="s-tag">YOUR CALL</span>' : ""}
      <span class="s-node"></span>
      <span class="s-lbl"><span class="full">${st.full}</span><span class="short">${st.short}</span></span>
      <span class="s-sub"></span>`;
    ol.appendChild(li);
  });
  S.steps = STEPS.map(() => "pending");
  S.subs = STEPS.map(() => "");
}

function paintSteps() {
  STEPS.forEach((st, i) => {
    const li = $(`step-${i}`);
    li.className = `step${st.human ? " human" : ""} ${S.steps[i]}`;
    li.querySelector(".s-sub").textContent = S.subs[i];
  });
}

function setSub(i, text) { S.subs[i] = text || ""; paintSteps(); }

function settleBefore(i) {
  for (let j = 0; j < i; j++) {
    if (S.steps[j] === "pending" || S.steps[j] === "active" || S.steps[j] === "wait") {
      S.steps[j] = "done";
      S.subs[j] = "";                       // live sub-labels belong to live steps
    }
  }
}

function stepActivate(i, sub) {
  settleBefore(i);
  if (S.steps[i] !== "done") S.steps[i] = "active";
  if (sub !== undefined) S.subs[i] = sub;
  paintSteps();
}

function stepWait(i, sub) {
  settleBefore(i);
  S.steps[i] = "wait";
  if (sub !== undefined) S.subs[i] = sub;
  paintSteps();
}

function stepDone(i) {
  S.steps[i] = "done";
  S.subs[i] = "";
  paintSteps();
}

function stepsFinish() {
  S.steps = S.steps.map((s) => (s === "active" || s === "wait") ? "done"
                             : (s === "pending") ? "skipped" : s);
  S.subs = S.subs.map(() => "");
  paintSteps();
}

function narrate(text, urgent) {
  const el = $("narration");
  if (el.textContent !== text) {       // ticker: old text fades, new slides in
    el.classList.remove("swap");
    void el.offsetWidth;               // restart the CSS animation
    el.textContent = text;
    el.classList.add("swap");
  }
  el.classList.toggle("urgent", !!urgent);
}

// ------------------------------------------------------------------ court record

/* "you" = the human gates (approval + rulings), amber like jury but distinct:
   JURY is reserved for the court calling the jury (jury.request) only. */
const ROLE_CLS = { judge: "judge", adv: "adv", skp: "skp", clerk: "clerk",
                   jury: "jury", you: "you", sys: "sys", tie: "tie" };
const ROLE_LBL = { judge: "JUDGE", adv: "ADV", skp: "SKP", clerk: "CLERK",
                   jury: "JURY", you: "YOU", sys: "SYS", tie: "TIE" };

function feedEl() { return $("record-feed"); }

function recAutoScroll() {
  if (S.scrolledUp) return;
  const f = feedEl();
  f.scrollTop = f.scrollHeight;
}

function rec(role, html, opts = {}) {
  S.burst = null;                 // any ordinary row ends a coalescing burst
  const row = document.createElement("div");
  row.className = `rec-row${opts.cls ? " " + opts.cls : ""}`;
  if (opts.claimId) { row.dataset.claim = opts.claimId; row.classList.add("click"); }
  row.innerHTML = `
    <span class="rec-t">${fmtClock(S.lastT)}</span>
    <span class="rec-role role-${ROLE_CLS[role] || "sys"}">${ROLE_LBL[role] || "SYS"}</span>
    <span class="rec-msg">${html}</span>`;
  feedEl().appendChild(row);
  recAutoScroll();
  return row;
}

/* plan/replan/synthesis stream into one growing JUDGE row each */
function recGrow(key, text, cap) {
  let g = S.grow[key];
  if (!g) {
    const row = rec("judge", "");
    g = S.grow[key] = { row, msg: row.querySelector(".rec-msg"), buf: "" };
  }
  g.buf += text;
  g.msg.textContent = cap ? tail(g.buf, cap) : g.buf;
  recAutoScroll();
}

function cidChip(id) { return `<span class="rc">${esc(String(id).toUpperCase())}</span>`; }

/* "c1,c2,c3,c5" -> "C1–C3, C5" */
function rangeList(ids) {
  const parsed = [...new Set(ids)].map((id) => {
    const m = String(id).match(/^([a-z]*)(\d+)$/i);
    return m ? { pre: m[1].toUpperCase(), n: Number(m[2]) } : null;
  });
  if (parsed.some((x) => !x)) {
    return [...new Set(ids)].map((i) => String(i).toUpperCase()).join(", ");
  }
  parsed.sort((a, b) => a.n - b.n);
  const parts = [];
  let s = null, e = null, pre = "";
  const flush = () => {
    if (s === null) return;
    parts.push(s === e ? `${pre}${s}` : `${pre}${s}–${pre}${e}`);
  };
  for (const seg of parsed) {
    if (s !== null && seg.pre === pre && seg.n === e + 1) { e = seg.n; continue; }
    flush();
    pre = seg.pre; s = e = seg.n;
  }
  flush();
  return parts.join(", ");
}

/* coalesce same-kind rows (agent spawn waves, "stands" verdicts) that arrive
   within ~2.5s of run time into ONE growing row instead of console spam */
function recBurst(key, role, claimId, render, opts) {
  if (S.burst && S.burst.key === key && S.lastT - S.burst.t0 <= 2.5) {
    S.burst.ids.push(claimId);
    S.burst.msg.innerHTML = render(S.burst.ids);
    recAutoScroll();
    return;
  }
  const ids = [claimId];
  const row = rec(role, render(ids), opts);   // rec() clears S.burst — re-arm after
  S.burst = { key, row, msg: row.querySelector(".rec-msg"), ids, t0: S.lastT };
}

// ------------------------------------------------------------------ claim board

const PILL = {
  pending: "PENDING", running: "RUNNING", supported: "SUPPORTED",
  challenged: "CHALLENGED", contested: "CONTESTED",
  human_ruled: "☗ HUMAN RULED", refuted: "REFUTED",
};

function makeCard(claim) {
  const card = document.createElement("article");
  card.className = "claim state-pending";
  card.id = `card-${claim.id}`;
  // deal-in: cards enter with a ~45ms stagger (cosmetic only)
  card.classList.add("deal");
  card.style.animationDelay = `${Math.max(0, S.claimOrder.length - 1) * 45}ms`;
  card.addEventListener("animationend", () => {
    card.classList.remove("deal");
    card.style.animationDelay = "";
  }, { once: true });
  const deps = (claim.depends_on || []).length
    ? `<div class="c-deps">${claim.depends_on.map((d) =>
        `<span class="dep-chip">needs ${esc(d.toUpperCase())}</span>`).join("")}</div>` : "";
  card.innerHTML = `
    <div class="c-top">
      <span class="cid">${esc(claim.id.toUpperCase())}</span>
      <span class="c-branch">${esc(claim.branch || "")}</span>
      <span class="c-pill">PENDING</span>
    </div>
    <p class="c-text">${esc(claim.text)}</p>
    <div class="c-agents">
      <span class="slot adv" data-state="idle"><i class="slot-ind"></i><b class="slot-lbl">Defender waiting</b><em></em></span>
      <span class="slot skp" data-state="idle"><i class="slot-ind"></i><b class="slot-lbl">Attacker waiting</b><em></em></span>
      <span class="slot tie" data-state="idle" hidden><i class="slot-ind"></i><b class="slot-lbl">Tie-break</b><em></em></span>
    </div>
    <div class="c-live"></div>
    ${deps}
    <span class="seal">⚖ human ruled</span>`;
  card.addEventListener("click", () => {
    if (!S.cards.get(claim.id)?.slim) openDrawer(claim.id);
  });
  $("board").appendChild(card);
  S.cards.set(claim.id, {
    card,
    pill: card.querySelector(".c-pill"),
    live: card.querySelector(".c-live"),
    slots: {
      advocate: card.querySelector(".slot.adv"),
      skeptic: card.querySelector(".slot.skp"),
      tiebreaker: card.querySelector(".slot.tie"),
    },
    state: "pending",
    slim: false,
  });
}

function buildBoard(graph) {
  const board = $("board");
  board.innerHTML = "";
  $("board-dead").innerHTML = "";
  $("board-dead").hidden = true;
  S.cards.clear();
  S.claims.clear();
  S.claimOrder = [];
  (graph.claims || []).forEach((c) => {
    S.claims.set(c.id, c);
    S.claimOrder.push(c.id);
    makeCard(c);
  });
  document.body.dataset.view = "board";
}

function collapseSlim(claimId, state, note) {
  const entry = S.cards.get(claimId);
  const claim = S.claims.get(claimId);
  if (!entry || !claim) return;
  entry.slim = true;
  entry.state = state;          // verdict chips read this before verdict.complete
  entry.card.className = `claim slim state-${state}`;
  const stamp = state === "struck" ? "STRUCK" : "timed out — excluded";
  entry.card.innerHTML = `
    <span class="cid">${esc(claimId.toUpperCase())}</span>
    <span class="slim-text">${esc(claim.text)}</span>
    <span class="stamp">${stamp}</span>`;
  if (note) entry.card.title = note;
  // dead weight pins into a fixed strip under the board: a slim row is always
  // fully visible, never cut in half by the scroll fold
  const dead = $("board-dead");
  dead.appendChild(entry.card);
  dead.hidden = false;
}

/* a CHALLENGED claim is only "under challenge" while its skeptic is still
   working (or none has reported yet); the bare verdict word CHALLENGED is
   reserved for a challenge that actually stands after the skeptic reports */
function challengeInFlight(claimId) {
  const skps = (S.agentsByClaim.get(claimId) || [])
    .map((aid) => S.agents.get(aid))
    .filter((a) => a && a.role === "skeptic");
  if (!skps.length) return true;
  return skps.some((a) => a.status === "live");
}

function refreshChallenged(claimId) {
  const entry = S.cards.get(claimId);
  if (!entry || entry.slim || entry.state !== "challenged") return;
  const prov = challengeInFlight(claimId);
  entry.card.classList.toggle("provisional", prov);
  entry.pill.textContent = prov ? "↻ UNDER CHALLENGE" : PILL.challenged;
  if (S.drawerClaimId === claimId) renderDrawer(claimId);
}

function setClaimState(claimId, state, note) {
  const entry = S.cards.get(claimId);
  if (!entry) return;
  if (state === "struck" || state === "excluded") { collapseSlim(claimId, state, note); return; }
  if (entry.slim) return;                       // struck/excluded never come back
  const wasContested = entry.state === "contested";
  entry.state = state;
  /* while the skeptic is still working, CHALLENGED means "under attack right
     now", not a verdict — so the pill says UNDER CHALLENGE (outlined, spinner)
     and the board never reads "everything just failed" mid-attack */
  const prov = state === "challenged" && challengeInFlight(claimId);
  entry.card.className = `claim state-${state}${prov ? " provisional" : ""}`;
  entry.pill.textContent = prov ? "↻ UNDER CHALLENGE" : (PILL[state] || state.toUpperCase());
  if (note) entry.card.title = note;
  paintSlots(claimId);        // skeptic slot outcome suffix tracks the state
  if (state === "contested" && !wasContested) {
    entry.card.classList.add("flash");
    entry.card.addEventListener("animationend", () => entry.card.classList.remove("flash"),
                                { once: true });
  }
  if (S.drawerClaimId === claimId) renderDrawer(claimId);
}

/* aggregate per-role slot state on a card */
function paintSlots(claimId) {
  const entry = S.cards.get(claimId);
  if (!entry || entry.slim) return;
  const ids = S.agentsByClaim.get(claimId) || [];
  const byRole = { advocate: [], skeptic: [], tiebreaker: [] };
  ids.forEach((aid) => {
    const a = S.agents.get(aid);
    if (a && byRole[a.role]) byRole[a.role].push(a);
  });
  const ev = S.evByClaim.get(claimId) || [];
  for (const [role, list] of Object.entries(byRole)) {
    const slot = entry.slots[role];
    if (!slot) continue;
    if (!list.length) { slot.hidden = role === "tiebreaker"; continue; }
    slot.hidden = false;
    const live = list.some((a) => a.status === "live");
    const bad = list.length && list.every((a) => a.status === "timeout" || a.status === "error");
    slot.dataset.state = live ? "live" : bad ? "err" : "done";
    slot.dataset.outcome = "";
    slot.title = list.map((a) => `${role} ${a.id} · ${a.model || ""} · ${a.status}`).join("\n");
    const lbl = slot.querySelector(".slot-lbl");
    const em = slot.querySelector("em");
    em.textContent = "";
    /* plain words a judge can read from 3 m — no ADV/SKP codes, no bare decimals */
    if (role === "advocate") {
      const n = ev.filter((e) => e.stance !== "refute").length;
      lbl.textContent = live ? "Defending…" : bad ? "Defender timed out"
        : n ? `Defended · ${n} ${n === 1 ? "source" : "sources"}` : "Defended · no source";
    } else if (role === "skeptic") {
      if (live) { lbl.textContent = "Attacking…"; }
      else if (bad) { lbl.textContent = "Attacker timed out"; }
      else {
        const st = Math.max(0, ...ev.filter((e) => e.stance === "refute").map((e) => e.strength || 0));
        const [txt, oc] = !st ? ["Attacked — found nothing", "held"]
          : entry.state === "supported" ? ["Attacked — held", "held"]
          : (entry.state === "contested" || entry.state === "human_ruled") ? ["Attacked — disputed", "disputed"]
          : entry.state === "refuted" ? ["Attacked — broke it", "broke"]
          : entry.state === "challenged" ? ["Attacked — clerk weighing", "disputed"]
          : ["Attacked", ""];
        lbl.textContent = txt;
        slot.dataset.outcome = oc;
        if (st) slot.title += `\nchallenge strength ${st.toFixed(1)} of 1`;
      }
    } else {
      lbl.textContent = live ? "Tie-break…" : bad ? "Tie-break timed out" : "Tie-break done";
    }
  }
}

/* one-line live region: latest evidence quote, else latest agent activity */
function paintLive(claimId) {
  const entry = S.cards.get(claimId);
  if (!entry || entry.slim) return;
  const ev = S.evByClaim.get(claimId) || [];
  if (ev.length) {
    const last = ev[ev.length - 1];
    entry.live.className = "c-live";
    entry.live.innerHTML = `
      <span class="q"></span>
      <span class="src-badge">${ev.length} ${ev.length === 1 ? "source" : "sources"}</span>`;
    setFittedLine(entry.live.querySelector(".q"), last.quote || "", true);
    return;
  }
  const ids = S.agentsByClaim.get(claimId) || [];
  for (let i = ids.length - 1; i >= 0; i--) {
    const a = S.agents.get(ids[i]);
    if (a && a.status === "live" && a.last) {
      // agent working-status text, NOT a quote: plain sans "activity" voice,
      // working glyph, no raw "+ URL"-style tail fragments
      let act = a.last.replace(/\s*\+\s*URL\S*/gi, "").trim();
      if (/^[A-Z][a-z]/.test(act)) act = act[0].toLowerCase() + act.slice(1);
      entry.live.className = "c-live activity";
      entry.live.innerHTML = `<span class="act-glyph">⟳</span><span class="q"></span>`;
      setFittedLine(entry.live.querySelector(".q"), act, false);
      return;
    }
  }
}

// ------------------------------------------------------------------ markdown

function mdToHtml(md) {
  const inline = (s) => esc(s)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*]+)\*(?!\*)/g, "$1<em>$2</em>")
    .replace(/\[(\d+)\]/g, (m, n) => {
      const c = S.citations.get(Number(n));
      if (!c) return `<span class="cite">${n}</span>`;
      const tip = esc(`${c.label || ""}${c.quote ? ` — “${c.quote}”` : ""}`);
      return c.url
        ? `<a class="cite" href="${esc(c.url)}" target="_blank" rel="noopener" title="${tip}">${n}</a>`
        : `<span class="cite" title="${tip}">${n}</span>`;
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

/* the pane is already labeled VERDICT, so strip a leading "Research Verdict —"
   from the streamed title to avoid printing the same words twice stacked */
function dedupeVerdictTitle(md) {
  return md.replace(/^(#{1,3})\s+Research Verdict\s*[—:–-]\s*(\S)/i,
    (m, h, c) => `${h} ${c.toUpperCase()}`);
}

function renderSynthesis() {
  if (S.renderQueued) return;
  S.renderQueued = true;
  requestAnimationFrame(() => {
    S.renderQueued = false;
    $("verdict-body").innerHTML = mdToHtml(dedupeVerdictTitle(S.synthesisBuf));
  });
}

// ------------------------------------------------------------------ tabs + verdict

function showTab(name) {
  const rec_ = name === "record";
  $("tab-record").classList.toggle("on", rec_);
  $("tab-record").setAttribute("aria-selected", String(rec_));
  $("tab-verdict").classList.toggle("on", !rec_);
  $("tab-verdict").setAttribute("aria-selected", String(!rec_));
  $("record-pane").hidden = !rec_;
  $("verdict-pane").hidden = rec_;
  if (rec_) recAutoScroll();
}

const VCLS = { SUPPORTED: "g", REFUTED: "r", CONTESTED: "a", HUMAN_ADJUDICATED: "gold",
               EXCLUDED: "x", STRUCK: "x sk" };
const SCLS = { supported: "g", challenged: "r", refuted: "r", contested: "a",
               human_ruled: "gold", excluded: "x", struck: "x sk" };

function renderVerdictChips() {
  const box = $("verdict-chips");
  box.innerHTML = "";
  S.claimOrder.forEach((id) => {
    const v = S.verdicts.get(id);
    const entry = S.cards.get(id);
    const cls = v ? (VCLS[v.verdict] || "") : (SCLS[entry?.state] || SCLS[entry?.slim ? "struck" : ""] || "");
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = `v-chip ${cls}`;
    chip.textContent = id.toUpperCase();
    chip.title = v ? `${v.verdict}${v.confidence ? ` · ${Math.round(v.confidence * 100)}%` : ""}` : "";
    chip.addEventListener("click", () => {
      openDrawer(id);
      $(`card-${id}`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    });
    box.appendChild(chip);
  });
}

const VCOLOR = { SUPPORTED: "var(--green)", REFUTED: "var(--red)", CONTESTED: "var(--amber)",
                 HUMAN_ADJUDICATED: "var(--gold)", EXCLUDED: "var(--grey)", STRUCK: "var(--grey)" };

function renderLedger(verdicts) {
  const box = $("verdict-ledger");
  box.innerHTML = `<div class="ledger-head">Confidence ledger</div>`;
  verdicts.forEach((v) => {
    const row = document.createElement("div");
    row.className = "ledger-row";
    const color = VCOLOR[v.verdict] || "var(--grey)";
    const pct = Math.round((v.confidence || 0) * 100);
    // struck/excluded claims never ran: honest absence (—), not a zero score
    const dead = v.verdict === "STRUCK" || v.verdict === "EXCLUDED";
    row.innerHTML = `
      <span class="cid">${esc(v.claim_id.toUpperCase())}</span>
      <span class="lv" style="color:${color}">${esc(v.verdict.replace("_", " "))}</span>
      ${dead ? '<span class="ledger-bar none"></span>'
             : `<span class="ledger-bar"><i style="width:${pct}%;background:${color}"></i></span>`}
      <span class="lp">${dead ? "—" : `${pct}%`}</span>`;
    if (v.summary) row.title = v.summary;
    box.appendChild(row);
  });
  box.hidden = false;
}

/* one big plain-word tally bar: how many claims held, broke, or were ruled by you */
function renderTally(verdicts) {
  const box = $("verdict-tally");
  const n = { held: 0, broke: 0, you: 0, open: 0, out: 0 };
  verdicts.forEach((v) => {
    const k = { SUPPORTED: "held", REFUTED: "broke", HUMAN_ADJUDICATED: "you",
                CONTESTED: "open", STRUCK: "out", EXCLUDED: "out" }[v.verdict] || "open";
    n[k]++;
  });
  const total = verdicts.length - n.out;
  if (!total) { box.hidden = true; return; }
  const seg = (k, lbl) => n[k] ? `<i class="t-${k}" style="flex:${n[k]}">${n[k]} ${lbl}</i>` : "";   // label sits in the words line too
  const words = [n.held && `${n.held} held up`, n.broke && `${n.broke} broke under attack`,
                 n.you && `${n.you} ruled by you`, n.open && `${n.open} still disputed`,
                 n.out && `${n.out} struck`].filter(Boolean).join(" · ");
  box.innerHTML = `<div class="tally-bar">${seg("held", "held")}${seg("broke", "broke")}${seg("you", "ruled by you")}${seg("open", "disputed")}</div>
    <div class="tally-words">${esc(words)}</div>`;
  box.hidden = false;
}

function renderCompletion() {
  const box = $("completion-card");
  const c = S.lastCost;
  const qr = S.runId ? `
    <div class="cc-qr">
      <img src="/api/qr/${encodeURIComponent(S.runId)}.png" alt="QR code to the phone brief">
      <a href="/brief/${encodeURIComponent(S.runId)}" target="_blank" rel="noopener">
        Open the brief on your phone →</a>
    </div>` : "";
  box.innerHTML = `
    <div class="cc-head">Case closed</div>
    <div class="cc-stats">
      <span class="ct"><b>£${(c.est_gbp || 0).toFixed(2)}</b><i>estimate</i></span>
      <span class="ct"><b>${c.sources || 0}</b><i>sources</i></span>
      <span class="ct"><b>${c.agents_done || 0}</b><i>agents</i></span>
      <span class="ct"><b>${fmtClock(S.maxT)}</b><i>${S.mode === "replay" ? "replayed" : "elapsed"}</i></span>
    </div>
    ${qr}
    <div class="cc-meta">trace: runs/${esc(S.runId || "?")}/trace.jsonl · press R to replay the latest case</div>`;
  box.hidden = false;
}

// ------------------------------------------------------------------ evidence drawer

function openDrawer(claimId) {
  if (!S.claims.has(claimId)) return;
  S.drawerClaimId = claimId;
  renderDrawer(claimId);
  // the header grows when the question wraps — anchor the drawer to its live
  // bottom edge so the two never overlap
  $("drawer").style.top = `${Math.round($("header").getBoundingClientRect().bottom)}px`;
  $("drawer").hidden = false;
}

function closeDrawer() {
  S.drawerClaimId = null;
  $("drawer").hidden = true;
}

function renderDrawer(claimId) {
  const claim = S.claims.get(claimId);
  const entry = S.cards.get(claimId);
  if (!claim) return;
  const state = entry?.slim ? (entry.card.className.includes("struck") ? "struck" : "excluded")
                            : (entry?.state || "pending");
  $("drawer-cid").textContent = claimId.toUpperCase();
  $("drawer-branch").textContent = claim.branch || "";
  const pill = $("drawer-pill");
  const prov = state === "challenged" && entry && !entry.slim
    && entry.card.classList.contains("provisional");
  pill.textContent = prov ? "↻ UNDER CHALLENGE" : (PILL[state] || state.toUpperCase());
  pill.className = `c-pill pill-${state}`;   // state colors match the board card
  const card = entry?.card;
  const body = $("drawer-body");
  const deps = (claim.depends_on || []).length
    ? `<div class="c-deps">${claim.depends_on.map((d) =>
        `<span class="dep-chip">needs ${esc(d.toUpperCase())}</span>`).join("")}</div>` : "";
  const note = card?.title ? `<p class="d-note">${esc(card.title)}</p>` : "";

  const ids = S.agentsByClaim.get(claimId) || [];
  const agentRows = ids.map((aid) => {
    const a = S.agents.get(aid);
    if (!a) return "";
    const roleCls = { advocate: "adv", skeptic: "skp", tiebreaker: "tie" }[a.role] || "adv";
    const st = a.status === "live" ? "running…"
      : a.status === "ok" ? `✓ ${a.elapsed != null ? a.elapsed.toFixed(1) + "s" : "done"}`
      : a.status.toUpperCase();
    return `<div class="d-agent">
      <span class="role ${roleCls}">${roleCls.toUpperCase()}</span>
      <span>${esc(aid)} · ${esc(a.model || "")}</span>
      <span class="st${a.status === "timeout" || a.status === "error" ? " err" : ""}">${esc(st)}</span>
    </div>`;
  }).join("");

  const ev = S.evByClaim.get(claimId) || [];
  const evRows = ev.map((e) => {
    const stance = e.stance === "refute" ? "refute" : "support";
    const metric = stance === "refute" ? (e.strength || 0) : (e.confidence || 0);
    const metricLbl = stance === "refute" ? "strength" : "confidence";
    const color = stance === "refute" ? "var(--red)" : "var(--green)";
    const src = e.url
      ? `<a href="${esc(e.url)}" target="_blank" rel="noopener">${esc(e.source_label || e.url)}</a>`
      : `<span>${esc(e.source_label || "unsourced")}</span>`;
    return `<div class="d-ev ${stance}">
      <p class="q">“${esc(e.quote || "")}”</p>
      <div class="meta">
        <span class="stance-w ${stance}">${stance}</span>
        ${src}
        <span class="prov ${e.provenance === "grounded" ? "grounded" : ""}">${esc(e.provenance || "model-reported")}</span>
        <span>${metricLbl} ${Math.round(metric * 100)}%</span>
      </div>
      <div class="d-bar"><i style="width:${Math.round(metric * 100)}%;background:${color}"></i></div>
    </div>`;
  }).join("");

  const v = S.verdicts.get(claimId);
  const verdictSect = v ? `
    <div class="d-sect">Verdict</div>
    <p class="d-claim" style="font-size:13px">${esc(v.verdict.replace("_", " "))}
      · ${Math.round((v.confidence || 0) * 100)}%${v.summary ? ` — ${esc(v.summary)}` : ""}</p>` : "";

  body.innerHTML = `
    <p class="d-claim">${esc(claim.text)}</p>
    ${note}${deps}
    <div class="d-sect">Agents</div>
    ${agentRows || '<p class="d-empty">No agents assigned yet.</p>'}
    <div class="d-sect">Evidence</div>
    ${evRows || '<p class="d-empty">No evidence on the record yet.</p>'}
    ${verdictSect}`;
}

// ------------------------------------------------------------------ gates

function showGate(text) {
  $("gate-banner-text").textContent = text;
  $("gate-banner").hidden = false;
  document.body.classList.add("gated");
}

function hideGate() {
  $("gate-banner").hidden = true;
  document.body.classList.remove("gated");
}

function approvalNumbers() {
  const g = S.approvalGraph;
  if (!g) return { total: 0, kept: 0, agents: 0, cost: 0 };
  const total = (g.claims || []).length;
  const kept = total - S.struck.size;
  let cost = g.est_total_cost_gbp || 0;
  (g.claims || []).forEach((c) => { if (S.struck.has(c.id)) cost -= (c.est_cost_gbp || 0); });
  const base = g.est_agents || Math.round(total * 1.5);
  const agents = total ? Math.max(kept, Math.round(base * kept / total)) : 0;
  return { total, kept, agents, cost: Math.max(0, cost) };
}

function paintRecount() {
  const { total, kept, agents, cost } = approvalNumbers();
  const lead = S.struck.size ? `${total} → ${kept}` : `${total}`;
  // mono is for figures only; the connecting words are tiny caps (same pattern
  // as the counter bar labels)
  $("approval-recount").innerHTML =
    `<b>${lead}</b> <i>claims</i> · <b>~${agents}</b> <i>agents</i> · ` +
    `<i>est</i> <b>£${cost.toFixed(2)}</b> <i>(estimate)</i>`;
  $("approve-btn").textContent = `Approve & empanel ${agents} agents`;
  // dependency warnings on struck rows
  document.querySelectorAll("#approval-rows .arow").forEach((row) => {
    const warnBox = row.querySelector(".arow-warns");
    if (!warnBox) return;
    const id = row.dataset.id;
    if (S.struck.has(id)) {
      const dependents = (S.dependents.get(id) || []).filter((d) => !S.struck.has(d));
      warnBox.innerHTML = dependents.map((d) =>
        `<span class="dep-warn">${esc(d.toUpperCase())} depends on this</span>`).join("");
    } else {
      warnBox.innerHTML = "";
    }
  });
}

function openApproval(graph) {
  S.approvalGraph = graph;
  S.struck.clear();
  S.dependents.clear();
  (graph.claims || []).forEach((c) => {
    (c.depends_on || []).forEach((d) => {
      if (!S.dependents.has(d)) S.dependents.set(d, []);
      S.dependents.get(d).push(c.id);
    });
  });
  const rows = $("approval-rows");
  rows.innerHTML = "";
  (graph.claims || []).forEach((c) => {
    const row = document.createElement("div");
    row.className = "arow";
    row.dataset.id = c.id;
    const deps = (c.depends_on || []).map((d) =>
      `<span class="dep-chip">needs ${esc(d.toUpperCase())}</span>`).join("");
    row.innerHTML = `
      <span class="cid">${esc(c.id.toUpperCase())}</span>
      <span class="arow-main">
        <span class="arow-text"><b class="arow-branch">${esc(c.branch || "")}</b> —
          ${esc(c.text)}</span>
        ${deps}<span class="arow-warns"></span>
      </span>
      <span class="stamp">Struck</span>
      <span class="arow-cost">est £${(c.est_cost_gbp || 0).toFixed(2)}</span>
      <button class="arow-strike" type="button" aria-label="Strike ${esc(c.id)}" title="Strike this claim">⊘</button>`;
    const toggle = () => {
      row.classList.toggle("struck");
      if (row.classList.contains("struck")) S.struck.add(c.id);
      else S.struck.delete(c.id);
      paintRecount();
    };
    row.addEventListener("click", toggle);
    row.querySelector(".arow-strike").addEventListener("click", (e) => {
      e.stopPropagation();
      toggle();
    });
    rows.appendChild(row);
  });
  paintRecount();
  $("approval-overlay").hidden = false;
}

async function submitApproval() {
  $("approval-overlay").hidden = true;
  hideGate();
  try {
    const r = await post("/api/approve", {
      run_id: S.runId, approved: true, struck_claim_ids: [...S.struck],
    });
    toast(r.resolved ? "Plan approved — empaneling agents"
                     : "Approval sent (no live gate — mock/replay)");
  } catch (e) {
    toast(`Approval failed: ${e.message}`);
  }
}

/* "Kang, Li, Dai, Lee & Hu, Science 361, 575 (2018)" -> "Kang 2018" */
function shortSource(label) {
  label = String(label || "").trim();
  if (!label) return "";
  const year = (label.match(/\((\d{4})\)/) || [])[1] || "";
  const name = (label.split(/[,&(]/)[0] || "").trim().replace(/\s+et al\.?$/i, "");
  if (name && year && name.length <= 18) return `${name} ${year}`;
  return truncWord(label, 24);
}

function openJury(req) {
  S.juryClaimId = req.claim_id;
  // lead with the claim id + claim text — the heading above already says
  // "the agents cannot settle this", so never repeat that phrase here
  const claim = S.claims.get(req.claim_id);
  const lead = claim ? claim.text
    : String(req.question || "").replace(/^the agents cannot settle this claim:\s*/i, "");
  $("jury-question").innerHTML =
    `<span class="cid">${esc((req.claim_id || "").toUpperCase())}</span> ${esc(lead)}`;
  const box = $("jury-options");
  box.innerHTML = "";
  (req.options || []).forEach((opt) => {
    // option "a" is always the uphold/support side, "b" the counter-evidence
    // (engine contract) — carry the takeover's green-vs-red stance coding and
    // make each CTA self-contained, so nobody has to read body text to rule
    const uphold = (opt.key || "").toLowerCase() === "a";
    const verb = uphold ? "Uphold" : "Overturn";
    const src = shortSource(opt.source_label);
    const cta = src ? `${verb} — rule for ${src}` : `${verb} this claim`;
    const el = document.createElement("div");
    el.className = `jury-opt ${uphold ? "uphold" : "counter"}`;
    el.innerHTML = `
      <span class="opt-key">OPTION ${esc((opt.key || "").toUpperCase())}</span>
      <span class="opt-summary">${esc(opt.summary || "")}</span>
      <span class="opt-quote">“${esc(opt.quote || "")}”</span>
      <span class="opt-src">${opt.url
        ? `<a href="${esc(opt.url)}" target="_blank" rel="noopener">${esc(opt.source_label || opt.url)}</a>`
        : esc(opt.source_label || "")}</span>
      <button class="btn rule" type="button">${esc(cta)}</button>`;
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
        toast(`Ruling failed: ${e.message}`);
      }
      setTimeout(() => { $("jury-overlay").hidden = true; hideGate(); }, 600);
    });
    box.appendChild(el);
  });
  $("jury-overlay").hidden = false;
}

// ------------------------------------------------------------------ takeover

function showTakeover(p) {
  const fill = (id, side) => {
    const lbl = id === "takeover-left" ? "The record says" : "The skeptic found";
    $(id).innerHTML = `<span class="tk-lbl">${lbl}</span>“${esc(side.quote || "")}”<span class="src">${side.url
      ? `<a href="${esc(side.url)}" target="_blank" rel="noopener" style="color:inherit">${esc(side.source_label || side.url)}</a>`
      : esc(side.source_label || "")}</span>`;
  };
  fill("takeover-left", p.left || {});
  fill("takeover-right", p.right || {});
  const claim = S.claims.get(p.claim_id);
  $("takeover-claim").textContent = claim ? claim.text : (p.claim_id || "");
  // name the claim so the takeover ties to the board + the "ruling on C2" banner
  const cid = (p.claim_id || "").toUpperCase();
  const branch = (claim?.branch || "").toUpperCase();
  $("tk-cid").textContent = branch ? `${cid} · ${branch}` : cid;
  $("tk-meta").hidden = !cid;
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
    .map(([k, v]) => `<span class="vc ${cls[k] || "x"}">${v} ${esc(k.toLowerCase().replace(/_/g, "-"))}</span>`)
    .join("");
}

async function loadDocket(targetEl) {
  let runs = [];
  try { runs = await (await fetch("/api/runs")).json(); } catch (e) { /* server gone */ }
  targetEl.innerHTML = runs.length ? "" : `<p>No case files yet — the first verdict lands here.</p>`;
  runs.forEach((r, i) => {
    const el = document.createElement("div");
    el.className = "case-file";
    const when = (r.started_at || "").slice(0, 16).replace("T", " · ");
    // /api/runs is newest-first; the newest case is what hotkey R replays
    const latest = i === 0 ? '<span class="case-latest">Latest</span>' : "";
    el.innerHTML = `
      <div class="case-q">${latest}${esc(r.question || r.run_id)}</div>
      <div class="case-verdicts">${verdictCountChips(r.verdict_counts)}</div>
      <div class="case-meta">
        <span class="num">${esc(when)}</span>
        <span><b class="num">${r.n_claims || 0}</b> claims</span>
        <span class="num">${fmtClock(r.duration_s || 0)}</span>
        <span class="replay-cta">▶ Replay</span>
      </div>`;
    el.addEventListener("click", () => replayRun(r.run_id));
    targetEl.appendChild(el);
  });
}

async function replayRun(runId) {
  try {
    const r = await post("/api/replay", { run_id: runId || "" });
    // the POST confirms the source switch — paint REPLAY now, so the recorded
    // run.start's original mode never flashes on the chip
    S.replayPending = true;
    S.replaySpeed = r.speed || 1;
    setMode("replay");
    $("docket-overlay").hidden = true;
    toast(`REPLAY — ${r.run_id}`);
  } catch (e) {
    toast(`Replay failed: ${e.message}`);
  }
}

// ------------------------------------------------------------------ reset + mode

function resetRun() {
  S.claims.clear(); S.cards.clear(); S.agents.clear(); S.agentsByClaim.clear();
  S.evByClaim.clear(); S.citations.clear(); S.verdicts.clear();
  S.claimOrder = [];
  S.synthesisBuf = "";
  S.grow = { plan: null, replan: null, synth: null };
  S.struck.clear(); S.approvalGraph = null; S.dependents.clear();
  S.juryClaimId = null; S.drawerClaimId = null;
  S.maxT = 0; S.lastT = 0; S.frozen = false;
  S.scrolledUp = false; S.autoSwitched = false;
  S.roleStats = { advocate: { spawned: 0, done: 0 }, skeptic: { spawned: 0, done: 0 },
                  tiebreaker: { spawned: 0, done: 0 } };
  S.takeoverTimers.forEach(clearTimeout); S.takeoverTimers = [];
  S.lastCost = { est_gbp: 0, sources: 0, agents_live: 0, agents_done: 0 };
  S.burst = null;
  S.pendingGate = null; S.pendingTakeover = null;   // gates of a previous run are void

  buildStepper();
  narrate("The court assembles…");
  $("board").innerHTML = `<div class="board-empty" id="board-empty">The Judge is drafting the claim board…</div>`;
  $("board-dead").innerHTML = "";
  $("board-dead").hidden = true;
  $("record-feed").innerHTML = "";
  $("jump-live").hidden = true;
  $("jump-live").textContent = "jump to live ↓";
  $("verdict-chips").innerHTML = "";
  $("verdict-body").innerHTML = `<p class="vp-empty">The verdict is written at the end of the trial.</p>`;
  $("verdict-ledger").hidden = true;
  $("verdict-ledger").innerHTML = "";
  $("completion-card").hidden = true;
  $("vp-status").textContent = "streaming…";
  showTab("record");
  closeDrawer();
  hideGate();
  $("guard-banner").hidden = true;
  ["approval-overlay", "jury-overlay", "takeover", "docket-overlay"].forEach(
    (id) => { $(id).hidden = true; });
  tweens.clear();
  $("ct-cost").textContent = "£0.00";
  $("ct-sources").textContent = "0";
  $("ct-agents").textContent = "0 live · 0 done";
  $("ct-clock").textContent = "0:00";
  $("ct-clock").nextElementSibling.textContent = "elapsed";
  document.body.classList.remove("verdict-stage");
  $("verdict-tally").hidden = true;
}

function setMode(mode, extra) {
  S.mode = mode;
  const chip = $("mode-chip");
  chip.className = `mode-chip mode-${mode}`;
  $("mode-text").textContent = mode.toUpperCase() + (extra ? ` · ${extra}` : "");
}

/* "Replay ×1 of a recorded mock run (scripted, compressed timings) — original took 0:28" */
function replayLine(elapsed) {
  const o = S.replayOf || {};
  const sp = S.replaySpeed && S.replaySpeed !== 1 ? ` ×${S.replaySpeed}` : "";
  const dur = fmtClock(o.dur || elapsed);
  const kind = o.mode === "live" ? `live run took ${dur}`
    : o.mode === "mock" ? `recorded MOCK run (scripted timings, ${dur}) — a live run takes ~2–3 min`
    : `recorded run took ${dur}`;
  return `Case closed — replay${sp} · ${kind}.`;
}

function advSub() {
  if (S.steps[STEP.ADVOCATES] !== "active") return;
  const r = S.roleStats.advocate;
  setSub(STEP.ADVOCATES, r.spawned ? `${r.done} of ${r.spawned} reported` : "");
}
function skpSub() {
  if (S.steps[STEP.SKEPTICS] !== "active") return;
  const r = S.roleStats.skeptic;
  setSub(STEP.SKEPTICS, r.spawned ? `${r.done} of ${r.spawned} reported` : "");
}

// ------------------------------------------------------------------ event handlers

const HANDLERS = {
  "run.start": (p) => {
    resetRun();
    S.runId = p.run_id;
    $("question").textContent = p.question || "";
    // a replay's recorded run.start carries the ORIGINAL mode — the chip must
    // keep saying REPLAY (replay.info adds the recorded-time suffix next)
    setMode(S.replayPending ? "replay" : (p.mode || "live"));
    S.replayPending = false;
    document.body.dataset.view = "board";
    stepActivate(STEP.PLAN, "reading the question…");
    narrate("The court is in session — the Judge reads the question…");
    rec("sys", `Court is in session — case ${esc(p.run_id || "")} (${esc(p.mode || "live")}).`, { cls: "dim" });
  },

  "replay.info": (p) => {
    const recAt = (p.recorded_at_iso || "").slice(11, 16);
    setMode("replay", recAt ? `rec ${recAt}` : (p.original_run_id || ""));
    /* honest labelling: say what kind of run is being replayed and how long it
       really took, from the docket (meta.json / trace) — never let a 0:28 mock
       recording pass for a live 2–3 min run */
    S.replayOf = { id: p.original_run_id, mode: null, dur: null };
    $("ct-clock").nextElementSibling.textContent = "replay clock";
    fetch("/api/runs").then((r) => r.json()).then((runs) => {
      const m = (runs || []).find((x) => x.run_id === p.original_run_id);
      if (m && S.replayOf && S.replayOf.id === p.original_run_id) {
        S.replayOf.dur = m.duration_s || null;
        S.replayOf.mode = m.mode || null;
      }
    }).catch(() => {});
  },

  "guardrail.result": (p) => {
    if (p.ok) {
      rec("judge", `Question admitted — ${esc(p.reason || "within the court's remit")}.`);
    } else {
      $("guard-banner").textContent = `Guardrail refused the question — ${p.reason || "out of scope"}`;
      $("guard-banner").hidden = false;
      narrate("The guardrail refused this question.", true);
      rec("sys", `Guardrail REFUSED — ${esc(p.reason || "")}.`, { cls: "hl-red" });
    }
  },

  "plan.delta": (p) => {
    stepActivate(STEP.PLAN, "decomposing…");
    narrate("The Judge is decomposing the question into testable claims…");
    recGrow("plan", p.text || "");
  },

  "plan.complete": (p) => {
    buildBoard(p);
    const n = (p.claims || []).length;
    const branches = new Set((p.claims || []).map((c) => c.branch || "")).size;
    setSub(STEP.PLAN, `${n} claims filed`);
    rec("judge", `The Judge files ${n} claims across ${branches} branches.`);
  },

  "approval.request": (p) => {
    if (!S.cards.size) buildBoard(p);
    stepWait(STEP.APPROVAL, "awaiting you");
    narrate("Paused — your approval is needed before any agent runs.", true);
    rec("you", "THE COURT ASKS YOUR APPROVAL — strike any claim, then empanel.", { cls: "hl-amber" });
    if (S.draining) { S.pendingGate = { kind: "approval", p }; return; }
    showGate("Run paused — the court waits for your approval");
    openApproval(p);
  },

  "approval.result": (p) => {
    if (S.pendingGate && S.pendingGate.kind === "approval") S.pendingGate = null;
    $("approval-overlay").hidden = true;
    hideGate();
    stepDone(STEP.APPROVAL);
    const struck = p.struck_claim_ids || [];
    struck.forEach((id) => setClaimState(id, "struck", "struck by you at approval — never runs"));
    if (p.approved) {
      narrate("Plan approved — empaneling the agents…");
      rec("you", `You approved the plan — ${struck.length
        ? `${struck.map((s) => s.toUpperCase()).join(", ")} struck.` : "nothing struck."}`);
    } else {
      narrate("You rejected the plan — case closed.", true);
      rec("you", "You rejected the plan — case closed.", { cls: "hl-red" });
    }
  },

  "claim.state": (p) => {
    const prev = S.cards.get(p.claim_id)?.state;
    setClaimState(p.claim_id, p.state, p.note);
    const C = cidChip(p.claim_id);
    if (p.state === "struck") {
      rec("clerk", `${C} struck by you — it never runs.`, { cls: "dim" });
    } else if (p.state === "refuted") {
      rec("clerk", `${C} REFUTED${p.note ? ` — ${esc(p.note)}` : ""}.`, { cls: "hl-red" });
    } else if (p.state === "supported" && p.note && prev === "challenged") {
      // identical notes arriving in a burst coalesce into one row
      const note = p.note;
      recBurst(`stands:${note}`, "clerk", p.claim_id, (ids) =>
        ids.length === 1
          ? `${cidChip(ids[0])} stands — ${esc(note)}.`
          : `${cidChip(rangeList(ids))} stand — ${esc(
              note === "challenge did not hold" ? "challenges did not hold" : note)}.`);
    }
  },

  "agent.spawn": (p) => {
    if (S.agents.has(p.agent_id)) return;       // replayed backlog after reconnect
    const role = p.role || "advocate";
    S.agents.set(p.agent_id, { id: p.agent_id, claimId: p.claim_id, role,
                               buf: "", status: "live", elapsed: null, model: p.model });
    if (p.claim_id) {
      if (!S.agentsByClaim.has(p.claim_id)) S.agentsByClaim.set(p.claim_id, []);
      S.agentsByClaim.get(p.claim_id).push(p.agent_id);
      paintSlots(p.claim_id);
      if (S.drawerClaimId === p.claim_id) renderDrawer(p.claim_id);
    }
    if (p.model) $("leaf-model").textContent = p.model.replace(/^gemini-/, "");
    const C = p.claim_id ? cidChip(p.claim_id) : "";
    if (role === "advocate") {
      S.roleStats.advocate.spawned++;
      stepActivate(STEP.ADVOCATES);
      advSub();
      narrate(`${S.roleStats.advocate.spawned} Advocates are searching the literature…`);
      if (p.claim_id) {
        recBurst("spawn-adv", "adv", p.claim_id, (ids) =>
          ids.length === 1 ? `Advocate takes ${cidChip(ids[0])}.`
            : `${ids.length} Advocates take the field — one per claim (${cidChip(rangeList(ids))}).`);
      } else {
        rec("adv", "Advocate takes the field.");
      }
    } else if (role === "skeptic") {
      S.roleStats.skeptic.spawned++;
      stepActivate(STEP.SKEPTICS);
      skpSub();
      narrate("Skeptics are attacking every supported claim…");
      if (p.claim_id) {
        recBurst("spawn-skp", "skp", p.claim_id, (ids) =>
          ids.length === 1 ? `Skeptic challenges ${cidChip(ids[0])}.`
            : `${ids.length} Skeptics challenge ${cidChip(rangeList(ids))}.`);
      } else {
        rec("skp", "Skeptic takes the field.");
      }
    } else if (role === "tiebreaker") {
      S.roleStats.tiebreaker.spawned++;
      stepActivate(STEP.REPLAN, `tie-breaker on ${(p.claim_id || "").toUpperCase()}`);
      rec("tie", `Tie-breaker re-examines ${C}.`);
    }
  },

  "agent.delta": (p) => {
    const a = S.agents.get(p.agent_id);
    if (!a) return;
    a.buf += p.text || "";
    if ((p.text || "").trim()) a.last = p.text.trim();
    if (a.claimId) paintLive(a.claimId);
  },

  "agent.evidence": (p) => {
    if (!p.claim_id) return;
    if (!S.evByClaim.has(p.claim_id)) S.evByClaim.set(p.claim_id, []);
    S.evByClaim.get(p.claim_id).push(p);
    // the pill stays UNDER CHALLENGE while the skeptic is still working — the
    // bare CHALLENGED verdict word lands at agent.done (refreshChallenged)
    paintLive(p.claim_id);
    paintSlots(p.claim_id);
    if (S.drawerClaimId === p.claim_id) renderDrawer(p.claim_id);
    const refute = p.stance === "refute";
    // skeptic attack beat: red sweep + shake on the card (cosmetic only)
    if (refute) {
      const entry = S.cards.get(p.claim_id);
      if (entry && !entry.slim) {
        entry.card.classList.remove("attack");
        void entry.card.offsetWidth;
        entry.card.classList.add("attack");
        entry.card.addEventListener("animationend",
          () => entry.card.classList.remove("attack"), { once: true });
      }
    }
    const pct = Math.round(((refute ? p.strength : p.confidence) || 0) * 100);
    const src = p.source_label ? `${esc(p.source_label)} — ` : "";
    rec(refute ? "skp" : "adv",
        `${cidChip(p.claim_id)} · ${src}<span class="rq">“${esc(truncWord(p.quote || "", 90))}”</span> (${pct}%)`,
        { claimId: p.claim_id });
  },

  "agent.done": (p) => {
    const a = S.agents.get(p.agent_id);
    if (!a) return;
    a.status = p.status || "ok";
    a.elapsed = p.elapsed_s ?? null;
    if (S.roleStats[a.role]) S.roleStats[a.role].done++;
    if (a.role === "advocate") advSub();
    if (a.role === "skeptic") skpSub();
    if (a.claimId) {
      paintSlots(a.claimId);
      paintLive(a.claimId);
      // the skeptic has reported: a still-challenged claim's pill may now say
      // the bare verdict word CHALLENGED
      if (a.role === "skeptic") refreshChallenged(a.claimId);
      if (S.drawerClaimId === a.claimId) renderDrawer(a.claimId);
    }
    if (p.status === "timeout" || p.status === "error") {
      const C = a.claimId ? cidChip(a.claimId) : esc(p.agent_id);
      const roleWord = { advocate: "advocate", skeptic: "skeptic", tiebreaker: "tie-breaker" }[a.role] || "agent";
      rec("clerk", `${C} ${roleWord} ${p.status === "timeout" ? "timed out" : "failed"} — excluded, run continues.`, { cls: "dim" });
    }
  },

  "dispute.found": (p) => {
    stepActivate(STEP.DISPUTE, "weighing contradictions…");
    narrate(`The Clerk finds the sources in conflict on ${(p.claim_id || "").toUpperCase()}…`);
    rec("clerk", `${cidChip(p.claim_id)} CONTESTED — sources conflict (strength ${(p.strength ?? 0).toFixed(2)}).`,
        { cls: "hl-red", claimId: p.claim_id });
  },

  "takeover.show": (p) => {
    if (S.draining) { S.pendingTakeover = p; return; }
    showTakeover(p);
  },

  // flip timers are NOT cleared on hide: at high replay speeds hide can arrive
  // before the red beat lands, and the chip is invisible anyway once hidden
  "takeover.hide": () => { S.pendingTakeover = null; $("takeover").hidden = true; },

  "jury.request": (p) => {
    stepWait(STEP.RULING, "awaiting you");
    const cid = (p.claim_id || "").toUpperCase();
    narrate(`Paused — waiting for your ruling on ${cid}.`, true);
    rec("jury", `THE COURT CALLS THE JURY — your ruling needed on ${cid}.`,
        { cls: "hl-amber rec-jury" });
    if (S.draining) { S.pendingGate = { kind: "jury", p }; return; }
    showGate(`Run paused — your ruling is needed on ${cid}`);
    openJury(p);
  },

  "jury.ruling": (p) => {
    if (S.pendingGate && S.pendingGate.kind === "jury") S.pendingGate = null;
    $("jury-overlay").hidden = true;
    hideGate();
    stepDone(STEP.RULING);
    narrate("Your ruling is on the record — the Judge folds it back in…");
    rec("you", `You ruled for option ${(p.choice_key || "").toUpperCase()} — stamped human-adjudicated.`,
        { cls: "hl-gold" });
    // gold stamp lands INSIDE the ruled card, then fades — the header pill
    // "☗ HUMAN RULED" stays as the one persistent marker
    const entry = S.cards.get(p.claim_id);
    if (entry && !entry.slim) {
      const seal = entry.card.querySelector(".seal");
      if (seal) {
        seal.classList.add("land");
        setTimeout(() => seal.classList.remove("land"), 2600);
      }
      entry.card.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  },

  "replan.delta": (p) => {
    stepActivate(STEP.REPLAN, "folding the ruling in…");
    narrate("The Judge is folding your ruling back into the plan…");
    recGrow("replan", p.text || "");
  },

  "replan.complete": (p) => {
    rec("judge", `Re-plan complete${p.notes ? ` — ${esc(p.notes)}` : ""}`);
  },

  "synthesis.delta": (p) => {
    stepActivate(STEP.VERDICT, "writing the brief…");
    narrate("The Judge is writing the cited verdict…");
    S.synthesisBuf += p.text || "";
    renderSynthesis();
    recGrow("synth", p.text || "", 180);
    if (!S.autoSwitched) {
      S.autoSwitched = true;
      renderVerdictChips();
      showTab("verdict");
    }
  },

  "verdict.complete": (p) => {
    (p.citations || []).forEach((c) => S.citations.set(c.id, c));
    (p.verdicts || []).forEach((v) => S.verdicts.set(v.claim_id, v));
    S.synthesisBuf = p.brief_markdown || S.synthesisBuf;
    $("verdict-body").innerHTML = mdToHtml(dedupeVerdictTitle(S.synthesisBuf));
    $("vp-status").textContent = "complete";
    renderVerdictChips();
    if ((p.verdicts || []).length) renderLedger(p.verdicts);
    renderTally(p.verdicts || []);
    document.body.classList.add("verdict-stage");   // verdict takes the stage
    const counts = {};
    (p.verdicts || []).forEach((v) => { counts[v.verdict] = (counts[v.verdict] || 0) + 1; });
    const summary = Object.entries(counts)
      .map(([k, v]) => `${v} ${k.toLowerCase().replace(/_/g, "-")}`).join(" · ");
    rec("judge", `VERDICT FILED — ${summary || "on the record"}.`, { cls: "hl-gold" });
    if (S.drawerClaimId) renderDrawer(S.drawerClaimId);
  },

  "cost.update": (p) => {
    if (S.frozen) return;
    S.lastCost = p;
    tweenMoney($("ct-cost"), p.est_gbp || 0);
    const roll = (el, text) => {       // counters tick as they roll to new values
      if (el.textContent === text) return;
      el.textContent = text;
      el.classList.remove("tick");
      void el.offsetWidth;
      el.classList.add("tick");
    };
    roll($("ct-sources"), String(p.sources || 0));
    roll($("ct-agents"), `${p.agents_live || 0} live · ${p.agents_done || 0} done`);
  },

  "run.complete": (p) => {
    stepsFinish();
    S.frozen = true;
    const elapsed = p.elapsed_s || S.maxT;
    $("ct-clock").textContent = fmtClock(elapsed);
    if (S.mode === "replay") $("ct-clock").nextElementSibling.textContent = "replayed";
    // counters settle: one gold pulse at run end
    $("counters").classList.add("settle");
    setTimeout(() => $("counters").classList.remove("settle"), 1000);
    if (p.status === "ok") {
      const line = S.mode === "replay" ? replayLine(elapsed)
        : `Case closed — verdict filed in ${fmtClock(elapsed)}${S.mode === "mock" ? " (scripted mock timings)" : ""}.`;
      narrate(line);
      rec("sys", esc(line), { cls: "dim" });
    } else {
      narrate(`Run ended: ${p.status}.`, true);
      rec("sys", `Run ended: ${esc(p.status || "")}.`, { cls: "hl-red" });
    }
    $("jump-live").textContent = "jump to latest ↓";   // nothing is live any more
    $("vp-status").textContent = "complete";
    renderCompletion();
    toast(`Run complete (${p.status}) — ${fmtClock(elapsed)}`);
  },
};

function dispatch(ev) {
  if (typeof ev.t === "number") S.lastT = ev.t;
  const h = HANDLERS[ev.type];
  if (h) {
    try { h(ev.payload || {}, ev); }
    catch (err) { console.warn("handler failed", ev.type, err); }
  } // unknown event types: ignored by contract
  if (typeof ev.t === "number" && ev.t >= S.maxT) {
    S.maxT = ev.t;
    if (!S.frozen) $("ct-clock").textContent = fmtClock(S.maxT);
  }
}

// ------------------------------------------------------------------ websocket

/* On (re)connect the server replays the full backlog before the live tail.
   While that drains, already-resolved gates and the takeover must not flash
   open-then-shut — their handlers record intent only, and endDrain() opens
   whatever is still genuinely unresolved. */
let drainTimer = null;

function endDrain() {
  if (!S.draining) return;
  S.draining = false;
  if (S.pendingTakeover) { showTakeover(S.pendingTakeover); S.pendingTakeover = null; }
  const g = S.pendingGate;
  S.pendingGate = null;
  if (!g) return;
  if (g.kind === "approval") {
    showGate("Run paused — the court waits for your approval");
    openApproval(g.p);
  } else {
    showGate(`Run paused — your ruling is needed on ${(g.p.claim_id || "").toUpperCase()}`);
    openJury(g.p);
  }
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => {
    $("conn-dot").classList.remove("off");
    $("mode-chip").title = "connected to the court";
    S.draining = true;
    S.pendingGate = null;
    S.pendingTakeover = null;
    clearTimeout(drainTimer);
    drainTimer = setTimeout(endDrain, 400);   // no backlog at all → drain over
  };
  ws.onmessage = (msg) => {
    let ev;
    try { ev = JSON.parse(msg.data); } catch (e) { return; }
    dispatch(ev);
    if (S.draining) {         // backlog arrives in one burst: the first
      clearTimeout(drainTimer);                 // macrotask gap ends the drain
      drainTimer = setTimeout(endDrain, 0);
    }
  };
  ws.onclose = () => {
    $("conn-dot").classList.add("off");
    $("mode-chip").title = "connection lost — reconnecting…";
    setTimeout(connect, 1200);
  };
  ws.onerror = () => { ws.close(); };
}

// ------------------------------------------------------------------ wiring

const DEMO_QUESTION = "Does the amyloid-beta assembly Aβ*56 cause memory loss in Alzheimer's disease?";

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
    S.replayPending = false;    // a fresh live/mock run, not a replay
    if (S.mode === "mock" && q !== DEMO_QUESTION) {
      toast("MOCK mode: the case below is the scripted demo, whatever the question — restart the server WITHOUT --mock for a live run on your question", 7000);
    }
  } catch (err) {
    $("run-error").textContent = err.message;
  }
});

$("demo-btn").addEventListener("click", () => {
  $("question-input").value = DEMO_QUESTION;
  $("run-form").requestSubmit();
});

$("ex-chips").addEventListener("click", (e) => {
  const chip = e.target.closest(".ex-chip");
  if (!chip) return;
  $("question-input").value = chip.textContent.trim();
  $("question-input").focus();
});

$("tab-record").addEventListener("click", () => showTab("record"));
$("tab-verdict").addEventListener("click", () => {
  renderVerdictChips();
  showTab("verdict");
});

$("drawer-close").addEventListener("click", closeDrawer);

$("record-feed").addEventListener("click", (e) => {
  const row = e.target.closest(".rec-row[data-claim]");
  if (row && !e.target.closest("a")) openDrawer(row.dataset.claim);
});
$("record-feed").addEventListener("scroll", () => {
  const f = feedEl();
  S.scrolledUp = f.scrollTop + f.clientHeight < f.scrollHeight - 40;
  $("jump-live").hidden = !S.scrolledUp;
});
$("jump-live").addEventListener("click", () => {
  S.scrolledUp = false;
  $("jump-live").hidden = true;
  feedEl().scrollTop = feedEl().scrollHeight;
});

let rArmedAt = 0;              // two-press guard: R during a LIVE gate
document.addEventListener("keydown", (e) => {
  const typing = /^(INPUT|TEXTAREA)$/.test(document.activeElement?.tagName || "");
  if (typing) return;
  if (e.key === "r" || e.key === "R") {
    // a stray R during a live human gate would derail the live demo beat —
    // require a deliberate double press there (the escape hatch survives)
    const gateOpen = !$("approval-overlay").hidden || !$("jury-overlay").hidden;
    if (S.mode === "live" && gateOpen && Date.now() - rArmedAt > 1500) {
      rArmedAt = Date.now();
      toast("Press R again to abandon the live run and replay the latest case");
      return;
    }
    rArmedAt = 0;
    replayRun("");             // server resolves "" to the newest recorded run
  } else if (e.key === "Escape") {
    if (!$("drawer").hidden) closeDrawer();
    else $("docket-overlay").hidden = true;
  }
});

buildStepper();
loadDocket($("idle-docket"));
connect();
