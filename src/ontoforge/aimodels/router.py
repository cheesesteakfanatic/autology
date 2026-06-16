"""ModelRouter — task-scoped model selection over the frozen ``ModelClient`` seam.

The AI-native scaffolding's front door (docs/AI_NATIVE_AND_UI_PLAN.md §A). A
LiteLLM-style ``complete(task, prompt, schema?)`` routes to a per-task
:class:`ModelSpec` and, on failure, falls back across an EXPLICIT priority-ordered
chain of specs. The router builds ON ``contracts.models.ModelClient`` (FROZEN); it
never edits the contract.

Design constraints (all test-enforced):

* **Keyless by default.** Every task's default spec is the deterministic
  ``HeuristicAdapter`` (tier=deterministic). No key is required at import or run.
* **Explicit fallback.** Research (deep-research, 2026-06-15) *refuted* the claim
  that routing libraries give automatic fallback for free (0-3). So fallback is
  implemented and tested here, not assumed: a spec whose adapter raises (or whose
  adapter cannot be constructed because a key is absent) is skipped and the next
  spec in priority order is tried. If every spec fails, the original error from the
  last attempt is re-raised wrapped in :class:`RouterExhausted`.
* **Layerable later, zero rework.** ``register_model(task, spec)`` lets Kimi K2 /
  Qwen / Opus (OpenAI-compatible) be added as additional specs activated only when
  a key is present. Adding a live model is registering a spec — the routing/fallback
  logic is unchanged.

A :class:`ModelSpec` carries a *factory* (a zero-arg callable returning a
``ModelClient``), not a live client, so construction is lazy and a key-requiring
adapter (``AnthropicAdapter``) never raises at registration time — only when first
actually invoked, where the fallback chain catches it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

from ontoforge.contracts.models import ModelClient, ModelRequest, ModelResponse
from ontoforge.ledger.models import HeuristicAdapter

if TYPE_CHECKING:  # avoid an import cycle: library imports nothing from router
    from .library import PromptLibrary
    from .observation import ObservationLog

__all__ = [
    "TIERS",
    "ModelRouter",
    "ModelSpec",
    "Observer",
    "RouterExhausted",
    "default_router",
    "make_observer",
]

#: an observer is invoked AFTER a successful propose; it is purely a side-effect
#: sink (recording) and must never alter the response. It receives the task, the
#: winning :class:`ModelSpec`, the :class:`ModelResponse`, and the prompt that was
#: sent (needed for the stable fingerprint). A router with ``observer=None`` never
#: builds or calls one, so the default path is byte-for-byte unchanged.
Observer = Callable[[str, "ModelSpec", ModelResponse, str], None]

#: cost/latency tiers, cheapest/most-available first (plan §A).
TIERS = ("deterministic", "fast", "frontier")

#: a factory builds a ModelClient on demand; raising is a legitimate "unavailable"
#: signal (e.g. AnthropicAdapter with no key) and triggers fallback.
ClientFactory = Callable[[], ModelClient]


class RouterExhausted(RuntimeError):
    """Every spec in a task's fallback chain failed; carries the last cause."""


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """One routable model option for a task.

    ``factory`` is a zero-arg callable that constructs the adapter on demand
    (lazy: a key-requiring adapter does not raise until invoked, where fallback
    catches it). ``priority`` orders the fallback chain ASCENDING (0 = primary).
    ``tier`` is one of :data:`TIERS`.
    """

    factory: ClientFactory
    model_id: str = "heuristic"
    tier: str = "deterministic"
    temperature: float = 0.0
    max_tokens: int = 1024
    priority: int = 0

    def __post_init__(self) -> None:
        if self.tier not in TIERS:
            raise ValueError(f"tier must be one of {TIERS}, got {self.tier!r}")


