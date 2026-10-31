"""CASSATION server: SPA, WebSocket event stream, human gates, docket, brief, replay.

Binding contracts: cassation/PROTOCOL.md and cassation/orchestrator/schemas.py.

The orchestrator modules (bus/engine/mock_client/gemini_client) are developed
concurrently, so they are imported lazily inside the functions that need them.
The server runs fully without them in --fake / --replay modes, both of which
stream a recorded trace.jsonl through the same WS path the live engine uses.
"""
from __future__ import annotations

import asyncio
import contextlib
import html
import io
import json
import logging
import os
import socket
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.websockets import WebSocketDisconnect

from cassation.orchestrator.schemas import ApprovalResult, JuryRuling

log = logging.getLogger("cassation.server")

ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = ROOT / "cassation" / "web"
RUNS_DIR = ROOT / "runs"


# --------------------------------------------------------------------------- settings

@dataclass
class Settings:
    mock: bool = False
    replay: Optional[str] = None   # run_id or trace path, started at boot
    fake: Optional[str] = None     # trace path streamed at boot (dev UI data)
    speed: float = 1.0
    port: int = 8990


SETTINGS = Settings()


def configure(**kwargs: Any) -> None:
    for k, v in kwargs.items():
        setattr(SETTINGS, k, v)


# --------------------------------------------------------------------------- event hub

class Hub:
    """Per-connection backlog-then-tail fan-out of event dicts (PROTOCOL: WS /ws)."""

    def __init__(self) -> None:
        self.backlog: list[dict] = []
        self.subs: set[asyncio.Queue] = set()

    def reset(self) -> None:
        self.backlog = []

    def publish(self, ev: dict) -> None:
        self.backlog.append(ev)
        for q in list(self.subs):
            q.put_nowait(ev)

    def subscribe(self) -> tuple[list[dict], asyncio.Queue]:
        q: asyncio.Queue = asyncio.Queue()
        self.subs.add(q)
        return list(self.backlog), q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subs.discard(q)


HUB = Hub()


# --------------------------------------------------------------------------- human gates

class ServerGates:
    """HumanGates implementation: futures resolved by POST /api/approve, /api/jury."""

    def __init__(self) -> None:
        self._approval: Optional[asyncio.Future] = None
        self._jury: Optional[asyncio.Future] = None
        self._jury_claim_id: Optional[str] = None  # the pending JuryRequest's claim

    async def await_approval(self, graph: Any) -> ApprovalResult:
        self._approval = asyncio.get_running_loop().create_future()
        try:
            return await self._approval
        finally:
            self._approval = None

    async def await_jury(self, req: Any) -> JuryRuling:
        self._jury = asyncio.get_running_loop().create_future()
        self._jury_claim_id = getattr(req, "claim_id", None)
        try:
            return await self._jury
        finally:
            self._jury = None
            self._jury_claim_id = None

    def pending_jury_claim_id(self) -> Optional[str]:
        """The claim_id a ruling must carry while the jury gate is open, else None."""
        if self._jury is not None and not self._jury.done():
            return self._jury_claim_id
        return None

    def resolve_approval(self, result: ApprovalResult) -> bool:
        f = self._approval
        if f is not None and not f.done():
            f.set_result(result)
            return True
        return False

    def resolve_jury(self, ruling: JuryRuling) -> bool:
        f = self._jury
        if f is not None and not f.done():
            f.set_result(ruling)
            return True
        return False


GATES = ServerGates()


# --------------------------------------------------------------------------- app state

@dataclass
class State:
    run_id: Optional[str] = None
    kind: str = "idle"                       # idle | run | replay | fake
    run_task: Optional[asyncio.Task] = None     # engine coroutine
    pump_task: Optional[asyncio.Task] = None    # bus -> hub forwarder
    stream_task: Optional[asyncio.Task] = None  # trace re-emitter (replay/fake)
    lock: asyncio.Lock = None  # type: ignore[assignment]

    def engine_active(self) -> bool:
        return self.kind == "run" and self.run_task is not None and not self.run_task.done()


STATE = State()
STATE.lock = asyncio.Lock()


