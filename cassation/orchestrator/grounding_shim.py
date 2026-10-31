"""Grounding shim: pull resolvable source URLs out of Google Search grounding
metadata, degrading honestly when the metadata is absent or thinner than
documented.

VERIFIED AT KICKOFF (2026-10-31, fixtures/kickoff-grounded.json +
fixtures/kickoff2-p4-leaf-refetch.json): the Interactions API DOES expose
grounding attribution, but not where the pre-kickoff key lists guessed:

- `google_search_result` steps carry ONLY the Search-Suggestions chip HTML
  (`result[0].search_suggestions`) — no URLs, no snippets.
- The real attribution lives on the `model_output` step: each `content` item
  may carry `annotations` of `type="url_citation"` with `url` (a
  vertexaisearch.cloud.google.com redirect that resolves to the source),
  `title` (the source DOMAIN, e.g. "researchgate.net"), and
  `start_index`/`end_index` character offsets into that content item's text.
- There is NO exact source quote in the metadata: the annotated span is the
  MODEL's own text. So an upgraded Evidence keeps the model's quote and gains
  the resolvable redirect URL — "grounded" means "search-attributed", and the
  jury cards keep displaying the agent's claimed-exact quote.

Pure functions over plain dicts/objects (no SDK imports) so this stays unit-
testable against recorded fixtures.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from .schemas import Evidence, Provenance, Stance

# Legacy key sweep kept as defense for google_search_result-style payloads.
_URL_KEYS = ("url", "uri", "link", "source_url", "page_url")
_TITLE_KEYS = ("title", "source_title", "label", "display_name", "domain")
_QUOTE_KEYS = ("quote", "snippet", "segment", "text")


def _get(obj: Any, key: str) -> Any:
    """Read a field from a dict or an attribute from an SDK object."""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def domain_of(url: str) -> str:
    try:
        host = urlparse(url).netloc
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def resolvable_url(candidate: Any) -> str:
    """Return the candidate if it looks like a fetchable http(s) URL, else ''."""
    if not isinstance(candidate, str):
        return ""
    candidate = candidate.strip()
    try:
        p = urlparse(candidate)
    except Exception:
        return ""
    if p.scheme in ("http", "https") and p.netloc and "." in p.netloc:
        return candidate
    return ""


def _domain_like(title: str) -> str:
    """The live API puts the source DOMAIN in annotation `title`."""
    t = (title or "").strip().lower()
    if t.startswith("www."):
        t = t[4:]
    return t if ("." in t and " " not in t and "/" not in t) else ""


def _sources_from_annotations(step: Any) -> list[dict]:
    """url_citation annotations on a model_output step's content items —
    the REAL grounding channel (see module docstring)."""
    found: list[dict] = []
    for item in _get(step, "content") or []:
        text = _get(item, "text") or ""
        for ann in _get(item, "annotations") or []:
            if _get(ann, "type") != "url_citation":
                continue
            url = resolvable_url(_get(ann, "url"))
            if not url:
                continue
            title = _get(ann, "title") or ""
            start = _get(ann, "start_index")
            end = _get(ann, "end_index")
            segment = ""
            # start/end are UTF-8 BYTE offsets (fixture: 302-char text, end 308):
            # slice bytes, decode leniently.
            if isinstance(start, int) and isinstance(end, int) and 0 <= start < end:
                raw = text.encode("utf-8")
                segment = raw[start:min(end, len(raw))].decode("utf-8", errors="ignore")
            found.append({"url": url, "title": title, "quote": "",
                          "domain": _domain_like(title) or domain_of(url),
                          "segment": segment})
    return found


def _sources_from_step(step: Any) -> list[dict]:
    """Best-effort source records from one google_search_result-ish step
    (legacy/defensive: live steps carry only the Search-Suggestions chip)."""
    found: list[dict] = []
    for container_key in ("results", "sources", "citations", "content",
                          "grounding_chunks", "web_results"):
        items = _get(step, container_key)
        if not isinstance(items, (list, tuple)):
            continue
        for item in items:
            url = next((resolvable_url(_get(item, k)) for k in _URL_KEYS
                        if resolvable_url(_get(item, k))), "")
            title = next((_get(item, k) for k in _TITLE_KEYS
                          if isinstance(_get(item, k), str) and _get(item, k)), "")
            quote = next((_get(item, k) for k in _QUOTE_KEYS
                          if isinstance(_get(item, k), str) and _get(item, k)), "")
            if url or quote:
                found.append({"url": url, "title": title, "quote": quote,
                              "domain": _domain_like(title) or domain_of(url),
                              "segment": ""})
    return found


def extract_sources(steps: list[Any]) -> list[dict]:
    """All grounding sources across an interaction's steps:
    [{url, title, quote, domain, segment}], deduplicated by url."""
    out: list[dict] = []
    seen: dict[str, dict] = {}

    def _add(src: dict) -> None:
        key = src["url"] or f"quote:{src['quote'][:60]}"
        if key in seen:
            for f in ("quote", "segment"):
                if src[f] and not seen[key][f]:
                    seen[key][f] = src[f]
            return
        seen[key] = src
        out.append(src)

    for step in steps or []:
        stype = _get(step, "type")
        if stype == "model_output":
            for src in _sources_from_annotations(step):
                _add(src)
        elif stype in ("google_search_result", "url_context_result"):
            for src in _sources_from_step(step):
                _add(src)
    return out


def ground_evidence(ev: Evidence, sources: list[dict]) -> Evidence:
    """Upgrade one model-reported Evidence using grounding annotations, or
    degrade honestly. Matching reality (see module docstring):

    - The model's evidence URL domain matches an annotation's domain (its
      `title`), or the model's quote falls inside an annotated text span →
      provenance='grounded'; the annotation's redirect URL wins (it resolves;
      the model's own URL may be hallucinated) and the MODEL's quote is kept
      (the metadata has no source quote to prefer).
    - Model gave a resolvable URL but no annotation matches → provenance stays
      'model-reported', URL degraded to its domain link.
    - Nothing resolvable → provenance='model-reported', url=''.
    """
    model_url = resolvable_url(ev.url)
    match = None
    if model_url:
        md = domain_of(model_url)
        match = next((s for s in sources if s["url"] and s.get("domain") == md), None)
    if match is None and ev.quote:
        probe = ev.quote[:50].lower()
        match = next((s for s in sources
                      if probe in (s.get("segment") or "").lower()
                      or probe in (s.get("quote") or "").lower()), None)

    if match and match["url"]:
        return ev.model_copy(update={
            "url": match["url"],
            "quote": ev.quote or match["quote"],
            "source_label": ev.source_label or match.get("domain") or match["title"],
            "provenance": Provenance.GROUNDED})
    if model_url:
        return ev.model_copy(update={
            "url": f"https://{domain_of(model_url)}/",
            "provenance": Provenance.MODEL_REPORTED})
    return ev.model_copy(update={"url": "", "provenance": Provenance.MODEL_REPORTED})


def _clip_words(text: str, cap: int = 40) -> str:
    words = " ".join(text.split()).split(" ")
    return " ".join(words[:cap])


def evidence_from_sources(claim_id: str, stance: Stance, sources: list[dict],
                          limit: int = 3) -> list[Evidence]:
    """Fallback: when the model returned no structured evidence at all, build
    Evidence directly from the annotations. The annotated span is MODEL text,
    not a source quote, so these are labeled model-reported (honest degrade)
    while keeping the resolvable redirect URL."""
    out = []
    for s in sources[:limit]:
        quote = s["quote"] or s.get("segment") or ""
        if not (s["url"] and quote):
            continue
        out.append(Evidence(claim_id=claim_id, stance=stance,
                            quote=_clip_words(quote),
                            url=s["url"],
                            source_label=s["title"] or s.get("domain")
                            or domain_of(s["url"]),
                            provenance=Provenance.MODEL_REPORTED))
    return out
