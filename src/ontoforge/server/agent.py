"""The agent-loop turn: deterministic, keyless classification of ONE utterance
into a tagged artifact envelope, dispatched to the EXISTING engine paths.

The conversation-first shell talks to a single endpoint — ``POST /api/agent
{utterance}`` — and gets back a short ``narration`` plus a list of rich, typed
``artifacts`` (an answer card, a chart, join-confirm cards, an op preview, a data
map). This module is a thin ORCHESTRATOR: it never duplicates engine logic. It
classifies the utterance with a fixed cue table (reusing
``engineer.commands._cue_kind`` verbatim for the data-engineering verbs) and then
calls the already-tested service functions on the active world — ``world.ask``,
``views.run_view``, ``world.interpret``, ``world.read_atlas`` — and wraps each
result in a discriminated artifact.

Why a router with no confidence threshold of its own: every downstream endpoint
already enforces the clarify-don't-guess contract (``ask`` abstains below the soft
floor and clarifies in the band; ``view`` abstains or clarifies on an ambiguous
chart request; ``interpret`` returns a clarification or unsupported reason). So a
mis-route is SAFE — the engine adjudicates and the worst case is an honest
clarification, never a confident wrong answer. The router only routes.

Classification precedence (all deterministic, first-match-wins):

1. **engineer-op** — a non-None ``_cue_kind`` (link/synonym/retype/merge/split/
   rename) routes to ``world.interpret`` → ``op_preview`` | ``clarification`` |
   ``text`` (unsupported). This is the SAME cue table the Console uses.
2. **show-model / confirm-joins / build** — a small fixed phrase table:
   - show-model  → ``world.read_atlas`` → ``datamap`` artifact.
   - confirm-joins → ``world`` review queue (+ atlas likely-arc evidence) →
     ``confirm_joins`` artifact.
   - build → narration that points at the catalog/build flow (no destructive
     side effect from a chat turn) → ``text``.
3. **chart/measure** — chart-shape cues → ``views.run_view`` → ``chart`` |
   ``clarification`` | ``text`` (abstained).
4. **question** (default) — everything else, especially anything ending in '?'
   → ``world.ask`` → ``answer`` | ``clarification`` | ``text`` (abstained).

Everything here is keyless / offline / deterministic: equal (utterance, world)
always yields the same envelope.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from ontoforge.engineer.commands import _cue_kind

__all__ = [
    "INTENTS",
    "classify",
    "run_agent",
    "build_opener",
]

#: the closed set of intents the router emits (the contract's ``intent`` field).
INTENTS = (
    "engineer-op",
    "show-model",
    "confirm-joins",
    "build",
    "chart",
    "question",
)

# --------------------------------------------------------------- phrase tables
# Each is a regex over the LOWERCASED utterance. The tables are fixed and
# deterministic — there is no fuzzy intent inference. Mirrors the spirit of the
# engineer cue table (commands._CUES): a verb/phrase routes the sentence.

#: show the induced model / map / ontology / graph / schema
_SHOW_MODEL = re.compile(
    r"\b(show|see|view|draw|display|render|map)\b.*\b(model|map|ontology|"
    r"entit(?:y|ies)|graph|schema|atlas|data\s*map|diagram|structure)\b"
)

#: confirm / review the pending likely joins
_CONFIRM_JOINS = re.compile(
    r"\b(joins?|links?|relationships?|connections?)\b.*\b(confirm|review|"
    r"pending|waiting|approve|verify|check)\b"
    r"|\b(confirm|review|approve)\b.*\b(joins?|links?|relationships?|connections?)\b"
    r"|\bwhat\s+(?:should|do)\s+i\s+(?:confirm|review|approve)\b"
)

#: a NL "confirm all above 0.9" batch over the pending likely joins
_CONFIRM_BATCH = re.compile(
    r"\b(confirm|accept|approve)\b.*\b(all|every|the)\b.*"
    r"(?:above|over|>=?|at\s+least|≥)\s*(0?\.\d+|\d+%?)"
)

#: bring in / wire up more data (a build request)
_BUILD = re.compile(
    r"\b(add|build|wire\s*up|connect|ingest|load|import|bring\s+in|pull\s+in)\b"
    r".*\b(data|datasets?|catalog|sources?|tables?|files?|csvs?)\b"
)

#: chart-shape cues — an explicit ask for a visualization or a temporal/grouped
#: breakdown. ``by <word>`` and ``over time`` are the grouping signals.
_CHART = re.compile(
    r"\b(chart|graph|plot|trend|bar\s*chart|line\s*chart|breakdown|"
    r"over\s+time|visuali[sz]e|visuali[sz]ation)\b"
    r"|\bby\s+(month|year|quarter|week|day|date|\w+)\b"
)


def _confirm_threshold(utterance: str) -> Optional[float]:
    """Parse a 'confirm all above 0.9' / 'above 90%' threshold, else None."""
    m = _CONFIRM_BATCH.search(utterance.lower())
    if not m:
        return None
    raw = m.group(3)
    try:
        if raw.endswith("%"):
            return float(raw[:-1]) / 100.0
        v = float(raw)
        return v / 100.0 if v > 1.0 else v
    except (TypeError, ValueError):  # pragma: no cover - regex guarantees a number
        return None


# --------------------------------------------------------------- classification


def classify(utterance: str) -> str:
    """Classify one utterance into an :data:`INTENTS` member (deterministic).

    Precedence: engineer-op (cue table) → show-model → confirm-joins → build →
    chart → question (default). First match wins; ties never arise because the
    branches are checked in this fixed order."""
    text = (utterance or "").strip().lower()
    if not text:
        return "question"
    # 1) data-engineering verb (reuse the engineer cue table verbatim)
    if _cue_kind(text) is not None:
        return "engineer-op"
    # 2/3) the fixed phrase tables
    if _SHOW_MODEL.search(text):
        return "show-model"
    if _CONFIRM_JOINS.search(text) or _CONFIRM_BATCH.search(text):
        return "confirm-joins"
    if _BUILD.search(text):
        return "build"
    # 4) chart vs question — chart cues win, else the safe default
    if _CHART.search(text):
        return "chart"
    return "question"


# ------------------------------------------------------------- artifact helpers
# Each returns an ENVELOPE dict {narration, artifacts, followups, intent,
# clarification?}. The handlers reuse the active-world service functions; they
# never recompute. Numbers are left as-is (the mono treatment is a UI concern).


def _envelope(
    *,
    intent: str,
    narration: str,
    artifacts: list[dict[str, Any]],
    followups: Optional[list[str]] = None,
    clarification: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "intent": intent,
        "narration": narration,
        "artifacts": artifacts,
        "followups": list(followups or []),
        "clarification": clarification,
    }


def _scalar_value(columns: list[str], rows: list[list[Any]]) -> Optional[Any]:
    """The single scalar of a 1×1 answer (a count/measure), else None — lets the
    UI render a big-number card without re-deriving it."""
    if len(rows) == 1 and len(rows[0]) == 1:
        return rows[0][0]
    return None


def _answer_text(payload: dict[str, Any]) -> dict[str, Any]:
    """Route the result of ``world.ask`` into an artifact envelope.

    Three downstream states, all honest: abstained → ``text``; a pending
    clarification → ``text`` + ``clarification`` (one question, never a guess);
    otherwise the cited ``answer`` artifact (value/rows/columns/citations)."""
    if payload.get("abstained"):
        reason = payload.get("abstain_reason") or "I don't have grounded data for that."
        return _envelope(
            intent="question",
            narration=reason,
            artifacts=[{"kind": "text", "text": reason}],
        )
    if payload.get("clarification"):
        q = str(payload["clarification"])
        opts = list(payload.get("clarification_options") or [])
        return _envelope(
            intent="question",
            narration=q,
            artifacts=[{"kind": "text", "text": q}],
            followups=opts,
            clarification=q,
        )
    columns = list(payload.get("columns") or [])
    rows = list(payload.get("rows") or [])
    artifact = {
        "kind": "answer",
        "question": payload.get("question", ""),
        "value": _scalar_value(columns, rows),
        "columns": columns,
        "rows": rows,
        "confidence": float(payload.get("confidence", 0.0)),
        "citations": list(payload.get("citations") or []),
        "plain_english": payload.get("question", ""),
    }
    return _envelope(
        intent="question",
        narration="Here's what the data says.",
        artifacts=[artifact],
    )


def _chart_envelope(view: Any) -> dict[str, Any]:
    """Route a ``views.run_view`` result (a ``ViewOut``) into an envelope.

    ``ViewOut`` is a pydantic model; we read its fields and re-key them onto a
    ``chart`` artifact, falling back to its own clarification/abstention."""
    if getattr(view, "abstained", False):
        reason = view.abstain_reason or "I couldn't build a chart from that."
        return _envelope(
            intent="chart",
            narration=reason,
            artifacts=[{"kind": "text", "text": reason}],
        )
    if getattr(view, "clarification", None):
        q = str(view.clarification)
        opts = list(view.options or [])
        return _envelope(
            intent="chart",
            narration=q,
            artifacts=[{"kind": "text", "text": q}],
            followups=opts,
            clarification=q,
        )
    spec = view.spec.model_dump() if getattr(view, "spec", None) is not None else None
    artifact = {
        "kind": "chart",
        "spec": spec,
        "vega": dict(view.vega or {}),
        "columns": list(view.columns or []),
        "rows": list(view.rows or []),
        "citations": [c.model_dump() for c in (view.citations or [])],
        "plain_english": view.plain_english or "",
    }
    return _envelope(
        intent="chart",
        narration=view.plain_english or "Here's the chart.",
        artifacts=[artifact],
    )


def _op_preview_envelope(out: dict[str, Any]) -> dict[str, Any]:
    """Route ``world.interpret`` (the discriminated union) into an envelope.

    Unsupported → ``text`` (with the supported examples as followups);
    clarification → ``text`` + ``clarification``; otherwise the ``op_preview``
    artifact (op + preview, including the ``op_token`` apply echoes back)."""
    if out.get("unsupported"):
        reason = out.get("reason", "I can't do that as a data-engineering step.")
        return _envelope(
            intent="engineer-op",
            narration=reason,
            artifacts=[{"kind": "text", "text": reason}],
            followups=list(out.get("supported_examples") or []),
        )
    if out.get("clarification") is not None:
        q = str(out["clarification"])
        return _envelope(
            intent="engineer-op",
            narration=q,
            artifacts=[{"kind": "text", "text": q}],
            followups=list(out.get("options") or []),
            clarification=q,
        )
    op = out.get("op", {})
    preview = out.get("preview", {})
    artifact = {"kind": "op_preview", "op": op, "preview": preview}
    summary = op.get("human_summary") or "Here's the change I'd make."
    blocked = bool(preview.get("blocked"))
    narration = (
        f"I can't apply this: {preview.get('block_reason', 'it falls below the join floor.')}"
        if blocked
        else f"I can {summary}. Review the preview, then confirm to apply."
    )
    return _envelope(
        intent="engineer-op",
        narration=narration,
        artifacts=[artifact],
    )


def _datamap_envelope(atlas: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Route the persisted atlas into a ``datamap`` artifact (the data map).

    ``None`` (atlas not built) is an honest ``text`` nudge to build first —
    never a 404 inside a conversation turn."""
    if atlas is None:
        msg = (
            "I haven't mapped a model yet. Add some data and I'll induce the "
            "entities and the joins between them."
        )
        return _envelope(
            intent="show-model",
            narration=msg,
            artifacts=[{"kind": "text", "text": msg}],
            followups=["add data", "build the catalog"],
        )
    stats = atlas.get("stats", {})
    artifact = {
        "kind": "datamap",
        "components": atlas.get("components", []),
        "links": atlas.get("links", []),
        "stats": stats,
    }
    n_types = int(stats.get("classes", 0))
    n_conf = int(stats.get("confirmed", 0))
    n_likely = int(stats.get("likely", 0))
    narration = (
        f"Here's the model: {n_types} entities, {n_conf} confirmed joins, "
        f"{n_likely} likely ones still to confirm."
    )
    followups = ["confirm the likely joins"] if n_likely else []
    return _envelope(
        intent="show-model",
        narration=narration,
        artifacts=[artifact],
        followups=followups,
    )


