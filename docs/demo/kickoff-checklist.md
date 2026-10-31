# Kickoff checklist — verify the live path against the real API (H0–0:30)

Every `# VERIFY-AT-KICKOFF:` comment in the repo (`cassation/orchestrator/
gemini_client.py` and `cassation/server/main.py`) has a checkbox here. Work top
to bottom; each item names the code it unblocks. Nothing below can be tested
before the key exists.

## 0. Smoke test (Gate 0 — plan §6 H0)

- [x] Claim key/credits — key lives in repo-root `.env` (`GEMINI_API_KEY`),
      auto-loaded by `cassation/__init__.py`. NOTE: key was ROTATED mid-kickoff;
      fresh processes pick the new one up automatically.
- [x] Smoke all three models: argon 5.3s / flash 4.8s / flash-lite 2.5s —
      `fixtures/kickoff-argon.json`, `kickoff-flash.json`,
      `kickoff2-p1-flashlite-structured.json`.
- [x] Grounded flash smoke: 22.2s, steps
      `[google_search_call, google_search_result]×3 + thought + model_output`
      — `fixtures/kickoff-grounded.json`.
- [x] Fixtures appear at repo root (`fixtures/kickoff*.json`, incl. every
      kickoff2-* probe).
- [x] **Prompt files are live**: marker edit in `skeptic.md` visible in a
      freshly constructed `GeminiModelClient._prompts`, absent after revert.
- [x] First live run: completed end-to-end, see §7.

## 1. SDK / auth assumptions

- [x] **Env var name**: `genai.Client()` self-discovers BOTH (`GOOGLE_API_KEY`
      wins if both set — SDK `_api_client.py`); our `.env` sets `GEMINI_API_KEY`.
- [x] **Async surface**: `client.aio.interactions.create(**body)` works live
      for every combination used.
- [x] **Async streaming shape**: `await create(stream=True)` returns an
      `AsyncStream` async-iterator directly.
- [x] **Model ids live on this key**: all three respond.
- [x] **Quota errors**: no 429 provoked even at 14 parallel grounded calls;
      client-side stalls surface as `APITimeoutError("Request timed out...")`
      — added to `_retryable`. 429 string match kept from docs, untuned
      against a real one.

## 2. Streaming event shapes (`_stream`)

- [x] **Interaction id location**: `event.interaction.id` on
      `interaction.created` — confirmed.
- [x] **Thought-summary delta type**: `"thought_summary"` with text at
      `delta.text` (direct; the nested fallback never fires). `"thought"` is a
      STEP type, not a delta type. Summaries can be legitimately EMPTY on easy
      calls ("didn't reason enough" — hosted thinking.md): a quiet ticker on a
      trivial call is expected.
- [x] **Tool step visibility**: `step.start` carries
      `step.type == "google_search_call"`; client now keys the "Searching
      Google…" line on `*_call` only (result step no longer double-prints).
- [x] **Continuation hops**: the SDK consumes hops internally; the stream
      yields ONE final `interaction.completed` (status `completed`) carrying
      metadata only — final steps require `interactions.get(id)`.

## 3. Structured output

- [x] `response_format` + `schema` works on flash-lite (2.5s) and argon
      (streamed mini-decompose) — WITHOUT tools in the same call.
- [x] `interaction.output_text` exists on non-streamed responses and holds the
      raw JSON.
- [x] **JSON arrives via text deltas** when streaming with `response_format`;
      accumulated text parses raw.
- [x] **Fences**: never observed on structured calls; `_strip_fences` kept as
      defense.
- [x] **Thought summaries**: `thinking_summaries: "auto"` is accepted together
      with `response_format`, BUT no `thought_summary` delta ever fired on
      argon live — not at low or medium, with or without `response_format`
      (live run: 0 plan.delta, 0 replan.delta). The judge ticker is SILENT
      during decompose/replan on this key; the client handles summaries
      correctly if they ever appear. Demo mitigation: narrate the decompose
      beat verbally (the claim board popping at ~15s carries the moment).
