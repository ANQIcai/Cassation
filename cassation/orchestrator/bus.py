"""Event bus: the spine of Cassation.

Every UI-visible fact is one Event, stamped (seq, t) here at emit time,
appended as one flushed JSON line to runs/<run_id>/trace.jsonl, and fanned
out to subscriber queues (the WebSocket layer subscribes; so does the CLI
printer). Replay re-emits a recorded trace through a Bus(record=False) so
nothing is re-appended to disk. Contract: cassation/PROTOCOL.md.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .schemas import Event


def new_run_id(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("run-%Y%m%d-%H%M%S")


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    return obj


class Bus:
    def __init__(self, run_dir: Path | None, record: bool = True):
        self.run_dir = run_dir
        self.record = record and run_dir is not None
        self._seq = 0
        self._t0 = time.monotonic()
        self._backlog: list[Event] = []
        self._queues: list[asyncio.Queue[Event]] = []
        self._fh = None
        if self.record:
            run_dir.mkdir(parents=True, exist_ok=True)
            self._fh = open(run_dir / "trace.jsonl", "a", encoding="utf-8")

    @property
    def run_id(self) -> str:
        return self.run_dir.name if self.run_dir else "replay"

    async def emit(self, type: str, payload: BaseModel | dict) -> Event:
        self._seq += 1
        event = Event(
            seq=self._seq,
            t=round(time.monotonic() - self._t0, 3),
            type=type,
            payload=_jsonable(payload),
        )
        self._backlog.append(event)
        if self._fh:
            self._fh.write(json.dumps(event.model_dump(mode="json"),
                                      ensure_ascii=False, separators=(",", ":")) + "\n")
            self._fh.flush()
        for q in list(self._queues):
            q.put_nowait(event)
        return event

    def subscribe(self) -> tuple[list[Event], asyncio.Queue[Event]]:
        q: asyncio.Queue[Event] = asyncio.Queue()
        self._queues.append(q)
        return list(self._backlog), q

    def unsubscribe(self, q: asyncio.Queue[Event]) -> None:
        if q in self._queues:
            self._queues.remove(q)

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None


# --- runs/<run_id>/meta.json helpers (shape: PROTOCOL.md) -------------------

def write_meta(run_dir: Path, run_id: str, question: str, mode: str) -> dict:
    meta = {
        "run_id": run_id,
        "question": question,
        "mode": mode,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "finished_at": None,
        "status": "running",
        "counts": {},
    }
    _dump_meta(run_dir, meta)
    return meta


def finalize_meta(run_dir: Path, status: str, counts: dict) -> dict:
    path = run_dir / "meta.json"
    meta = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    meta["finished_at"] = datetime.now().isoformat(timespec="seconds")
    meta["status"] = status
    meta["counts"] = counts
    _dump_meta(run_dir, meta)
    return meta


def _dump_meta(run_dir: Path, meta: dict) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