def _likely_joins(atlas: Optional[dict[str, Any]], threshold: Optional[float]) -> list[dict[str, Any]]:
    """The atlas's likely-tier arcs (the joins awaiting confirmation), each with
    its evidence — optionally filtered to ``score >= threshold``."""
    if not atlas:
        return []
    cards: list[dict[str, Any]] = []
    for lk in atlas.get("links", []):
        if lk.get("tier") != "likely":
            continue
        if threshold is not None and float(lk.get("score", 0.0)) < threshold:
            continue
        cards.append(lk)
    cards.sort(key=lambda c: float(c.get("score", 0.0)), reverse=True)
    return cards


def _confirm_envelope(
    review: dict[str, Any], atlas: Optional[dict[str, Any]], threshold: Optional[float]
) -> dict[str, Any]:
    """Route the review queue + atlas likely-arcs into a ``confirm_joins``
    artifact (the confirm cards). ``threshold`` filters a 'confirm all above
    0.9' batch to the matching likely joins."""
    items = list(review.get("items") or [])
    likely = _likely_joins(atlas, threshold)
    artifact = {
        "kind": "confirm_joins",
        "items": items,
        "likely_joins": likely,
        "threshold": threshold,
    }
    n = len(items) + len(likely)
    if n == 0:
        narration = "Nothing is waiting for review — every join is settled."
    elif threshold is not None:
        narration = (
            f"{len(likely)} likely join(s) score at or above {threshold:g} — "
            "review the cards and confirm."
        )
    else:
        narration = (
            f"{n} thing(s) are waiting for you: {len(likely)} likely join(s) and "
            f"{len(items)} flagged decision(s)."
        )
    return _envelope(
        intent="confirm-joins",
        narration=narration,
        artifacts=[artifact],
    )


