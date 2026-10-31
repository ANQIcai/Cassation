"""ModelClient protocol + small helpers shared by mock and gemini clients.

Contract: cassation/PROTOCOL.md "Module interfaces". Engine and clients must
agree on agent ids, so the id scheme lives here.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Protocol, runtime_checkable

from .schemas import (
    AgentFindings,
    AgentRole,
    Claim,
    ClaimGraph,
    DisputeScore,
    Dossier,
    Evidence,
    GuardrailResult,
    JuryRuling,
    Verdict,
)

OnDelta = Callable[[str], Awaitable[None]]

_ROLE_PREFIX = {
    AgentRole.ADVOCATE: "adv",
    AgentRole.SKEPTIC: "skp",
    AgentRole.TIEBREAKER: "tie",
    AgentRole.JUDGE: "judge",
    AgentRole.CLERK: "clerk",
}


def agent_id_for(role: AgentRole, claim_id: str) -> str:
    return f"{_ROLE_PREFIX[role]}-{claim_id}"


def agent_label(role: AgentRole, claim: Claim) -> str:
    short = claim.text if len(claim.text) <= 48 else claim.text[:45] + "..."
    return f"{role.value.capitalize()} · {claim.id}: {short}"


@runtime_checkable
class ModelClient(Protocol):
    mode: str  # "mock" | "live" — used for run.start payload

    async def guardrail(self, question: str) -> GuardrailResult: ...

    async def decompose(self, question: str, on_delta: OnDelta) -> ClaimGraph: ...

    async def investigate(self, claim: Claim, role: AgentRole, context: str,
                          on_delta: OnDelta) -> AgentFindings: ...

    async def clerk_score(self, claim: Claim, support: list[Evidence],
                          counter: list[Evidence]) -> DisputeScore: ...

    async def replan(self, ruling: JuryRuling, graph: ClaimGraph,
                     on_delta: OnDelta) -> str: ...

    async def synthesize(self, dossier: Dossier, on_delta: OnDelta) -> Verdict: ...


def dossier_digest(dossier: Dossier) -> str:
    """Compact plain-text digest of a dossier: synthesis prompt input.

    One block per claim with its ok findings; struck claims were already
    filtered out by the engine before the dossier was built.
    """
    lines: list[str] = [f"QUESTION: {dossier.question}", ""]
    by_claim: dict[str, list] = {}
    for f in dossier.findings:
        by_claim.setdefault(f.claim_id, []).append(f)
    for claim in dossier.graph.claims:
        lines.append(f"CLAIM {claim.id} [{claim.branch}]: {claim.text}")
        for f in by_claim.get(claim.id, []):
            lines.append(f"  {f.role.value} (confidence {f.confidence:.2f}): {f.stance_summary}")
            for ev in f.evidence:
                lines.append(f'    - [{ev.stance.value}] "{ev.quote}" — {ev.source_label} {ev.url}')
        lines.append("")
    for d in dossier.disputes:
        if d.contested:
            lines.append(f"DISPUTE on {d.claim_id} (strength {d.strength:.2f}): {d.rationale}")
    if dossier.ruling:
        r = dossier.ruling
        lines.append(f"HUMAN RULING on {r.claim_id}: chose option '{r.choice_key}'. {r.note}")
    return "\n".join(lines)
