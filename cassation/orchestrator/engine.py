"""run_cassation: the state machine.

GUARDRAIL → DECOMPOSE → APPROVE (gate) → ADVOCATES → SKEPTICS → DISPUTES
→ JURY (gate) → REPLAN → SYNTHESIS → DONE   (contract: cassation/PROTOCOL.md)

Engine-enforced invariants (not prompt-level):
- struck claims never get an agent spawned and are filtered out of the Dossier;
- every leaf runs inside asyncio.wait_for(cfg.leaf_timeout_s): timeout/error
  emits agent.done{timeout|error} + claim.state{excluded} and the run continues;
- cost.update heartbeat at least every 1s from DECOMPOSE to DONE;
- jury safety net: no dispute clears the threshold → jury opens on the claim
  with the lowest advocate confidence below cfg.borderline_confidence; if
  neither triggers, JURY and REPLAN are skipped;
- after a ruling: client.replan, then exactly ONE tiebreaker leaf on the ruled
  claim's first dependent (or the ruled claim itself if it has none).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import Counter
from pathlib import Path
from typing import Protocol

from .bus import Bus, finalize_meta, write_meta
from .model_client import ModelClient, OnDelta, agent_id_for, agent_label
from .schemas import (
    AgentFindings,
    AgentRole,
    ApprovalResult,
    Claim,
    ClaimGraph,
    ClaimState,
    ClaimVerdict,
    CostSnapshot,
    DisputeScore,
    Dossier,
    Evidence,
    JuryOption,
    JuryRequest,
    JuryRuling,
    RunConfig,
    Verdict,
)

# Flat per-call cost estimates (GBP) for the live cost strip; labeled
# "estimate" in the UI. Calibrated against real billing on hackathon day.
_COST = {"decompose": 0.09, "leaf": 0.033, "clerk": 0.003, "synthesis": 0.18}

log = logging.getLogger("cassation.engine")


# --- cached-plan seed (plan §8 fallback layer 3) -----------------------------
# Every successful decompose writes its plan into runs/plan-cache.json keyed
# by the normalized question; if a later decompose blows cfg.decompose_deadline_s
# and the cache has an entry, the board is seeded from it (plan.delta says so
# honestly) and the leaves still run live.

def _normalized_question(question: str) -> str:
    return " ".join(question.lower().split())


def _plan_cache_path(bus: Bus) -> Path | None:
    return bus.run_dir.parent / "plan-cache.json" if bus.run_dir is not None else None


def _load_cached_plan(path: Path | None, question: str) -> ClaimGraph | None:
    if path is None or not path.exists():
        return None
    try:
        entry = json.loads(path.read_text(encoding="utf-8")).get(
            _normalized_question(question))
        if not entry:
            return None
        graph = ClaimGraph.model_validate(entry["graph"])
        graph.question = question
        return graph
    except Exception:
        log.exception("plan cache read failed (%s)", path)
        return None


def _store_cached_plan(path: Path | None, question: str, graph: ClaimGraph,
                       mode: str) -> None:
    if path is None:
        return
    try:
        data: dict = {}
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8")) or {}
        # Mock-poisoning guard (kickoff 2026-10-31): a mock rehearsal must not
        # overwrite a LIVE cached plan for the same question — the cache is the
        # decompose-deadline fallback and must stay real once seeded live.
        prior = data.get(_normalized_question(question))
        if prior and prior.get("mode") == "live" and mode != "live":
            return
        data[_normalized_question(question)] = {
            "mode": mode,
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "graph": graph.model_dump(mode="json"),
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    except Exception:
        log.exception("plan cache write failed (%s) — run continues", path)


class HumanGates(Protocol):
    async def await_approval(self, graph: ClaimGraph) -> ApprovalResult: ...
    async def await_jury(self, req: JuryRequest) -> JuryRuling: ...


class _Cost:
    def __init__(self) -> None:
        self.est_gbp = 0.0
        self.agents_live = 0
        self.agents_done = 0
        self.urls: set[str] = set()
        self.t0 = time.monotonic()

    def snapshot(self) -> CostSnapshot:
        return CostSnapshot(est_gbp=round(self.est_gbp, 3), sources=len(self.urls),
                            agents_live=self.agents_live, agents_done=self.agents_done,
                            elapsed_s=round(time.monotonic() - self.t0, 1))


async def _heartbeat(bus: Bus, cost: _Cost) -> None:
    while True:
        await bus.emit("cost.update", cost.snapshot())
        await asyncio.sleep(0.9)  # ">= every 1s" with emit/scheduler overhead included


async def _run_leaf(bus: Bus, client: ModelClient, cfg: RunConfig, cost: _Cost,
                    claim: Claim, role: AgentRole, context: str,
                    excluded: set[str], exclude_on_failure: bool = True) -> AgentFindings | None:
    """One advocate/skeptic/tiebreaker leaf, timeout-fenced. Never raises.

    exclude_on_failure=False for the TIEBREAKER: it is additive evidence after
    a human ruling, so its failure must never retract that ruling (or the
    dependent claim's own findings) by stamping the claim EXCLUDED."""
    agent_id = agent_id_for(role, claim.id)
    await bus.emit("agent.spawn", {"agent_id": agent_id, "role": role.value,
                                   "claim_id": claim.id, "model": cfg.model_leaf,
                                   "label": agent_label(role, claim)})
    cost.agents_live += 1
    cost.est_gbp += _COST["leaf"]

    async def on_delta(text: str) -> None:
        await bus.emit("agent.delta", {"agent_id": agent_id, "text": text})

    t0 = time.monotonic()
    status = "ok"
    error_repr = ""
    findings: AgentFindings | None = None
    try:
        findings = await asyncio.wait_for(
            client.investigate(claim, role, context, on_delta), cfg.leaf_timeout_s)
    except asyncio.TimeoutError:
        status = "timeout"
    except Exception as e:  # noqa: BLE001 — leaf failure is survivable by design
        status = "error"
        error_repr = repr(e)[:200]
        # Diagnosable on the day: a 429 wave, a schema reject and a shim bug
        # must be distinguishable without code changes (plan §6 H0-0:30).
        log.exception("leaf %s failed on %s", agent_id, claim.id)
    cost.agents_live -= 1
    cost.agents_done += 1
    if findings is not None:
        for ev in findings.evidence:
            if ev.url:
                cost.urls.add(ev.url)
            await bus.emit("agent.evidence", {**ev.model_dump(mode="json"),
                                              "agent_id": agent_id})
    done_payload = {"agent_id": agent_id, "status": status,
                    "elapsed_s": round(time.monotonic() - t0, 2)}
    if error_repr:  # Event envelope allows extras; the UI ignores unknown keys
        done_payload["error"] = error_repr
    await bus.emit("agent.done", done_payload)
    if status != "ok":
        if exclude_on_failure:
            excluded.add(claim.id)
            await bus.emit("claim.state", {"claim_id": claim.id,
                                           "state": ClaimState.EXCLUDED.value,
                                           "note": f"{role.value} {status} — honest omission"})
        return None
    return findings


async def run_cassation(question: str, bus: Bus, client: ModelClient,
                       gates: HumanGates, cfg: RunConfig) -> None:
    run_id = bus.run_id
    mode = getattr(client, "mode", "live")
    t_start = time.monotonic()
    cost = _Cost()
    heartbeat: asyncio.Task | None = None

    await bus.emit("run.start", {
        "run_id": run_id, "question": question, "mode": mode,
        "started_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S")})
    if bus.record:
        write_meta(bus.run_dir, run_id, question, mode)

    counts: dict = {}

    async def finish(status: str) -> None:
        if heartbeat:
            heartbeat.cancel()
        await bus.emit("cost.update", cost.snapshot())
        await bus.emit("run.complete", {"status": status,
                                        "elapsed_s": round(time.monotonic() - t_start, 2)})
        counts["events"] = bus._seq
        if bus.record:
            finalize_meta(bus.run_dir, status, counts)

    try:
        # --- GUARDRAIL -------------------------------------------------------
        guard = await client.guardrail(question)
        await bus.emit("guardrail.result", guard)
        if not guard.ok:
            await finish("aborted")
            return

        heartbeat = asyncio.create_task(_heartbeat(bus, cost))

        # --- DECOMPOSE (deadline-fenced; plan §8 fallback layer 3) ------------
        async def plan_delta(text: str) -> None:
            await bus.emit("plan.delta", {"text": text})

        cost.est_gbp += _COST["decompose"]
        cache_path = _plan_cache_path(bus)
        seeded = False
        decompose_task = asyncio.create_task(client.decompose(question, plan_delta))
        decompose_task.add_done_callback(
            lambda t: t.cancelled() or t.exception())  # never "never retrieved"
        try:
            graph = await asyncio.wait_for(asyncio.shield(decompose_task),
                                           cfg.decompose_deadline_s)
        except asyncio.TimeoutError:
            cached = _load_cached_plan(cache_path, question)
            if cached is None:
                # No cache for this question: keep waiting — an honest slow
                # plan beats no plan (hotspot/replay layers cover total death).
                graph = await decompose_task
            else:
                decompose_task.cancel()
                seeded = True
                graph = cached
                await bus.emit("plan.delta", {
                    "text": "Decompose exceeded its deadline — seeded from "
                            "cached plan; agents still run live. "})
        await bus.emit("plan.complete", graph)
        if not seeded:
            _store_cached_plan(cache_path, question, graph, mode)
        counts["claims"] = len(graph.claims)

        # --- APPROVE (gate #1) ----------------------------------------------
        await bus.emit("approval.request", graph)
        approval = await gates.await_approval(graph)
        await bus.emit("approval.result", approval)
        if not approval.approved:
            await finish("aborted")
            return
        struck = {cid for cid in approval.struck_claim_ids}
        for cid in struck:
            await bus.emit("claim.state", {"claim_id": cid,
                                           "state": ClaimState.STRUCK.value,
                                           "note": "struck at approval gate"})
        # Engine-enforced: struck claims never appear below this line.
        active = [c for c in graph.claims if c.id not in struck]
        counts["struck"] = len(struck)

        excluded: set[str] = set()
        findings: list[AgentFindings] = []

        # --- ADVOCATES -------------------------------------------------------
        adv_claims = active[: cfg.max_leaves]
        adv_by_claim: dict[str, AgentFindings] = {}

        async def _advocate(c: Claim) -> None:
            await bus.emit("claim.state", {"claim_id": c.id,
                                           "state": ClaimState.RUNNING.value})
            f = await _run_leaf(bus, client, cfg, cost, c, AgentRole.ADVOCATE,
                                "", excluded)
            if f is not None:
                adv_by_claim[c.id] = f
                findings.append(f)
                await bus.emit("claim.state", {"claim_id": c.id,
                                               "state": ClaimState.SUPPORTED.value})

        await asyncio.gather(*[_advocate(c) for c in adv_claims])

        # --- SKEPTICS --------------------------------------------------------
        skp_claims = [c for c in adv_claims if c.id in adv_by_claim][: cfg.max_leaves]

        async def _skeptic(c: Claim) -> AgentFindings | None:
            ctx = "Advocate found: " + adv_by_claim[c.id].stance_summary
            await bus.emit("claim.state", {"claim_id": c.id,
                                           "state": ClaimState.CHALLENGED.value,
                                           "note": "skeptic attacking"})
            return await _run_leaf(bus, client, cfg, cost, c, AgentRole.SKEPTIC,
                                   ctx, excluded)

        skp_results = await asyncio.gather(*[_skeptic(c) for c in skp_claims])
        skp_by_claim: dict[str, AgentFindings] = {}
        for c, f in zip(skp_claims, skp_results):
            if f is not None:
                skp_by_claim[c.id] = f
                findings.append(f)

        # --- DISPUTES --------------------------------------------------------
        disputes: list[DisputeScore] = []
        cleared: list[DisputeScore] = []
        scored_claims = [c for c in skp_claims
                         if c.id not in excluded and c.id in skp_by_claim]

        async def _clerk(c: Claim) -> DisputeScore | None:
            cost.est_gbp += _COST["clerk"]
            # Clerk fence (kickoff 2026-10-31): a flash-lite clerk call can
            # stall or return garbage (observed: 115s degeneration loop ->
            # invalid JSON). One bad clerk must not kill a live run — fence it
            # and skip dispute scoring for just that claim. Fenced at
            # cfg.clerk_timeout_s (demo calibration: healthy clerks are 2-7s;
            # the old leaf_timeout_s fence cost 45s per looping clerk).
            try:
                return await asyncio.wait_for(
                    client.clerk_score(c, adv_by_claim[c.id].evidence,
                                       skp_by_claim[c.id].evidence),
                    cfg.clerk_timeout_s)
            except Exception:  # noqa: BLE001 — clerk failure is survivable
                log.exception("clerk failed on %s — dispute scoring skipped", c.id)
                return None

        # Clerks are independent per-claim calls (normally 2-7s each): score
        # them concurrently so the phase costs one clerk, not the sum of nine
        # (run-20261031-150109 spent 190s here sequentially). Outcomes are
        # emitted AFTER the gather, in claim order, so the trace and the
        # board stay deterministic for replay/e2e.
        scores = list(await asyncio.gather(*[_clerk(c) for c in scored_claims]))
        # One parallel retry wave for fenced/failed clerks: flash-lite stalls
        # are per-call stochastic (measured 2026-10-31 15:35: same claim ok in
        # 2-8s on one call, >60s on the next), so a fresh call recovers most.
        # Worst case the whole phase costs 2 x clerk_timeout_s; a claim that
        # fails both waves keeps the honest "challenge unscored" chip.
        retry_idx = [i for i, s in enumerate(scores) if s is None]
        if retry_idx:
            retried = await asyncio.gather(
                *[_clerk(scored_claims[i]) for i in retry_idx])
            for i, s in zip(retry_idx, retried):
                scores[i] = s
        for c, score in zip(scored_claims, scores):
            if score is None:
                await bus.emit("claim.state", {
                    "claim_id": c.id, "state": ClaimState.SUPPORTED.value,
                    "note": "clerk failed — challenge unscored"})
                continue
            # The takeover/jury cards need two real quotes. Live flash-lite
            # echoes support_ev but returned counter_ev=None on 10/10 disputes
            # across run-20261031-144627/-150109: fall back to the strongest
            # original evidence — the same material the clerk judged, with
            # pristine (unslimmed) URLs.
            if score.support_ev is None and adv_by_claim[c.id].evidence:
                score.support_ev = max(adv_by_claim[c.id].evidence,
                                       key=lambda e: e.confidence)
            if score.counter_ev is None and skp_by_claim[c.id].evidence:
                score.counter_ev = max(skp_by_claim[c.id].evidence,
                                       key=lambda e: e.strength)
            disputes.append(score)
            if score.contested and score.strength >= cfg.dispute_threshold:
                cleared.append(score)
                await bus.emit("dispute.found", score)
                await bus.emit("claim.state", {"claim_id": c.id,
                                               "state": ClaimState.CONTESTED.value})
            elif (max((e.strength for e in skp_by_claim[c.id].evidence), default=0.0)
                  - adv_by_claim[c.id].confidence) >= 0.3:
                await bus.emit("claim.state", {"claim_id": c.id,
                                               "state": ClaimState.REFUTED.value,
                                               "note": "counter-evidence outweighs support"})
            else:
                await bus.emit("claim.state", {"claim_id": c.id,
                                               "state": ClaimState.SUPPORTED.value,
                                               "note": "challenge did not hold"})

        # --- JURY (gate #2), with borderline-confidence safety net ----------
        ruling: JuryRuling | None = None
        jury_req: JuryRequest | None = None
        if cleared:
            top = max(cleared, key=lambda d: d.strength)
            jury_req = _jury_request_from_dispute(graph, top)
        else:
            weakest = min((f for f in adv_by_claim.values()
                           if f.confidence < cfg.borderline_confidence
                           and f.claim_id not in excluded),  # never jury a half-investigated claim
                          key=lambda f: f.confidence, default=None)
            if weakest is not None:
                jury_req = _jury_request_from_findings(
                    graph, weakest, skp_by_claim.get(weakest.claim_id))
                await bus.emit("claim.state", {
                    "claim_id": weakest.claim_id,
                    "state": ClaimState.CONTESTED.value,
                    "note": "borderline confidence — jury safety net"})

        if jury_req is not None:
            left, right = jury_req.options[0], jury_req.options[1]
            await bus.emit("takeover.show", {
                "claim_id": jury_req.claim_id,
                "left": {"quote": left.quote, "url": left.url,
                         "source_label": left.source_label},
                "right": {"quote": right.quote, "url": right.url,
                          "source_label": right.source_label}})
            # Dwell on the full-bleed DISPUTED beat, then hide BEFORE the jury
            # panel opens: the takeover overlay sits above the jury dialog in
            # the UI (z-order), so hide-first is what makes the panel clickable.
            await asyncio.sleep(4.0 / max(cfg.mock_speed, 0.01))
            await bus.emit("takeover.hide", {})
            await bus.emit("jury.request", jury_req)
            ruling = await gates.await_jury(jury_req)
            await bus.emit("jury.ruling", ruling)
            await bus.emit("claim.state", {"claim_id": ruling.claim_id,
                                           "state": ClaimState.HUMAN_RULED.value,
                                           "note": f"jury ruled '{ruling.choice_key}'"})

        # --- REPLAN + one tiebreaker leaf ------------------------------------
        if ruling is not None:
            async def replan_delta(text: str) -> None:
                await bus.emit("replan.delta", {"text": text})

            notes = await client.replan(ruling, graph, replan_delta)
            target = _first_dependent(active, ruling.claim_id, excluded)
            if target is None:
                # Defense in depth: a ruling whose claim_id matches nothing
                # (should be rejected at the HTTP gate) must not kill the run.
                await bus.emit("replan.complete", {"notes": notes,
                                                   "added_agent_ids": []})
            else:
                tie_id = agent_id_for(AgentRole.TIEBREAKER, target.id)
                await bus.emit("replan.complete", {"notes": notes,
                                                   "added_agent_ids": [tie_id]})
                ctx = (f"Human jury ruled '{ruling.choice_key}' on claim "
                       f"{ruling.claim_id}. {notes}")
                if target.id != ruling.claim_id:
                    await bus.emit("claim.state", {"claim_id": target.id,
                                                   "state": ClaimState.RUNNING.value,
                                                   "note": "tiebreaker re-check"})
                # The tiebreaker is additive: its failure never excludes the
                # claim (a timeout must not erase a human adjudication).
                tie = await _run_leaf(bus, client, cfg, cost, target,
                                      AgentRole.TIEBREAKER, ctx, excluded,
                                      exclude_on_failure=False)
                if tie is not None:
                    findings.append(tie)
                    if target.id != ruling.claim_id:  # ruled claim stays human_ruled
                        await bus.emit("claim.state", {"claim_id": target.id,
                                                       "state": ClaimState.SUPPORTED.value,
                                                       "note": "tiebreaker re-check ok"})
                elif target.id == ruling.claim_id:
                    await bus.emit("claim.state", {"claim_id": target.id,
                                                   "state": ClaimState.HUMAN_RULED.value,
                                                   "note": "tiebreaker failed — the human ruling stands"})
                else:
                    await bus.emit("claim.state", {"claim_id": target.id,
                                                   "state": ClaimState.SUPPORTED.value,
                                                   "note": "tiebreaker failed — original findings stand"})

        # --- SYNTHESIS -------------------------------------------------------
        dossier = Dossier(
            question=question,
            # struck claims are excluded from the Dossier: the judge never sees them
            graph=ClaimGraph(question=graph.question, claims=active,
                             est_total_cost_gbp=graph.est_total_cost_gbp,
                             est_agents=graph.est_agents),
            approval=approval,
            # excluded claims are honest gaps: their surviving findings (e.g.
            # an advocate whose skeptic timed out) stay out of the digest and
            # the citation list, so chip and brief tell the same story
            findings=[f for f in findings
                      if f.status == "ok" and f.claim_id not in excluded],
            disputes=disputes,
            ruling=ruling)

        async def synth_delta(text: str) -> None:
            await bus.emit("synthesis.delta", {"text": text})

        cost.est_gbp += _COST["synthesis"]
        verdict = await client.synthesize(dossier, synth_delta)
        verdict = _enforce_verdict_chips(verdict, graph, struck, excluded,
                                         ruling.claim_id if ruling else None)
        await bus.emit("verdict.complete", verdict)

        counts["agents"] = cost.agents_done
        counts["sources"] = len(cost.urls)
        counts["excluded"] = len(excluded)
        counts["verdicts"] = dict(Counter(v.verdict for v in verdict.verdicts))
        await finish("ok")
    except Exception:
        await finish("error")
        raise
    finally:
        # CancelledError is a BaseException and skips the except clause above:
        # engine-task cancellation (replay hotkey R mid-run cancels run_task)
        # must never orphan the heartbeat into the still-open Bus.
        if heartbeat is not None:
            heartbeat.cancel()