- [x] **CRITICAL — tools + structured output in one call**: `google_search` +
      `response_format` WITH `schema` WEDGES gemini-3.8-flash — stream opens,
      searches, then stalls forever inside model_output
      (`fixtures/kickoff2-p3c-solo-combo-events.json`); non-streamed never
      returns headers. FIX (better than the flash-lite fallback): keep
      `response_format={"type":"text","mime_type":"application/json"}` and
      DROP the `schema` key — 28/28 ramp calls returned parseable JSON in
      8–25s; prompts carry the shape, `_LeafOut` validates client-side.
- [x] **Two-part synthesis protocol**: argon honored it on the full live run —
      441-word Part 1 markdown streamed to `synthesis.delta`, clean delimiter
      split, zero JSON leakage, Part 2 parsed into 9 verdicts + 17 citations
      (all with URLs, 8 grounded).
- [x] **Replan returns JSON**: parsed live; tiebreaker mandate flowed into
      tie-c2's context and the tiebreaker leaf ran ok. NOTE: `replan.delta`
      ticker got nothing (argon emits no thought summaries on this key — see
      the thought-summaries item above); the beat is carried by
      `replan.complete` + the tiebreaker spawn.

## 4. Judge chain

- [x] `previous_interaction_id` chains across calls: a fact planted in call 1
      is recalled in call 2 without resending
      (`fixtures/kickoff2-p5-chain.json`); full decompose→replan→synthesize
      chain exercised in the live run (§7).
- [x] `store=True` retention live at demo timescales (chain worked minutes
      apart).
- [x] `thinking_level` accepted by argon ("low"/"medium"/"high" used) AND
      flash ("low" on every leaf).
- [x] `interactions.get(iid)` POSITIONAL works after a streamed call and
      returns the full step list incl. annotated `model_output`
      (`fixtures/kickoff2-p4-leaf-refetch.json`).
- [x] **HTTP timeout units = MILLISECONDS**: SDK `_api_client.py` divides by
      1000 for httpx, and a live `APITimeoutError` fired at exactly 120s with
      `timeout=120_000`. `20*60*1000` is correct.
- [x] **Quota retries**: `_retryable` extended with `"timed out"`/`ReadTimeout`
      (observed shape); no real 429 obtainable at ≤14 concurrency to tune the
      429 match further.

## 5. Grounding metadata (`grounding_shim.py`)

- [x] Grounding sources ARE exposed — but NOT on `google_search_result` steps
      (those carry only the Search-Suggestions chip HTML in
      `result[0].search_suggestions`).
- [x] The real channel: `url_citation` **annotations on the `model_output`
      step's content items** — fields `url` (sometimes a
      vertexaisearch.cloud.google.com redirect, sometimes a direct link),
      `title` (source domain, may be null), `start_index`/`end_index` =
      **UTF-8 byte offsets** into that content item's text. No source quote
      exists in metadata — the annotated span is the MODEL's text, so upgraded
      Evidence keeps the agent's quote and gains the resolvable URL.
- [x] `grounding_shim.extract_sources` rewritten to the annotation channel
      (legacy key sweep kept as defense) and unit-verified against
      `fixtures/kickoff-grounded.json` + `kickoff2-p4-leaf-refetch.json`:
      upgrade on domain match, upgrade on quote-in-span, degrade to domain
      link, degrade to url="" all behave; fallback evidence from annotations
      is labeled model-reported (honest: no source quote).

## 6. Grounding concurrency ramp (plan §6 H0 / risk #1)

- [x] 2 parallel: p50 7.7s, p95 8.3s, max 8.3s — 0 errors.
- [x] 4 parallel: p50 8.2s, p95 12.9s, max 12.9s — 0 errors.
- [x] 8 parallel: p50 8.7s, p95 17.0s, max 17.0s — 0 errors.
- [x] 14 parallel: p50 11.0s, p95 19.2s, max 25.3s — 0 errors, 0 429s, 28/28
      parseable JSON (`fixtures/kickoff2-ramp-results.json`). Latency scales
      with the number of searches the model chooses (up to 8 seen).
      `max_leaves` stays 10 (14 clean = headroom).
