"""MockModelClient: canned-but-plausible e-bike battery charging-safety run.

Drives the whole system without a Gemini key. Content mirrors the everyday
"is it safe to charge an e-bike battery indoors overnight?" question (the
London Fire Brigade fire statistics, certified-pack failure rates, the
fire-service never-overnight guidance, thermal runaway behaviour, the
conversion-kit regulation gap, insurance exclusions) so the demo rehearses
with realistic claims, quotes and URLs. Source labels and URLs are plausible,
clearly-labelled mock citations (London Fire Brigade, NFCC, Electrical Safety
First, OPSS, Which?, BBC).

Deterministic: fixed per-(role, claim) jitter seeds, fixed content, so e2e
asserts are stable — the milestone sequence (everything except agent.delta
interleaving), each agent's own event stream, and the verdict payload are
identical across runs. Only the global interleaving of ticker deltas from
CONCURRENT agents can swap when two wakeups collide at ms resolution, so
assert on milestones, not on raw cross-agent delta order. All latencies
divide by cfg.mock_speed. Guaranteed dispute: claim c2 — the certification
headline (certified EN 50604/UL packs with a BMS have very low failure
rates, OPSS) vs the fire-service counter (never charge overnight/unattended
or in an escape route — most fatal fires started while charging at night):
the blanket "safe indoors" claim does not generalize.
"""
from __future__ import annotations

import asyncio
import random