def _first_dependent(active: list[Claim], ruled_id: str,
                     excluded: set[str]) -> Claim | None:
    """Tiebreaker target: first non-excluded dependent in graph order, else the
    ruled claim itself, else None (unknown ruled_id — caller skips the tiebreaker)."""
    for c in active:
        if ruled_id in c.depends_on and c.id not in excluded:
            return c
    return next((c for c in active if c.id == ruled_id), None)


def _jury_request_from_dispute(graph: ClaimGraph, d: DisputeScore) -> JuryRequest:
    claim = next(c for c in graph.claims if c.id == d.claim_id)
    sup: Evidence | None = d.support_ev
    cnt: Evidence | None = d.counter_ev
    return JuryRequest(
        claim_id=d.claim_id,
        question=f"The agents cannot settle this claim: {claim.text}",
        options=[
            JuryOption(key="a", summary="Uphold the claim as stated",
                       quote=sup.quote if sup else "", url=sup.url if sup else "",
                       source_label=sup.source_label if sup else ""),
            JuryOption(key="b", summary="Rule for the counter-evidence",
                       quote=cnt.quote if cnt else "", url=cnt.url if cnt else "",
                       source_label=cnt.source_label if cnt else ""),
        ])


def _jury_request_from_findings(graph: ClaimGraph, adv: AgentFindings,
                                skp: AgentFindings | None) -> JuryRequest:
    claim = next(c for c in graph.claims if c.id == adv.claim_id)
    sup = adv.evidence[0] if adv.evidence else None
    cnt = (max(skp.evidence, key=lambda e: e.strength)
           if skp and skp.evidence else None)
    return JuryRequest(
        claim_id=adv.claim_id,
        question=(f"Advocate confidence is borderline ({adv.confidence:.2f}) "
                  f"on: {claim.text}"),
        options=[
            JuryOption(key="a", summary="Accept the advocate's evidence",
                       quote=sup.quote if sup else "", url=sup.url if sup else "",
                       source_label=sup.source_label if sup else ""),
            JuryOption(key="b", summary="Rule the claim unproven",
                       quote=cnt.quote if cnt else "", url=cnt.url if cnt else "",
                       source_label=cnt.source_label if cnt else ""),
        ])