async def stop_active_source() -> None:
    for name in ("run_task", "pump_task", "stream_task"):
        task = getattr(STATE, name)
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        setattr(STATE, name, None)
    STATE.kind = "idle"


# --------------------------------------------------------------------------- helpers

def as_dict(ev: Any) -> dict:
    if isinstance(ev, dict):
        return ev
    if hasattr(ev, "model_dump"):
        return ev.model_dump()
    raise TypeError(f"unexpected event type: {type(ev)!r}")


def new_run_id() -> str:
    return datetime.now().strftime("run-%Y%m%d-%H%M%S")


def load_events(path: Path) -> list[dict]:
    """Load a trace, honoring the kill -9 'replayable prefix' guarantee: the
    bus flushes per line, but the final line can still be torn mid-write (an
    event bigger than the text-IO buffer, e.g. verdict.complete). Stop at the
    first unparseable line and replay the valid prefix instead of raising."""
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            log.warning("torn/unparseable trace line in %s — replaying the "
                        "%d-event prefix", path, len(events))
            break
    return events


def lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))  # no packets sent; just picks the route
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return "127.0.0.1"


def scan_trace(path: Path) -> dict:
    """Derive a run summary from trace.jsonl (works even if meta.json is absent)."""
    summary: dict[str, Any] = {
        "run_id": path.parent.name, "question": "", "mode": "",
        "started_at": "", "status": "running", "n_claims": 0,
        "verdict_counts": {}, "duration_s": 0.0, "events": 0,
    }
    try:
        events = load_events(path)
    except (OSError, json.JSONDecodeError):
        return summary
    summary["events"] = len(events)
    for ev in events:
        p = ev.get("payload", {})
        t = ev.get("type")
        if t == "run.start":
            summary["run_id"] = p.get("run_id", summary["run_id"])
            summary["question"] = p.get("question", "")
            summary["mode"] = p.get("mode", "")
            summary["started_at"] = p.get("started_at_iso", "")
        elif t == "plan.complete":
            summary["n_claims"] = len(p.get("claims", []))
        elif t == "verdict.complete":
            counts: dict[str, int] = {}
            for v in p.get("verdicts", []):
                counts[v.get("verdict", "?")] = counts.get(v.get("verdict", "?"), 0) + 1
            summary["verdict_counts"] = counts
        elif t == "run.complete":
            summary["status"] = p.get("status", "ok")
            summary["duration_s"] = p.get("elapsed_s", ev.get("t", 0.0))
    return summary


def write_meta(run_dir: Path, **fields: Any) -> None:
    """Merge fields into runs/<id>/meta.json (engine may also write it; merge, don't clobber)."""
    meta_path = run_dir / "meta.json"
    meta: dict[str, Any] = {}
    if meta_path.exists():
        with contextlib.suppress(OSError, json.JSONDecodeError):
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update({k: v for k, v in fields.items() if v is not None})
    meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")


def finalize_meta(run_dir: Path) -> None:
    trace = run_dir / "trace.jsonl"
    if not trace.exists():
        return
    s = scan_trace(trace)
    finished = ""
    if s["started_at"]:
        with contextlib.suppress(ValueError):
            start = datetime.fromisoformat(s["started_at"])
            finished = (start + timedelta(seconds=s["duration_s"])).isoformat()
    write_meta(
        run_dir, run_id=s["run_id"], question=s["question"], mode=s["mode"],
        started_at=s["started_at"], finished_at=finished, status=s["status"],
        counts={"claims": s["n_claims"], "events": s["events"],
                "verdicts": s["verdict_counts"], "duration_s": s["duration_s"]},
    )


def run_summary(run_dir: Path) -> Optional[dict]:
    trace = run_dir / "trace.jsonl"
    if not trace.exists():
        return None
    s = scan_trace(trace)
    meta_path = run_dir / "meta.json"
    if meta_path.exists():
        with contextlib.suppress(OSError, json.JSONDecodeError):
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            for key in ("question", "mode", "started_at", "status"):
                if meta.get(key):
                    s[key] = meta[key]
    return {
        "run_id": s["run_id"], "question": s["question"], "mode": s["mode"],
        "started_at": s["started_at"], "status": s["status"],
        "n_claims": s["n_claims"], "verdict_counts": s["verdict_counts"],
        "duration_s": s["duration_s"],
    }


