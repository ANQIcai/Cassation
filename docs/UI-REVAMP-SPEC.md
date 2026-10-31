# TRIBUNAL UI Revamp — Binding UX Spec (v2 "Mission-Control Courtroom")

Owner's one-sentence job: **a first-time viewer must understand within 5 seconds what the system is doing right now, what is already settled, and when it is their turn to act.** Every decision below serves that.

Scope: `tribunal/web/` (index.html, app.js, style.css) + `scripts/screenshot_tour.py` selector updates. NOTHING else. The event protocol (`tribunal/PROTOCOL.md`), server routes, and `schemas.py` are untouched. Old UI is preserved at `tribunal/web_v1_backup/` — never edit it.

## Hard constraints (all survive, non-negotiable)

Same WS consumption (backlog-then-tail, auto-reconnect, unknown event types ignored) · POST /api/run, /api/approve (with strikes), /api/jury · hotkey `R` → POST /api/replay newest · LIVE/MOCK/REPLAY chip always visible top-right (replay blinks + shows recorded time) · cost figures always labeled "estimate" · all claim states visible (pending/running/supported/challenged/contested/human_ruled/refuted/struck/excluded) · per-agent activity visible · DISPUTED takeover above everything (z-order: takeover > gates > drawer) · docket from /api/runs with click-to-replay · verdict with [n] citation chips linking out + QR/brief link · vanilla JS + CSS, no frameworks, no build step, 3 files · dark theme · legible at 3 m · clean at 1280×720.

## Layout — three fixed zones (CSS grid)

```
┌────────────────────────────────────────────────────────────────────┐
│ A HEADER  ⚖ TRIBUNAL · question (full, wraps ≤2 lines) · chip·Docket│
│ B PIPELINE STEPPER + counters (the spine of "what is happening")   │
├──────────────────────────────────────────┬─────────────────────────┤
│ C CLAIM BOARD (cards grid, scrolls)      │ D COURT RECORD (live    │
│                                          │ feed) ⇄ VERDICT (tabs)  │
└──────────────────────────────────────────┴─────────────────────────┘
```
D is a fixed right column (~360px desktop, 300px at 720p height).

## B — Pipeline stepper (the heart)

Eight steps, always visible from run start: **Plan → Your approval → Advocates → Skeptics → Dispute check → Your ruling → Re-plan → Verdict.** (Guardrail invisible unless it fails → inline error banner.)

- Step states: `done` (gold ✓), `active` (pulsing ring, brightened label, live sub-label: "7 of 9 reporting", "writing the brief…"), `human-waiting` (AMBER pulse + "YOUR CALL" tag — visually distinct from machine steps), `pending` (dim), `skipped` (dim strikethrough — e.g. no jury needed).
- Human gates ("Your approval", "Your ruling") are amber-accented from the start, so the audience knows in advance the human is in the loop twice.
- Under the stepper, one **narration line** in present tense, 15px, updating per phase: "The Judge is decomposing the question into testable claims…", "8 Advocates are searching the literature…", "Skeptics are attacking every supported claim…", "Paused — waiting for your ruling on C2."
- Right-aligned counters in the same bar: `est £0.64 · estimate` · `12 sources` · `3 live / 16 done agents` · `0:37`. Mono digits, labels tiny caps. Numbers tick smoothly.
- Event mapping: plan.delta→Plan active; approval.request→Your approval human-waiting; approval.result→done; first agent.spawn(advocate)→Advocates active (sub-label counts from agent.spawn/agent.done); first skeptic spawn→Skeptics; dispute.found/takeover→Dispute check; jury.request→Your ruling human-waiting; replan.*→Re-plan; synthesis.delta→Verdict active; run.complete→all done, counters freeze.

## C — Claim board

Card hierarchy top-down (max-width cards ~300px, min 260px, 14px claim text):
1. Top row: `C3` id chip + branch tag (small caps) + **state pill** top-right (color-coded word: PENDING / RUNNING / SUPPORTED / CHALLENGED / CONTESTED / ☗ HUMAN RULED / REFUTED).
2. Claim text, readable, clamp 3 lines (full text in the drawer).
3. **Agent row**: two compact slots — `ADV` (idle dot → spinner → ✓ n quotes) and `SKP` (idle → spinner → ✓ strength) + `TIE` slot when a tiebreaker spawns. Each slot tinted by role (green/red/amber).
4. Latest evidence quote: ONE line, italic serif, ellipsis at word boundary (never mid-word), + `n sources` badge.
5. `needs C1` dependency chips.
- 4px state-colored left border; state changes crossfade 150ms; a claim going CONTESTED flashes once.
- **Struck and excluded claims collapse to slim rows** (one line: id, STRUCK stamp strikethrough text / "timed out — excluded") so dead weight stops stealing space.
- Card click → **Evidence drawer** slides over column D: full claim text, every evidence item (full quote serif italic, source label, link, provenance chip `grounded`/`model-reported`, stance color, confidence/strength bar), agent + model attribution. Esc or ✕ closes. Gates and takeover render above it.

