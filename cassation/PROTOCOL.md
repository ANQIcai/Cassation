# CASSATION — Protocol & Module Contracts (v1)

This file + `orchestrator/schemas.py` are the **binding contracts**. All code conforms to them; change them only deliberately and update both sides.

## Philosophy

One envelope. Every UI-visible fact is an `Event` (see `schemas.py`) that is **simultaneously** (a) appended as one JSON line to `runs/<run_id>/trace.jsonl` and (b) broadcast on the WebSocket. Live and replay share this format: the replay engine re-emits a trace over the same WS at original cadence. The UI cannot tell the difference except for the `run.start.mode` field and the REPLAY chip. This is why the fallback cannot bit-rot.

- `run_id`: `run-YYYYmmdd-HHMMSS` (local time at run start).
- `runs/<run_id>/trace.jsonl` — events; `runs/<run_id>/meta.json` — `{run_id, question, mode, started_at, finished_at, status, counts}` written at start and finalized at end.
- Event envelope: `{"seq": int, "t": float_seconds_since_run_start, "type": str, "payload": {...}, "v": 1}`.

## State machine (engine)

`GUARDRAIL → DECOMPOSE → APPROVE (gate) → ADVOCATES → SKEPTICS → DISPUTES → JURY (gate) → REPLAN → SYNTHESIS → DONE`

- Gates block on `HumanGates` futures (resolved by HTTP POSTs). Everything else is autonomous.
- **Struck claims are enforced by the engine**: a struck claim never gets an agent spawned, and is excluded from synthesis input. Not a prompt-level instruction.
- Every leaf call is wrapped in `asyncio.wait_for(cfg.leaf_timeout_s)` → on timeout emit `agent.done{status:"timeout"}` + `claim.state{state:"excluded"}` and continue.
- If no dispute clears `cfg.dispute_threshold`, the jury opens anyway on the claim with the lowest advocate confidence below `cfg.borderline_confidence` (safety net for demo beat). If neither triggers, skip JURY+REPLAN.
- A `cost.update` heartbeat is emitted at least every 1s from DECOMPOSE to DONE.

## Event catalogue (type → payload)

Payload objects are the pydantic models from `schemas.py` serialized with `.model_dump()`, or the inline shapes below.

| type | emitted during | payload |
|---|---|---|
| `run.start` | start | `{run_id, question, mode: "live"\|"mock"\|"replay", started_at_iso}` |
| `guardrail.result` | GUARDRAIL | `GuardrailResult` |
| `plan.delta` | DECOMPOSE | `{text}` (streamed judge thinking/summary text, display in judge ticker) |
| `plan.complete` | DECOMPOSE | `ClaimGraph` |
| `approval.request` | APPROVE | `ClaimGraph` (UI opens modal) |
| `approval.result` | APPROVE | `ApprovalResult` |
| `claim.state` | any | `{claim_id, state: ClaimState, note?}` |
| `agent.spawn` | fan-outs | `{agent_id, role: AgentRole, claim_id?, model, label}` |
| `agent.delta` | fan-outs | `{agent_id, text}` (ticker text, append) |
| `agent.evidence` | fan-outs | `Evidence` + `{agent_id}` |
| `agent.done` | fan-outs | `{agent_id, status: "ok"\|"timeout"\|"error", elapsed_s}` |
| `dispute.found` | DISPUTES | `DisputeScore` |
| `takeover.show` | DISPUTES | `{claim_id, left: {quote,url,source_label}, right: {quote,url,source_label}}` |
| `takeover.hide` | DISPUTES/JURY | `{}` |
| `jury.request` | JURY | `JuryRequest` |
| `jury.ruling` | JURY | `JuryRuling` |
| `replan.delta` | REPLAN | `{text}` |
| `replan.complete` | REPLAN | `{notes, added_agent_ids: [..]}` |
| `synthesis.delta` | SYNTHESIS | `{text}` (markdown chunks with inline `[n]` citation markers) |
| `verdict.complete` | SYNTHESIS | `Verdict` |
| `cost.update` | heartbeat | `CostSnapshot` |
| `run.complete` | end | `{status: "ok"\|"error"\|"aborted", elapsed_s}` |
| `replay.info` | replay only | `{original_run_id, recorded_at_iso}` (first event after run.start) |

UI must tolerate unknown event types (ignore) and out-of-order resilience is NOT required — the bus guarantees in-order per-connection delivery (backlog then tail).

## HTTP / WS API (server, default port 8990)

