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
from typing import Callable, Optional

from ontoforge.contracts.models import ModelClient, ModelRequest, ModelResponse
from ontoforge.ledger.models import HeuristicAdapter

__all__ = [
    "TIERS",
    "ModelRouter",
    "ModelSpec",
    "RouterExhausted",
    "default_router",
]

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

    def __init__(self) -> None:
        # task -> list[ModelSpec] kept sorted by priority (ascending)
        self._specs: dict[str, list[ModelSpec]] = {}
        # memoized constructed clients keyed by id(spec) (lazy, per-spec)
        self._clients: dict[int, ModelClient] = {}

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
                return client.propose(req)
            except Exception as exc:  # noqa: BLE001 — any adapter failure => fallback
                last_exc = exc
                # drop a half-constructed/failed client so a later retry re-builds
                self._clients.pop(id(spec), None)
                continue
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
) -> ModelRouter:
    """A router pre-registered with the deterministic ``HeuristicAdapter`` as the
    default (priority 0, tier=deterministic) spec for every DE task — keyless,
    runs today. ``handlers`` maps task -> deterministic handler; an absent task
    falls through to a no-op echo handler so the router is always usable."""
    base: dict[str, Callable[[ModelRequest], object]] = {}
    if handlers:
        base.update(handlers)

    def _echo(req: ModelRequest) -> str:
        # deterministic, content-free placeholder; real handlers override per task
        return req.prompt

    for task in DEFAULT_TASKS:
        base.setdefault(task, _echo)

    shared = HeuristicAdapter(base)
    router = ModelRouter()
    for task in base:
        router.register_model(
            task,
            ModelSpec(factory=lambda c=shared: c, model_id="heuristic", tier="deterministic"),
        )
    return router