def _build_envelope(utterance: str) -> dict[str, Any]:
    """A build request from chat is NON-destructive: it narrates the catalog/
    build flow rather than silently mutating the world. The UI turns the
    followups into actions (open catalog, start a build)."""
    msg = (
        "Tell me which datasets to wire up and I'll induce the model and the "
        "joins. Open the catalog to pick sources, or name them and I'll build."
    )
    return _envelope(
        intent="build",
        narration=msg,
        artifacts=[{"kind": "text", "text": msg}],
        followups=["open the catalog", "show me what data is available"],
    )


# ------------------------------------------------------------------- the turn


def run_agent(
    world: Any,
    utterance: str,
    views_module: Any,
    review_fn: Optional[Any] = None,
) -> dict[str, Any]:
    """One agent turn over the ACTIVE world. Classify, dispatch to the existing
    engine path, wrap the result in a typed artifact envelope.

    ``views_module`` is injected (``server.views``) so the orchestrator stays
    import-light and testable. ``review_fn``, when provided, is a zero-arg
    callable the endpoint passes in — it returns the SAME flagged-decision list
    ``/api/review`` builds, so the confirm-joins turn reuses that one canonical
    query instead of duplicating its SQL. Without it, the turn still surfaces the
    atlas likely-join cards.

    Returns the contract dict; the endpoint maps it onto the pydantic
    ``AgentOut``. Keyless / offline / deterministic — and never confidently
    wrong, because each downstream call adjudicates ambiguity itself."""
    text = (utterance or "").strip()
    if not text:
        return _envelope(
            intent="question",
            narration="What would you like to know, build, or wire up?",
            artifacts=[],
        )

    intent = classify(text)

    if intent == "engineer-op":
        return _op_preview_envelope(world.interpret(text))

    if intent == "show-model":
        return _datamap_envelope(world.read_atlas())

    if intent == "confirm-joins":
        threshold = _confirm_threshold(text)
        items: list[dict[str, Any]] = []
        if review_fn is not None:
            try:
                items = list(review_fn() or [])
            except Exception:
                items = []
        return _confirm_envelope({"items": items}, world.read_atlas(), threshold)

    if intent == "build":
        return _build_envelope(text)

    if intent == "chart":
        from . import schemas as S

        view = views_module.run_view(world, S.ViewIn(text=text))
        return _chart_envelope(view)

    # default: question
    payload, _cached = world.ask(text)
    return _answer_text(payload)