def _run_completed_ok(run_dir: Path) -> bool:
    meta_path = run_dir / "meta.json"
    if not meta_path.exists():
        return False
    with contextlib.suppress(OSError, json.JSONDecodeError):
        return json.loads(meta_path.read_text(encoding="utf-8")).get("status") == "ok"
    return False


def resolve_trace(run_id: str) -> tuple[str, Path]:
    """Accept a run_id, a trace path, or ''/'newest'. Returns (run_id, trace_path).

    ''/'newest' resolves to the newest COMPLETED run (meta.json status 'ok'),
    never a live or just-aborted one: the hotkey R exists for mid-run network
    death, and the just-crashed partial live run is always the newest dir."""
    if run_id and run_id not in ("newest", "latest"):
        p = RUNS_DIR / run_id / "trace.jsonl"
        if p.exists():
            return run_id, p
        p = Path(run_id)
        if p.is_file():
            return p.parent.name, p
        raise HTTPException(status_code=404, detail=f"no trace for run_id {run_id!r}")
    candidates = sorted(
        (d for d in RUNS_DIR.glob("run-*")
         if (d / "trace.jsonl").exists() and _run_completed_ok(d)),
        key=lambda d: d.name, reverse=True,
    )
    if not candidates:
        raise HTTPException(status_code=404, detail="no completed recorded runs")
    return candidates[0].name, candidates[0] / "trace.jsonl"


# --------------------------------------------------------------------------- sources

async def stream_trace(path: Path, speed: float, as_replay: bool,
                       cap_s: Optional[float] = None, initial_delay: float = 0.6) -> None:
    """Re-emit a trace.jsonl into the hub. as_replay rewrites run.start.mode to
    'replay' and injects replay.info (PROTOCOL event catalogue); fake mode keeps
    the recorded mode and caps inter-event gaps so dev iterations stay quick."""
    events = load_events(path)
    HUB.reset()
    await asyncio.sleep(initial_delay)
    prev_t, seq = 0.0, 0
    for ev in events:
        delay = max(0.0, float(ev.get("t", 0.0)) - prev_t) / max(speed, 0.01)
        prev_t = float(ev.get("t", 0.0))
        if cap_s is not None:
            delay = min(delay, cap_s)
        await asyncio.sleep(delay)
        out = dict(ev)
        out["seq"] = seq
        seq += 1
        if as_replay and out.get("type") == "run.start":
            payload = dict(out.get("payload", {}))
            original_run_id = payload.get("run_id", path.parent.name)
            recorded_at = payload.get("started_at_iso", "")
            payload["mode"] = "replay"
            out["payload"] = payload
            HUB.publish(out)
            HUB.publish({"seq": seq, "t": out.get("t", 0.0), "type": "replay.info",
                         "payload": {"original_run_id": original_run_id,
                                     "recorded_at_iso": recorded_at}, "v": 1})
            seq += 1
            continue
        HUB.publish(out)


async def start_trace_source(run_id: str, path: Path, speed: float,
                             as_replay: bool, cap_s: Optional[float] = None) -> None:
    await stop_active_source()
    STATE.run_id = run_id
    STATE.kind = "replay" if as_replay else "fake"
    STATE.stream_task = asyncio.create_task(
        stream_trace(path, speed, as_replay=as_replay, cap_s=cap_s))


async def pump_bus(bus: Any, backlog: list, q: asyncio.Queue) -> None:
    """Forward an orchestrator Bus subscription into the hub."""
    try:
        HUB.reset()
        for ev in backlog:
            HUB.publish(as_dict(ev))
        while True:
            HUB.publish(as_dict(await q.get()))
    finally:
        with contextlib.suppress(Exception):
            bus.unsubscribe(q)