def _enforce_verdict_chips(verdict: Verdict, graph: ClaimGraph,
                           struck: set[str], excluded: set[str],
                           ruled_id: str | None = None) -> Verdict:
    """Engine-enforced honesty: struck/excluded chips exist and cannot be overwritten
    by the model, whatever the synthesis returned. A human ruling outranks an
    exclusion: the jury-ruled claim is never stamped EXCLUDED."""
    verdicts = [v for v in verdict.verdicts
                if v.claim_id not in struck
                and (v.claim_id not in excluded or v.claim_id == ruled_id)]
    present = {v.claim_id for v in verdicts}
    for c in graph.claims:
        if c.id in struck:
            verdicts.append(ClaimVerdict(
                claim_id=c.id, verdict="STRUCK", confidence=0.0,
                summary="Struck by a human at the approval gate; never investigated."))
        elif c.id == ruled_id and c.id not in present:
            verdicts.append(ClaimVerdict(
                claim_id=c.id, verdict="HUMAN_ADJUDICATED", confidence=0.9,
                summary="Adjudicated by the human jury; the ruling is binding."))
        elif c.id in excluded and c.id != ruled_id:
            verdicts.append(ClaimVerdict(
                claim_id=c.id, verdict="EXCLUDED", confidence=0.0,
                summary="Agent timed out or errored; honestly omitted."))
        elif c.id not in present:
            verdicts.append(ClaimVerdict(
                claim_id=c.id, verdict="CONTESTED", confidence=0.0,
                summary="Synthesis returned no verdict for this claim."))
    order = {c.id: i for i, c in enumerate(graph.claims)}
    verdicts.sort(key=lambda v: order.get(v.claim_id, 99))
    return Verdict(brief_markdown=verdict.brief_markdown, verdicts=verdicts,
                   citations=verdict.citations)