## D — Court record (live feed) ⇄ Verdict

Two tabs; auto-switch to Verdict when synthesis.delta begins (Court record stays reachable).

**Court record** — every event as a human sentence, newest at bottom, auto-scroll with "jump to live ↓" pill when the user scrolls up:
- Row = `mm:ss` + role tag (JUDGE gold / ADV green / SKP red / CLERK grey / JURY amber / SYS dim) + sentence.
- agent.spawn → "Advocate takes C3"; agent.evidence → "C3 · Kang et al., Science 2018 — "…first ~90 chars…" (85%)" (click → opens that claim's drawer); agent.done timeout → "C7 skeptic timed out — excluded, run continues"; dispute.found → highlighted row "C2 CONTESTED — sources conflict (strength 0.8)"; jury.request → strong amber row "THE COURT CALLS THE JURY — your ruling needed"; jury.ruling → "You ruled for option B — stamped human-adjudicated".
- plan.delta / replan.delta / synthesis.delta stream into a single growing JUDGE row each (not one row per chunk).
- Tiny header legend: "Judge gemini-4-argon · leaves gemini-3.8-flash · clerk flash-lite" (keeps model attribution; keep an element with id `leaf-model` for tour compatibility or update the tour).

**Verdict tab** — document typography: at top a **verdict chip row** (one chip per claim, color-coded, click scrolls/opens drawer; struck claims shown as struck chips — honest absence), then the streamed brief (16px/1.6 serif body, [n] citation chips inline → click opens source, hover shows quote), confidence ledger, then a completion card: frozen counters + QR image + "open the brief on your phone" + trace filename + replay hint.

## Gates — unmissable "your turn" moments

On approval.request / jury.request: the stepper step flares amber, a slim amber banner slides under the header ("⏸ Run paused — waiting for your decision"), backdrop blurs, overlay opens.
- **Approval sheet** (redesign of current modal): title "Approve the plan", rows with a dedicated ⊘ strike button per row (whole row also clickable), struck rows get stamp + strikethrough; **live recount footer** updates as you strike: "9 → 7 claims · ~14 agents · est £0.74 (estimate)"; primary CTA "Approve & empanel 14 agents". Dependency chips visible; striking a claim others need shows a small "C7 depends on this" warning tag (no auto-strike).
- **Jury panel**: keep two-card layout; add header "The agents cannot settle this — you rule"; subtext "your ruling is stamped human-adjudicated, permanently"; on click, gold stamp animation lands on the ruled card on the board.
- **DISPUTED takeover**: keep current full-bleed design (it works); ensure it sits above gates/drawer and auto-hides before jury per engine order.

## Idle / first-load state (new — currently missing)

Hero, centered: ⚖ TRIBUNAL · tagline "Adversarial due-diligence for claims you're about to spend money on." · big input ("Put a question on trial…") with Try-the-demo-question button · THREE example-question chips (click fills input; include the BAs demo question) · a 5-step "how it works" strip (Judge decomposes → You approve → Advocates vs Skeptics argue with live sources → You rule disputes → Cited verdict) · below: **The Docket** — past cases as cards (question, date, verdict chips mini-summary, duration, ▶ Replay) · footer hint "press R to replay the latest case".

## Visual system (tokens — define once at :root)

- bg `#0b0b10` · surface `#14141c` · raised `#1b1b25` · line `#26263a` · text `#ececf2` / muted `#9a9ab0`
- gold `#d4a73c` (judge/CTA) · green `#3fae6a` (supported/ADV) · red `#d65151` (challenged/refuted/SKP) · amber `#e0a93e` (contested/human gates/jury) · grey for pending/struck/excluded.
- Type: UI `-apple-system, "Segoe UI", Roboto, sans-serif`; quotes + verdict body `Georgia, "Times New Roman", serif` italic for quotes; `ui-monospace, Consolas` ONLY for ids, costs, timestamps. Base 13px UI / 14px claim text / 15px narration / stepper labels 11px caps / hero 28px.
- 8px spacing grid, 10px card radius, soft 1px borders, no heavy shadows.
- Motion: stepper pulse, 150ms card-state crossfade, 80ms feed-row fade-in, stamp animation, takeover flip. Nothing else. `prefers-reduced-motion` honored.
- @media (max-height 760px): compact stepper (icons + short labels), record column 300px, tighter cards. No horizontal scroll at 1280×720.

## Acceptance checklist (critics score against this)

1. 5-second test on every beat screenshot: current phase, settled-vs-open claims, and whose turn it is are readable at a glance.
2. Idle screen explains the product and starts a run in one click.
3. No clipped-mid-word text anywhere; no overlapping elements at 1280×720.
4. Both human gates are visually distinct (amber) and pre-announced in the stepper.
5. Court record reads as plain-English narration a judge could follow aloud.
6. Every hard constraint above verified present.
7. Craft: consistent tokens, aligned grids, no default-looking buttons, no visual noise from monospace fragments.