class ModelRouter:
    """Routes ``complete(task, ...)`` to a task's spec chain, with explicit,
    priority-ordered fallback. Keyless and deterministic by default."""

    def __init__(self, observer: Optional[Observer] = None) -> None:
        # task -> list[ModelSpec] kept sorted by priority (ascending)
        self._specs: dict[str, list[ModelSpec]] = {}
        # memoized constructed clients keyed by id(spec) (lazy, per-spec)
        self._clients: dict[int, ModelClient] = {}
        # optional side-effect sink invoked after a successful propose; when None
        # the routing path is identical to before the observation loop existed.
        self._observer = observer

    # ------------------------------------------------------------ registry

    def register_model(self, task: str, spec: ModelSpec) -> None:
        """Register a spec for a task. Multiple specs per task form the
        priority-ordered fallback chain (lower ``priority`` tried first). A
        live model (Kimi/Qwen/Opus) is added exactly here — no other change."""
        chain = self._specs.setdefault(task, [])
        chain.append(spec)
        chain.sort(key=lambda s: s.priority)

    def specs_for(self, task: str) -> tuple[ModelSpec, ...]:
        """The priority-ordered fallback chain for a task (empty if none)."""
        return tuple(self._specs.get(task, ()))

    def has_task(self, task: str) -> bool:
        return bool(self._specs.get(task))

    # ------------------------------------------------------------- routing

    def _client_for(self, spec: ModelSpec) -> ModelClient:
        key = id(spec)
        client = self._clients.get(key)
        if client is None:
            client = spec.factory()  # may raise -> caught by fallback in complete()
            self._clients[key] = client
        return client

    def complete(
        self,
        task: str,
        prompt: str,
        schema: Optional[str] = None,
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> ModelResponse:
        """Route ``prompt`` for ``task`` to the primary spec, falling back across
        the chain on failure. Per-call ``temperature``/``max_tokens`` override the
        spec defaults. Raises :class:`KeyError` if the task is unregistered and
        :class:`RouterExhausted` if every spec in the chain fails."""
        chain = self._specs.get(task)
        if not chain:
            raise KeyError(f"no ModelSpec registered for task {task!r}")
        last_exc: Optional[BaseException] = None
        for spec in chain:
            req = ModelRequest(
                task=task,
                prompt=prompt,
                schema=schema,
                temperature=spec.temperature if temperature is None else temperature,
                max_tokens=spec.max_tokens if max_tokens is None else max_tokens,
            )
            try:
                client = self._client_for(spec)  # construction may fail (no key)
                resp = client.propose(req)
            except Exception as exc:  # noqa: BLE001 — any adapter failure => fallback
                last_exc = exc
                # drop a half-constructed/failed client so a later retry re-builds
                self._clients.pop(id(spec), None)
                continue
            # success: notify the observer (recording only; never alters resp).
            if self._observer is not None:
                self._observer(task, spec, resp, prompt)
            return resp
        raise RouterExhausted(
            f"all {len(chain)} spec(s) for task {task!r} failed; last error: {last_exc!r}"
        ) from last_exc


# --------------------------------------------------------------------------
# Default keyless router
# --------------------------------------------------------------------------

#: the task names the keyless DE layer routes (plan §B prompt registry tasks).
DEFAULT_TASKS = ("join", "merge", "retype", "name_concept", "answer")


def default_router(
    handlers: Optional[dict[str, Callable[[ModelRequest], object]]] = None,
    *,
    observer: Optional[Observer] = None,
) -> ModelRouter:
    """A router pre-registered with the deterministic ``HeuristicAdapter`` as the
    default (priority 0, tier=deterministic) spec for every DE task — keyless,
    runs today. ``handlers`` maps task -> deterministic handler; an absent task
    falls through to a no-op echo handler so the router is always usable.

    ``observer`` is the optional observation-loop sink (default None => behaves
    exactly as before): pass ``make_observer(obs_log, library)`` to record one
    :class:`~ontoforge.aimodels.observation.Observation` per ``complete()`` call.
    """
    base: dict[str, Callable[[ModelRequest], object]] = {}
    if handlers:
        base.update(handlers)

    def _echo(req: ModelRequest) -> str:
        # deterministic, content-free placeholder; real handlers override per task
        return req.prompt

    for task in DEFAULT_TASKS:
        base.setdefault(task, _echo)

    shared = HeuristicAdapter(base)
    router = ModelRouter(observer=observer)
    for task in base:
        router.register_model(
            task,
            ModelSpec(factory=lambda c=shared: c, model_id="heuristic", tier="deterministic"),
        )
    return router


# --------------------------------------------------------------------------
# Observation loop wiring (LIVING prompt library, plan §3)
# --------------------------------------------------------------------------


def _extract_decision_confidence(resp: ModelResponse) -> tuple[str, float]:
    """Defensively read ``(decision, confidence)`` from a :class:`ModelResponse`.

    The structured decision lives in ``resp.parsed`` (the schema-validated object
    the adapter produced). If ``parsed`` is a mapping carrying ``decision`` /
    ``confidence`` we use them, coercing types safely; otherwise we fall back to
    ``decision=""`` and ``confidence=0.0`` so a non-decision task (name_concept,
    answer) or an unparsed response never raises. Never inspects free-text.
    """
    decision = ""
    confidence = 0.0
    parsed = resp.parsed
    if isinstance(parsed, dict):
        raw_decision = parsed.get("decision")
        if isinstance(raw_decision, str):
            decision = raw_decision
        raw_conf = parsed.get("confidence")
        if isinstance(raw_conf, (int, float)) and not isinstance(raw_conf, bool):
            confidence = float(raw_conf)
    return decision, confidence


def make_observer(obs_log: "ObservationLog", library: "PromptLibrary") -> Observer:
    """Build an :data:`Observer` that records each successful propose.

    The returned callable — invoked by the router as
    ``observer(task, spec, response, prompt)`` — derives ``decision`` and
    ``confidence`` defensively from the real :class:`ModelResponse` fields,
    computes the stable ``input_fingerprint`` from the sent ``prompt``, looks up
    the task's current champion version from ``library``, and appends an
    :class:`~ontoforge.aimodels.observation.Observation` to ``obs_log``.

    Keyless and deterministic: it drives the ``HeuristicAdapter`` path with no key
    and no wall-clock (seq is assigned by the log). A task that the library does
    not know is recorded with ``version=""`` rather than raising.
    """
    from .observation import fingerprint_prompt

    def _observer(task: str, spec: "ModelSpec", response: ModelResponse, prompt: str) -> None:
        try:
            version = library.champion(task)
        except KeyError:
            version = ""
        decision, confidence = _extract_decision_confidence(response)
        obs_log.append(
            task=task,
            version=version,
            input_fingerprint=fingerprint_prompt(prompt),
            model_id=response.model_id,
            tier=spec.tier,
            decision=decision,
            confidence=confidence,
        )

    return _observer
