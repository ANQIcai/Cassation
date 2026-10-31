"""CLI entry: python -m cassation.server --mock | --replay <run_id|path> [--speed N] [--port 8990]

Dev extra: --fake <trace.jsonl> streams a hand-authored trace over the same WS
(no orchestrator modules needed) — the UI development data source.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

from cassation.server.main import RUNS_DIR, app, configure


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m cassation.server", description=__doc__)
    p.add_argument("--mock", action="store_true",
                   help="POST /api/run uses the MockModelClient instead of Gemini")
    p.add_argument("--replay", metavar="RUN_ID|PATH",
                   help="start by replaying a recorded trace at original cadence")
    p.add_argument("--fake", metavar="PATH",
                   help="dev: stream a sample trace with capped delays (UI data source)")
    p.add_argument("--speed", type=float, default=1.0,
                   help="replay/mock latency divisor (2.0 = twice as fast)")
    p.add_argument("--port", type=int, default=8990)
    p.add_argument("--host", default="0.0.0.0",
                   help="bind address (default 0.0.0.0 so the phone brief works on LAN)")
    args = p.parse_args()

    if args.fake and not Path(args.fake).is_file():
        sys.exit(f"--fake: no such file: {args.fake}")
    if args.replay:
        is_run = (RUNS_DIR / args.replay / "trace.jsonl").is_file()
        if not is_run and not Path(args.replay).is_file():
            sys.exit(f"--replay: no run dir or trace file named {args.replay!r}")

    configure(mock=args.mock, replay=args.replay, fake=args.fake,
              speed=args.speed, port=args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
