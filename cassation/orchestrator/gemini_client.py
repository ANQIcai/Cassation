"""GeminiModelClient: the ModelClient protocol over the google-genai
Interactions API (SDK >= 2.30, vendored skill: vendor/gemini-skills/skills/
gemini-api-dev/SKILL.md).

KICKOFF-VERIFIED (2026-10-31) against the live API: every former
`# VERIFY-AT-KICKOFF:` mark below is now `# VERIFIED-AT-KICKOFF:` with the
observed behavior; the checklist in docs/demo/kickoff-checklist.md carries the
full results. Headline findings: (1) google_search + response_format WITH a
json `schema` wedges flash mid-output — leaves run JSON-mime-only; (2)
grounding attribution arrives as url_citation annotations on model_output
(grounding_shim reads them); (3) argon emits no thought_summary deltas on this
key, so the judge ticker is quiet during decompose/replan.

Design:
- Prompts are LOADED FROM cassation/orchestrator/prompts/*.md at client
  construction (one read per run — day-of calibration edits take effect on the
  next run). Placeholders are filled by literal `str.replace` per each file's
  TEMPLATE CONVENTION (never str.format: all other braces are literal JSON).
- Judge chain: one server-side interaction chain via previous_interaction_id
  across decompose -> replan -> synthesize (never resend the corpus).
- Synthesis is the two-part protocol from judge_synthesis.md: Part 1 markdown
  streams as synthesis.delta; after the ===VERDICT_JSON=== delimiter line,
  Part 2 is parsed as {verdicts, citations}.
- Leaves: gemini-3.8-flash + google_search tool + JSON-mime output (no schema:
  tools+schema wedges — see investigate()).
- Clerk + guardrail: gemini-3.5-flash-lite, structured JSON output.
- Every call retries on quota/transient errors with jittered backoff: the full
  ladder for judge/clerk calls, a single short backoff for leaves (must fit
  inside the engine's cfg.leaf_timeout_s budget). Streamed calls only retry if
  no delta has been forwarded yet (no duplicated ticker/brief text).
- Grounding metadata -> grounding_shim (exact quote + URL when present,
  degrade to provenance='model-reported' + domain link).
- Every raw response is dumped to fixtures/ (flight-recorder discipline).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import time
from pathlib import Path

from pydantic import BaseModel, Field

from . import grounding_shim as shim
from .model_client import OnDelta, agent_id_for
from .schemas import (
    AgentFindings,
    AgentRole,
    Citation,
    Claim,
    ClaimGraph,
    ClaimVerdict,
    DisputeScore,
    Dossier,
    Evidence,
    GuardrailResult,
    JuryRuling,
    Provenance,
    RunConfig,
    Stance,
    Verdict,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = _REPO_ROOT / "fixtures"          # anchored: CWD-independent
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

# prompt name -> file. These files are the day-of calibration dials
# (docs/demo/kickoff-checklist.md §0) — the client must load them, never ship
# divergent inline copies.
_PROMPT_FILES = {
    "guardrail": "guardrail.md",
    "decompose": "judge_decompose.md",
    "advocate": "advocate.md",          # TIEBREAKER reuses this (file's CLIENT NOTES)
    "skeptic": "skeptic.md",
    "clerk": "clerk.md",
    "replan": "judge_replan.md",
    "synthesis": "judge_synthesis.md",
}

# Retry ladders (seconds before each attempt; jitter applied at sleep time).
_RETRY_JUDGE = (0.0, 1.5, 4.0)   # decompose/replan/synthesis/clerk/guardrail
_RETRY_LEAF = (0.0, 1.0)         # one short backoff: stays inside leaf_timeout_s

# judge_synthesis.md Part 1/Part 2 delimiter; tolerate decoration (extra '='
# or whitespace) per the file's calibration note "strip in the client".
_VERDICT_SPLIT = re.compile(r"^[ \t]*=+[ \t]*VERDICT_JSON[ \t]*=+[ \t]*$", re.M)
_DELIM_HOLDBACK = 32  # max chars a split delimiter line can straddle chunks


# --- structured-output shapes (local to the live client; schemas.py is the
#     cross-module contract, these are just response_format payloads) --------

class _LeafEvidenceOut(BaseModel):
    """Evidence as advocate.md/skeptic.md's output contract shapes it."""
    claim_id: str = ""
    stance: str = "support"
    quote: str = Field(description="EXACT quote from a source found via search")
    url: str = Field(default="", description="resolvable source URL")
    source_label: str = Field(default="", description="e.g. 'Kang et al., Science 2018'")
    provenance: str = "model-reported"
    confidence: float = 0.5
    strength: float = 0.0