from .model_client import OnDelta, agent_id_for
from .schemas import (
    AgentFindings,
    AgentRole,
    Claim,
    ClaimGraph,
    ClaimVerdict,
    Citation,
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

DISPUTED_CLAIM_ID = "c2"  # the "safe to charge indoors" headline; always contested

_CLAIMS = [
    Claim(id="c1", text="UK e-bike and e-scooter battery fires rose sharply: London Fire Brigade attended one roughly every two days in 2023/24.",
          branch="fire-risk data", depends_on=[], est_cost_gbp=0.06),
    Claim(id="c2", text="Charging a certified (UL/EN 50604) e-bike battery indoors is safe.",
          branch="charging practice", depends_on=["c1"], est_cost_gbp=0.08),
    Claim(id="c3", text="Most e-bike battery fires involve damaged, aftermarket or mismatched chargers and packs, not certified originals.",
          branch="battery chemistry", depends_on=["c1"], est_cost_gbp=0.06),
    Claim(id="c4", text="Once a lithium battery enters thermal runaway it cannot be extinguished by normal domestic means, and the vented vapour cloud can ignite in seconds.",
          branch="battery chemistry", depends_on=["c3"], est_cost_gbp=0.07),
    Claim(id="c5", text="UK regulation lags the risk: there is no mandatory third-party certification for e-bike conversion kits at the point of sale.",
          branch="regulation", depends_on=["c1"], est_cost_gbp=0.06),
    Claim(id="c6", text="Charging in hallways or exits puts the fire between you and the only escape route — the main factor in fatal e-bike fires.",
          branch="charging practice", depends_on=["c3"], est_cost_gbp=0.06),
    Claim(id="c7", text="Insurers increasingly exclude or void claims where an uncertified e-bike battery was charged indoors.",
          branch="insurance", depends_on=["c2"], est_cost_gbp=0.07),
    Claim(id="c8", text="E-bike batteries explode spontaneously even when they are not charging.",
          branch="scare stories", depends_on=["c7"], est_cost_gbp=0.05),
    Claim(id="c9", text="The safe-practice consensus: charge while awake and present, use the original charger, keep the battery away from exits, and stop at any sign of damage or heat.",
          branch="safe practice", depends_on=["c8"], est_cost_gbp=0.05),
]

def _ev(claim_id, stance, quote, url, label, conf=0.75, strength=0.5):
    return Evidence(claim_id=claim_id, stance=stance, quote=quote, url=url,
                    source_label=label, provenance=Provenance.GROUNDED,
                    confidence=conf, strength=strength)

# Per-claim canned findings: (advocate evidence, advocate confidence,
#                             skeptic evidence, skeptic max strength)
_FINDINGS: dict[str, dict] = {
    "c1": dict(
        adv=[_ev("c1", Stance.SUPPORT,
                 "we attended an e-bike or e-scooter fire roughly once every two days in 2023/24 — the capital's fastest-growing fire risk",
                 "https://www.london-fire.gov.uk/safety/the-home/e-scooters-and-e-bikes/",
                 "London Fire Brigade (2024), e-bike and e-scooter fire data", conf=0.92)],
        adv_conf=0.92,
        skp=[_ev("c1", Stance.REFUTE,
                 "year-on-year comparisons are muddied by better incident coding: some of the rise reflects how battery fires are now recorded, not only how many occur",
                 "https://www.bbc.co.uk/news/articles/e-bike-battery-fires-explained",
                 "BBC (2024), e-bike battery fires explained", strength=0.35)],
        skp_strength=0.35),
    "c2": dict(
        adv=[_ev("c2", Stance.SUPPORT,
                 "battery systems certified to EN 50604 or UL 2849 with a functioning battery-management system showed very low failure rates in our field data",
                 "https://www.gov.uk/government/publications/safety-study-e-bike-and-e-scooter-batteries",
                 "OPSS (2024), e-bike battery safety study", conf=0.78)],
        adv_conf=0.78,
        skp=[_ev("c2", Stance.REFUTE,
                 "never charge an e-bike battery overnight or while you are asleep, and never in a hallway or escape route — most of the fatal fires we have attended started while a battery was charging at night",
                 "https://www.london-fire.gov.uk/safety/the-home/e-scooters-and-e-bikes/",
                 "London Fire Brigade / NFCC (2024), #ChargeSafe guidance", strength=0.80),
             _ev("c2", Stance.REFUTE,
                 "never leave a charging e-bike battery unattended, whatever the brand on the label — certification lowers the odds, it does not remove the hazard",
                 "https://www.electricalsafetyfirst.org.uk/guidance/safety-around-the-home/e-bikes-and-e-scooters/",
                 "Electrical Safety First (2024), e-bike charging guidance", strength=0.62)],
        skp_strength=0.80),
    "c3": dict(
        adv=[_ev("c3", Stance.SUPPORT,
                 "the majority of incidents we traced involved damaged, aftermarket or mismatched chargers and packs rather than certified original equipment",
                 "https://www.gov.uk/government/publications/safety-study-e-bike-and-e-scooter-batteries",
                 "OPSS (2024), e-bike battery safety study", conf=0.88)],
        adv_conf=0.88,
        skp=[_ev("c3", Stance.REFUTE,
                 "fire investigators often cannot recover enough of the pack to say whether it was original or aftermarket, so attribution figures carry real uncertainty",
                 "https://www.which.co.uk/news/article/e-bike-battery-fires-what-you-need-to-know",
                 "Which? (2024), e-bike battery fire investigation", strength=0.30)],
        skp_strength=0.30),
    "c4": dict(
        adv=[_ev("c4", Stance.SUPPORT,
                 "once a cell enters thermal runaway, a domestic extinguisher will not stop it — the pack reignites as each neighbouring cell cooks off",
                 "https://www.which.co.uk/news/article/e-bike-battery-fires-what-you-need-to-know",
                 "Which? (2024), e-bike battery fire investigation", conf=0.90),
             _ev("c4", Stance.SUPPORT,
                 "the battery vents a flammable vapour cloud that can ignite within seconds, turning a smoulder into a room fire almost instantly",
                 "https://www.electricalsafetyfirst.org.uk/guidance/safety-around-the-home/e-bikes-and-e-scooters/",
                 "Electrical Safety First (2024), thermal runaway explainer", conf=0.90)],
        adv_conf=0.90,
        skp=[_ev("c4", Stance.REFUTE,
                 "a fire blanket and charging inside a battery-safe metal container can limit early-stage escalation, so 'nothing can be done' overstates the helplessness",
                 "https://nfcc.org.uk/e-bike-and-e-scooter-battery-safety/",
                 "NFCC (2024), containment guidance", strength=0.40)],
        skp_strength=0.40),
    "c5": dict(
        adv=[_ev("c5", Stance.SUPPORT,
                 "there is no mandatory third-party certification for e-bike conversion kits at the point of sale — a legal gap the government has been urged to close",
                 "https://www.which.co.uk/news/article/e-bike-conversion-kits-regulation-gap",
                 "Which? (2025), conversion-kit regulation investigation", conf=0.91)],
        adv_conf=0.91,
        skp=[_ev("c5", Stance.REFUTE,
                 "the Office for Product Safety and Standards has issued recalls, enforcement notices and a safety study, so 'unregulated' goes too far — the gap is at the point of sale, not across the board",
                 "https://www.gov.uk/government/publications/safety-study-e-bike-and-e-scooter-batteries",
                 "OPSS (2024), enforcement record", strength=0.45)],
        skp_strength=0.45),
    "c6": dict(
        adv=[_ev("c6", Stance.SUPPORT,
                 "in the fatal fires we have attended, the bike was almost always charging in the hallway or by the front door — the fire sat between the household and their only way out",
                 "https://www.london-fire.gov.uk/safety/the-home/e-scooters-and-e-bikes/",
                 "London Fire Brigade (2024), fatal fire case notes", conf=0.80)],
        adv_conf=0.80,
        skp=[_ev("c6", Stance.REFUTE,
                 "flats without storage have no realistic alternative to the hallway; guidance that assumes a garage or shed does not fit high-density housing",
                 "https://www.bbc.co.uk/news/articles/e-bike-battery-fires-explained",
                 "BBC (2024), housing and charging report", strength=0.42)],
        skp_strength=0.42),
    "c7": dict(
        adv=[_ev("c7", Stance.SUPPORT,
                 "several home insurers now exclude fires started by uncertified e-bike batteries, or void claims where a conversion-kit pack was charged indoors",
                 "https://www.which.co.uk/news/article/e-bike-battery-fires-what-you-need-to-know",
                 "Which? (2024), home insurance and battery fires", conf=0.74)],
        adv_conf=0.74,
        skp=[_ev("c7", Stance.REFUTE,
                 "exclusions vary widely by policy and several insurers have paid out regardless — 'your claim will be voided' is not a universal rule",
                 "https://www.bbc.co.uk/news/articles/e-bike-battery-fires-explained",
                 "BBC (2024), insurance payout survey", strength=0.48)],
        skp_strength=0.48),
    "c8": dict(
        adv=[_ev("c8", Stance.SUPPORT,
                 "e-bike batteries are ticking time bombs that can explode at any moment, even sitting unused in the shed",
                 "https://example-scarestory.com/ebike-batteries-time-bombs",
                 "Viral scare post (2024), unverified", conf=0.35)],
        adv_conf=0.35,
        skp=[_ev("c8", Stance.REFUTE,
                 "spontaneous failure of an undamaged battery at rest is rare — nearly all fires start during or shortly after charging, or after physical damage",
                 "https://www.bbc.co.uk/news/articles/e-bike-battery-fires-explained",
                 "BBC (2024), e-bike battery fires explained", strength=0.85)],
        skp_strength=0.85),
    "c9": dict(
        adv=[_ev("c9", Stance.SUPPORT,
                 "charge while you are awake and present, use the charger that came with the battery, keep it out of escape routes, and stop using it at any sign of damage, heat or swelling",
                 "https://www.electricalsafetyfirst.org.uk/guidance/safety-around-the-home/e-bikes-and-e-scooters/",
                 "Electrical Safety First (2024), safe-charging checklist", conf=0.86)],
        adv_conf=0.86,
        skp=[_ev("c9", Stance.REFUTE,
                 "the advice assumes people have somewhere else to charge; without secure storage, compliance in flats is unrealistically low",
                 "https://nfcc.org.uk/e-bike-and-e-scooter-battery-safety/",
                 "NFCC (2024), expert commentary", strength=0.33)],
        skp_strength=0.33),
}

_TICKER = {
    AgentRole.ADVOCATE: [
        'Searching: "{q}"…',
        "Reading top results · ranking primary sources…",
        "Extracting exact quote + URL…",
        "Scoring confidence…",
    ],
    AgentRole.SKEPTIC: [
        'Searching: "{q} fire OR overnight OR unattended"…',
        "Hunting counter-evidence · checking fire-service guidance…",
        "Strongest counter found · scoring strength…",
    ],
    AgentRole.TIEBREAKER: [
        "Re-checking under the human ruling…",
        'Searching: "{q} certified charger awake attended"…',
        "Weighing adjudicated caveat against dependent claim…",
    ],
}

_PLAN_DELTAS = [
    "Reading the question… the everyday worry about charging an e-bike battery indoors overnight. ",
    "Decomposing along five branches: fire-risk data, battery chemistry, charging practice, regulation, insurance. ",
    "Fire-risk data anchors the case: London Fire Brigade attended an e-bike or e-scooter battery fire roughly every two days in 2023/24. ",
    "Battery chemistry matters too: thermal runaway cannot be put out by normal means, and the vented vapour cloud ignites in seconds. ",
    "Flagging the headline safety claim ('a certified pack is safe to charge indoors') for adversarial review — fire-service guidance suggests it may not generalise to overnight, unattended charging. ",
    "Adding the certification picture as its own claim: most fires trace to damaged, aftermarket or mismatched chargers and packs, not certified originals. ",
    "Insurance branch depends on the indoor-charging claim: exclusion and voided-claim figures inherit its risk. ",
    "The scare-story branch ('explodes spontaneously even when not charging') looks weakly sourced and off-topic for a charging question — advocates may struggle. ",
    "Claim graph ready: 9 claims, 5 branches, ~18 agents, est £0.90. ",
]

_REPLAN_DELTAS = [
    "Ruling received: never charge overnight or unattended, and never in an escape route — the 'safe indoors' claim is narrowed, not overturned. ",
    "Updating the record: c2 is human-adjudicated; its dependents inherit the awake-and-present caveat. ",
    "The insurance-exposure claim was argued from the blanket 'safe indoors' line, and the safe-practice consensus (c9) frames the narrowed ruling — one re-check is needed. ",
    "Dispatching a single tiebreaker agent with the ruling as context, re-checking the safe-practice consensus against the adjudicated claim. ",
]

_SYNTH_CITATIONS = [
    Citation(id=1, url="https://www.london-fire.gov.uk/safety/the-home/e-scooters-and-e-bikes/",
             label="London Fire Brigade (2024), e-bike and e-scooter fire data",
             quote="an e-bike or e-scooter fire roughly once every two days in 2023/24",
             provenance=Provenance.GROUNDED),
    Citation(id=2, url="https://www.gov.uk/government/publications/safety-study-e-bike-and-e-scooter-batteries",
             label="OPSS (2024), e-bike battery safety study",
             quote="the majority of incidents involved damaged, aftermarket or mismatched chargers and packs rather than certified original equipment",
             provenance=Provenance.GROUNDED),
    Citation(id=3, url="https://www.gov.uk/government/publications/safety-study-e-bike-and-e-scooter-batteries",
             label="OPSS (2024), certified battery failure-rate data",
             quote="packs certified to EN 50604 or UL 2849 with a functioning battery-management system showed very low failure rates",
             provenance=Provenance.GROUNDED),
    Citation(id=4, url="https://www.which.co.uk/news/article/e-bike-battery-fires-what-you-need-to-know",
             label="Which? (2024), e-bike battery fire investigation",
             quote="once a cell enters thermal runaway, a domestic extinguisher will not stop it",
             provenance=Provenance.GROUNDED),
    Citation(id=5, url="https://www.electricalsafetyfirst.org.uk/guidance/safety-around-the-home/e-bikes-and-e-scooters/",
             label="Electrical Safety First (2024), thermal runaway explainer",
             quote="the battery vents a flammable vapour cloud that can ignite within seconds",
             provenance=Provenance.GROUNDED),
    Citation(id=6, url="https://www.which.co.uk/news/article/e-bike-conversion-kits-regulation-gap",
             label="Which? (2025), conversion-kit regulation investigation",
             quote="there is no mandatory third-party certification for e-bike conversion kits at the point of sale",
             provenance=Provenance.GROUNDED),
    Citation(id=7, url="https://www.bbc.co.uk/news/articles/e-bike-battery-fires-explained",
             label="BBC (2024), e-bike battery fires explained",
             quote="spontaneous failure at rest is rare — nearly all fires start during or shortly after charging",
             provenance=Provenance.GROUNDED),
    Citation(id=8, url="https://nfcc.org.uk/e-bike-and-e-scooter-battery-safety/",
             label="NFCC (2024), overnight charging guidance",
             quote="never charge overnight or while asleep — most fatal fires started while a battery was charging at night",
             provenance=Provenance.GROUNDED),
]


class MockModelClient:
    mode = "mock"

    def __init__(self, cfg: RunConfig):
        self.cfg = cfg

    async def _sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds / max(self.cfg.mock_speed, 0.01))

    def _jitter(self, role: AgentRole, claim_id: str, lo: float, hi: float) -> float:
        # Seeded per (role, claim): deterministic across runs.
        rng = random.Random(f"cassation-mock:{role.value}:{claim_id}")
        return rng.uniform(lo, hi)

    async def guardrail(self, question: str) -> GuardrailResult:
        await self._sleep(0.4)
        if not question.strip():
            return GuardrailResult(ok=False, reason="empty question")
        return GuardrailResult(ok=True, reason="in scope: consumer safety-claim verification")

    async def decompose(self, question: str, on_delta: OnDelta) -> ClaimGraph:
        for chunk in _PLAN_DELTAS:  # ~6s total at speed 1
            await self._sleep(6.0 / len(_PLAN_DELTAS))
            await on_delta(chunk)
        return ClaimGraph(question=question, claims=list(_CLAIMS),
                          est_total_cost_gbp=0.90, est_agents=18)

    async def investigate(self, claim: Claim, role: AgentRole, context: str,
                          on_delta: OnDelta) -> AgentFindings:
        total = self._jitter(role, claim.id, 4.0, 12.0)
        ticker = _TICKER.get(role, _TICKER[AgentRole.ADVOCATE])
        q = claim.text[:60]
        for line in ticker:
            await self._sleep(total / len(ticker))
            await on_delta(line.format(q=q))
        canned = _FINDINGS.get(claim.id)
        if role == AgentRole.SKEPTIC:
            evidence = list(canned["skp"])
            conf = 1.0 - canned["skp_strength"]
            summary = "Strongest counter-evidence: " + evidence[0].source_label
        elif role == AgentRole.TIEBREAKER:
            evidence = [_ev(claim.id, Stance.SUPPORT,
                            "insurers' exclusions track the fire-service consensus: cover holds where the battery is certified and charged awake-and-present with the original charger, away from exits — overnight unattended charging is what voids both safety and cover",
                            "https://www.electricalsafetyfirst.org.uk/guidance/safety-around-the-home/e-bikes-and-e-scooters/",
                            "Electrical Safety First (2025), insurance and safe-charging guidance",
                            conf=0.84)]
            conf = 0.84
            summary = "Holds, narrowed, under the human ruling: the safe-practice consensus (awake and present, original charger, away from exits) keeps both cover and safety; overnight unattended charging voids both."
        else:
            evidence = list(canned["adv"])
            conf = canned["adv_conf"]
            summary = "Supported by " + evidence[0].source_label
        return AgentFindings(
            agent_id=agent_id_for(role, claim.id),
            claim_id=claim.id, role=role,
            stance_summary=summary, evidence=evidence, confidence=conf)

    async def clerk_score(self, claim: Claim, support: list[Evidence],
                          counter: list[Evidence]) -> DisputeScore:
        await self._sleep(0.6)
        best_support = max(support, key=lambda e: e.confidence) if support else None
        best_counter = max(counter, key=lambda e: e.strength) if counter else None
        if claim.id == DISPUTED_CLAIM_ID:
            return DisputeScore(
                claim_id=claim.id, contested=True, strength=0.82,
                support_ev=best_support, counter_ev=best_counter,
                rationale=("Direct contradiction between primary sources: OPSS field data shows "
                           "certified EN 50604/UL packs with a BMS have very low failure rates, "
                           "but fire-service guidance says never charge overnight, unattended or "
                           "in an escape route — most fatal fires started while charging at "
                           "night. The blanket 'safe indoors' claim does not generalise."))
        strength = max((e.strength for e in counter), default=0.0) * 0.5
        return DisputeScore(
            claim_id=claim.id, contested=False, strength=round(strength, 2),
            support_ev=best_support, counter_ev=best_counter,
            rationale="Counter-evidence does not directly contradict the primary sources.")

    async def replan(self, ruling: JuryRuling, graph: ClaimGraph,
                     on_delta: OnDelta) -> str:
        for chunk in _REPLAN_DELTAS:
            await self._sleep(3.0 / len(_REPLAN_DELTAS))
            await on_delta(chunk)
        return ("Human ruling on " + ruling.claim_id + " (option '" + ruling.choice_key +
                "') accepted; dependents re-checked against the adjudicated value.")

    async def synthesize(self, dossier: Dossier, on_delta: OnDelta) -> Verdict:
        active = {c.id for c in dossier.graph.claims}
        ruled = dossier.ruling.claim_id if dossier.ruling else None
        contested = {d.claim_id for d in dossier.disputes if d.contested}
        ok_claims = {f.claim_id for f in dossier.findings if f.status == "ok"}

        chunks = self._brief_chunks(ruled, active)
        for chunk in chunks:  # ~10s total at speed 1
            await self._sleep(10.0 / len(chunks))
            await on_delta(chunk)

        verdicts: list[ClaimVerdict] = []
        for c in dossier.graph.claims:
            if c.id not in ok_claims:
                verdicts.append(ClaimVerdict(claim_id=c.id, verdict="EXCLUDED", confidence=0.0,
                                             summary="No agent findings returned (timeout/error)."))
            elif c.id == ruled:
                verdicts.append(ClaimVerdict(
                    claim_id=c.id, verdict="HUMAN_ADJUDICATED", confidence=0.9,
                    citation_ids=[3, 8],
                    summary="Jury ruled for the fire-service guidance: a certified pack may be charged indoors only awake-and-present — never overnight, unattended or in an escape route — narrowing the blanket 'safe indoors' claim."))
            elif c.id in contested:
                verdicts.append(ClaimVerdict(claim_id=c.id, verdict="CONTESTED", confidence=0.5,
                                             citation_ids=[3, 8],
                                             summary="Sources directly disagree; no human ruling recorded."))
            elif c.id == "c8" and "c8" in active:
                verdicts.append(ClaimVerdict(
                    claim_id=c.id, verdict="REFUTED", confidence=0.85, citation_ids=[7],
                    summary="'Explodes spontaneously' is a scare overclaim: spontaneous failure at rest is rare — nearly all fires start during or just after charging."))
            else:
                cites = {"c1": [1, 6], "c3": [2], "c4": [4, 5], "c5": [6],
                         "c6": [2, 8], "c7": [3, 8], "c9": [7]}.get(c.id, [])
                verdicts.append(ClaimVerdict(claim_id=c.id, verdict="SUPPORTED",
                                             confidence=0.85, citation_ids=cites,
                                             summary="Primary sources agree; skeptic challenge did not hold."))
        return Verdict(brief_markdown="".join(chunks), verdicts=verdicts,
                       citations=list(_SYNTH_CITATIONS))

    def _brief_chunks(self, ruled: str | None, active: set[str]) -> list[str]:
        """The brief narrative, built only from ACTIVE claims: a struck claim's
        content (not just its id) must be absent — the anti-canned proof the
        demo leans on (plan beat 2:05, Q&A #3/#6)."""
        ruling_line = (
            "A human juror ruled for the fire-service never-overnight guidance [8] over the blanket "
            "certification case [3]; that claim is stamped **human-adjudicated** and its dependents were re-checked.\n\n"
            if ruled else
            "No claim required human adjudication on this run.\n\n")

        chunks = [
            "# Research Verdict — charging an e-bike battery indoors overnight\n\n",
            "**Question:** is it safe to charge an e-bike battery indoors overnight?\n\n",
            "## The short answer\n\n",
            "With the original, certified kit, **yes — with conditions**: certified packs with a "
            "working BMS have very low failure rates [3]. But **never overnight or unattended, and "
            "never in an escape route**: most fatal fires started while a battery was charging at "
            "night [8]. 'Safe indoors' depends on how — and when — you charge.\n\n",
        ]
        if "c1" in active:
            risk = "The risk is real and rising: London Fire Brigade attended an e-bike or e-scooter battery fire roughly every two days in 2023/24 [1]."
            if "c5" in active:
                risk += (" And regulation lags: there is no mandatory third-party certification for "
                         "conversion kits at the point of sale [6], so the pack on your landing may "
                         "never have been independently tested.")
            chunks.append(risk + "\n\n")
        chunks.append("## What the fire data shows\n\n")
        history = ""
        if "c3" in active:
            history = "Most fires trace to damaged, aftermarket or mismatched chargers and packs, not certified originals [2]"
            if "c4" in active:
                history += "; once thermal runaway starts, no domestic means will stop it, and the vented vapour cloud ignites in seconds [4][5]"
            history += "."
        elif "c4" in active:
            history = "Once thermal runaway starts, no domestic means will stop it, and the vented vapour cloud ignites in seconds [4][5]."
        if "c6" in active:
            history += " Location is what kills: charging in a hallway or exit puts the fire between you and the only way out [2]."
        history += " Where and when you charge is what flips the risk past the headline statistics [8].\n\n"
        chunks.append(history.lstrip())
        chunks.extend([
            "## Adjudication\n\n",
            ruling_line,
            "## Cover and practice implications\n\n",
        ])
        spend = ("Under the adjudicated narrowing [8], the certified-kit case stands for attended, "
                 "awake-and-present charging [3]; insurers increasingly exclude or void claims where "
                 "an uncertified pack was charged indoors, so the paperwork follows the practice"
                 if ruled else
                 "Certified packs charged attended carry low risk [3], but insurers increasingly exclude or void claims where an uncertified pack was charged indoors")
        if "c8" in active:
            spend += " — and the viral 'explodes spontaneously at rest' line does **not** hold [7]"
        if "c9" in active:
            spend += "; the consensus is charge awake and present, original charger, away from exits, and stop at any sign of damage or heat [7]"
        chunks.append(spend + ".\n")
        return chunks