async def start_engine_run(question: str) -> str:
    # Lazy imports: these modules are written concurrently (see module docstring).
    from cassation.orchestrator.bus import Bus
    from cassation.orchestrator.engine import run_cassation
    from cassation.orchestrator.schemas import RunConfig

    cfg = RunConfig(mock_speed=SETTINGS.speed if SETTINGS.speed > 0 else 1.0)
    has_key = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    if not SETTINGS.mock and not has_key:
        # Demo-trap guard: a "live" run with no key would just crash; demote loudly.
        log.warning("no GEMINI_API_KEY/GOOGLE_API_KEY found - falling back to MOCK "
                    "(scripted case, ignores the typed question)")
        SETTINGS.mock = True
    if SETTINGS.mock:
        from cassation.orchestrator.mock_client import MockModelClient
        client = MockModelClient(cfg)
        mode = "mock"
        log.warning("MOCK run: scripted demo case - the typed question is displayed "
                    "but the canned case runs; start without --mock for live")
    else:
        from cassation.orchestrator.gemini_client import GeminiModelClient  # VERIFIED-AT-KICKOFF: live server run run-20261031-150109 ok
        client = GeminiModelClient(cfg)
        mode = "live"

    run_id = new_run_id()
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    bus = Bus(run_dir)

    await stop_active_source()
    STATE.run_id = run_id
    STATE.kind = "run"
    write_meta(run_dir, run_id=run_id, question=question, mode=mode,
               started_at=datetime.now().astimezone().isoformat(), status="running")

    backlog, q = bus.subscribe()
    STATE.pump_task = asyncio.create_task(pump_bus(bus, backlog, q))

    async def engine_wrapper() -> None:
        try:
            await run_cassation(question, bus, client, GATES, cfg)
        except asyncio.CancelledError:
            # No finalize_meta here: the scan would see no run.complete and
            # stamp "running" over the honest "aborted".
            write_meta(run_dir, status="aborted")
            raise
        except Exception:
            log.exception("engine run %s failed", run_id)
            write_meta(run_dir, status="error")
        else:
            finalize_meta(run_dir)
        finally:
            bus.close()  # the engine never closes it; don't leak one fd per run

    STATE.run_task = asyncio.create_task(engine_wrapper())
    return run_id


def materialize_trace(path: Path) -> tuple[str, Path]:
    """Copy an external trace into runs/<id>/ so docket/brief/QR/replay see it."""
    events = load_events(path)
    run_id = path.stem
    for ev in events:
        if ev.get("type") == "run.start":
            run_id = ev.get("payload", {}).get("run_id", run_id)
            break
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    dest = run_dir / "trace.jsonl"
    dest.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    finalize_meta(run_dir)
    return run_id, dest


# --------------------------------------------------------------------------- app

@asynccontextmanager
async def lifespan(app: FastAPI):
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    if SETTINGS.fake:
        run_id, dest = materialize_trace(Path(SETTINGS.fake))
        await start_trace_source(run_id, dest, SETTINGS.speed, as_replay=False, cap_s=1.0)
        log.info("fake source streaming %s as %s", SETTINGS.fake, run_id)
    elif SETTINGS.replay:
        run_id, path = resolve_trace(SETTINGS.replay)
        await start_trace_source(run_id, path, SETTINGS.speed, as_replay=True)
        log.info("replaying %s", run_id)
    yield
    await stop_active_source()


