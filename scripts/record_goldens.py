"""Record golden mock traces at natural speed (--speed 1) via the server path.

    ./.venv/bin/python scripts/record_goldens.py [--port 8992]

Boots `python -m cassation.server --mock --speed 1`, drives three full gated
runs (question variants worded differently so the docket wall has distinct
rows, different claims struck, human-paced gate delays) and leaves the traces
in runs/. These are the stand-in goldens until real ones are recorded on the day.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from e2e_server import boot_server, drive_run, stop_server

# (question variant, strike_index, gate_delay_s)
GOLDENS = [
    ("Is an air fryer really cheaper to run than ~1300 W/mK "
     "at room temperature?", 0, 4.0),
    ("Is an air fryer really cheaper to run than an electric oven?", 1, 3.0),
    ("Is an air fryer really cheaper to run than an electric oven?", 2, 3.5),
]


async def amain(port: int) -> int:
    base = f"http://127.0.0.1:{port}"
    proc = boot_server(port, speed=1.0)
    recorded = []
    try:
        for question, strike_index, gate_delay in GOLDENS:
            print(f"recording: {question[:60]}…")
            run_id, struck, tap = await drive_run(
                base, question, strike_index=strike_index,
                gate_delay=gate_delay, timeout=300.0)
            last = tap.events[-1]
            status = last.get("payload", {}).get("status")
            print(f"  -> {run_id}: {len(tap.events)} events, struck={struck}, "
                  f"status={status}")
            if status != "ok":
                print("  FAIL: run did not complete ok")
                return 1
            recorded.append(run_id)
            await asyncio.sleep(1.5)  # distinct run-id timestamps
    finally:
        stop_server(proc)
    print("\ngolden traces recorded:")
    for rid in recorded:
        print(f"  runs/{rid}/trace.jsonl")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8992)
    sys.exit(asyncio.run(amain(ap.parse_args().port)))
