"""Headless CLI runner: the product before any UI exists.

    python -m cassation.orchestrator.cli "question" --mock [--speed 8] [--live]

Prints one compact line per event, auto-resolves both human gates (approve-all
after 1.5s/speed; jury picks "b"), and writes a real trace + meta.json under
runs/<run_id>/ — the same artifacts the server and replay engine consume.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from .bus import Bus, new_run_id
from .engine import run_cassation
from .mock_client import MockModelClient
from .schemas import ApprovalResult, ClaimGraph, JuryRequest, JuryRuling, RunConfig


class AutoGates:
    """CLI gate resolution so e2e runs headless (PROTOCOL: approve-all after
    1.5s/speed; jury picks option 'b')."""

    def __init__(self, speed: float):
        self.delay = 1.5 / max(speed, 0.01)

    async def await_approval(self, graph: ClaimGraph) -> ApprovalResult:
        await asyncio.sleep(self.delay)
        return ApprovalResult(approved=True, struck_claim_ids=[])

    async def await_jury(self, req: JuryRequest) -> JuryRuling:
        await asyncio.sleep(self.delay)
        return JuryRuling(claim_id=req.claim_id, choice_key="b",
                          note="auto-resolved (CLI)")


def _summarize(type_: str, p: dict) -> str:
    text = p.get("text")
    if text is not None:
        return repr(text if len(text) <= 70 else text[:67] + "...")
    bits = []
    for key in ("run_id", "mode", "claim_id", "agent_id", "state", "status",
                "choice_key", "contested", "strength", "ok"):
        if key in p and p[key] not in (None, ""):
            bits.append(f"{key}={p[key]}")
    if type_ == "agent.evidence":
        bits.append(f'quote="{p.get("quote", "")[:50]}..."')
    if type_ == "cost.update":
        bits = [f"£{p.get('est_gbp', 0):.2f}", f"sources={p.get('sources', 0)}",
                f"live={p.get('agents_live', 0)}", f"done={p.get('agents_done', 0)}"]
    if type_ in ("plan.complete", "approval.request"):
        bits = [f"claims={len(p.get('claims', []))}",
                f"est=£{p.get('est_total_cost_gbp', 0):.2f}"]
    if type_ == "verdict.complete":
        counts: dict[str, int] = {}
        for v in p.get("verdicts", []):
            counts[v["verdict"]] = counts.get(v["verdict"], 0) + 1
        bits = [f"{k}:{n}" for k, n in sorted(counts.items())]
        bits.append(f"citations={len(p.get('citations', []))}")
    return " ".join(str(b) for b in bits)


async def _print_events(bus: Bus) -> None:
    backlog, q = bus.subscribe()

    def show(ev) -> None:
        print(f"{ev.seq:4d} {ev.t:7.2f}s  {ev.type:<18} {_summarize(ev.type, ev.payload)}")

    try:
        for ev in backlog:  # events emitted before this task first ran
            show(ev)
        while True:
            ev = await q.get()
            show(ev)
            if ev.type == "run.complete":
                return
    finally:
        bus.unsubscribe(q)


async def _main(args: argparse.Namespace) -> int:
    cfg = RunConfig(mock_speed=args.speed)
    if args.live:
        from .gemini_client import GeminiModelClient  # lazy: needs a key
        client = GeminiModelClient(cfg)
    else:
        client = MockModelClient(cfg)

    run_dir = Path("runs") / new_run_id()
    bus = Bus(run_dir)
    printer = asyncio.create_task(_print_events(bus))
    try:
        await run_cassation(args.question, bus, client, AutoGates(args.speed), cfg)
        await printer
    finally:
        printer.cancel()
        bus.close()
    print(f"\ntrace: {run_dir / 'trace.jsonl'}")
    print(f"meta:  {run_dir / 'meta.json'}")
    ok = any(e.type == "verdict.complete" for e in bus._backlog)
    return 0 if ok else 1


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m cassation.orchestrator.cli",
                                 description="Headless Cassation run (writes a real trace).")
    ap.add_argument("question")
    ap.add_argument("--mock", action="store_true",
                    help="use the canned MockModelClient (default unless --live)")
    ap.add_argument("--live", action="store_true",
                    help="use the Gemini Interactions API (needs GEMINI_API_KEY)")
    ap.add_argument("--speed", type=float, default=1.0,
                    help="mock latency divisor, e.g. 8 = 8x faster")
    args = ap.parse_args()
    if args.mock and args.live:
        ap.error("--mock and --live are mutually exclusive")
    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
