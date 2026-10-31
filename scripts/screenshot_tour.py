"""Headless-browser tour of a live mock run; saves the eight demo beats.

    python3 scripts/screenshot_tour.py [--port 9120] [--speed 2] [--keep]
                                       [--out docs/screens]

Needs Playwright (system python3, not the venv). Boots the venv server with
--mock, drives the question box / approval strike / jury ruling through the
real UI, and captures the eight 1280x720 demo beats into --out (default
docs/screens/), plus one supplementary completion-card view (07b):

    01-idle.png       hero before any run
    02-approval.png   approval sheet open, one row struck (dep warning visible)
    03-fanout.png     advocates mid-flight, court record filling
    04-skeptics.png   red wave: skeptic slots live
    05-takeover.png   DISPUTED full-bleed (red chip landed)
    06-jury.png       jury panel open
    07-verdict.png    verdict tab: chips + citations (top of the brief)
    07b-completion.png  verdict scrolled to the completion card: confidence
                      ledger + CASE CLOSED counters + QR (below 07's fold)
    08-record.png     court record after the run, scrolled to the jury moment

The tour's run dir is deleted afterwards unless --keep. No stray processes:
the server it boots is terminated in a finally block.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[1]
VENV_PY = REPO / ".venv" / "bin" / "python"
RUNS_DIR = REPO / "runs"

QUESTION = ("Is an air fryer really cheaper to run than an electric oven?")

# struck in beat 02: c9 depends on c8, so the dep-warning tag shows, and the
# guaranteed mock dispute (c2) is untouched so beats 05/06 still fire.
STRIKE_ID = "c8"


def boot_server(port: int, speed: float) -> subprocess.Popen:
    proc = subprocess.Popen(
        [str(VENV_PY), "-m", "cassation.server", "--mock",
         "--port", str(port), "--speed", str(speed)],
        cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    deadline = time.monotonic() + 20
    url = f"http://127.0.0.1:{port}/api/runs"
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("server exited at boot:\n" + (proc.stdout.read() or ""))
        try:
            with urllib.request.urlopen(url, timeout=1):
                return proc
        except OSError:
            time.sleep(0.2)
    proc.terminate()
    raise RuntimeError("server not ready within 20s")


def tour(base: str, out_dir: Path) -> list[str]:
    """Drive the eight beats; returns console errors/warnings + page errors."""
    out_dir.mkdir(parents=True, exist_ok=True)
    console: list[str] = []

    def shot(page, name: str) -> None:
        try:  # never capture a beat with a transient toast over the board
            page.wait_for_selector("#toast", state="hidden", timeout=4000)
        except Exception:
            pass
        page.screenshot(path=str(out_dir / name))
        print(f"  {name}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        page.on("console", lambda msg: console.append(f"[{msg.type}] {msg.text}")
                if msg.type in ("error", "warning") else None)
        page.on("pageerror", lambda exc: console.append(f"[pageerror] {exc}"))
        page.goto(base)
        page.wait_for_load_state("networkidle")

        # 01 — idle hero, before any run.
        page.wait_for_selector("#hero", state="visible", timeout=10000)
        page.wait_for_timeout(400)          # docket fetch settles
        shot(page, "01-idle.png")

        page.fill("#question-input", QUESTION)
        page.press("#question-input", "Enter")

        # 02 — approval sheet: strike one row (dep warning appears), shoot.
        page.wait_for_selector("#approval-overlay", state="visible", timeout=60000)
        page.click(f'.arow[data-id="{STRIKE_ID}"]')
        page.wait_for_timeout(350)
        shot(page, "02-approval.png")
        page.click("#approve-btn")

        # 03 — advocates mid-flight, court record filling.
        page.wait_for_selector('.slot.adv[data-state="live"]', timeout=60000)
        page.wait_for_timeout(2500)
        shot(page, "03-fanout.png")

        # 04 — the red wave: first skeptic slots live.
        page.wait_for_selector('.slot.skp[data-state="live"]', timeout=120000)
        page.wait_for_timeout(1200)
        shot(page, "04-skeptics.png")

        # 05 — DISPUTED takeover (chip flips green->amber->red over 1.8s; the
        # engine dwells ~4s/speed before hiding — speed 1 catches the red chip).
        page.wait_for_selector("#takeover", state="visible", timeout=120000)
        page.wait_for_timeout(2200)
        shot(page, "05-takeover.png")

        # 06 — jury panel (takeover hides first by engine ordering).
        page.wait_for_selector("#jury-overlay", state="visible", timeout=60000)
        page.wait_for_timeout(400)
        shot(page, "06-jury.png")
        page.click("#jury-options .jury-opt:nth-child(2) button")

        # 07 — verdict tab: chips + streamed brief (pane top).
        page.wait_for_selector("#completion-card", state="visible", timeout=180000)
        page.wait_for_timeout(900)          # QR <img> fetch
        shot(page, "07-verdict.png")

        # 07b — the completion card sits below column D's fold; scroll it into
        # view so the QR + frozen counters + replay hint are evidenced too.
        page.eval_on_selector("#completion-card",
                              "el => el.scrollIntoView({block: 'end'})")
        page.wait_for_timeout(300)
        shot(page, "07b-completion.png")

        # 08 — court record after the run, scrolled to the jury moment.
        page.click("#tab-record")
        page.wait_for_selector(".rec-jury", timeout=10000)
        page.eval_on_selector(".rec-jury", "el => el.scrollIntoView({block: 'center'})")
        page.wait_for_timeout(300)
        shot(page, "08-record.png")

        browser.close()
    return console


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9120)
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--keep", action="store_true",
                    help="keep the tour's run directory in runs/")
    ap.add_argument("--out", type=Path, default=REPO / "docs" / "screens",
                    help="directory for the eight PNGs (default docs/screens/)")
    args = ap.parse_args()

    before = {d.name for d in RUNS_DIR.glob("run-*")}
    proc = boot_server(args.port, args.speed)
    console: list[str] = []
    try:
        console = tour(f"http://127.0.0.1:{args.port}", args.out)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        if not args.keep:
            import json
            import shutil
            for d in RUNS_DIR.glob("run-*"):
                if d.name in before:
                    continue
                try:  # other agents write runs/ concurrently: delete ONLY the
                    meta = json.loads((d / "meta.json").read_text())  # tour's own
                except (OSError, ValueError):
                    continue
                if meta.get("question") == QUESTION:
                    shutil.rmtree(d, ignore_errors=True)
                    print(f"cleaned up tour run {d.name}")
    log = args.out / "console.txt"
    log.write_text("\n".join(console) + ("\n" if console else ""))
    if console:
        print(f"browser console ({len(console)} errors/warnings, saved to {log}):")
        for line in console:
            print(f"  {line}")
    else:
        print(f"browser console clean (log at {log})")
    print(f"screenshots in {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