class _LeafOut(BaseModel):
    claim_id: str = ""
    stance_summary: str = ""
    evidence: list[_LeafEvidenceOut] = Field(default_factory=list)
    confidence: float = 0.5


class _ReplanAdjustment(BaseModel):
    claim_id: str = ""
    new_confidence: float = 0.5
    reason: str = ""


class _ReplanTiebreaker(BaseModel):
    claim_id: str = ""
    mandate: str = ""


class _ReplanOut(BaseModel):
    """judge_replan.md output: {notes, adjustments, tiebreaker}."""
    notes: str = ""
    adjustments: list[_ReplanAdjustment] = Field(default_factory=list)
    tiebreaker: _ReplanTiebreaker | None = None


class _SynthOut(BaseModel):
    """judge_synthesis.md Part 2 (brief_markdown is supplied by the client)."""
    verdicts: list[ClaimVerdict] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)


def _json_schema(model: type[BaseModel]) -> dict:
    return model.model_json_schema()


def _strip_fences(text: str) -> str:
    # VERIFIED-AT-KICKOFF: no fences observed on any structured call (flash-lite,
    # argon, flash JSON mode); kept as pure defense.
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    return m.group(1).strip() if m else text.strip()


log = logging.getLogger("cassation.gemini")


def _repair_json(text: str) -> str:
    """Best-effort repair of stochastic model-JSON damage seen live:
    (a) digit-loop degeneration (a number running away in repeated digits
    until truncation), (b) truncated tail -> close open strings/brackets."""
    # collapse absurd digit runs inside numbers: keep a sane prefix
    text = re.sub(r"(\d\.\d{1,6})\d{18,}", r"\1", text)
    text = re.sub(r"(?<=\d)(\d)\1{18,}", r"\1", text)
    try:
        json.loads(text)
        return text
    except Exception:
        pass
    # truncated: cut back to the last structural comma/bracket, then close
    stack, in_str, esc, last_safe = [], False, False, 0
    for i, ch in enumerate(text):
        if in_str:
            if esc: esc = False
            elif ch == "\\": esc = True
            elif ch == '"': in_str = False
            continue
        if ch == '"': in_str = True
        elif ch in "{[": stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if stack: stack.pop()
            last_safe = i + 1
        elif ch == ",": last_safe = i
    candidate = text[:last_safe].rstrip().rstrip(",")
    # recompute closers for the candidate prefix
    stack, in_str, esc = [], False, False
    for ch in candidate:
        if in_str:
            if esc: esc = False
            elif ch == "\\": esc = True
            elif ch == '"': in_str = False
            continue
        if ch == '"': in_str = True
        elif ch in "{[": stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack: stack.pop()
    repaired = candidate + "".join(reversed(stack))
    try:
        json.loads(repaired)
        return repaired
    except Exception:
        return text  # let the caller's retry take over


def _fill(template: str, **values) -> str:
    """Literal `{placeholder}` substitution per the prompt files' TEMPLATE
    CONVENTION: str.replace, never str.format (other braces are literal JSON)."""
    for key, value in values.items():
        template = template.replace("{" + key + "}", str(value))
    return template


def _stance_of(raw: str, default: Stance) -> Stance:
    try:
        return Stance(raw)
    except ValueError:
        return default


def _match_original(chosen: Evidence | None,
                    originals: list[Evidence]) -> Evidence | None:
    """clerk.md calibration note: the clerk's echo only needs to IDENTIFY the
    item — re-match it to the original Evidence so the jury's quote cards stay
    exact. Quote prefix is the primary key (the clerk now receives domain-only
    urls — see clerk_score — so url equality is secondary)."""
    if chosen is None:
        return None
    for orig in originals:
        if (orig.quote[:40] == chosen.quote[:40]
                or (chosen.url and orig.url == chosen.url)):
            return orig
    return chosen