| route | behavior |
|---|---|
| `GET /` | the SPA (`web/index.html` + static) |
| `WS /ws` | on connect: send full event backlog of the active (or replayed) run, then live tail. Mid-run refresh must work. |
| `POST /api/run` `{question}` | start a live/mock run → `{run_id}`; 409 if one is in progress |
| `POST /api/approve` `{run_id, approved, struck_claim_ids}` | resolves the APPROVE gate |
| `POST /api/jury` `{run_id, claim_id, choice_key}` | resolves the JURY gate |
| `POST /api/replay` `{run_id, speed?}` | switch UI data source to replaying that trace (the demo hotkey calls this) |
| `GET /api/runs` | docket: `[{run_id, question, started_at, status, n_claims, verdict_counts, duration_s}]` from `runs/*/meta.json` |
| `GET /brief/{run_id}` | standalone server-rendered brief page (phone-friendly, citation links) |
| `GET /api/qr/{run_id}.png` | QR of `http://<lan-ip>:8990/brief/<run_id>` |

Server CLI: `python -m cassation.server --mock | --replay <run_id|path> [--speed 2.0] [--port 8990]`
CLI-first runner (no server): `python -m cassation.orchestrator.cli "question" --mock [--speed 8]` → prints events, writes a real trace.
Approve/jury in CLI mode: auto-resolve after 1.5s (approve all; jury picks option "b") so e2e runs headless.

## Module interfaces (python)

```python
# bus.py
class Bus:
    def __init__(self, run_dir: Path): ...
    async def emit(self, type: str, payload: BaseModel | dict) -> Event   # seq+t stamped here; append + broadcast
    def subscribe(self) -> tuple[list[Event], asyncio.Queue[Event]]       # (backlog_snapshot, live_queue)
    def unsubscribe(self, q) -> None

# engine.py
async def run_cassation(question: str, bus: Bus, client: ModelClient,
                       gates: HumanGates, cfg: RunConfig) -> None

class HumanGates(Protocol):
    async def await_approval(self, graph: ClaimGraph) -> ApprovalResult
    async def await_jury(self, req: JuryRequest) -> JuryRuling
# server implements with asyncio.Future resolved by the POST endpoints;
# cli.py implements with auto-resolve timers.

# model_client.py
class ModelClient(Protocol):
    async def guardrail(self, question: str) -> GuardrailResult
    async def decompose(self, question: str, on_delta: OnDelta) -> ClaimGraph
    async def investigate(self, claim: Claim, role: AgentRole, context: str,
                          on_delta: OnDelta) -> AgentFindings          # advocate | skeptic | tiebreaker
    async def clerk_score(self, claim: Claim, support: list[Evidence],
                          counter: list[Evidence]) -> DisputeScore
    async def replan(self, ruling: JuryRuling, graph: ClaimGraph,
                     on_delta: OnDelta) -> str                          # notes; engine spawns tiebreaker
    async def synthesize(self, dossier: Dossier, on_delta: OnDelta) -> Verdict
OnDelta = Callable[[str], Awaitable[None]]
```

- Class names are pinned: `MockModelClient(cfg)` in mock_client.py, `GeminiModelClient(cfg)` in gemini_client.py. `meta.json` ownership: the process that starts the run owns it (server for server runs, cli for CLI runs), written merge-safely.
- `mock_client.py` — `MockModelClient(cfg)`: canned-but-plausible everyday demo content (e-bike battery charging safety), per-call latencies (decompose ~6s, leaves 4–12s jittered, clerk <1s, synthesis ~10s streamed) all divided by `cfg.mock_speed`; one guaranteed dispute (the running-cost claim); deterministic enough for tests.
- `gemini_client.py` — same protocol over `google-genai` Interactions API per `vendor/gemini-skills/skills/gemini-api-dev/SKILL.md`: judge chain keeps `previous_interaction_id` across decompose → replan → synthesize; leaves = `gemini-3.8-flash` + Google Search tool + structured output; clerk = `gemini-3.5-flash-lite`; `thinking_level` from cfg. Grounding metadata → `grounding_shim.py` (exact quote + URL when present; degrade to `provenance="model-reported"` + domain link). **Cannot be run before kickoff — mark every assumption `# VERIFY-AT-KICKOFF` and keep all of them listed in `docs/demo/kickoff-checklist.md`.**
- `replay.py` — read trace.jsonl, re-emit through a Bus at recorded `t` cadence × 1/speed (don't re-append to disk when replaying: Bus gets `record=False` mode).

## Non-negotiable demo affordances (UI)

Claim cards with 5 visual states (pending/green/red/amber/human-stamped) · per-agent tickers · cost strip (est £, sources, agents, elapsed — "estimate" label) · approval modal with click-to-strike rows · jury panel (two quote cards + Rule buttons) · DISPUTED full-screen takeover · docket wall (from `/api/runs`) · verdict view rendering `synthesis.delta` markdown + citation chips · LIVE/MOCK/REPLAY chip top-right, always visible · replay hotkey `R` → `POST /api/replay` with newest golden · dark theme, legible at 3 m, safe at 1280×720, no frameworks, no build step.