app = FastAPI(title="CASSATION", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


class RunRequest(BaseModel):
    question: str


class ApproveRequest(BaseModel):
    run_id: str = ""
    approved: bool = True
    struck_claim_ids: list[str] = Field(default_factory=list)


class JuryRequestBody(BaseModel):
    run_id: str = ""
    claim_id: str
    choice_key: str


class ReplayRequest(BaseModel):
    run_id: str = ""     # empty/"newest" -> most recent recorded run
    speed: Optional[float] = None


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    backlog, q = HUB.subscribe()

    async def sender() -> None:
        for ev in backlog:
            await ws.send_text(json.dumps(ev, ensure_ascii=False, default=str))
        while True:
            ev = await q.get()
            await ws.send_text(json.dumps(ev, ensure_ascii=False, default=str))

    send_task = asyncio.create_task(sender())
    try:
        while True:
            await ws.receive_text()  # we never expect input; raises on disconnect
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        send_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await send_task
        HUB.unsubscribe(q)


@app.post("/api/run")
async def api_run(body: RunRequest) -> dict:
    async with STATE.lock:
        if STATE.engine_active():
            raise HTTPException(status_code=409, detail="a run is already in progress")
        try:
            run_id = await start_engine_run(body.question)
        except (ModuleNotFoundError, ImportError, AttributeError) as e:
            raise HTTPException(
                status_code=503,
                detail=f"orchestrator modules not available yet: {e}") from e
    return {"run_id": run_id}


@app.post("/api/approve")
async def api_approve(body: ApproveRequest) -> dict:
    result = ApprovalResult(approved=body.approved, struck_claim_ids=body.struck_claim_ids)
    matches = not body.run_id or not STATE.run_id or body.run_id == STATE.run_id
    resolved = GATES.resolve_approval(result) if matches else False
    # Unresolved is not an error: in fake/replay there is no engine gate (no-op).
    return {"ok": True, "resolved": resolved, "run_id": STATE.run_id}


@app.post("/api/jury")
async def api_jury(body: JuryRequestBody) -> dict:
    matches = not body.run_id or not STATE.run_id or body.run_id == STATE.run_id
    # Gate validation: a ruling for anything but the pending request's claim is
    # rejected here — one stale/buggy POST must never reach the engine, where
    # an unknown claim_id would kill the live run.
    pending = GATES.pending_jury_claim_id()
    if matches and pending is not None and body.claim_id != pending:
        raise HTTPException(
            status_code=400,
            detail=f"jury ruling for unknown claim {body.claim_id!r}: "
                   f"the pending jury request is for {pending!r}")
    ruling = JuryRuling(claim_id=body.claim_id, choice_key=body.choice_key)
    resolved = GATES.resolve_jury(ruling) if matches else False
    return {"ok": True, "resolved": resolved, "run_id": STATE.run_id}


@app.post("/api/replay")
async def api_replay(body: ReplayRequest) -> dict:
    run_id, path = resolve_trace(body.run_id)
    speed = body.speed if body.speed and body.speed > 0 else SETTINGS.speed
    async with STATE.lock:
        await start_trace_source(run_id, path, speed, as_replay=True)
    return {"ok": True, "run_id": run_id, "speed": speed}


@app.get("/api/runs")
async def api_runs() -> list[dict]:
    if not RUNS_DIR.exists():
        return []
    rows = [s for d in RUNS_DIR.glob("run-*") if (s := run_summary(d)) is not None]
    rows.sort(key=lambda r: r["run_id"], reverse=True)
    return rows


@app.get("/api/qr/{run_id}.png")
async def api_qr(run_id: str) -> Response:
    if not (RUNS_DIR / run_id / "trace.jsonl").exists():
        raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}")
    import qrcode
    url = f"http://{lan_ip()}:{SETTINGS.port}/brief/{run_id}"
    img = qrcode.make(url, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return Response(content=buf.getvalue(), media_type="image/png",
                    headers={"X-Brief-Url": url})


# --------------------------------------------------------------------------- brief page

VERDICT_COLORS = {
    "SUPPORTED": "#3fb950", "REFUTED": "#f85149", "CONTESTED": "#d29922",
    "HUMAN_ADJUDICATED": "#d4a72c", "EXCLUDED": "#6e7681", "STRUCK": "#6e7681",
}

STATE_LABELS = {
    "pending": "#6e7681", "running": "#6e7681", "supported": "#3fb950",
    "challenged": "#f85149", "contested": "#d29922", "human_ruled": "#d4a72c",
    "refuted": "#f85149", "struck": "#6e7681", "excluded": "#6e7681",
}


def md_to_html(md: str, citations: dict[int, dict]) -> str:
    """Tiny markdown renderer: #/##/### headings, - lists, **bold**, *em*,
    [n] -> citation links. Mirrors the client renderer in web/app.js."""
    import re

    def inline(s: str) -> str:
        s = html.escape(s, quote=False)
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", s)

        def cite(m: "re.Match[str]") -> str:
            n = int(m.group(1))
            c = citations.get(n)
            if c and c.get("url"):
                return (f'<a class="cite" href="{html.escape(c["url"])}" '
                        f'target="_blank" rel="noopener">{n}</a>')
            return f'<span class="cite">{n}</span>'

        return re.sub(r"\[(\d+)\]", cite, s)

    out: list[str] = []
    for block in re.split(r"\n\s*\n", md.strip()):
        lines = [l for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        first = lines[0]
        if first.startswith("### "):
            out.append(f"<h4>{inline(first[4:])}</h4>")
            lines = lines[1:]
        elif first.startswith("## "):
            out.append(f"<h3>{inline(first[3:])}</h3>")
            lines = lines[1:]
        elif first.startswith("# "):
            out.append(f"<h2>{inline(first[2:])}</h2>")
            lines = lines[1:]
        if not lines:
            continue
        if all(l.lstrip().startswith("- ") for l in lines):
            items = "".join(f"<li>{inline(l.lstrip()[2:])}</li>" for l in lines)
            out.append(f"<ul>{items}</ul>")
        else:
            out.append(f"<p>{inline(' '.join(lines))}</p>")
    return "\n".join(out)


@app.get("/brief/{run_id}", response_class=HTMLResponse)
async def brief(run_id: str) -> HTMLResponse:
    trace = RUNS_DIR / run_id / "trace.jsonl"
    if not trace.exists():
        raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}")
    events = load_events(trace)

    question, mode, started_at = "", "", ""
    claims: dict[str, dict] = {}
    states: dict[str, str] = {}
    struck: list[str] = []
    verdict: Optional[dict] = None
    ruling: Optional[dict] = None
    cost: dict = {}
    status = "running"
    for ev in events:
        p, t = ev.get("payload", {}), ev.get("type")
        if t == "run.start":
            question, mode = p.get("question", ""), p.get("mode", "")
            started_at = p.get("started_at_iso", "")
        elif t in ("plan.complete", "approval.request"):
            for c in p.get("claims", []):
                claims[c["id"]] = c
                states.setdefault(c["id"], "pending")
        elif t == "approval.result":
            struck = p.get("struck_claim_ids", [])
        elif t == "claim.state":
            states[p.get("claim_id", "")] = p.get("state", "")
        elif t == "jury.ruling":
            ruling = p
        elif t == "verdict.complete":
            verdict = p
        elif t == "cost.update":
            cost = p
        elif t == "run.complete":
            status = p.get("status", "ok")

    citations = {c["id"]: c for c in (verdict or {}).get("citations", [])}
    esc = lambda s: html.escape(str(s), quote=False)

    chips = []
    if verdict:
        verdict_by_claim = {v["claim_id"]: v for v in verdict.get("verdicts", [])}
        for cid, c in claims.items():
            v = verdict_by_claim.get(cid)
            label = v["verdict"] if v else states.get(cid, "pending").upper()
            color = VERDICT_COLORS.get(label, "#6e7681")
            extra = " text-decoration:line-through;opacity:.5;" if label == "STRUCK" else ""
            seal = " &#9878;" if label == "HUMAN_ADJUDICATED" else ""
            summary = esc(v.get("summary", "")) if v else ""
            chips.append(
                f'<div class="vrow"><span class="chip" style="border-color:{color};'
                f'color:{color}">{esc(label)}{seal}</span>'
                f'<span style="{extra}"><b>{esc(cid.upper())}</b> {esc(c["text"])}'
                f'{f"<br><small>{summary}</small>" if summary else ""}</span></div>')
    else:
        for cid, c in claims.items():
            st = states.get(cid, "pending")
            color = STATE_LABELS.get(st, "#6e7681")
            chips.append(
                f'<div class="vrow"><span class="chip" style="border-color:{color};'
                f'color:{color}">{esc(st.upper())}</span>'
                f'<span><b>{esc(cid.upper())}</b> {esc(c["text"])}</span></div>')

    body_html = (md_to_html(verdict["brief_markdown"], citations) if verdict
                 else "<p><em>Verdict pending — the court is still in session. "
                      "Refresh this page once the run completes.</em></p>")

    cite_rows = "".join(
        f'<li value="{c["id"]}"><a href="{html.escape(c.get("url", "#"))}" target="_blank" '
        f'rel="noopener">{esc(c.get("label") or c.get("url", ""))}</a>'
        f'{f"<br><small>&ldquo;{esc(c['quote'])}&rdquo;</small>" if c.get("quote") else ""}'
        f'{" <small>(model-reported)</small>" if c.get("provenance") == "model-reported" else ""}'
        f"</li>"
        for c in (verdict or {}).get("citations", []))

    struck_note = ""
    if struck:
        names = ", ".join(s.upper() for s in struck)
        struck_note = (f'<p class="note">Struck at approval by the human and never '
                       f'investigated: {esc(names)}.</p>')
    ruling_note = ""
    if ruling:
        ruling_note = (f'<p class="note">&#9878; Human jury ruling on '
                       f'{esc(ruling.get("claim_id", "").upper())}: option '
                       f'&ldquo;{esc(ruling.get("choice_key", ""))}&rdquo;'
                       f'{" — " + esc(ruling["note"]) if ruling.get("note") else ""}.</p>')

    counters = (f'est £{cost.get("est_gbp", 0):.2f} <small>(estimate)</small> · '
                f'{cost.get("sources", 0)} sources · '
                f'{cost.get("agents_done", 0)} agents · '
                f'{cost.get("elapsed_s", 0):.0f}s')

    page = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CASSATION brief — {esc(run_id)}</title>
<style>
 body{{margin:0;background:#0c0e12;color:#e8e6df;font:16px/1.55 system-ui,sans-serif}}
 .wrap{{max-width:720px;margin:0 auto;padding:20px 16px 48px}}
 h1{{font:700 22px/1.2 Georgia,serif;letter-spacing:.08em;margin:0 0 2px}}
 h2,h3,h4{{font-family:Georgia,serif;line-height:1.25;margin:22px 0 8px}}
 .meta{{color:#8a8f98;font-size:13px;margin-bottom:18px}}
 .mode{{display:inline-block;border:1px solid #d29922;color:#d29922;border-radius:4px;
        padding:0 6px;font-size:11px;letter-spacing:.1em;text-transform:uppercase}}
 .q{{font-size:18px;font-weight:600;margin:10px 0 18px}}
 .vrow{{display:flex;gap:10px;align-items:flex-start;margin:8px 0;font-size:14px}}
 .chip{{flex:0 0 auto;border:1.5px solid;border-radius:4px;padding:1px 7px;font-size:11px;
        font-weight:700;letter-spacing:.06em;margin-top:1px;white-space:nowrap}}
 .cite{{display:inline-block;background:#1d2635;color:#79b8ff;border-radius:4px;
        padding:0 5px;margin:0 1px;font-size:12px;font-weight:700;text-decoration:none}}
 a{{color:#79b8ff}} small{{color:#8a8f98}}
 .note{{border-left:3px solid #d29922;padding:4px 10px;background:#15130c;font-size:14px}}
 .counters{{margin-top:26px;padding-top:10px;border-top:1px solid #2a2f37;color:#8a8f98;
            font-size:14px}}
 ol li{{margin:8px 0;font-size:14px;overflow-wrap:anywhere}}
 hr{{border:0;border-top:1px solid #2a2f37;margin:24px 0}}
</style></head><body><div class="wrap">
<h1>CASSATION &middot; RESEARCH BRIEF</h1>
<div class="meta">{esc(run_id)} · <span class="mode">{esc(mode or "?")}</span>
 · {esc(started_at[:19].replace("T", " "))} · status: {esc(status)}</div>
<div class="q">{esc(question)}</div>
<h3>Claim verdicts</h3>
{"".join(chips)}
{struck_note}
{ruling_note}
<hr>
{body_html}
<hr>
<h3>Citations</h3>
<ol>{cite_rows or "<li>none yet</li>"}</ol>
<div class="counters">{counters} · adversarially cross-examined by Gemini agents;
 cost figures are estimates; mode is labeled above.</div>
</div></body></html>"""
    return HTMLResponse(page)
