# FINAL BATTLE PLAN — Build with Gemini 4 Argon, Imperial College, 2026-10-31
**Builder: Oskar (solo + Claude Code). Single source of truth for the day. Location: `/celestia/oskar/imperial-gemini/` (mirror: `/home/ssm-user/oskar/imperial-gemini`). Guide + vendored API skills already in repo.**

---

## 1. RANKING (weighted merit: rubric 0.40, theater 0.35, feasibility 0.25; feasibility ≤ 4 = disqualified from winning)

| # | Concept | Track | Rubric | Theater | Feas. | Weighted | Eligible |
|---|---------|-------|--------|---------|-------|----------|----------|
| 1 | **CASSATION** (research) | T1 | 8 | 7 | 6 | **7.15** | ✅ **WINNER** |
| 2 | Second Engineer (antigravity) | T1 | 8 | 7 | 6 | 7.15 | ✅ (tiebreak lost, see below) |
| 3 | Drawbridge (live-av) | T2 | 8 | 8 | 4 | 7.00 | ❌ DQ (feas. 4) |
| 4 | Lectern (access-lang) | T2 | 8 | 8 | 4 | 7.00 | ❌ DQ (feas. 4) |
| 5 | CloseLoop (financial) | T1 | 7.5 | 7 | 6 | 6.95 | ✅ **designated fallback** |
| 6 | Sandtable (world-3d) | T2 | 8 | 8.5 | 3 | 6.93 | ❌ DQ (feas. 3) |
| 7 | Napkin Physics (wildcard) | T2 | 8 | 8 | 3 | 6.75 | ❌ DQ (feas. 3) |
| 8 | Second Officer (verify-loop) | T1 | 7.5 | 7 | 4 | 6.45 | ❌ DQ (feas. 4) |

**Tiebreak vs Second Engineer (same 7.15):** Second Engineer's entire live arc hangs on three properties of a preview product that cannot be verified before kickoff and cannot be fixed on the day — granular trace streaming from the antigravity sandbox, per-mission environment setup time (pip-installing a science stack could alone blow the 3-minute budget), and pre-warmed session reuse. Those failures are binary. CASSATION's risks (rate limits, latency, calibration) are all measurable in the first 30 minutes and degrade gracefully (fewer leaves, timed-out nodes excluded, run continues). Second Engineer also carries the "Jules with a mission-control skin" novelty discount with this exact panel, while CASSATION's visible adversarial fan-out is the most room-legible Track 1 spectacle available. Control beats variance: CASSATION wins.

---

## 2. THE PICK

**CASSATION — a research court run by a Gemini agent swarm.**
**Track 1: Frontier Agents with Gemini.**
**One-liner:** Gemini 4 Argon sits as Judge, decomposes a contested technical question into a claim board, and puts every claim on trial before parallel Flash Advocates and Skeptics with live Google Search grounding — with a judge striking a plan branch at the start, a human Jury ruling on the one claim the agents can't settle, and a cited Research Verdict streaming out the end.

**Why this beats the field for THESE judges:** It is the Track 1 brief performed verbatim — "autonomous agents, multi-step reasoning, verification loops, research workflows" — with the verification loop made *visible* as red Skeptic agents attacking green findings, which is the single most wrapper-proof thing you can put in front of DeepMind engineers. Its two proof-of-live beats (a judge personally amputating a plan branch that then provably never runs, and a claim flipping red on real search results mid-run) directly disarm the canned-demo allergy this panel brings, and the named user is Oskar himself with a verifiable financial scar (a wrong literature number cost a GPU-weekend of his NEO materials pipeline budget). Finally, it is the only top-scoring concept whose every runtime component Oskar builds and controls, with an event-sourced replay fallback that is the same code path as the live run — it cannot bit-rot, and the demo degrades gracefully instead of dying.

---

## 3. RUNNER-UP / FALLBACK: CloseLoop

**CloseLoop** (month-end close agent: parallel Flash extraction of invoices/contracts/bank CSV → Argon global reconciliation → agent-written audit code executed locally → planted IBAN-fraud catch → human-gated payment run emitting pain.001 + audit log).

