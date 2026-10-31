"""Typed contracts for Cassation: claim graph, evidence, verdicts, and the event envelope.

Every UI-visible fact crosses exactly one boundary: an Event appended to
runs/<run_id>/trace.jsonl and broadcast on the WebSocket. Live and replay share
this format. Event catalogue and module interfaces: cassation/PROTOCOL.md.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1


class ClaimState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUPPORTED = "supported"      # advocate evidence landed, unchallenged
    CHALLENGED = "challenged"    # skeptic attacking / counter-evidence present
    CONTESTED = "contested"      # clerk routed to jury
    HUMAN_RULED = "human_ruled"  # jury verdict stamped
    REFUTED = "refuted"
    STRUCK = "struck"            # human removed at approval gate; engine-enforced
    EXCLUDED = "excluded"        # leaf timeout/error; honest omission


class AgentRole(str, Enum):
    JUDGE = "judge"
    ADVOCATE = "advocate"
    SKEPTIC = "skeptic"
    CLERK = "clerk"
    TIEBREAKER = "tiebreaker"


class Stance(str, Enum):
    SUPPORT = "support"
    REFUTE = "refute"


class Provenance(str, Enum):
    GROUNDED = "grounded"              # exact quote + resolvable URL from search-grounding metadata
    MODEL_REPORTED = "model-reported"  # degraded: model-echoed quote, domain-level link


class Claim(BaseModel):
    id: str                      # "c1".. stable within a run
    text: str
    branch: str = ""             # short grouping label, e.g. "measurement history"
    depends_on: list[str] = Field(default_factory=list)
    est_cost_gbp: float = 0.0


class ClaimGraph(BaseModel):
    question: str
    claims: list[Claim]
    est_total_cost_gbp: float = 0.0
    est_agents: int = 0


class Evidence(BaseModel):
    claim_id: str
    stance: Stance
    quote: str
    url: str = ""
    source_label: str = ""       # e.g. "Kang et al., Science 2018"
    provenance: Provenance = Provenance.MODEL_REPORTED
    confidence: float = 0.5      # 0..1 (advocate belief in the claim given this)
    strength: float = 0.5        # 0..1 (skeptic: how damaging the counter-evidence is)


class AgentFindings(BaseModel):
    agent_id: str
    claim_id: str
    role: AgentRole
    stance_summary: str = ""
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float = 0.5
    status: Literal["ok", "timeout", "error"] = "ok"


class DisputeScore(BaseModel):
    claim_id: str
    contested: bool
    strength: float = 0.0        # 0..1 contradiction strength
    support_ev: Optional[Evidence] = None
    counter_ev: Optional[Evidence] = None
    rationale: str = ""


class JuryOption(BaseModel):
    key: str                     # "a" | "b"
    summary: str
    quote: str
    url: str = ""
    source_label: str = ""


class JuryRequest(BaseModel):
    claim_id: str
    question: str                # the contested claim, restated for the human
    options: list[JuryOption]


class JuryRuling(BaseModel):
    claim_id: str
    choice_key: str
    note: str = ""
    stamped_by: str = "human"


class ApprovalResult(BaseModel):
    approved: bool
    struck_claim_ids: list[str] = Field(default_factory=list)


class Citation(BaseModel):
    id: int                      # the [n] marker used in brief_markdown
    url: str
    label: str
    quote: str = ""
    provenance: Provenance = Provenance.MODEL_REPORTED


class ClaimVerdict(BaseModel):
    claim_id: str
    verdict: Literal["SUPPORTED", "REFUTED", "CONTESTED", "HUMAN_ADJUDICATED", "EXCLUDED", "STRUCK"]
    confidence: float = 0.5
    citation_ids: list[int] = Field(default_factory=list)
    summary: str = ""


class Verdict(BaseModel):
    brief_markdown: str          # full cited brief; inline [n] markers resolve via citations
    verdicts: list[ClaimVerdict]
    citations: list[Citation]


class Dossier(BaseModel):
    """Synthesis input: everything the Judge holds in one window."""
    question: str
    graph: ClaimGraph
    approval: ApprovalResult
    findings: list[AgentFindings]
    disputes: list[DisputeScore]
    ruling: Optional[JuryRuling] = None


class GuardrailResult(BaseModel):
    ok: bool
    reason: str = ""


class CostSnapshot(BaseModel):
    est_gbp: float = 0.0
    sources: int = 0
    agents_live: int = 0
    agents_done: int = 0
    elapsed_s: float = 0.0


class RunConfig(BaseModel):
    max_leaves: int = 9  # kickoff ramp 2026-10-31: clean up to 14 parallel grounded leaves (0 errors/429s); demo calibration: Argon chose 9 claims on both live runs anyway, 9 caps the wave barrier at what we rehearsed
    leaf_timeout_s: float = 45.0  # kickoff ramp 2026-10-31: healthy grounded leaf max 25.3s at concurrency 14 (p50 11s, p95 19.2s) + get() re-fetch + one retry; 20s was cutting off good multi-search leaves
    clerk_timeout_s: float = 10.0  # demo calibration 2026-10-31: healthy flash-lite clerk is 2-7s; a looping clerk fenced at leaf_timeout_s cost 45s each (4x in run-20261031-150109) — fence clerks separately
    decompose_deadline_s: float = 25.0   # past this, seed from runs/plan-cache.json if present
    dispute_threshold: float = 0.55
    borderline_confidence: float = 0.6   # jury safety net (see PROTOCOL state machine)
    thinking_level_decompose: str = "medium"
    thinking_level_synthesis: str = "medium"  # demo calibration 2026-10-31: "high" gave 58s time-to-first-token on run-20261031-150109 (6s on -144627); A/B'd live — revert to "high" if citation quality drops
    mock_speed: float = 1.0              # mock/replay latency divisor (8 = 8x faster)
    model_judge: str = "gemini-4-argon"
    model_leaf: str = "gemini-3.8-flash"
    model_clerk: str = "gemini-3.5-flash-lite"


class Event(BaseModel):
    """The one envelope. One JSON line per event in trace.jsonl; same JSON on the WS."""
    model_config = ConfigDict(extra="allow")

    seq: int
    t: float                     # seconds since run start; replay cadence source
    type: str                    # catalogue in PROTOCOL.md
    payload: dict[str, Any] = Field(default_factory=dict)
    v: int = SCHEMA_VERSION
