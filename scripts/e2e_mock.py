"""e2e, no server: engine + MockModelClient + auto-gates, then assert the trace.

Checks (exit non-zero on any failure):
- the required demo beats appear in order (run.start .. run.complete ok),
  with >=6 agent.spawn, >=6 agent.evidence, >=3 synthesis.delta;
- one claim is struck programmatically at the approval gate; the struck id
  never gets an agent spawned, never yields evidence, and appears in the
  verdict only as the engine-forced STRUCK chip (no citations) — per
  PROTOCOL/schemas the verdict list carries an honest STRUCK chip, which is
  how "visibly absent" is rendered;
- a successful decompose writes the plan into <runs root>/plan-cache.json
  (the seed for fallback layer 3), keyed by the normalized question;
- replaying the trace at speed 100 yields the identical event-type sequence
  (modulo the documented replay.info injection after run.start);
- meta.json is finalized (status ok, finished_at set, counts present);
- offline prompt wiring: GeminiModelClient loads every prompts/*.md file and
  placeholder substitution leaves no unfilled {placeholder} (no API call).

Usage: ./.venv/bin/python scripts/e2e_mock.py [--speed 25]
Runs in a temp dir so test traces never pollute the runs/ docket.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root

from cassation.orchestrator.bus import Bus, new_run_id
from cassation.orchestrator.engine import run_cassation
from cassation.orchestrator.mock_client import MockModelClient
from cassation.orchestrator.replay import load_trace, replay_run
from cassation.orchestrator.schemas import (
    ApprovalResult,
    ClaimGraph,
    JuryRequest,
    JuryRuling,
    RunConfig,
)

QUESTION = ("Is it safe to charge an e-bike battery indoors overnight?")

_failures: list[str] = []


def check(ok: bool, label: str) -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")
    if not ok:
        _failures.append(label)


def pick_strike(graph: ClaimGraph) -> str:
    """First claim no other claim depends on: safe to strike without orphaning
    the dispute (c2) or the tiebreaker dependency chain."""
    referenced = {d for c in graph.claims for d in c.depends_on}
    return next(c.id for c in graph.claims if c.id not in referenced)


class StrikingGates:
    """Auto-gates like the CLI's, but strikes one claim at approval."""

    def __init__(self, speed: float):
        self.delay = 1.5 / max(speed, 0.01)
        self.struck_id: str | None = None

    async def await_approval(self, graph: ClaimGraph) -> ApprovalResult:
        await asyncio.sleep(self.delay)
        self.struck_id = pick_strike(graph)
        return ApprovalResult(approved=True, struck_claim_ids=[self.struck_id])

    async def await_jury(self, req: JuryRequest) -> JuryRuling:
        await asyncio.sleep(self.delay)
        return JuryRuling(claim_id=req.claim_id, choice_key="b",
                          note="auto-resolved (e2e_mock)")


# Ordered beats: (event type, how many to consume, payload predicate or None).
def required_beats():
    return [
        ("run.start", 1, None),
        ("guardrail.result", 1, lambda p: p.get("ok") is True),
        ("plan.complete", 1, None),
        ("approval.request", 1, None),
        ("approval.result", 1,
         lambda p: p.get("approved") and len(p.get("struck_claim_ids", [])) >= 1),
        ("agent.spawn", 6, None),
        ("agent.evidence", 6, None),
        ("dispute.found", 1, None),
        ("takeover.show", 1, None),
        # hide must precede jury.request: the takeover overlay sits above the
        # jury dialog in the UI, so this order is what keeps it clickable
        ("takeover.hide", 1, None),
        ("jury.request", 1, None),
        ("jury.ruling", 1, None),
        ("replan.complete", 1, None),
        ("synthesis.delta", 3, None),
        ("verdict.complete", 1, None),
        ("run.complete", 1, lambda p: p.get("status") == "ok"),
    ]


def check_beat_order(events) -> None:
    beats = required_beats()
    bi, need = 0, required_beats()[0][1]
    for ev in events:
        if bi >= len(beats):
            break
        type_, _, pred = beats[bi]
        if ev.type == type_ and (pred is None or pred(ev.payload)):
            need -= 1
            if need == 0:
                bi += 1
                if bi < len(beats):
                    need = beats[bi][1]
    missing = [f"{t} x{n}" for t, n, _ in beats[bi:]]
    check(not missing, "required beats in order"
          + (f" — missing from: {missing[0]}" if missing else ""))


def check_struck_enforcement(events, struck_id: str) -> None:
    spawned = [e.payload.get("claim_id") for e in events if e.type == "agent.spawn"]
    check(struck_id not in spawned, f"struck {struck_id} never in agent.spawn")
    evidenced = [e.payload.get("claim_id") for e in events if e.type == "agent.evidence"]
    check(struck_id not in evidenced, f"struck {struck_id} never in agent.evidence")

    verdict = next(e.payload for e in events if e.type == "verdict.complete")
    rows = [v for v in verdict["verdicts"] if v["claim_id"] == struck_id]
    only_struck_chip = all(
        v["verdict"] == "STRUCK" and not v.get("citation_ids") for v in rows)
    check(len(rows) <= 1 and only_struck_chip,
          f"struck {struck_id} in verdict only as the forced STRUCK chip")
    check(struck_id not in verdict.get("brief_markdown", ""),
          f"struck {struck_id} absent from the brief markdown")


