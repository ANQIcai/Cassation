"""Replay a recorded trace.jsonl through a Bus at the recorded cadence.

NOTE — script/CLI-only path: the SERVER's replay (hotkey R, POST /api/replay)
is `stream_trace` in cassation/server/main.py, which re-implements cadence,
mode rewrite and replay.info against the Hub. A hackathon-day fix to replay
behavior must be applied to BOTH, or routed through one of them.

Same code path the CLI/e2e consumes live: the Bus re-stamps seq/t and fans out
to subscribers, but with record=False nothing is re-appended to disk. The only
differences a client can observe: run.start.mode becomes "replay" and a
replay.info event follows run.start (contract: PROTOCOL.md event catalogue).
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from pydantic import ValidationError

from .bus import Bus
from .schemas import Event

log = logging.getLogger("cassation.replay")


def load_trace(path: Path) -> list[Event]:
    """path may be a trace.jsonl file or a runs/<run_id> directory.

    Honors the kill -9 'replayable prefix' guarantee: a torn final line (an
    event bigger than the text-IO buffer at kill time) stops the load at the
    valid prefix instead of failing the whole trace."""
    if path.is_dir():
        path = path / "trace.jsonl"
    events = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(Event.model_validate_json(line))
            except (ValidationError, json.JSONDecodeError, ValueError):
                log.warning("torn/unparseable trace line in %s — replaying the "
                            "%d-event prefix", path, len(events))
                break
    return events


async def replay_run(trace_path: Path, bus: Bus, speed: float = 1.0) -> int:
    """Re-emit a trace through `bus` at recorded cadence / speed. Returns the
    number of events emitted (including the injected replay.info)."""
    events = load_trace(trace_path)
    if not events:
        return 0
    speed = max(speed, 0.01)
    run_dir = trace_path if trace_path.is_dir() else trace_path.parent
    original_run_id = run_dir.name

    first = events[0]
    n = 0
    if first.type == "run.start":
        payload = dict(first.payload)
        recorded_at = payload.get("started_at_iso", "")
        payload["mode"] = "replay"
        await bus.emit("run.start", payload)
        await bus.emit("replay.info", {"original_run_id": original_run_id,
                                       "recorded_at_iso": recorded_at})
        n += 2
        rest = events[1:]
        prev_t = first.t
    else:
        rest = events
        prev_t = rest[0].t if rest else 0.0

    for ev in rest:
        delay = (ev.t - prev_t) / speed
        if delay > 0:
            await asyncio.sleep(delay)
        prev_t = ev.t
        await bus.emit(ev.type, ev.payload)
        n += 1
    return n
