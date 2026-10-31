"""MockModelClient: canned-but-plausible air-fryer running-cost run.

Drives the whole system without a Gemini key. Content mirrors the everyday
"is an air fryer really cheaper to run than an electric oven?" question
(per-use energy, preheat times, the Which? 2024 small-portion testing, the
batch-size caveat where a full oven wins per portion, 2025-cap electricity
prices) so the demo rehearses with realistic claims, quotes and URLs. Source
labels and URLs are plausible, clearly-labelled mock citations (Which?,
Energy Saving Trust, Uswitch, BBC).

Deterministic: fixed per-(role, claim) jitter seeds, fixed content, so e2e
asserts are stable — the milestone sequence (everything except agent.delta
interleaving), each agent's own event stream, and the verdict payload are
identical across runs. Only the global interleaving of ticker deltas from
CONCURRENT agents can swap when two wakeups collide at ms resolution, so
assert on milestones, not on raw cross-agent delta order. All latencies
divide by cfg.mock_speed. Guaranteed dispute: claim c2 — the per-use
headline (~10p vs ~35p, Which? 2024) vs the batch-cooking counter (for
large family batches the oven is cheaper PER PORTION): the blanket
"cheaper" claim does not generalize.
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

DISPUTED_CLAIM_ID = "c2"  # the per-use cost headline; always contested

_CLAIMS = [
    Claim(id="c1", text="A typical air fryer uses ~0.4-0.5 kWh per use, versus ~1.0-1.5 kWh for a full-size electric oven cooking a comparable meal.",
          branch="energy per use", depends_on=[], est_cost_gbp=0.06),
    Claim(id="c2", text="Running an air fryer costs about 10p per typical use, versus about 35p for an electric oven at 2025 UK electricity prices.",
          branch="running cost", depends_on=["c1"], est_cost_gbp=0.08),
    Claim(id="c3", text="Air fryers preheat in 2-3 minutes versus 10-15 minutes for an electric oven, cutting total cooking time for most dishes.",
          branch="cooking time", depends_on=["c1"], est_cost_gbp=0.06),
    Claim(id="c4", text="Which? 2024 testing found air fryers cheaper than an electric oven for small portions such as chips, chicken and roast vegetables.",
          branch="cooking time", depends_on=["c3"], est_cost_gbp=0.07),
    Claim(id="c5", text="Typical UK electricity costs about 24.5p per kWh under the 2025 price cap, the unit rate behind both appliance estimates.",
          branch="running cost", depends_on=["c1"], est_cost_gbp=0.06),
    Claim(id="c6", text="An air fryer basket holds roughly 3-6 litres, so family-sized meals need several rounds where one oven cycle cooks everything at once.",
          branch="batch size", depends_on=["c3"], est_cost_gbp=0.06),
    Claim(id="c7", text="A household swapping four oven sessions a week for the air fryer saves roughly £50 a year on electricity.",
          branch="savings", depends_on=["c2"], est_cost_gbp=0.07),
    Claim(id="c8", text="Air fryers are always healthier than cooking the same food in an oven.",
          branch="claims in ads", depends_on=["c7"], est_cost_gbp=0.05),
    Claim(id="c9", text="The 'healthier' marketing line rests on using less oil than deep frying — it says nothing about running cost.",
          branch="claims in ads", depends_on=["c8"], est_cost_gbp=0.05),
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
                 "a typical air fryer uses around 0.4 to 0.5 kWh per use, against 1.0 to 1.5 kWh for a full-size electric oven cooking a comparable meal",
                 "https://energysavingtrust.org.uk/advice/home-appliances/",
                 "Energy Saving Trust (2025), cooking appliance running costs", conf=0.92)],
        adv_conf=0.92,
        skp=[_ev("c1", Stance.REFUTE,
                 "per-use figures vary widely with the dish: a quick oven bake can use under 1 kWh while a long air-fryer roast creeps upward",
                 "https://www.uswitch.com/energy/guides/air-fryer-vs-oven/",
                 "Uswitch (2024), air fryer vs oven energy guide", strength=0.35)],
        skp_strength=0.35),
    "c2": dict(
        adv=[_ev("c2", Stance.SUPPORT,
                 "our testing puts a typical air fryer session at around 10p, against roughly 35p for the same meal in an electric oven",
                 "https://www.which.co.uk/news/article/air-fryer-vs-oven-which-is-cheaper-to-run",
                 "Which? (2024), air fryer vs oven running costs", conf=0.78)],
        adv_conf=0.78,
        skp=[_ev("c2", Stance.REFUTE,
                 "cooking a family-sized batch took three air-fryer rounds and used more electricity than a single oven cycle — per portion, the oven won",
                 "https://www.which.co.uk/news/article/air-fryer-batch-cooking-test",
                 "Which? (2024), batch-cooking follow-up test", strength=0.80),
             _ev("c2", Stance.REFUTE,
                 "for larger quantities an oven can work out more economical per portion, since one cycle cooks what a basket needs several rounds to finish",
                 "https://energysavingtrust.org.uk/advice/home-appliances/",
                 "Energy Saving Trust (2025), portion-size guidance", strength=0.62)],
        skp_strength=0.80),
    "c3": dict(
        adv=[_ev("c3", Stance.SUPPORT,
                 "the air fryer was at temperature in under three minutes; the full-size electric oven took over twelve",
                 "https://www.bbc.co.uk/food/articles/air_fryer_vs_oven",
                 "BBC Food (2024), air fryer vs oven test", conf=0.88)],
        adv_conf=0.88,
        skp=[_ev("c3", Stance.REFUTE,
                 "modern fan ovens with rapid-preheat modes reach temperature in 5-6 minutes, narrowing the preheat gap",
                 "https://www.uswitch.com/energy/guides/air-fryer-vs-oven/",
                 "Uswitch (2024), appliance preheat comparison", strength=0.30)],
        skp_strength=0.30),
    "c4": dict(
        adv=[_ev("c4", Stance.SUPPORT,
                 "for small portions — chips, chicken pieces, roast vegetables — the air fryer was cheaper than the electric oven every time we tested",
                 "https://www.which.co.uk/reviews/air-fryers/article/air-fryer-testing-2024",
                 "Which? (2024), air fryer lab testing", conf=0.90),
             _ev("c4", Stance.SUPPORT,
                 "our measurements agree: for single-dish meals the air fryer used less than half the electricity of a full-size oven",
                 "https://www.uswitch.com/energy/guides/air-fryer-vs-oven/",
                 "Uswitch (2024), air fryer vs oven energy guide", conf=0.90)],
        adv_conf=0.90,
        skp=[_ev("c4", Stance.REFUTE,
                 "measured savings ranged from 5p to 25p per dish depending on the recipe, so quoting one headline number overstates the agreement",
                 "https://www.which.co.uk/news/article/air-fryer-vs-oven-which-is-cheaper-to-run",
                 "Which? (2024), methods notes", strength=0.40)],
        skp_strength=0.40),
    "c5": dict(
        adv=[_ev("c5", Stance.SUPPORT,
                 "under the 2025 price cap, typical UK electricity costs about 24.5p per kilowatt-hour",
                 "https://www.uswitch.com/energy/guides/energy-price-cap/",
                 "Uswitch (2025), energy price cap explained", conf=0.91)],
        adv_conf=0.91,
        skp=[_ev("c5", Stance.REFUTE,
                 "standing charges, regional variation and off-peak tariffs mean the effective unit rate differs by household; 24.5p is an average, not a universal price",
                 "https://energysavingtrust.org.uk/advice/home-appliances/",
                 "Energy Saving Trust (2025), tariff guidance", strength=0.45)],
        skp_strength=0.45),
    "c6": dict(
        adv=[_ev("c6", Stance.SUPPORT,
                 "the 4.5-litre basket needed two rounds to feed a family of four, while the oven cooked everything at once on two shelves",
                 "https://www.bbc.co.uk/food/articles/air_fryer_vs_oven",
                 "BBC Food (2024), family meal test", conf=0.80)],
        adv_conf=0.80,
        skp=[_ev("c6", Stance.REFUTE,
                 "dual-drawer and 9-litre models close much of the batch gap, so basket size is a shrinking constraint",
                 "https://www.which.co.uk/reviews/air-fryers/article/air-fryer-testing-2024",
                 "Which? (2024), large-capacity model reviews", strength=0.42)],
        skp_strength=0.42),
    "c7": dict(
        adv=[_ev("c7", Stance.SUPPORT,
                 "a household swapping four oven sessions a week for the air fryer saves roughly £50 a year at current electricity prices",
                 "https://energysavingtrust.org.uk/advice/home-appliances/",
                 "Energy Saving Trust (2025), cooking costs guidance", conf=0.74)],
        adv_conf=0.74,
        skp=[_ev("c7", Stance.REFUTE,
                 "the £50 figure assumes every oven session is replaceable; with mixed cooking the realistic saving is nearer £30 a year",
                 "https://www.uswitch.com/energy/guides/air-fryer-vs-oven/",
                 "Uswitch (2024), savings assumptions", strength=0.48)],
        skp_strength=0.48),
    "c8": dict(
        adv=[_ev("c8", Stance.SUPPORT,
                 "the air fryer is the healthier choice for every meal, every time",
                 "https://example-advertorial.com/air-fryer-always-healthier",
                 "Advertorial (2024), unverified", conf=0.35)],
        adv_conf=0.35,
        skp=[_ev("c8", Stance.REFUTE,
                 "the health benefit comes from using less oil, not from the appliance itself — air-fried food is not automatically healthy",
                 "https://www.bbc.co.uk/news/articles/air-fryer-health-claims",
                 "BBC (2024), air fryer health claims examined", strength=0.85)],
        skp_strength=0.85),
    "c9": dict(
        adv=[_ev("c9", Stance.SUPPORT,
                 "air frying uses a fraction of the oil of deep frying, which is where the lower-fat claim comes from — it is a claim about oil, not electricity",
                 "https://www.bbc.co.uk/news/articles/air-fryer-health-claims",
                 "BBC (2024), air fryer health claims examined", conf=0.86)],
        adv_conf=0.86,
        skp=[_ev("c9", Stance.REFUTE,
                 "less oil is not the whole health story — browning high-starch foods still forms acrylamide whichever appliance you use",
                 "https://www.bbc.co.uk/news/articles/air-fryer-health-claims",
                 "BBC (2024), expert commentary", strength=0.33)],
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
        'Searching: "{q} contradiction OR batch OR per portion"…',
        "Hunting counter-evidence · checking batch-cooking tests…",
        "Strongest counter found · scoring strength…",
    ],
    AgentRole.TIEBREAKER: [
        "Re-checking under the human ruling…",
        'Searching: "{q} per portion large batch"…',
        "Weighing adjudicated caveat against dependent claim…",
    ],
}

_PLAN_DELTAS = [
    "Reading the question… the everyday air-fryer vs electric-oven running-cost debate. ",
    "Decomposing along five branches: running cost, cooking time, batch size, savings, advertising claims. ",
    "Per-use energy anchors the cost branch: ~0.4-0.5 kWh for an air fryer vs ~1.0-1.5 kWh for an oven. ",
    "Cooking time matters too: 2-3 minute preheats vs 10-15 for a full oven. ",
    "Flagging the headline cost claim (~10p vs ~35p per use) for adversarial review — batch-cooking tests suggest it may not generalise. ",
    "Adding the 2025 electricity price cap (~24.5p/kWh) as its own claim: every cost figure depends on it. ",
    "Savings branch depends on the per-use figure: the £50-a-year estimate inherits its risk. ",
    "The advertising branch ('always healthier') looks weakly sourced and off-topic for a cost question — advocates may struggle. ",
    "Claim graph ready: 9 claims, 5 branches, ~18 agents, est £0.90. ",
]

_REPLAN_DELTAS = [
    "Ruling received: for large family batches the oven can be cheaper per portion — the blanket 'air fryer is cheaper' claim is narrowed, not overturned. ",
    "Updating the record: c2 is human-adjudicated; its dependents inherit the per-portion caveat. ",
    "The £50-a-year savings claim (c7) was argued from the blanket per-use figure — it needs one re-check under the caveat. ",
    "Dispatching a single tiebreaker agent on c7 with the ruling as context. ",
]

_SYNTH_CITATIONS = [
    Citation(id=1, url="https://energysavingtrust.org.uk/advice/home-appliances/",
             label="Energy Saving Trust (2025), cooking appliance running costs",
             quote="around 0.4 to 0.5 kWh per use, against 1.0 to 1.5 kWh for a full-size electric oven",
             provenance=Provenance.GROUNDED),
    Citation(id=2, url="https://www.bbc.co.uk/food/articles/air_fryer_vs_oven",
             label="BBC Food (2024), air fryer vs oven test",
             quote="at temperature in under three minutes; the full-size electric oven took over twelve",
             provenance=Provenance.GROUNDED),
    Citation(id=3, url="https://www.which.co.uk/news/article/air-fryer-vs-oven-which-is-cheaper-to-run",
             label="Which? (2024), air fryer vs oven running costs",
             quote="around 10p per air-fryer session, against roughly 35p for the same meal in an electric oven",
             provenance=Provenance.GROUNDED),
    Citation(id=4, url="https://www.which.co.uk/reviews/air-fryers/article/air-fryer-testing-2024",
             label="Which? (2024), air fryer lab testing",
             quote="for small portions the air fryer was cheaper than the electric oven every time we tested",
             provenance=Provenance.GROUNDED),
    Citation(id=5, url="https://www.uswitch.com/energy/guides/air-fryer-vs-oven/",
             label="Uswitch (2024), air fryer vs oven energy guide",
             quote="for single-dish meals the air fryer used less than half the electricity of a full-size oven",
             provenance=Provenance.GROUNDED),
    Citation(id=6, url="https://www.uswitch.com/energy/guides/energy-price-cap/",
             label="Uswitch (2025), energy price cap explained",
             quote="typical UK electricity costs about 24.5p per kilowatt-hour under the 2025 price cap",
             provenance=Provenance.GROUNDED),
    Citation(id=7, url="https://www.bbc.co.uk/news/articles/air-fryer-health-claims",
             label="BBC (2024), air fryer health claims examined",
             quote="the health benefit comes from using less oil, not from the appliance itself",
             provenance=Provenance.GROUNDED),
    Citation(id=8, url="https://www.which.co.uk/news/article/air-fryer-batch-cooking-test",
             label="Which? (2024), batch-cooking follow-up test",
             quote="a family-sized batch took three air-fryer rounds and used more electricity than a single oven cycle",
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
        return GuardrailResult(ok=True, reason="in scope: consumer cost-claim verification")

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
                            "for a typical small-portion household the saving of roughly £50 a year stands; batch-cooking families should expect less, since per portion a full oven can win",
                            "https://energysavingtrust.org.uk/advice/home-appliances/",
                            "Energy Saving Trust (2025), cooking costs guidance",
                            conf=0.84)]
            conf = 0.84
            summary = "Holds, narrowed, under the human ruling: the yearly saving stands for small-batch use; large-batch households should expect less."
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
                rationale=("Direct contradiction between primary sources: Which? per-use testing "
                           "puts the air fryer at ~10p vs ~35p for the oven, but its own "
                           "batch-cooking test found a family-sized batch cheaper PER PORTION in "
                           "the oven — the blanket 'cheaper' claim does not generalise."))
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
                    summary="Jury ruled for the per-portion caveat: for large family batches the oven can be cheaper per portion, narrowing the blanket per-use claim."))
            elif c.id in contested:
                verdicts.append(ClaimVerdict(claim_id=c.id, verdict="CONTESTED", confidence=0.5,
                                             citation_ids=[3, 8],
                                             summary="Sources directly disagree; no human ruling recorded."))
            elif c.id == "c8" and "c8" in active:
                verdicts.append(ClaimVerdict(
                    claim_id=c.id, verdict="REFUTED", confidence=0.85, citation_ids=[7],
                    summary="'Always healthier' is an advertising overclaim: the benefit comes from using less oil, not the appliance."))
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
            "A human juror ruled for the per-portion batch caveat [8] over the blanket per-use "
            "figure [3]; that claim is stamped **human-adjudicated** and its dependents were re-checked.\n\n"
            if ruled else
            "No claim required human adjudication on this run.\n\n")

        chunks = [
            "# Research Verdict — air fryer vs electric oven running cost\n\n",
            "**Question:** is an air fryer really cheaper to run than an electric oven?\n\n",
            "## The short answer\n\n",
            "For typical single-batch cooking, **yes**: about **10p per use** against **~35p** for "
            "the oven [3]. But the claim does not generalise: batch-cooking tests found that for "
            "large family batches a full oven can be **cheaper per portion** [8]. 'Cheaper' depends "
            "on how much you cook at once.\n\n",
        ]
        if "c1" in active:
            energy = "The energy gap drives the cost gap: ~0.4-0.5 kWh per air-fryer use against ~1.0-1.5 kWh for the oven [1]."
            if "c5" in active:
                energy += (" At the 2025 price-cap rate of ~24.5p/kWh [6], that is the difference "
                           "between pence and tens of pence every time you cook.")
            chunks.append(energy + "\n\n")
        chunks.append("## What testing shows\n\n")
        history = ""
        if "c3" in active:
            history = "Air fryers preheat in 2-3 minutes against 10-15 for a full oven [2]"
            if "c4" in active:
                history += "; Which?'s 2024 lab testing then found them cheaper for every small-portion dish tried [4][5]"
            history += "."
        elif "c4" in active:
            history = "Which?'s 2024 lab testing found air fryers cheaper for every small-portion dish tried [4][5]."
        if "c6" in active:
            history += " The limit is the basket: family-sized meals need several air-fryer rounds where one oven cycle cooks the lot [2]."
        history += " Batch size is what flips the per-portion result past the headline numbers [8].\n\n"
        chunks.append(history.lstrip())
        chunks.extend([
            "## Adjudication\n\n",
            ruling_line,
            "## Spend implications\n\n",
        ])
        spend = ("Under the adjudicated per-portion caveat [8], a typical small-batch household "
                 "still saves — roughly £50 a year swapping four oven sessions a week [3]; "
                 "batch-cooking families should budget for less"
                 if ruled else
                 "At ~10p vs ~35p per use [3], a typical household saves roughly £50 a year")
        if "c8" in active:
            spend += " — but the adverts' blanket 'always healthier' line does **not** hold [7]"
        if "c9" in active:
            spend += "; the health point is about oil, not electricity [7]"
        chunks.append(spend + ".\n")
        return chunks
