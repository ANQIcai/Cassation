# JUDGE / REPLAN — after the human ruling (gemini-4-argon, same interaction chain as decompose)

TEMPLATE CONVENTION: loaded verbatim; placeholders filled by literal string
replacement (`str.replace`, never `str.format` — all other braces are literal
JSON). Placeholders: `{graph_json}` (ClaimGraph), `{jury_request_json}`
(JuryRequest — the contested claim and both options as shown to the human),
`{ruling_json}` (JuryRuling).
CLIENT NOTES: runs on the Judge's `previous_interaction_id` chain, so the graph
is usually already in context — `{graph_json}` may be replaced with "(see plan
above)". There is no Replan model in schemas.py: the client extracts `notes` as
ModelClient.replan's return value, feeds `tiebreaker.mandate` into the tiebreaker
leaf's `{context}`, and may surface `adjustments` as claim.state notes. The UI's
`replan.delta` ticker is fed from streamed thinking text; the final output must
be pure JSON.

## Situation

The court deadlocked on one claim and a human jury has ruled. The ruling is
BINDING. It is not evidence to be weighed; it is a fact of the record. You never
re-litigate it, even if you disagree.

Plan of record:
{graph_json}

The contested claim as put to the jury:
{jury_request_json}

The ruling:
{ruling_json}

## Your two tasks

1. **Propagate.** For each claim whose `depends_on` chain passes through the ruled
   claim, state its adjusted confidence given the ruling. Only downstream claims;
   leave the rest alone. Skip struck or excluded claims.
2. **Name ONE tie-breaker.** The single highest-value residual uncertainty the
   ruling creates, as one existing claim id plus one sharp search mandate for one
   extra agent. Exactly one — not zero, not two. It must not target a struck or
   excluded claim, nor re-open the ruled claim itself.

## Output

Return ONLY one JSON object — no markdown fences, no prose:

{
  "notes": "<2-4 plain sentences for the record: what the ruling changes and why the tie-breaker>",
  "adjustments": [
    {"claim_id": "c7", "new_confidence": 0.4, "reason": "<max 15 words>"}
  ],
  "tiebreaker": {
    "claim_id": "c7",
    "mandate": "<one imperative sentence: exactly what the extra agent must search for>"
  }
}

- "notes" is displayed in the judge ticker and quoted in the record — refer to the
  ruling as binding ("Per the human ruling on c4, ...").
- "new_confidence" 0..1; "claim_id" values must exist in the plan of record.

## Calibration notes

- Notes length is a ticker dial: 2–4 sentences fills the re-plan beat (~10 s);
  cut to 2 if the beat drags.
- If adjustments routinely touch > 3 claims, the decompose graph is too deep —
  fix dependency fan-out there, not here.
- Tie-breaker aggressiveness: the mandate should be checkable in one 20 s grounded
  call ("Find post-2024 isotope-enriched κ measurements of c-BAs"), never a
  mini-literature-review.
- schemas.py has no Replan model (recorded as an open contract issue); if one is
  added, mirror its field names here.
