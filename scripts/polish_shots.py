"""After-screenshots for the demo-polish PR: R-replay at 1920x1080 + 1280x720.
    .venv/bin/python scripts/polish_shots.py --port 8997 --out docs/screens/polish
"""
import argparse, subprocess, sys, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
REPO = Path(__file__).resolve().parents[1]
ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, default=8997)
ap.add_argument("--out", default="docs/screens/polish"); ap.add_argument("--tag", default="after")
a = ap.parse_args(); out = REPO / a.out; out.mkdir(parents=True, exist_ok=True)
proc = subprocess.Popen([str(REPO/".venv/bin/python"), "-m", "cassation.server", "--mock", "--port", str(a.port)],
                        cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
errs = []
try:
    for _ in range(100):
        try: urllib.request.urlopen(f"http://127.0.0.1:{a.port}/api/runs", timeout=1); break
        except OSError: time.sleep(0.2)
    with sync_playwright() as p:
        b = p.chromium.launch()
        for w, h in ((1920, 1080), (1280, 720)):
            pg = b.new_page(viewport={"width": w, "height": h})
            pg.on("console", lambda m: errs.append(f"{w}: [{m.type}] {m.text}") if m.type == "error" else None)
            pg.on("pageerror", lambda e: errs.append(f"{w}: [pageerror] {e}"))
            pg.goto(f"http://127.0.0.1:{a.port}/"); pg.wait_for_load_state("networkidle")
            pg.keyboard.press("r")
            pg.wait_for_selector('.slot.skp[data-state="done"]', timeout=90000); pg.wait_for_timeout(1500)
            pg.screenshot(path=str(out / f"{a.tag}-fanout-{w}x{h}.png"))
            pg.wait_for_selector("#completion-card", state="visible", timeout=120000); pg.wait_for_timeout(1500)
            pg.screenshot(path=str(out / f"{a.tag}-verdict-{w}x{h}.png"))
            print(w, h, "narration:", pg.inner_text("#narration"), "| mode:", pg.inner_text("#mode-text"))
            pg.close()
        b.close()
finally:
    proc.terminate(); proc.wait(10)
print("console errors:", errs or "none")