async def check_replay(trace_path: Path, original_types: list[str]) -> None:
    bus = Bus(None, record=False)
    _, q = bus.subscribe()
    n = await replay_run(trace_path, bus, speed=100.0)
    replayed = []
    while not q.empty():
        replayed.append(q.get_nowait())
    check(n == len(replayed), "replay emitted event count matches")
    expected = ["run.start", "replay.info"] + original_types[1:]
    got = [e.type for e in replayed]
    check(got == expected,
          "replay event-type sequence identical (plus injected replay.info)")
    check(replayed[0].payload.get("mode") == "replay",
          "replayed run.start.mode == 'replay'")


def check_meta(run_dir: Path) -> None:
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    check(meta.get("status") == "ok", "meta.json status ok")
    check(bool(meta.get("finished_at")), "meta.json finished_at set")
    check(bool(meta.get("counts")), "meta.json counts present")


def check_plan_cache(run_dir: Path) -> None:
    """Fallback layer 3: every successful decompose seeds <runs>/plan-cache.json."""
    cache = run_dir.parent / "plan-cache.json"
    check(cache.exists(), "plan-cache.json written next to the run dir")
    if not cache.exists():
        return
    data = json.loads(cache.read_text(encoding="utf-8"))
    key = " ".join(QUESTION.lower().split())
    entry = data.get(key)
    check(bool(entry) and bool(entry.get("graph", {}).get("claims")),
          "plan cache holds the normalized question's claim graph")


def check_prompt_wiring() -> None:
    """Offline: the live client must load prompts/*.md and fill placeholders
    (the critical day-of calibration path). No API call is made."""
    try:
        from cassation.orchestrator.gemini_client import (
            _PROMPT_FILES, GeminiModelClient, _fill)
    except Exception as e:  # noqa: BLE001
        check(False, f"gemini_client import failed: {e!r}")
        return
    try:
        client = GeminiModelClient(RunConfig(), api_key="offline-wiring-check")
    except Exception as e:  # noqa: BLE001
        check(False, f"GeminiModelClient offline construction failed: {e!r}")
        return
    check(set(client._prompts) == set(_PROMPT_FILES),
          "all prompt files loaded at client construction")
    filled = _fill(client._prompts["advocate"], question="q?", claim_id="c1",
                   claim_text="t", branch="b", context="(none)")
    leftovers = [p for p in ("{question}", "{claim_id}", "{claim_text}",
                             "{branch}", "{context}") if p in filled]
    check(not leftovers, f"advocate prompt placeholders all filled {leftovers or ''}")
    decomp = _fill(client._prompts["decompose"], question="q?", max_claims=6)
    check("{max_claims}" not in decomp and "never more than 6" in decomp,
          "decompose prompt takes cfg.max_leaves via {max_claims}")


async def main(speed: float) -> int:
    with tempfile.TemporaryDirectory(prefix="cassation-e2e-") as tmp:
        run_dir = Path(tmp) / new_run_id()
        cfg = RunConfig(mock_speed=speed)
        bus = Bus(run_dir)
        gates = StrikingGates(speed)
        try:
            await run_cassation(QUESTION, bus, MockModelClient(cfg), gates, cfg)
        finally:
            bus.close()

        events = load_trace(run_dir / "trace.jsonl")
        print(f"run complete: {len(events)} events, struck={gates.struck_id}")

        check_beat_order(events)
        counts = {}
        for e in events:
            counts[e.type] = counts.get(e.type, 0) + 1
        check(counts.get("agent.spawn", 0) >= 6,
              f"agent.spawn >= 6 (got {counts.get('agent.spawn', 0)})")
        check(counts.get("agent.evidence", 0) >= 6,
              f"agent.evidence >= 6 (got {counts.get('agent.evidence', 0)})")
        check(counts.get("synthesis.delta", 0) >= 3,
              f"synthesis.delta >= 3 (got {counts.get('synthesis.delta', 0)})")
        check(counts.get("cost.update", 0) >= 2, "cost.update heartbeat present")

        assert gates.struck_id is not None
        check_struck_enforcement(events, gates.struck_id)
        await check_replay(run_dir / "trace.jsonl", [e.type for e in events])
        check_meta(run_dir)
        check_plan_cache(run_dir)

    check_prompt_wiring()

    if _failures:
        print(f"\ne2e_mock: {len(_failures)} FAILURE(S)")
        return 1
    print("\ne2e_mock: PASS")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--speed", type=float, default=25.0,
                    help="mock latency divisor for the test run")
    sys.exit(asyncio.run(main(ap.parse_args().speed)))