- [x] p95 19.2s ≈ the old 20s `leaf_timeout_s` → RAISED to 45s (max healthy
      leaf 25.3s + `get()` re-fetch + one retry; hang-wedges are what the
      timeout is for, not healthy multi-search leaves).

## 7. After all green

- [x] Full live CLI run end-to-end: **run-20261031-144627**, 136.3s, status ok
      — 9 claims, 19 agents, 7 disputes (pre-calibration), jury on c1, ruling
      'b', replan + tiebreaker, 441-word streamed brief, verdicts
      CONTESTED:6/HUMAN_ADJUDICATED:1/SUPPORTED:2, 17 citations.
- [x] **Live run through the SERVER path**: **run-20261031-150109**, 338s,
      status ok on port 9300 — `POST /api/run`, approve + jury resolved via
      the HTTP gates (driven from the WS feed), verdicts
      SUPPORTED:5/CONTESTED:2/HUMAN_ADJUDICATED:1/EXCLUDED:1, docket row
      correct, `/brief/<id>` 200 (17KB, citations render) and `/api/qr` 200
      (real PNG). Lazy `GeminiModelClient` import verified (mark resolved in
      server/main.py). This run also proved the resilience paths live: a
      skeptic error → honest EXCLUDED chip, and two flaky clerk calls caught
      by the new engine clerk fence (see addendum).
- [x] **Plan cache seeded live** at 14:46:42 (mock entry for the demo question
      was scrubbed first). ALSO: engine now refuses to overwrite a LIVE cache
      entry with a mock plan, so mock rehearsals can no longer poison it —
      the "re-run live decompose after mock rehearsal" chore is obsolete.
- [x] Replay of the live trace: 349 events re-emitted, identical event-type
      sequence (+ injected `replay.info`).
- [x] Hotkey-R resolution verified while a live run was mid-flight:
      `resolve_trace("newest")` skipped the running run (meta status
      "running") and returned the newest status-ok run. NOTE: "newest
      completed" includes other rehearsals — make the last completed run
      before stage the golden you want hotkey R to show.
- [x] Every stale `# VERIFY-AT-KICKOFF:` resolved to `# VERIFIED-AT-KICKOFF:`
      with the observed behavior (gemini_client.py, server/main.py).

## 8. Kickoff addendum — live findings beyond the original list (2026-10-31)

- **tools+schema wedge** (the day's biggest find): see §3 CRITICAL item.
- **flash-lite is flaky under load**: clerk calls usually take 2–7s but can
  stall >100s or degenerate into a repetition loop while copying evidence
  (fixtures/145445-clerk-c1.json: 95KB of looped hex → invalid JSON, killed a
  run before the fix). Three layers now protect the demo: the clerk gets
  domain-only URLs (root cause), one parse-retry in the client, and an engine
  fence (leaf_timeout_s) that skips dispute scoring for just that claim
  ("clerk failed — challenge unscored"). If flash-lite is bad on the day,
  `RunConfig(model_clerk="gemini-3.8-flash")` is a one-line constructor swap.
- **Judge ticker is silent**: argon emitted zero thought_summary deltas in
  every live configuration tried — plan.delta/replan.delta carry nothing.
  Narrate the decompose beat verbally; the board popping at ~15s is the visual.
- **Skeptic calibration**: first live run escalated 7/9 claims (target ≤2);
  skeptic.md's 0.6–0.8 band now requires a primary source targeting the
  claim's specific quantity. Second live run: 3 disputes found, 2 CONTESTED
  chips + 1 jury beat — in target.
- **Live demo timing**: CLI run 136s with fast clerks; server run 338s with
  two 45s clerk fences. Budget ~2.5–6 min per live run; the 2:30 demo script
  should keep the mock/replay fallback one keypress away.
- **runs/ hygiene**: run dirs were deleted out from under two in-flight runs
  during kickoff (mid-session sweep by another session?). The engine survives
  (Bus holds its file handle) but meta finalize + file-watchers break. Do NOT
  prune `runs/` while any run or server is live.