# --------------------------------------------------------------- proactive opener


def build_opener(world: Any) -> dict[str, Any]:
    """The proactive opener: 'I mapped N datasets into M entities…'.

    Summarizes the active world from its workspace state + atlas + criticality —
    all read-only, all already computed. Drives the conversation's first message
    so the agent opens by narrating what it mapped and what still needs the user
    (likely joins to confirm). Never raises: an unbuilt world yields a gentle
    'point me at some data' opener."""
    state = world.workspace_state()
    stats = state.get("stats", {}) or {}
    n_datasets = len(state.get("datasets") or [])
    n_types = int(stats.get("types", 0))
    n_confirmed = int(stats.get("confirmed", 0))
    n_likely = int(stats.get("likely", 0))
    n_silos = int(stats.get("silos", 0))

    # the atlas grounds the entity/join counts when the workspace stats are blank
    atlas = world.read_atlas()
    if atlas and not n_types:
        astats = atlas.get("stats", {})
        n_types = int(astats.get("classes", 0))
        n_confirmed = int(astats.get("confirmed", 0))
        n_likely = int(astats.get("likely", 0))
        n_silos = int(astats.get("silos", 0))

    # the most critical entities focus the opener on what matters
    critical: list[dict[str, Any]] = []
    try:
        from . import usage as _usage

        critical = _usage.top_criticality(world, 3)
    except Exception:
        critical = []

    built = n_types > 0
    if not built:
        narration = (
            "I'm ready. Point me at some data — a folder of CSVs or a catalog "
            "pick — and I'll induce the entities and the joins between them."
        )
        followups = ["show me what data is available", "what can you do?"]
    else:
        ds_phrase = f"{n_datasets} dataset(s) " if n_datasets else ""
        narration = (
            f"I mapped {ds_phrase}into {n_types} entities with {n_confirmed} "
            f"confirmed joins between them."
        )
        if n_likely:
            narration += (
                f" {n_likely} more joins look likely — confirm them and the model "
                "tightens up."
            )
        if n_silos:
            narration += f" {n_silos} entit{'y' if n_silos == 1 else 'ies'} stand alone for now."
        followups = []
        if n_likely:
            followups.append("confirm the likely joins")
        followups.append("show me the model")
        if critical:
            top = critical[0].get("label", "")
            if top:
                followups.append(f"what's in {top}?")

    return {
        "narration": narration,
        "built": built,
        "stats": {
            "datasets": n_datasets,
            "entities": n_types,
            "confirmed": n_confirmed,
            "likely": n_likely,
            "standalone": n_silos,
        },
        "critical": critical,
        "followups": followups,
    }