class _BriefStream:
    """Forwards Part-1 markdown deltas and withholds everything from the
    ===VERDICT_JSON=== delimiter onward (judge_synthesis.md two-part output).
    A small tail is held back in case the delimiter straddles two chunks."""

    def __init__(self, on_delta: OnDelta | None):
        self._on = on_delta
        self.buf = ""
        self._sent = 0
        self._stopped = False

    async def feed(self, chunk: str) -> None:
        self.buf += chunk
        await self._advance(final=False)

    async def flush(self) -> None:
        await self._advance(final=True)

    async def _advance(self, final: bool) -> None:
        if self._stopped or self._on is None:
            return
        m = _VERDICT_SPLIT.search(self.buf)
        if m:
            cut = m.start()
            self._stopped = True
        elif final:
            cut = len(self.buf)
            self._stopped = True
        else:
            cut = max(self._sent, len(self.buf) - _DELIM_HOLDBACK)
        if cut > self._sent:
            await self._on(self.buf[self._sent:cut])
            self._sent = cut


class GeminiModelClient:
    mode = "live"

    def __init__(self, cfg: RunConfig, api_key: str | None = None):
        from google import genai  # lazy: only needed in --live mode

        self.cfg = cfg
        # VERIFIED-AT-KICKOFF: Client() self-discovers BOTH GEMINI_API_KEY and
        # GOOGLE_API_KEY (GOOGLE_API_KEY wins if both are set — SDK
        # _api_client.py). The repo .env sets GEMINI_API_KEY only.
        key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        # VERIFIED-AT-KICKOFF: http_options timeout is MILLISECONDS (SDK
        # converts /1000 for httpx; live APITimeoutError fired at exactly
        # 120s with timeout=120_000). 20 min per docs/guide.txt advice for
        # high-thinking synthesis streams.
        http_options = {"timeout": 20 * 60 * 1000}
        self._client = (genai.Client(api_key=key, http_options=http_options)
                        if key else genai.Client(http_options=http_options))
        # VERIFIED-AT-KICKOFF: client.aio.interactions.create works live;
        # `await create(stream=True)` returns an AsyncStream iterator directly.
        self._aio = self._client.aio
        self._judge_chain_id: str | None = None  # previous_interaction_id chain
        self._question: str = ""  # set at decompose; leaves' {question} placeholder
        # Prompt files: read ONCE per client (= once per run). Day-of edits to
        # prompts/*.md take effect on the next run, not mid-run.
        self._prompts: dict[str, str] = {
            name: (PROMPTS_DIR / fname).read_text(encoding="utf-8")
            for name, fname in _PROMPT_FILES.items()
        }
        FIXTURES_DIR.mkdir(exist_ok=True)

    # --- plumbing -----------------------------------------------------------

    def _record_fixture(self, name: str, obj) -> None:
        """Flight recorder: dump every raw response; never let it break a run."""
        try:
            path = FIXTURES_DIR / f"{time.strftime('%H%M%S')}-{name}.json"
            if hasattr(obj, "model_dump_json"):
                path.write_text(obj.model_dump_json(), encoding="utf-8")
            elif hasattr(obj, "model_dump"):
                path.write_text(json.dumps(obj.model_dump(), default=str), encoding="utf-8")
            else:
                path.write_text(json.dumps(obj, default=str), encoding="utf-8")
        except Exception:
            pass

    @staticmethod
    def _retryable(e: Exception) -> bool:
        # VERIFIED-AT-KICKOFF: client-side stalls surface as
        # google.genai...APITimeoutError("Request timed out...") — retryable
        # (stream retries only fire before any delta was forwarded). 429 shape
        # kept from docs; no real 429 was provoked at concurrency <= 14.
        return any(s in str(e) for s in
                   ("429", "RESOURCE_EXHAUSTED", "500", "503", "overload",
                    "timed out", "ReadTimeout"))

    @staticmethod
    async def _backoff(delay: float) -> None:
        if delay:
            await asyncio.sleep(delay * random.uniform(0.75, 1.25))  # jitter

    async def _create(self, name: str, retries: tuple[float, ...] = _RETRY_JUDGE,
                      **body):
        """Non-streaming create with jittered retry/backoff on quota/transient errors."""
        last: Exception | None = None
        for delay in retries:
            await self._backoff(delay)
            try:
                # VERIFIED-AT-KICKOFF: kwargs-style create() accepted live for
                # every combination used below.
                interaction = await self._aio.interactions.create(**body)
                self._record_fixture(name, interaction)
                return interaction
            except Exception as e:  # noqa: BLE001 — retry then surface
                last = e
                if not self._retryable(e):
                    raise
        raise last

    async def _stream(self, name: str, on_text: OnDelta | None,
                      on_thought: OnDelta | None, on_status: OnDelta | None,
                      retries: tuple[float, ...] = _RETRY_JUDGE,
                      **body) -> tuple[str, str | None]:
        """Streaming create with jittered retry/backoff. Returns
        (accumulated_text, interaction_id).

        Retries cover the stream-open and any failure BEFORE a delta has been
        forwarded; once ticker/brief text has gone out, errors surface (a
        retry would duplicate streamed output).

        on_text: raw model text deltas (markdown brief, or JSON when
        response_format is set — callers pass None to keep JSON silent).
        on_thought: thought-summary deltas (judge ticker).
        on_status: one-liners derived from tool steps ("Searching Google...").
        """
        last: Exception | None = None
        for delay in retries:
            await self._backoff(delay)
            forwarded = False
            text_parts: list[str] = []
            interaction_id: str | None = None
            raw_events = []
            try:
                # VERIFIED-AT-KICKOFF: `await create(stream=True)` returns an
                # AsyncStream async-iterator directly.
                stream = await self._aio.interactions.create(stream=True, **body)
                async for event in stream:
                    raw_events.append(event)
                    etype = getattr(event, "event_type", None)
                    if etype == "interaction.created":
                        # VERIFIED-AT-KICKOFF: id lives at event.interaction.id
                        # on interaction.created.
                        interaction_id = getattr(getattr(event, "interaction", None), "id", None)
                    elif etype == "step.start" and on_status is not None:
                        step_type = getattr(getattr(event, "step", None), "type", "") or ""
                        # VERIFIED-AT-KICKOFF: step.start carries step.type
                        # ("google_search_call" then "google_search_result");
                        # only the *_call opens the "searching" ticker line so
                        # the pair doesn't double-print.
                        if "search" in step_type and step_type.endswith("_call"):
                            forwarded = True
                            await on_status("Searching Google…")
                    elif etype == "step.delta":
                        delta = getattr(event, "delta", None)
                        dtype = getattr(delta, "type", None)
                        # VERIFIED-AT-KICKOFF: delta text lives at delta.text
                        # (direct); the delta.content.text fallback never fired
                        # live and is kept as defense.
                        dtext = (getattr(delta, "text", None)
                                 or getattr(getattr(delta, "content", None), "text", None)
                                 or "")
                        # VERIFIED-AT-KICKOFF: the live delta type is
                        # "thought_summary" ("thought" is a STEP type, kept in
                        # the match as defense). Summaries may legitimately be
                        # EMPTY when the model "didn't reason enough" (hosted
                        # thinking.md) — a silent ticker on an easy call is
                        # expected, not a bug.
                        if dtype in ("thought", "thought_summary"):
                            if on_thought is not None and dtext:
                                forwarded = True
                                await on_thought(dtext)
                        elif dtype == "text" and dtext:
                            text_parts.append(dtext)
                            if on_text is not None:
                                forwarded = True
                                await on_text(dtext)
                    elif etype == "interaction.completed":
                        final = getattr(event, "interaction", None)
                        if interaction_id is None:
                            interaction_id = getattr(final, "id", None)
                        # VERIFIED-AT-KICKOFF: the SDK consumes continuation
                        # hops internally; the one interaction.completed seen
                        # here is final (status "completed") and carries only
                        # metadata — final steps need interactions.get(id).
                self._record_fixture(name, [getattr(e, "model_dump", lambda: str(e))() for e in raw_events])
                return "".join(text_parts), interaction_id
            except Exception as e:  # noqa: BLE001 — retry then surface
                last = e
                if forwarded or not self._retryable(e):
                    raise
        raise last

    # --- ModelClient protocol ------------------------------------------------

    async def guardrail(self, question: str) -> GuardrailResult:
        interaction = await self._create(
            "guardrail",
            model=self.cfg.model_clerk,
            input=_fill(self._prompts["guardrail"], question=question),
            # VERIFIED-AT-KICKOFF: this response_format shape works on
            # gemini-3.5-flash-lite (2.5s, raw JSON in output_text).
            response_format={"type": "text", "mime_type": "application/json",
                             "schema": _json_schema(GuardrailResult)},
        )
        # VERIFIED-AT-KICKOFF: interaction.output_text exists on non-streamed
        # calls and holds the raw (unfenced) JSON payload.
        return GuardrailResult.model_validate_json(_strip_fences(interaction.output_text))

    async def decompose(self, question: str, on_delta: OnDelta) -> ClaimGraph:
        self._question = question  # leaves fill their {question} placeholder from this
        last_err: Exception | None = None
        for attempt in range(3):   # model can stochastically degenerate (digit loops)
            text, iid = await self._stream(
                "decompose",
                on_text=None,          # JSON graph: not ticker material
                on_thought=on_delta,   # judge thinking summary -> plan.delta
                on_status=None,
                model=self.cfg.model_judge,
                # judge_decompose.md: {max_claims} is fed from cfg.max_leaves —
                # the adaptive leaf-count dial (pivot-gate criterion #1).
                input=_fill(self._prompts["decompose"], question=question,
                            max_claims=self.cfg.max_leaves),
                response_format={"type": "text", "mime_type": "application/json",
                                 "schema": _json_schema(ClaimGraph)},
                # VERIFIED-AT-KICKOFF: thinking_level + thinking_summaries="auto"
                # are accepted by gemini-4-argon WITH response_format+schema (no
                # tools on this call, so the schema is safe here — the wedge is
                # specific to tools+schema). Summaries can be empty on easy
                # prompts; the real decompose at "medium" does produce them.
                generation_config={"thinking_level": self.cfg.thinking_level_decompose,
                                   "thinking_summaries": "auto",
                                   # fail FAST on digit-loop degeneration (seen live:
                                   # a cost field ran away in zeros until truncation)
                                   "max_output_tokens": 4096},
            )
            try:
                graph = ClaimGraph.model_validate_json(_repair_json(_strip_fences(text)))
                self._judge_chain_id = iid
                graph.question = question
                return graph
            except Exception as e:  # truncated/degenerate JSON -> fresh roll
                last_err = e
                log.warning("decompose parse failed (attempt %d): %r", attempt + 1, repr(e)[:160])
        raise last_err  # type: ignore[misc]

    async def investigate(self, claim: Claim, role: AgentRole, context: str,
                          on_delta: OnDelta) -> AgentFindings:
        # advocate.md CLIENT NOTES: the TIEBREAKER role reuses the advocate
        # prompt; the Judge's tie-breaker mandate arrives inside {context}.
        tmpl = self._prompts["skeptic" if role == AgentRole.SKEPTIC else "advocate"]
        default_stance = Stance.REFUTE if role == AgentRole.SKEPTIC else Stance.SUPPORT

        text, leaf_iid = await self._stream(
            f"leaf-{role.value}-{claim.id}",
            on_text=None,        # structured JSON — ticker gets status lines
            on_thought=on_delta,
            on_status=on_delta,
            retries=_RETRY_LEAF,  # one short backoff: fits the 20s leaf budget
            model=self.cfg.model_leaf,
            input=_fill(tmpl, question=self._question, claim_id=claim.id,
                        claim_text=claim.text, branch=claim.branch,
                        context=context or "(none)"),
            # VERIFIED-AT-KICKOFF (2026-10-31): google_search + response_format
            # WITH a json `schema` wedges gemini-3.8-flash — the stream opens,
            # searches, then stalls forever inside model_output (fixtures/
            # kickoff2-p3c-solo-combo-events.json); non-streamed never returns.
            # google_search + mime_type-only JSON mode works (8-10s): the
            # prompt files carry the exact output shape and _LeafOut validates
            # client-side, so the schema key is dropped — NOT the whole
            # response_format (the flash-lite structuring fallback was not
            # needed).
            tools=[{"type": "google_search"}],
            response_format={"type": "text", "mime_type": "application/json"},
            generation_config={"thinking_level": "low"},
        )
        out = _LeafOut.model_validate_json(_strip_fences(text))

        # Grounding: upgrade/degrade provenance from search metadata.
        # VERIFIED-AT-KICKOFF: grounding attribution IS exposed, but as
        # url_citation annotations on the model_output step's content — NOT on
        # google_search_result steps (those carry only the Search-Suggestions
        # chip HTML). grounding_shim.extract_sources reads the annotations.
        # The final steps are NOT on the stream's interaction.completed event,
        # so the one-shot re-fetch by id below is required.
        sources: list[dict] = []
        if leaf_iid:
            try:
                # VERIFIED-AT-KICKOFF: positional get(leaf_iid) works and
                # returns the full step list (incl. annotated model_output)
                # after a streamed call.
                final = await self._aio.interactions.get(leaf_iid)
                self._record_fixture(f"leaf-steps-{role.value}-{claim.id}", final)
                sources = shim.extract_sources(getattr(final, "steps", []) or [])
            except Exception:
                sources = []

        # Provenance starts MODEL_REPORTED regardless of the model's own
        # claim; the shim upgrades to GROUNDED only against real metadata.
        evidence = [shim.ground_evidence(
            Evidence(claim_id=claim.id, stance=_stance_of(e.stance, default_stance),
                     quote=e.quote, url=e.url, source_label=e.source_label,
                     provenance=Provenance.MODEL_REPORTED,
                     confidence=e.confidence, strength=e.strength),
            sources) for e in out.evidence]
        if not evidence and sources:
            evidence = shim.evidence_from_sources(claim.id, default_stance, sources)

        return AgentFindings(agent_id=agent_id_for(role, claim.id),
                             claim_id=claim.id, role=role,
                             stance_summary=out.stance_summary,
                             evidence=evidence, confidence=out.confidence)

    async def clerk_score(self, claim: Claim, support: list[Evidence],
                          counter: list[Evidence]) -> DisputeScore:
        # VERIFIED-AT-KICKOFF (fixtures/145445-clerk-c1.json): feeding the
        # clerk full Evidence JSON with ~300-char vertexaisearch redirect URLs
        # can throw flash-lite into a repetition loop while verbatim-copying
        # (95KB of looped hex, truncated mid-string -> invalid JSON, 115s
        # call). The clerk never follows URLs (clerk.md: judge from quote +
        # source_label only), so it gets domain-level urls; _match_original
        # restores the pristine originals for the jury cards afterwards.
        def ev_json(evs: list[Evidence]) -> str:
            slim = []
            for e in evs:
                d = e.model_dump(mode="json")
                d["url"] = f"https://{shim.domain_of(e.url)}/" if e.url else ""
                slim.append(d)
            return json.dumps(slim, ensure_ascii=False)

        async def call():
            return await self._create(
                f"clerk-{claim.id}",
                model=self.cfg.model_clerk,
                input=_fill(self._prompts["clerk"], claim_id=claim.id,
                            claim_text=claim.text,
                            dispute_threshold=self.cfg.dispute_threshold,
                            support_evidence_json=ev_json(support),
                            counter_evidence_json=ev_json(counter)),
                response_format={"type": "text", "mime_type": "application/json",
                                 "schema": _json_schema(DisputeScore)},
            )

        interaction = await call()
        try:
            out = DisputeScore.model_validate_json(_strip_fences(interaction.output_text))
        except Exception:
            # One fresh attempt clears stochastic degeneration; a second
            # failure surfaces (the engine's clerk fence keeps the run alive).
            interaction = await call()
            out = DisputeScore.model_validate_json(_strip_fences(interaction.output_text))
        out.claim_id = claim.id  # hard-pin: the clerk never re-routes a claim
        # Verbatim-copy repair (clerk.md calibration note): the chosen items
        # become the jury's quote cards, so restore the originals when matched.
        out.support_ev = _match_original(out.support_ev, support)
        out.counter_ev = _match_original(out.counter_ev, counter)
        return out

    async def replan(self, ruling: JuryRuling, graph: ClaimGraph,
                     on_delta: OnDelta) -> str:
        # judge_replan.md CLIENT NOTES: the graph is usually already in the
        # judge chain's context — elide it when the chain is live.
        graph_json = ("(see the plan of record above in this interaction chain)"
                      if self._judge_chain_id else graph.model_dump_json())
        jury_request_json = json.dumps({
            "claim_id": ruling.claim_id,
            "question": "(the contested claim as shown to the human on the dispute takeover)",
            "options": [{"key": "a", "summary": "uphold the claim as stated"},
                        {"key": "b", "summary": "rule for the counter-evidence"}],
        })
        text, iid = await self._stream(
            "replan",
            on_text=None,          # final output is pure JSON (judge_replan.md)
            on_thought=on_delta,   # streamed thinking -> replan.delta ticker
            on_status=None,
            model=self.cfg.model_judge,
            input=_fill(self._prompts["replan"], graph_json=graph_json,
                        jury_request_json=jury_request_json,
                        ruling_json=ruling.model_dump_json()),
            # VERIFIED-AT-KICKOFF: previous_interaction_id chaining works live
            # on this key (fixtures/kickoff2-p5-chain.json: a fact from call 1
            # is recalled in call 2 without resending it) — store/retention is
            # live at demo timescales.
            previous_interaction_id=self._judge_chain_id,
            response_format={"type": "text", "mime_type": "application/json",
                             "schema": _json_schema(_ReplanOut)},
            generation_config={"thinking_level": "low",
                               "thinking_summaries": "auto"},
        )
        self._judge_chain_id = iid or self._judge_chain_id
        out = _ReplanOut.model_validate_json(_strip_fences(text))
        notes = out.notes.strip()
        # Mandate -> context wiring (judge_replan.md CLIENT NOTES): the engine
        # feeds these notes into the tiebreaker leaf's {context}, so the
        # mandate rides along in the returned notes string.
        if out.tiebreaker and out.tiebreaker.mandate:
            notes = (f"{notes} Tie-breaker mandate ({out.tiebreaker.claim_id}): "
                     f"{out.tiebreaker.mandate}").strip()
        return notes

    async def synthesize(self, dossier: Dossier, on_delta: OnDelta) -> Verdict:
        # Two-part protocol (judge_synthesis.md): Part 1 markdown streams to
        # synthesis.delta; Part 2 after ===VERDICT_JSON=== is parsed as
        # {verdicts, citations}. No response_format — the output is mixed.
        brief_stream = _BriefStream(on_delta)
        text, iid = await self._stream(
            "synthesize",
            on_text=brief_stream.feed,
            on_thought=None,
            on_status=None,
            model=self.cfg.model_judge,
            input=_fill(self._prompts["synthesis"],
                        dossier_json=dossier.model_dump_json()),
            previous_interaction_id=self._judge_chain_id,
            generation_config={"thinking_level": self.cfg.thinking_level_synthesis,
                               "thinking_summaries": "auto"},
        )
        self._judge_chain_id = iid or self._judge_chain_id
        await brief_stream.flush()

        parts = _VERDICT_SPLIT.split(text, maxsplit=1)
        brief = parts[0].strip()
        if len(parts) < 2:
            # VERIFIED-AT-KICKOFF: argon honored the delimiter on the live run
            # (clean split, no JSON leaked into synthesis.delta). Kept loud in
            # case a future prompt edit breaks it.
            raise ValueError("synthesis output missing the ===VERDICT_JSON=== part")
        out = _SynthOut.model_validate_json(_strip_fences(parts[1]))
        return Verdict(brief_markdown=brief, verdicts=out.verdicts,
                       citations=out.citations)
