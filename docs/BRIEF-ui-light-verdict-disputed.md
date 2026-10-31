# UI brief for Oskar's agent — Cassation (TRIBUNAL), 31 Oct 2026, 17:00 London

From: Builder (Angela's agent). Angela chose these after reviewing a reference sheet
(`cassation-ui-references.png`, tiles 3 and 4). **Do these three things and nothing else in the UI.**
Your own `tribunal/web/*` stays the base. Keep every element id, data attribute and event handler the
tests and scripts use (grep `scripts/e2e_mock.py`, `scripts/e2e_server.py`, `scripts/*tour*.py` before renaming).
No frameworks, no build step (PROTOCOL non-negotiable).

## 1. Light theme — "Google AI Studio white"

Angela wants a light background like Google AI Studio's light theme, replacing the dark courtroom.

Starting palette (Google Material 3 light tokens as used in Gemini / AI Studio — **from memory, not sampled**;
if Oskar is signed in to AI Studio, open it in light theme, sample the real values with DevTools, and use those):

| Token | Value | Use |
|---|---|---|
| `--bg` | `#FFFFFF` | page |
| `--surface` | `#F8FAFD` | header, strips, side columns |
| `--panel` | `#F0F4F9` | cards, quote boxes |
| `--line` | `#C4C7C5` | hairlines (use sparingly) |
| `--ink` | `#1F1F1F` | main text |
| `--muted` | `#444746` | secondary text (never lighter: projectors wash out grey) |
| `--accent` | `#0B57D0` | links, focus, the Judge |
| `--green` | `#188038` | holds / supported |
| `--red` | `#D93025` | refuted / disputed |
| `--amber` | `#B06000` | contested |
| `--gold` | `#8A6100` | ruled by you (human seal) — dark enough for white |

Rules:
- Change token VALUES in `:root`, then remove hard-coded dark colours you find (`grep -n "#0b0d11\|#14171d\|rgba(0" style.css`).
- **Contrast ≥ 4.5:1 for every text colour on its real surface** (white, `--surface`, `--panel`, and the
  tinted state backgrounds). Check with a script, print the table. Fix any token that fails.
- State tints on white are light washes (e.g. green at 10–12 % alpha), never saturated blocks.
- Shadows: at most one soft elevation (`0 1px 2px #0000001a, 0 1px 3px 1px #0000000d`) on cards; no glow effects
  that only worked on dark.
- Type: Google Sans / Google Sans Text if available locally, else Inter, else `system-ui`. Must look fine offline.
- Projector check: screenshot at 1280×720 and confirm muted text and hairlines are still visible
  (white themes lose thin grey lines on projectors — prefer spacing over lines).

## 2. Verdict opens with ONE sentence + ONE bar (pattern: Consensus.app "Consensus Meter")

At the top of the verdict view, before the long brief:
- **One headline sentence** = the answer in plain words, e.g.
  *"'~1300 W/mK' is outdated: the best samples now exceed 2100 W/mK."* Serif or display face, 28–40 px.
  Source it from the synthesis (ask Argon for a one-sentence verdict as the first line, or take the first
  sentence of the synthesis — say which you did).
- **One horizontal bar**, segments sized by count: HOLDS (green) · CONTESTED (amber) · REFUTED (red) ·
  RULED BY YOU (gold, with ⚖). Label inside a segment when it fits, else in a legend underneath.
- **Legend line:** counts + `17 sources · 19 agents · £0.92 · 1:44` (the frozen counters).
- Motion: the bar segments grow left→right in ≤ 600 ms when the verdict completes; the sentence fades in first.
- The detailed brief (citations, per-claim verdicts) follows below, unchanged in content.
- Goal: a judge knows the answer within 2 seconds of the verdict appearing.

## 3. DISPUTED = one sharp interruption (pattern: the "OBJECTION!" moment in the Ace Attorney games)

Replace the current DISPUTED takeover with:
- **One word, "DISPUTED!"**, huge (72–110 px), heavy italic, slightly rotated (−3° to −5°), in `--red`
  with a hard offset shadow. No second label (no separate CONTESTED box, no chip) — one message only.
- **Two quote cards facing each other** under it, with "vs" between:
  left **THE RECORD SAYS · {year}** (green label), right **THE SKEPTIC FOUND · {year}** (red label).
  Quote text 18–24 px, readable at 3 m. Source line under each quote (journal, year, domain) at ≥ 16 px.
- Motion: on a light page, a brief red radial flash behind the word (≤ 150 ms), the word punches in
  (scale 1.4 → 1 with a slight shake, ≤ 350 ms), then the two cards slide in from left and right (≤ 300 ms).
  Total ≤ 600 ms. Hold until the jury step, as today.
- Background: stays light (white with a faint red wash), not a dark overlay.
- `prefers-reduced-motion`: no punch/shake/slide; the word and cards simply appear.

## Done means (run these and report the real output)

- `.venv/bin/python scripts/e2e_mock.py` → PASS and `scripts/e2e_server.py` → PASS.
- A full mock run in a real browser at **1280×720** and **1920×1080**, no console errors; hotkey **R** replay
  still works.
- Screenshots: board mid-fan-out, DISPUTED, jury, verdict — light theme — and the contrast table.
- You looked at your own screenshots and fixed overlaps, clipped text and anything unreadable before
  saying done.

## Out of scope

Anything else in the UI (board layout, agent rows, approval modal, docket) unless it breaks under the light
theme. The orchestrator and server: don't touch. The live-path fixes are in the separate handover file.
