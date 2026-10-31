"""e2e, server path: boot `python -m cassation.server --mock`, drive a full gated
run over HTTP + WS, then check brief / QR / docket / replay. Exit non-zero on
any failure.

    ./.venv/bin/python scripts/e2e_server.py [--port 8991] [--speed 8] [--keep]

By default the test run's runs/<id>/ directory is deleted afterwards so
repeated e2e runs do not pollute the docket wall (--keep retains it).
scripts/record_goldens.py reuses boot_server/drive_run from here to record
natural-speed golden traces that DO stay in runs/.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
import websockets

REPO = Path(__file__).resolve().parents[1]
PYTHON = REPO / ".venv" / "bin" / "python"
RUNS_DIR = REPO / "runs"

_failures: list[str] = []


def check(ok: bool, label: str) -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")
    if not ok:
        _failures.append(label)


# --------------------------------------------------------------------- server

def boot_server(port: int, speed: float, extra: list[str] | None = None) -> subprocess.Popen:
    cmd = [str(PYTHON), "-m", "cassation.server", "--mock",
           "--port", str(port), "--speed", str(speed)] + (extra or [])
    proc = subprocess.Popen(cmd, cwd=REPO, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
            raise RuntimeError(f"server exited at boot:\n{out}")
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/api/runs", timeout=1.0)
            if r.status_code == 200:
                return proc
        except httpx.HTTPError:
            time.sleep(0.2)
    proc.terminate()
    raise RuntimeError("server did not become ready within 20s")


def stop_server(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


# --------------------------------------------------------------------- WS tap

class WsTap:
    """Buffers every event seen; read_until returns the first match."""

    def __init__(self, ws):
        self.ws = ws
        self.events: list[dict] = []

    async def read_until(self, type_: str, timeout: float = 120.0,
                         pred=None) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"no {type_} within {timeout}s "
                                   f"(saw {len(self.events)} events)")
            raw = await asyncio.wait_for(self.ws.recv(), remaining)
            ev = json.loads(raw)
            self.events.append(ev)
            if ev.get("type") == type_ and (pred is None or pred(ev.get("payload", {}))):
                return ev

    def types(self) -> list[str]:
        return [e.get("type") for e in self.events]


def pick_strike(graph: dict, index: int = 0) -> str:
    """Nth claim no other claim depends on (mirrors scripts/e2e_mock.py)."""
    referenced = {d for c in graph.get("claims", []) for d in c.get("depends_on", [])}
    leafless = [c["id"] for c in graph["claims"] if c["id"] not in referenced]
    return leafless[min(index, len(leafless) - 1)]


# --------------------------------------------------------------------- driver

async def drive_run(base: str, question: str, strike: bool = True,
                    jury_choice: str = "b", gate_delay: float = 0.0,
                    timeout: float = 240.0,
                    strike_index: int = 0,
                    probe_bad_jury: bool = False) -> tuple[str, list[str], WsTap]:
    """POST /api/run and resolve both human gates over HTTP, watching the WS.
    Returns (run_id, struck_claim_ids, tap). Raises on timeout/HTTP error.
    probe_bad_jury: while the jury gate is pending, POST a ruling with an
    unknown claim_id and require a 400 (gate validation, PROTOCOL fix)."""
    ws_url = base.replace("http", "ws", 1) + "/ws"
    async with httpx.AsyncClient(base_url=base, timeout=30.0) as client, \
            websockets.connect(ws_url) as ws:
        tap = WsTap(ws)
        r = await client.post("/api/run", json={"question": question})
        r.raise_for_status()
        run_id = r.json()["run_id"]

        # The backlog on a fresh WS may still hold the PREVIOUS run's events
        # (the hub resets when the new run's pump starts): sync to this run's
        # run.start before trusting any gate event.
        await tap.read_until("run.start", timeout,
                             pred=lambda p: p.get("run_id") == run_id)
        ev = await tap.read_until("approval.request", timeout)
        struck = [pick_strike(ev["payload"], strike_index)] if strike else []
        await asyncio.sleep(gate_delay)
        r = await client.post("/api/approve", json={
            "run_id": run_id, "approved": True, "struck_claim_ids": struck})
        r.raise_for_status()
        if not r.json().get("resolved"):
            raise RuntimeError("approval gate did not resolve")

        ev = await tap.read_until("jury.request", timeout)
        await asyncio.sleep(gate_delay)
        if probe_bad_jury:
            r = await client.post("/api/jury", json={
                "run_id": run_id, "claim_id": "zz99", "choice_key": "a"})
            if r.status_code != 400:
                raise RuntimeError(
                    f"jury ruling for an unknown claim_id returned "
                    f"{r.status_code}, expected 400 (gate must reject it)")
        r = await client.post("/api/jury", json={
            "run_id": run_id, "claim_id": ev["payload"]["claim_id"],
            "choice_key": jury_choice})
        r.raise_for_status()
        if not r.json().get("resolved"):
            raise RuntimeError("jury gate did not resolve")

        await tap.read_until("run.complete", timeout)
        return run_id, struck, tap


# --------------------------------------------------------------------- checks

async def run_checks(base: str, port: int) -> str:
    question = ("Is it safe to charge an e-bike battery indoors "
                "overnight at ~1300 W/mK room temperature?")
    run_id, struck, tap = await drive_run(base, question, probe_bad_jury=True)
    print(f"run {run_id} complete: {len(tap.events)} WS events, struck={struck}")
    check(True, "jury ruling with unknown claim_id rejected with 400 (gate)")

    seen = set(tap.types())
    for t in ("run.start", "guardrail.result", "plan.complete", "approval.result",
              "dispute.found", "takeover.show", "takeover.hide", "jury.ruling",
              "replan.complete", "synthesis.delta", "verdict.complete", "cost.update"):
        check(t in seen, f"WS saw {t}")
    types = tap.types()
    check(types.index("takeover.hide") < types.index("jury.request"),
          "takeover hides before the jury panel opens")
    last = next(e for e in tap.events if e["type"] == "run.complete")
    check(last["payload"].get("status") == "ok", "run.complete status ok")
    start = next(e for e in tap.events if e["type"] == "run.start")
    check(start["payload"].get("mode") == "mock", "run.start mode == mock")
    spawned = [e["payload"].get("claim_id") for e in tap.events
               if e["type"] == "agent.spawn"]
    check(struck[0] not in spawned, f"struck {struck[0]} never spawned")

    async with httpx.AsyncClient(base_url=base, timeout=30.0) as client:
        r = await client.get(f"/brief/{run_id}")
        check(r.status_code == 200, "GET /brief/<id> 200")
        check("CASSATION" in r.text and question[:40] in r.text,
              "brief contains title and question")
        check("HUMAN_ADJUDICATED" in r.text, "brief shows the human-ruled chip")
        check("Struck at approval" in r.text, "brief notes the struck claim")

        r = await client.get(f"/api/qr/{run_id}.png")
        check(r.status_code == 200 and r.content[:4] == b"\x89PNG",
              "GET /api/qr/<id>.png returns a PNG")

        r = await client.get("/api/runs")
        rows = {row["run_id"]: row for row in r.json()}
        check(run_id in rows, "docket lists the run")
        row = rows.get(run_id, {})
        check(row.get("status") == "ok" and row.get("n_claims", 0) > 0,
              "docket row finalized (status ok, claims counted)")

        # ---- replay 'newest': must resolve to a COMPLETED run (meta ok) -----
        # (not necessarily this one: pre-recorded goldens may sort newer by
        # name; the invariant is "never a live/aborted run")
        r = await client.post("/api/replay", json={"run_id": "", "speed": 50})
        newest = r.json().get("run_id", "")
        newest_row = rows.get(newest) or {}
        check(r.status_code == 200 and newest_row.get("status") == "ok",
              f"POST /api/replay '' resolves to a completed run ({newest})")

        # ---- replay: WS must re-stream the same run labeled replay ----------
        r = await client.post("/api/replay", json={"run_id": run_id, "speed": 50})
        check(r.status_code == 200 and r.json().get("run_id") == run_id,
              "POST /api/replay accepted")

    ws_url = base.replace("http", "ws", 1) + "/ws"
    async with websockets.connect(ws_url) as ws:
        rtap = WsTap(ws)
        start = await rtap.read_until("run.start", 30.0)
        check(start["payload"].get("mode") == "replay",
              "replayed run.start.mode == replay")
        info = await rtap.read_until("replay.info", 10.0)
        check(info["payload"].get("original_run_id") == run_id,
              "replay.info names the original run")
        await rtap.read_until("run.complete", 120.0)
        check("verdict.complete" in rtap.types(), "replay re-streamed the verdict")
    return run_id


async def amain(args: argparse.Namespace) -> int:
    base = f"http://127.0.0.1:{args.port}"
    proc = boot_server(args.port, args.speed)
    run_id = None
    try:
        run_id = await run_checks(base, args.port)
    except Exception as e:  # noqa: BLE001 — report, fail, still stop the server
        check(False, f"unhandled: {type(e).__name__}: {e}")
    finally:
        stop_server(proc)
        if run_id and not args.keep:
            shutil.rmtree(RUNS_DIR / run_id, ignore_errors=True)
            print(f"cleaned up test run {run_id} (use --keep to retain)")

    if _failures:
        print(f"\ne2e_server: {len(_failures)} FAILURE(S)")
        return 1
    print("\ne2e_server: PASS")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8991)
    ap.add_argument("--speed", type=float, default=8.0,
                    help="mock latency divisor for the test run")
    ap.add_argument("--keep", action="store_true",
                    help="keep the test run directory in runs/")
    sys.exit(asyncio.run(amain(ap.parse_args())))
