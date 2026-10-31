"""MockModelClient: canned-but-plausible Aβ*56 / Alzheimer's retraction run.

Drives the whole system without a Gemini key. Content mirrors the question
"does the amyloid-beta assembly Aβ*56 cause memory loss in Alzheimer's
disease?" (the Lesné et al. Nature 2006 paper, Matthew Schrag's 2022
image-forensics findings reported by Science, the June 2024 Nature
retraction, the repeated failures to detect Aβ*56 in human tissue, the
zombie-citation problem) so the demo rehearses with realistic claims, quotes
and URLs. Source labels and URLs are plausible, clearly-labelled mock
citations (Nature, Science, Alzforum, PubPeer, Vanderbilt).

Deterministic: fixed per-(role, claim) jitter seeds, fixed content, so e2e
asserts are stable — the milestone sequence (everything except agent.delta
interleaving), each agent's own event stream, and the verdict payload are
identical across runs. Only the global interleaving of ticker deltas from
CONCURRENT agents can swap when two wakeups collide at ms resolution, so
assert on milestones, not on raw cross-agent delta order. All latencies
divide by cfg.mock_speed. Guaranteed dispute: claim c2 — the published
record (landmark Nature 2006 paper, thousands of citations, follow-on
amyloid work) vs the retraction counter (Nature retracted the paper in June
2024 after Schrag's image-forensics findings; all authors agreed except
Lesné): the "evidence still stands" claim is OVERTURNED by the retraction.
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

DISPUTED_CLAIM_ID = "c2"  # the "2006 evidence still stands" headline; always contested

_CLAIMS = [
    Claim(id="c1", text="Lesné et al. (Nature, 2006) reported that the amyloid-beta assembly Aβ*56 impairs memory in rats — among the most-cited Alzheimer's papers of the century.",
          branch="published record", depends_on=[], est_cost_gbp=0.06),
    Claim(id="c2", text="The 2006 Nature evidence that Aβ*56 causes memory deficits still stands.",
          branch="evidence status", depends_on=["c1"], est_cost_gbp=0.08),
    Claim(id="c3", text="Matthew Schrag's forensic analysis found manipulated western-blot images in the 2006 paper and roughly 20 related papers.",
          branch="image forensics", depends_on=["c1"], est_cost_gbp=0.06),
    Claim(id="c4", text="Independent laboratories have repeatedly failed to detect Aβ*56 in human brain tissue at all.",
          branch="replication", depends_on=["c3"], est_cost_gbp=0.07),
    Claim(id="c5", text="The broader amyloid hypothesis does not rest on Aβ*56 alone: the approved anti-amyloid antibodies target other amyloid-beta species.",
          branch="amyloid hypothesis", depends_on=["c1"], est_cost_gbp=0.06),
    Claim(id="c6", text="The error steered the field for 18 years; the four additional retractions requested by Lesné's university were alone cited roughly 600 times.",
          branch="cost of the error", depends_on=["c3"], est_cost_gbp=0.06),
    Claim(id="c7", text="Zombie citations persist: papers keep citing retracted work because authors rarely check retraction status.",
          branch="citation practice", depends_on=["c2"], est_cost_gbp=0.07),
    Claim(id="c8", text="The retraction disproves the entire amyloid hypothesis.",
          branch="overclaims", depends_on=["c7"], est_cost_gbp=0.05),
    Claim(id="c9", text="The safe-practice consensus: before funding work built on a landmark result, verify its retraction and correction status and look for independent replication.",
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
                 "a 56-kDa soluble amyloid-beta assembly, Aβ*56, impairs memory independently of plaques or neuronal loss",
                 "https://www.nature.com/articles/nature04533",
                 "Lesné et al. (2006), Nature — the Aβ*56 paper", conf=0.92)],
        adv_conf=0.92,
        skp=[_ev("c1", Stance.REFUTE,
                 "citation counts measure influence, not truth — the paper's rank among the century's most-cited Alzheimer's papers says nothing about whether the reported bands were real",
                 "https://www.alzforum.org/news/community-news/abeta56-citation-record",
                 "Alzforum (2022), citation-record analysis", strength=0.35)],
        skp_strength=0.35),
    "c2": dict(
        adv=[_ev("c2", Stance.SUPPORT,
                 "the published record stood for sixteen years: a landmark Nature paper, thousands of citations, and a drug pipeline of follow-on amyloid-oligomer work built on Aβ*56",
                 "https://www.nature.com/articles/nature04533",
                 "Nature (2006) / Web of Science citation record", conf=0.78)],
        adv_conf=0.78,
        skp=[_ev("c2", Stance.REFUTE,
                 "the authors retract this article owing to concerns regarding some of the figures — all authors agree with the retraction except S. Lesné",
                 "https://www.nature.com/articles/s41586-024-07691-8",
                 "Nature (June 2024), retraction notice", strength=0.80),
             _ev("c2", Stance.REFUTE,
                 "a six-month investigation found evidence that the images may have been improperly manipulated, raising doubts about a key theory of Alzheimer's disease",
                 "https://www.science.org/content/article/potential-fabrication-research-images-threatens-key-theory-alzheimers-disease",
                 "Science (2022), image-forensics investigation", strength=0.62)],
        skp_strength=0.80),
    "c3": dict(
        adv=[_ev("c3", Stance.SUPPORT,
                 "Schrag, working on his own time, identified apparently duplicated and spliced western-blot bands across roughly 20 papers from the same group",
                 "https://www.science.org/content/article/potential-fabrication-research-images-threatens-key-theory-alzheimers-disease",
                 "Science (2022), image-forensics investigation", conf=0.88)],
        adv_conf=0.88,
        skp=[_ev("c3", Stance.REFUTE,
                 "image anomalies alone cannot prove intent — band splicing was once common practice, and some flagged figures could have innocent explanations",
                 "https://pubpeer.com/search?q=lesne",
                 "PubPeer (2022), figure-concern threads", strength=0.30)],
        skp_strength=0.30),
    "c4": dict(
        adv=[_ev("c4", Stance.SUPPORT,
                 "multiple independent laboratories report they could not detect Aβ*56 in human brain tissue or cerebrospinal fluid",
                 "https://www.alzforum.org/news/research-news/abeta56-replication-failures",
                 "Alzforum (2022), replication roundup", conf=0.90),
             _ev("c4", Stance.SUPPORT,
                 "outside the original group, Aβ*56 has never been convincingly isolated from human samples — several groups doubt it exists as a stable species at all",
                 "https://www.science.org/content/article/potential-fabrication-research-images-threatens-key-theory-alzheimers-disease",
                 "Science (2022), replication sidebar", conf=0.90)],
        adv_conf=0.90,
        skp=[_ev("c4", Stance.REFUTE,
                 "detection is assay-dependent: soluble oligomers are notoriously unstable, so a failure to find Aβ*56 is not proof it never forms",
                 "https://www.alzforum.org/news/research-news/abeta56-methods-commentary",
                 "Alzforum (2022), methods commentary", strength=0.40)],
        skp_strength=0.40),
    "c5": dict(
        adv=[_ev("c5", Stance.SUPPORT,
                 "lecanemab and donanemab were developed against other amyloid-beta species — protofibrils and pyroglutamate plaque forms — not Aβ*56",
                 "https://www.alzforum.org/news/community-news/lesne-retraction-and-amyloid-hypothesis",
                 "Alzforum (2024), what the retraction means for the amyloid hypothesis", conf=0.91)],
        adv_conf=0.91,
        skp=[_ev("c5", Stance.REFUTE,
                 "the retraction still bruises the hypothesis it propped up: funding and trials were steered toward toxic-oligomer work partly on the strength of Aβ*56",
                 "https://www.science.org/content/article/potential-fabrication-research-images-threatens-key-theory-alzheimers-disease",
                 "Science (2022), field-impact analysis", strength=0.45)],
        skp_strength=0.45),
    "c6": dict(
        adv=[_ev("c6", Stance.SUPPORT,
                 "after its own inquiry, Lesné's university requested retraction of four more of his papers — works cited together roughly 600 times — and Lesné resigned",
                 "https://www.science.org/content/article/lesne-resigns-university-requests-four-retractions",
                 "Science (2024), follow-up reporting", conf=0.80)],
        adv_conf=0.80,
        skp=[_ev("c6", Stance.REFUTE,
                 "18 years of field-steering cannot be pinned on a single paper: amyloid dominated funding before 2006, and other oligomer results pulled in the same direction",
                 "https://www.alzforum.org/news/community-news/lesne-retraction-and-amyloid-hypothesis",
                 "Alzforum (2024), field commentary", strength=0.42)],
        skp_strength=0.42),
    "c7": dict(
        adv=[_ev("c7", Stance.SUPPORT,
                 "most citations of retracted papers are made after the retraction, and the vast majority do not acknowledge the retraction at all",
                 "https://pubpeer.com/publications/lesne-2006-nature",
                 "PubPeer (2024), post-retraction citation tracking", conf=0.74)],
        adv_conf=0.74,
        skp=[_ev("c7", Stance.REFUTE,
                 "citation databases now flag retracted items prominently and journals increasingly run reference checks — the zombie-citation rate is falling",
                 "https://www.nature.com/articles/retracted-paper-citation-hygiene",
                 "Nature (2024), citation-hygiene editorial", strength=0.48)],
        skp_strength=0.48),
    "c8": dict(
        adv=[_ev("c8", Stance.SUPPORT,
                 "Alzheimer's research is built on a lie — the retraction proves the whole amyloid theory was a fraud all along",
                 "https://example-hottake.net/amyloid-house-of-cards",
                 "Viral commentary thread (2024), unverified", conf=0.35)],
        adv_conf=0.35,
        skp=[_ev("c8", Stance.REFUTE,
                 "the retraction removes one pillar, not the edifice: the approved anti-amyloid antibodies target other amyloid-beta species, and the genetic evidence for amyloid's role predates Aβ*56 entirely",
                 "https://www.alzforum.org/news/community-news/lesne-retraction-expert-commentary",
                 "Alzforum (2024), expert commentary", strength=0.85)],
        skp_strength=0.85),
    "c9": dict(
        adv=[_ev("c9", Stance.SUPPORT,
                 "before funding or building on a landmark result, check its retraction and correction status, look for independent replication outside the original group, and scan PubPeer for unresolved figure concerns",
                 "https://news.vanderbilt.edu/research-integrity-image-forensics-guidance",
                 "Vanderbilt (2023), research-integrity guidance (Schrag)", conf=0.86)],
        adv_conf=0.86,
        skp=[_ev("c9", Stance.REFUTE,
                 "manual image forensics took months of one neuroscientist's own time; expecting every grant panel to repeat that is unrealistic without tooling",
                 "https://www.science.org/content/article/potential-fabrication-research-images-threatens-key-theory-alzheimers-disease",
                 "Science (2022), investigation epilogue", strength=0.33)],
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
        'Searching: "{q} retraction OR manipulation OR replication"…',
        "Hunting counter-evidence · checking retraction notices…",
        "Strongest counter found · scoring strength…",
    ],
    AgentRole.TIEBREAKER: [
        "Re-checking under the human ruling…",
        'Searching: "{q} retracted 2024 citation status"…',
        "Weighing the adjudicated retraction against the dependent claim…",
    ],
}

_PLAN_DELTAS = [
    "Reading the question… whether the amyloid-beta assembly Aβ*56 causes memory loss in Alzheimer's disease. ",
    "Decomposing along five branches: published record, image forensics, replication, field impact, citation practice. ",
    "The published record anchors the case: Lesné et al. (Nature, 2006) reported Aβ*56 impairs memory in rats — among the most-cited Alzheimer's papers of the century. ",
    "Forensics and replication matter too: reported image manipulation across roughly 20 related papers, and repeated failures to detect Aβ*56 in human brain tissue. ",
    "Flagging the headline evidence claim ('the 2006 Nature evidence still stands') for adversarial review — there are signs the record changed in 2024. ",
    "Adding the scope question as its own claim: the broader amyloid hypothesis does not rest on Aβ*56 alone — approved antibodies target other species. ",
    "Citation practice depends on the evidence-status claim: the zombie-citation figures inherit its risk. ",
    "The overclaim branch ('the retraction disproves the entire amyloid hypothesis') looks overstated and weakly sourced — advocates may struggle. ",
    "Claim graph ready: 9 claims, 5 branches, ~18 agents, est £0.90. ",
]

_REPLAN_DELTAS = [
    "Ruling received: the June 2024 retraction stands over the published record — the 'evidence still stands' claim is overturned, not narrowed. ",
    "Updating the record: c2 is human-adjudicated against the 2006 evidence; its dependents must be re-checked against the retraction. ",
    "The zombie-citation claim was argued from the pre-retraction citation record, and the safe-practice consensus (c9) frames the ruling — one re-check is needed. ",
    "Dispatching a single tiebreaker agent with the ruling as context, re-checking the citation-practice claim against the adjudicated retraction. ",
]

_SYNTH_CITATIONS = [
    Citation(id=1, url="https://www.nature.com/articles/nature04533",
             label="Lesné et al. (2006), Nature — the Aβ*56 paper",
             quote="a 56-kDa soluble amyloid-beta assembly, Aβ*56, impairs memory independently of plaques or neuronal loss",
             provenance=Provenance.GROUNDED),
    Citation(id=2, url="https://www.science.org/content/article/potential-fabrication-research-images-threatens-key-theory-alzheimers-disease",
             label="Science (2022), image-forensics investigation",
             quote="apparently duplicated and spliced western-blot images across roughly 20 papers from the same group",
             provenance=Provenance.GROUNDED),
    Citation(id=3, url="https://www.alzforum.org/news/community-news/abeta56-citation-record",
             label="Web of Science via Alzforum (2022), citation record",
             quote="among the most-cited Alzheimer's papers of the century, with a drug pipeline of follow-on work built on Aβ*56",
             provenance=Provenance.GROUNDED),
    Citation(id=4, url="https://www.alzforum.org/news/research-news/abeta56-replication-failures",
             label="Alzforum (2022), replication roundup",
             quote="multiple independent laboratories could not detect Aβ*56 in human brain tissue or cerebrospinal fluid",
             provenance=Provenance.GROUNDED),
    Citation(id=5, url="https://www.science.org/content/article/potential-fabrication-research-images-threatens-key-theory-alzheimers-disease",
             label="Science (2022), replication sidebar",
             quote="outside the original group, Aβ*56 has never been convincingly isolated from human samples",
             provenance=Provenance.GROUNDED),
    Citation(id=6, url="https://www.alzforum.org/news/community-news/lesne-retraction-and-amyloid-hypothesis",
             label="Alzforum (2024), the retraction and the amyloid hypothesis",
             quote="lecanemab and donanemab target other amyloid-beta species — the hypothesis never rested on Aβ*56 alone",
             provenance=Provenance.GROUNDED),
    Citation(id=7, url="https://www.alzforum.org/news/community-news/lesne-retraction-expert-commentary",
             label="Alzforum (2024), expert commentary",
             quote="the retraction removes one pillar, not the edifice — but every claim built on Aβ*56 now needs independent verification",
             provenance=Provenance.GROUNDED),
    Citation(id=8, url="https://www.nature.com/articles/s41586-024-07691-8",
             label="Nature (June 2024), retraction notice",
             quote="the authors retract this article owing to concerns regarding some of the figures — all authors agree except S. Lesné",
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
        return GuardrailResult(ok=True, reason="in scope: research-claim verification")

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
                            "citations of the 2006 paper have kept accumulating since the June 2024 retraction, and most do not mention the retraction — exactly the pattern the ruling anticipated",
                            "https://pubpeer.com/publications/lesne-2006-nature",
                            "PubPeer (2024), post-retraction citation tracking",
                            conf=0.84)]
            conf = 0.84
            summary = "Holds — strengthened — under the human ruling: with the 2006 paper formally retracted, every uncritical citation of it is a citation of retracted work; the zombie-citation pattern is re-confirmed against the adjudicated retraction."
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
                rationale=("Direct contradiction between primary sources: the published "
                           "record shows a landmark Nature paper with thousands of citations "
                           "and a drug pipeline built on it, but Nature retracted the paper "
                           "in June 2024 after image-forensics findings — all authors agreed "
                           "except Lesné. 'The evidence still stands' cannot survive the "
                           "retraction notice."))
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
                    summary="Jury ruled for the retraction: Nature withdrew the 2006 paper in June 2024 after image-manipulation findings (all authors agreed except Lesné) — the 'evidence still stands' claim is overturned, and its dependents were re-checked against the retraction."))
            elif c.id in contested:
                verdicts.append(ClaimVerdict(claim_id=c.id, verdict="CONTESTED", confidence=0.5,
                                             citation_ids=[3, 8],
                                             summary="Sources directly disagree; no human ruling recorded."))
            elif c.id == "c8" and "c8" in active:
                verdicts.append(ClaimVerdict(
                    claim_id=c.id, verdict="REFUTED", confidence=0.85, citation_ids=[7],
                    summary="'Disproves the entire amyloid hypothesis' is an overclaim: the approved anti-amyloid antibodies target other amyloid-beta species — the retraction removes one pillar, not the edifice."))
            else:
                cites = {"c1": [1, 3], "c3": [2], "c4": [4, 5], "c5": [6],
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
            "A human juror ruled for the June 2024 retraction notice [8] over the published "
            "record [3]; the evidence-status claim is stamped **human-adjudicated** — overturned — and its dependents were re-checked against the retraction.\n\n"
            if ruled else
            "No claim required human adjudication on this run.\n\n")

        short = ("No — the foundational 2006 evidence was retracted in June 2024 after "
                 "image-manipulation findings [8]")
        if "c4" in active:
            short += (", and Aβ*56 itself has repeatedly failed independent replication [4]. "
                      "Independent laboratories could not detect it in human brain tissue at all [4][5]")
        short += (". What stood for sixteen years as one of the century's most-cited "
                  "Alzheimer's results [1][3] no longer counts as evidence.")
        chunks = [
            "# Research Verdict — Aβ*56 and memory loss in Alzheimer's\n\n",
            "**Question:** does the amyloid-beta assembly Aβ*56 cause memory loss in Alzheimer's disease?\n\n",
            "## The short answer\n\n",
            short + "\n\n",
        ]
        if "c1" in active:
            record = "The claim's pedigree was real: Lesné et al. (Nature, 2006) reported that Aβ*56 impairs memory in rats, and the paper became one of the most-cited Alzheimer's papers of the century [1][3]."
            if "c5" in active:
                record += (" But the broader amyloid hypothesis never rested on Aβ*56 alone: "
                           "the approved anti-amyloid antibodies were developed against other "
                           "amyloid-beta species [6], so the question is narrower than the headlines.")
            chunks.append(record + "\n\n")
        chunks.append("## What the forensic record shows\n\n")
        history = ""
        if "c3" in active:
            history = "In 2022, Matthew Schrag — on his own time — found manipulated western-blot images in the 2006 paper and roughly 20 related papers, and Science broke the story [2]"
            if "c4" in active:
                history += "; meanwhile independent labs repeatedly failed to detect Aβ*56 in human brain tissue [4][5]"
            history += "."
        elif "c4" in active:
            history = "Independent labs repeatedly failed to detect Aβ*56 in human brain tissue at all [4][5]."
        if "c6" in active:
            history += " The cost of the error ran for 18 years of field-steering: the four further retractions requested by Lesné's university were alone cited roughly 600 times [2][8]."
        history += " Schrag's manual forensics took months; on trial here, the same dispute surfaced and was ruled on in minutes [8].\n\n"
        chunks.append(history.lstrip())
        chunks.extend([
            "## Adjudication\n\n",
            ruling_line,
            "## Citation hygiene and practice implications\n\n",
        ])
        spend = ("Under the adjudicated overturn [8], everything downstream of the 2006 result "
                 "inherits the retraction [3]; zombie citations keep the claim alive regardless, "
                 "because authors rarely check retraction status, so the paperwork lags the record"
                 if ruled else
                 "The 2006 result's downstream literature inherits its risk [3]; zombie citations keep retracted claims alive because authors rarely check retraction status")
        if "c8" in active:
            spend += " — yet the viral 'the whole amyloid hypothesis is disproven' line does **not** hold [7]"
        if "c9" in active:
            spend += "; the consensus is to verify retraction and correction status and look for independent replication before funding work built on a landmark result [7]"
        chunks.append(spend + ".\n")
        return chunks