**Why CloseLoop and not the nominally-#2 Second Engineer as fallback:** a midday pivot cannot afford a new unverified API surface. CloseLoop uses the *same* surfaces CASSATION already proved by then (Interactions API, Argon + flash fan-out + flash-lite, no search grounding needed), its drama is deterministic (planted traps in seeded data — guaranteed to fire), and **~60–70% of CASSATION's code carries over**: the orchestrator skeleton, asyncio fan-out, JSONL event bus, WebSocket UI shell, replay engine, cost meter, and approval-gate component. New work is only the seed-data generator (~1.5h), the reconciliation prompt, and reskinning the claim board into an Evidence Board.

**SWITCH CRITERION — hard pivot gate at H3.5 (decide in 10 minutes, no sentiment):** switch to CloseLoop if ANY of:
1. Search-grounded parallel calls cannot sustain ≥ 6 concurrent leaves after backoff/batching tuning (quota wall — unfixable on the day);
2. Three consecutive calibration runs on the prepared question produce **zero** genuine contested claims (Skeptic behavior won't calibrate on the day-one model);
3. Median end-to-end live run (decompose → verdict streaming) exceeds 2:15 after latency tuning.

If pivoting: traps are planted to be caught by *Argon's* reconciliation pass (not a deterministic pre-pass), every Evidence Board card carries a provenance chip, and the runway panel is pre-declared out of scope.

---

## 4. FINAL CONCEPT SPEC (CASSATION, with grafts)

**Named user:** Oskar — runs NEO, an AI materials-discovery pipeline; before committing a paid multi-hour DFT/GPU screening run he must verify contested literature claims. Demo question: cubic boron arsenide lattice thermal conductivity (~2200 W/mK first-principles prediction (Lindsay/Broido/Reinecke 2013) → ~1400 four-phonon revision (Feng/Lindsay/Ruan 2017) → defect-limited early measurements ~200 → the 2018 Science trio at ~1000–1300 → **2025 purified-arsenic measurements >2100 W/mK** (Niyikiza et al., Materials Today; isotope enrichment alone is only ~8–13%) — a real, famous, still-moving controversy he can defend under cross-examination; full armor in `docs/demo/bas-controversy-armor.md`). Opening line: "Last month one wrong literature number cost me a GPU-weekend of compute budget."

**Problem:** technical due-diligence is confirmation-biased — you find three agreeing papers and stop. Nobody goes hunting the counter-evidence (errata, superseded measurements, contested methods). For anyone gating real spend on a claim, that's dangerous, and doing adversarial cross-checking by hand costs an afternoon per claim.

**Feature set (ruthlessly scoped — this list is complete):**
1. **One input box** → Argon (Judge) decomposes into a typed claim board (~8–10 claims, dependencies, per-branch cost estimate), schema-validated JSON.
2. **Plan & budget approval modal** — human (ideally a judge) strikes any branch (it greys out and never runs) and approves. Human gate #1.
3. **Advocate fan-out** — 8–10 parallel gemini-3.8-flash agents with Google Search grounding; each returns evidence, exact quotes, URLs, confidence; streamed to claim cards with live tickers and climbing source/cost counters.
4. **Skeptic wave** — same model, inverted mandate ("find why this is false, contested, or superseded"), attacking every supported claim; always reports its strongest counter-evidence with a strength score.
5. **Clerk** — gemini-3.5-flash-lite filters evidence relevance, detects Advocate/Skeptic contradictions, routes disputes past a threshold to CONTESTED.
6. **[GRAFT — Napkin/theater] DISPUTED takeover** — a claim flip triggers a full-screen, poster-sized side-by-side of the two primary-source quotes with giant green→red state change (legible at 3 m), plus an optional pre-cached TTS stinger. This is the loud event the flip needs.
7. **Jury panel** — the CONTESTED claim cannot self-resolve; human rules between two cited primary sources; verdict permanently stamped "human-adjudicated." Human gate #2. Argon re-plans on the ruling in the same `previous_interaction_id` chain and spawns one tie-breaker agent.
8. **Research Verdict** — streamed cited brief; every sentence carries citation chips and a SUPPORTED/REFUTED/CONTESTED verdict chip; struck branch visibly absent; confidence ledger; cost/sources/agents counters freeze.
9. **[GRAFT — Second Engineer] Judge-phone brief** — QR code to the final brief served from the laptop; a judge opens it on their own phone and taps a citation through to the primary source. Proof that leaves the laptop.
10. **[GRAFT — Second Engineer's "Tonight's shift"] The Docket** — a side wall of case files: 3–4 complete cassation runs from the build day (rendered from traces that already exist), each replayable. Fills the Skeptic-wave quiet stretch and proves it works beyond one question.
11. **[GRAFT — CloseLoop] Provenance chips** — every finding is labeled with which agent/model/search produced it. Pre-empts the provenance probe.
12. **[GRAFT — Lectern] LIVE / REPLAY honesty chip** — persistent corner status; replay is always labeled.
13. **Judge-question lane** — a second run on a judge's own contested question, offered in Q&A (same pipeline, zero new code, honestly framed: "it surfaces the sourced disagreement; I don't know the answer either").

**Honesty corrections baked in:** synthesis is over leaf transcripts + grounded quotes (never claim "every fetched page verbatim" — no page scraping). Cost meter labeled "estimate." Replay labeled on screen and out loud.

**Explicitly OUT of scope:** d3/force-directed graph (CSS-grid claim board with hand-drawn SVG dependency arcs), full-page fetching/scraping, deep-research appendix as a demo beat (kick it off in background; mention in one sentence only if it's actually running), per-user accounts/auth, more than one polished demo question (+2 calibrated backups), free-form chat with the system, TTS beyond 2 pre-cached stinger files, any CloseLoop features unless the pivot gate fires.

---

## 5. ARCHITECTURE

**Models (exact jobs):**
- **gemini-4-argon** — the Judge. (a) Decompose question → typed claim-graph JSON (`thinking_level=medium` — stage latency matters more than depth here); (b) mid-run re-plan after the jury ruling; (c) final synthesis holding all leaf transcripts + grounded quotes in the shared 1M window (`thinking_level=high`, streamed). One server-side chain via `previous_interaction_id` across plan → re-plan → verdict; never resend the corpus.
- **gemini-3.8-flash** — the litigants. 8–10 parallel Advocates (Google Search grounding, structured output: claim_id, stance, quotes[], urls[], confidence) and the Skeptic wave (inverted mandate, strongest-counter-evidence + strength score always required).
- **gemini-3.5-flash-lite** — the clerk. Sub-second evidence-relevance filter, Advocate/Skeptic contradiction scoring, dispute routing, input guardrail on the question.
- **deep-research-preview-04-2026** — `background=True` overnight appendix, fired at run start, never demo-critical.
- **gemini-3.8-flash-tts** — two pre-generated, cached stinger audio files ("Objection sustained." / "The court finds this claim contested."). Works offline. First thing cut.

**Components:**
1. `orchestrator/` — Python 3.12, `google-genai>=2.3.0`, pydantic v2 schemas for every event and the claim graph; state machine: GUARDRAIL → DECOMPOSE → APPROVE → ADVOCATES → SKEPTICS → DISPUTES → JURY → REPLAN → SYNTHESIS. `asyncio.gather` fan-out, **hard 20s timeout per leaf** (timed-out nodes marked "excluded," run never blocks), exponential backoff, adaptive leaf count (14 → 10 → 8 → batched-6).
2. `bus/` — the spine: every event is one JSON line appended to `runs/<id>/trace.jsonl` **and** pushed over a single WebSocket. Live and replay share one format, so the replay engine is the live code path.
3. `server/` — FastAPI + uvicorn; serves the SPA, the WS, the phone-brief page (+ `qrcode` lib), and `--replay <trace>` mode.
4. `web/` — one `index.html`, vanilla JS, CSS grid. Claim cards with 5 states (pending / advocate-green / skeptic-red / contested-amber / human-stamped), per-agent streaming tickers, cost meter, approval modal, jury panel, DISPUTED takeover overlay, Docket wall, verdict view with citation chips, LIVE/REPLAY chip. **No build step, no d3, no framework.**
5. `grounding_shim.py` — the known-fiddly bit, built early against real responses: extracts exact quotes + resolvable URLs from grounding metadata; degrades to model-echoed quotes labeled "model-reported" + domain-level links if metadata is uglier than documented.
6. `fixtures/` — every raw API response recorded from call #1 (flight-recorder discipline, graft from CloseLoop).

**Data flow:** question → flash-lite guardrail → Argon plan → human approval (strike/approve) → parallel Advocates (grounded) → Skeptic wave → clerk contradiction scoring → CONTESTED → jury ruling → Argon re-plan (+1 tie-breaker leaf) → Argon synthesis streamed → brief + QR + frozen counters. Every arrow emits bus events.

**Stack, opinionated:** Python 3.12, FastAPI, uvicorn, websockets, httpx, pydantic v2, qrcode, google-genai ≥ 2.3.0. Frontend: vanilla JS + CSS grid + inline SVG arcs. Dark board, three state colors, zero animation rabbit holes.

---

## 6. HOUR-BY-HOUR BUILD PLAN (H0 = key in hand; demo-critical path first)

| Hours | Work | Gate |
|---|---|---|
| **H0–0:30** | Claim key/credits. `npx skills add google-gemini/gemini-skills --skill gemini-api-dev`. Smoke-test argon + flash + Search grounding. **Measure grounding concurrency: fire 2/4/8/14 parallel grounded calls, record limits.** Save every response as fixtures. Confirm quotas with Google mentors. | **Gate 0:** if Interactions API itself is broken → mentors immediately; nothing else proceeds. |
| **H0:30–1:45** | **CLI orchestrator end-to-end** (no UI): decompose → advocate fan-out → skeptic wave → clerk → synthesis, printing events; trace.jsonl from the first event. Build `grounding_shim.py` against real responses here. | **Gate A (H1:45):** full CLI run completes on the BAs question. If chaining/structured-output is fighting back, simplify schemas before touching UI. |
| **H1:45–2:30** | FastAPI + WS event bus + **replay engine** (same event format — replay falls out nearly free). Hotspot test. | Always demoable from here: CLI + replay. |
| **H2:30–4:00** | **Claim board UI**: cards + 5 states first, then tickers, cost meter, approval modal (with branch-strike), jury panel, DISPUTED takeover. SVG arcs LAST. | **Gate B (H3:30–4:00) = PIVOT GATE:** evaluate the three CloseLoop switch criteria (§3). 10-minute decision. |
| **H4:00–5:00** | Adversarial calibration: 3 consecutive clean runs on BAs question + verify 2 backup questions; tune Skeptic prompt + CONTESTED threshold so ≥1 claim escalates per run; jury also opens on borderline confidence (safety net). Tune Argon latency (medium decompose). | **Gate C (H5:00):** if disputes still unreliable → demo question is locked to the best-behaving of the 3; jury beat rides borderline-confidence. |
| **H5:00–5:45** | Verdict brief rendering (citation chips, confidence ledger), jury-ruling → re-plan loop, QR phone-brief page. | |
| **H5:45–6:30** | Hardening: leaf timeouts/retries verified under induced failure, replay hotkey, **record 3 golden runs**, wifi-kill drill (pull the plug mid-run — watch it degrade then flip replay), calibrate cost meter vs actual billing, build Docket wall from the day's traces. | **Gate D:** 3 golden traces on disk or nothing else gets built. |
| **H6:30–7:15** | TTS stingers (cached files), DISPUTED takeover polish, kick off deep-research background job on the demo question, SVG arcs if time. | |
| **H7:15–8:00** | **Three timed rehearsals at a 2:30 target** (one live, one on hotspot, one forced-replay). Freeze code. Write AGENTS.md quirk notes (guide pattern 5). Print judge card. | |

**Cut order (pre-agreed, no debate on the day):** TTS stingers → SVG dependency arcs → QR phone brief → Docket wall → leaf count 10→8→6 → jury-on-borderline-confidence replaces calibrated dispute.

**Build-first rule:** the CLI loop (H0:30–1:45) is the product; everything after is presentation. There is a demoable artifact from H1:45 onward at every moment of the day.

---

## 7. THREE-MINUTE DEMO SCRIPT (content cut to 2:30; 0:30 structural slack)

- **0:00–0:12 — Hook.** No slides. "Before I burn a £400 GPU day on a candidate material, I have to know if the literature number is real. Last month one wrong number cost me a weekend of compute. This is Cassation." Paste the boron-arsenide question. Enter.
- **0:12–0:30 — Plan + LIVE-RISK & CONTROL BEAT #1.** Argon's decomposition streams: ~9 claim cards with dependencies, "~14 agents, est. £0.90 — approve?" **Hand a judge the trackpad: they strike one branch dead.** It greys out and will never run — on-screen proof nothing is pre-baked. They click Approve.
- **0:30–0:55 — WOW 1: the fan-out.** Eight-plus Advocates ignite simultaneously; quotes and URLs tick in; sources/cost counters climb. "You're watching an afternoon of literature triage happen in twenty-five seconds."
- **0:55–1:25 — The Skeptic wave.** Red agents visibly attack green findings. While they search, gesture at the Docket: "Nine cases tried today while I built this — every one a replayable signed trace."
- **1:25–1:45 — WOW 2: the DISPUTED takeover.** The clerk flags a contradiction; the screen takes over full-bleed: two primary sources side by side, poster-sized quotes — the Skeptic surfaced the 2025 purified-arsenic measurement (>2100 W/mK) blowing past the 2018 headline (~1300): the literature's best number nearly doubled, and anyone who stopped at the 2018 papers is designing against a stale value. Node flips green→amber→red. (Cached TTS stinger if it survived the cut.)
- **1:45–2:05 — HUMAN-IN-CONTROL BEAT #2: the Jury.** "The agents can't settle this. That's the point — they don't get to." Oskar rules for the newer measurement; the verdict stamps "human-adjudicated"; Argon visibly re-plans in the same interaction chain and spawns one tie-breaker agent for the dependent cooling-application claim.
- **2:05–2:25 — The Verdict.** Brief streams in: verdict chips per sentence, numbered citations, the struck branch *absent*, the human ruling stamped. Counters freeze: £-estimate, sources, agents, elapsed. **QR up: a judge opens the brief on their own phone and taps a citation to the primary source.**
- **2:25–2:40 — Close.** "Every event you watched is one line in a signed trace — here's the replay key. The same question is running on Deep Research overnight as the appendix. A wrong number used to cost me a weekend; now it costs a Skeptic forty seconds. And if you have a contested question of your own — we'll run it in Q&A."
- **2:40–3:00 — Slack.** Absorbs one latency hiccup or a slow judge-trackpad moment. If synthesis is still streaming at close: pre-planned line — "the brief is still being written as we speak; the chips land as each claim clears" — and close over the moving stream.

---

## 8. OFFLINE / FAILURE FALLBACK (layered, each rehearsed in H5:45–6:30 and H7:15+)

1. **Partial degradation is native (no action needed):** every leaf has a 20s timeout; failed/slow calls are marked "timed out — excluded" on their card and the run continues fully live. The Judge synthesizes from what returned. A flaky network produces a visibly honest, still-live run.
2. **Connectivity loss:** phone hotspot, pre-paired, one toggle, ~15 seconds of patter ("venue wifi is doing venue wifi things"). Tested at H1:45 and again in rehearsal.
3. **Argon slow at decompose only:** locally cached plan for the prepared question seeds the claim board instantly; the Advocates/Skeptics still run live. The branch-strike beat survives because struck branches are enforced by the orchestrator, not the plan.
4. **Total API/network death:** one hotkey flips the UI's data source from the live WS to the JSONL trace replayer — one of 3 golden runs recorded that afternoon replays pixel-identically at original cadence through the identical UI, with the persistent chip flipped to **REPLAY — recorded 15:40 today** and a spoken acknowledgment. Because live and replay share one event format, this path gets exercised every development hour and cannot bit-rot.
5. **Zero-network beats that still work:** the Docket wall, the final brief page, the QR phone view (hotspot LAN), the replay scrubber, and the cached TTS stingers are all local static artifacts.
6. **Posture if replay fires during judging:** say it immediately, keep the judge-interaction beats live where possible (approval modal and jury panel still accept real clicks against the replayed run — they're UI-local), and point at the trace file. Honesty is the only move that preserves any score.

---

## 9. RISK REGISTER (top 8)

| # | Risk | Mitigation |
|---|------|-----------|
| 1 | **Search-grounding concurrency/rate limits on the day-one key** (top external kill risk) | Measure at H0 with a 2/4/8/14 ramp; adaptive leaf count with wave batching; exponential backoff; confirm limits with onsite Google mentors in the first 30 min; fixtures recorded from call #1; pivot-gate criterion #1. |
| 2 | **Argon latency blowout (decompose or synthesis)** | `thinking_level=medium` for decompose, `high` only for synthesis; stream everything so rendering starts immediately; synthesis input = leaf digests, not raw dumps; cached-plan seed; script cut to 2:30 with a pre-planned "still streaming" close. |
| 3 | **Skeptic finds nothing — no dispute, no flip** | Demo question chosen for a known real controversy + 2 calibrated backups; Skeptic prompt must always return strongest counter-evidence with a strength score; jury panel also opens on borderline confidence so the human-adjudication beat happens regardless; pivot-gate criterion #2. |
| 4 | **Frontend overrun (the H2:30–4:00 slot always slips)** | No d3, no framework, no build step; cards-with-states built first, arcs last; pre-agreed cut order; CLI + replay is demoable from H1:45. |
| 5 | **Grounding-metadata quote/URL extraction brittle** | `grounding_shim.py` built at H1 against real responses, not docs; graceful degrade to model-echoed quotes labeled "model-reported" and domain-level citation chips — honest labels beat broken links. |
| 6 | **3-minute overrun with a judge in the loop** | 2:30 content target, rehearsed three times with a timer; droppable beats pre-marked (Docket mention, TTS, QR); judge interaction scoped to two bounded clicks (strike + approve). |
| 7 | **Forced into replay during judging** | Layered fallbacks mean replay is the *fourth* resort, not the first; honest chip + verbal acknowledgment; jury/approval clicks stay live against the replay; judge-question offer in Q&A restores live credibility if the network returns. |
| 8 | **A judge insists "run it on MY question"** | That's a feature: the Q&A second-run lane is rehearsed; framing — "Cassation surfaces the sourced disagreement; it doesn't need me to know the answer" — plus the struck-branch beat already proved nothing is pre-baked. A flat, dispute-free judge run is narrated as the honest result: "the record agrees — that claim is safe to spend against." |

---

## 10. PRE-EVENT PREP (legitimate — no hackathon-spirit violations; no Gemini calls possible before the key anyway)

1. **Read, again:** `docs/guide.txt` and both vendored skills (`vendor/gemini-skills/skills/gemini-api-dev/SKILL.md`, `gemini-live-api-dev/SKILL.md`) — the Interactions surface (`client.interactions.create`, `previous_interaction_id`, streaming shape, `thinking_level`, `background`) is already documented in-repo.
2. **Generic scaffolding (non-Gemini):** FastAPI + WS + JSONL event-bus + replay-engine shell with a `MockModelClient` emitting fake events; pydantic schemas for the claim graph and all event types; this is reusable plumbing, not the project.
3. **UI shell** driven entirely by mock events: claim cards, states, approval modal, jury panel, takeover overlay — so on the day the UI work is wiring, not inventing.
4. **Question prep:** hand-verify the boron-arsenide controversy — DONE, see `docs/demo/bas-controversy-armor.md` (timeline: ~2200 prediction 2013, ~1400 four-phonon 2017, ~200 defect-limited 2015–17, ~1000–1300 Science trio 2018, >2100 purified-arsenic 2025; memorize it — this is the Q&A armor) and backup questions in `docs/demo/backup-questions.md`.
5. **Rehearsal script** printed; judge instruction card printed ("strike any branch you like"); 2:30 beat sheet memorized.
6. **Logistics:** phone hotspot pre-paired and tested with the laptop; laptop power settings pinned; projector-resolution layout test (1280×720 worst case); AGENTS.md skeleton for quirk notes.
7. **Prompt drafts** for Judge/Advocate/Skeptic/Clerk written as files in-repo, to be calibrated (not authored) on the day.

---

## 11. JUDGE Q&A — the 6 hardest questions, with crisp answers

1. **"Isn't this just multi-agent debate plus Deep Research in a courtroom skin?"**
"Deep Research confirms; Cassation cross-examines. Three structural differences: every supported claim gets an adversary whose only mandate is to kill it; a contested claim *cannot* resolve without a human ruling — the jury is a required node in the dataflow, not a confirm dialog; and the Judge re-plans mid-run on that ruling inside one server-side interaction chain. Deep Research is literally a component here — it's running the overnight appendix in the background — the product is the trial, not the report."

2. **"What here needed Argon rather than any long-context model?"**
"Three load-bearing things: decomposition into a typed, dependency-aware claim graph that survives schema validation; mid-run re-planning on a human verdict via `previous_interaction_id` without resending the corpus; and the synthesis pass, which held every leaf transcript and grounded quote in one window — [N] tokens on this run, the counter's right there — so every citation chip points at an exact quote, no RAG, no summarize-and-pray."

3. **"You picked a question you knew was contested. Run it on mine."**
"Gladly — give me one now, that's the second run. One honest caveat: Cassation's value isn't knowing the answer, it's surfacing the sourced disagreement — if your question turns out uncontested, the all-green board *is* the correct result and the brief will say so with citations. And you already proved this run wasn't canned: you struck a branch at the start and it's absent from the brief."

4. **"What stops a Skeptic hallucinating a controversy, or an Advocate hallucinating a source?"**
"Every finding must carry an exact quote plus a resolvable URL from search grounding, the flash-lite clerk filters relevance before anything reaches the board, and — the real answer — the system never auto-rules on a dispute. A contested claim goes to a human with both primary sources side by side. Tap any citation on the brief on your phone; it resolves. Hallucinated evidence fails the quote-and-URL check before it's ever shown."

5. **"Your approval gate guards a pound of API spend. Where are the real stakes?"**
"The money gate is the small one; the ruling gate is the real one. The output of this run gates a £400 GPU screening decision I'd otherwise make on a confirmation-biased skim — the verdict brief is the artifact I spend against, and any claim a human ruled on is permanently stamped 'human-adjudicated' so downstream consumers know its provenance. The agents advise; the human decides; the record shows which was which."

6. **"What happens when the wifi dies — and how do I know this was live at all?"**
"Three things, in order: timed-out agents get excluded and the run continues live; then my hotspot; then one hotkey replays this afternoon's recorded trace through this same UI with a REPLAY badge you can see — live and replay are one event format, which is also why every run is a signed, step-through-able trace. As for 'was it live': you struck claim [X] off the plan ninety seconds ago, and it never ran. That branch's absence from the brief is the proof."

---

*Decision recorded in `/celestia/oskar/imperial-gemini/MASTER.md` (Current state, 2026-10-31). Source docs: `/celestia/oskar/imperial-gemini/docs/`. Vendored API skills: `/celestia/oskar/imperial-gemini/vendor/gemini-skills/`.*
