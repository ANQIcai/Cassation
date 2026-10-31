"""Dev probe: connect to the CASSATION WS, collect events, print a histogram.

Usage: python scripts/dev_ws_probe.py [--port 8990] [--seconds 30] [--expect-complete]
Exits non-zero if --expect-complete is set and no run.complete arrives in time.
"""
import argparse
import asyncio
import json
import sys

import websockets


async def probe(port: int, seconds: float, expect_complete: bool) -> int:
    url = f"ws://127.0.0.1:{port}/ws"
    counts: dict[str, int] = {}
    first, last, total = None, None, 0
    got_complete = False
    try:
        async with websockets.connect(url) as ws:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + seconds
            while loop.time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - loop.time()))
                except asyncio.TimeoutError:
                    break
                ev = json.loads(raw)
                total += 1
                counts[ev["type"]] = counts.get(ev["type"], 0) + 1
                first = first or ev
                last = ev
                if ev["type"] == "run.complete":
                    got_complete = True
                    break
    except OSError as e:
        print(f"connect failed: {e}")
        return 2

    print(f"received {total} events from {url}")
    for k in sorted(counts):
        print(f"  {counts[k]:3d}  {k}")
    if first:
        print(f"first: seq={first['seq']} type={first['type']} "
              f"payload_keys={sorted(first.get('payload', {}))}")
    if last:
        print(f"last:  seq={last['seq']} t={last['t']} type={last['type']}")
    if expect_complete and not got_complete:
        print("FAIL: no run.complete within the window")
        return 1
    return 0


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8990)
    p.add_argument("--seconds", type=float, default=30.0)
    p.add_argument("--expect-complete", action="store_true")
    a = p.parse_args()
    sys.exit(asyncio.run(probe(a.port, a.seconds, a.expect_complete)))


if __name__ == "__main__":
    main()
