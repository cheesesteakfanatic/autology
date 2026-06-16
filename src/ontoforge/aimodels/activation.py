"""activation — the SINGLE seam where env -> live-client resolution happens.

This is the ONLY place (besides ``ledger/models.py``, which reads
``ANTHROPIC_API_KEY`` inside ``AnthropicAdapter``) that reads model-provider env at
runtime. Setting an API key is the ONLY change needed to put a live model behind the
engine: every call-site already builds a deterministic fallback and passes it to
:func:`resolve_client`; with no provider env that fallback is returned UNCHANGED
(keyless byte-identical path), and with a provider + key it is wrapped in the
safety/validation/fallback chain below.

Deterministic resolution (scout design):

1. read ``ONTOFORGE_MODEL_PROVIDER`` in ``{'', 'anthropic', 'openai', 'moonshot',
   'qwen'}``. Empty/unset -> return the passed-in deterministic fallback UNCHANGED.
2. ``anthropic`` requires ``ANTHROPIC_API_KEY`` -> ``AnthropicAdapter``;
   ``openai``/``moonshot``/``qwen`` require the matching key + a base URL (provider
   default if ``OPENAI_BASE_URL`` unset) -> ``OpenAICompatAdapter``.
3. provider set but key MISSING -> return the deterministic fallback, never raise
   (lazy-user fail-safe to keyless).
4. wrap the LIVE adapter (never the fallback), innermost-first:
   ``ValidatingModelClient(SecureModelClient(live), fallback=deterministic_fallback)``.
   ``SecureModelClient`` redacts PII + spotlights + refuses high-injection prompts
   BEFORE the send; ``ValidatingModelClient`` validates against ``req.schema``,
   retries deterministically, then degrades to the deterministic fallback.
5. compose as a 2-spec :class:`ModelRouter` chain: priority 0 = the wrapped live
   client, priority 1 = the deterministic fallback. ANY live exception (429/5xx/
   timeout/``RouterExhausted``) falls through to byte-identical deterministic
   behavior. The returned object is a thin ``ModelClient`` adapter over that router.

Keyless and offline by default; NEVER constructs a live adapter without a key and
NEVER constructs one at import (no module-level env reads).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from ontoforge.contracts.models import ModelClient, ModelRequest, ModelResponse

from .router import ModelRouter, ModelSpec
from .secure_client import SecureModelClient
from .validate import ValidatingModelClient

__all__ = [
    "PROVIDERS",
    "ActiveModel",
    "model_status",
    "resolve_client",
]

#: number of deterministic schema retries the ValidatingModelClient attempts before
#: degrading to the deterministic fallback.
SCHEMA_RETRIES = 1

#: env var selecting the provider; empty/unset => keyless deterministic path.
PROVIDER_ENV = "ONTOFORGE_MODEL_PROVIDER"

#: recognized providers; "" (the default) means keyless/deterministic.
PROVIDERS = ("", "anthropic", "openai", "moonshot", "qwen")

#: per-provider (key_env, base_url_default, model_default). ``anthropic`` is special
#: (its own adapter + key handling), the rest share the OpenAI-compatible adapter.
_OPENAI_COMPAT: dict[str, tuple[str, str, str]] = {
    "openai": ("OPENAI_API_KEY", "https://api.openai.com/v1", "gpt-4o-mini"),
    "moonshot": ("MOONSHOT_API_KEY", "https://api.moonshot.cn/v1", "kimi-k2-0905-preview"),
    "qwen": ("QWEN_API_KEY", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-max"),
}

#: explicit base-url override (provider-agnostic); falls back to the provider default.
BASE_URL_ENV = "OPENAI_BASE_URL"
#: explicit model-id override (provider-agnostic); falls back to the provider default.
MODEL_ID_ENV = "ONTOFORGE_MODEL_ID"


@dataclass(frozen=True, slots=True)
class ActiveModel:
    """Observability summary of which model backs a resolved client."""

    provider: str          # "" when keyless/deterministic
    model_id: str          # adapter model id, or "heuristic" when keyless
    live: bool             # True iff a live adapter is wired as priority 0
    reason: str            # human-readable: why this resolution happened

    @property
    def label(self) -> str:
        return self.provider if self.live else "deterministic/keyless"


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _build_live_adapter(provider: str) -> Optional[ModelClient]:
    """Construct the LIVE adapter for ``provider`` from env, or ``None`` when the
    required key is missing. NEVER raises on a missing key (lazy-user fail-safe)."""
    if provider == "anthropic":
        if not _env("ANTHROPIC_API_KEY"):
            return None
        # import lazily so a missing key never trips construction at module import.
        from ontoforge.ledger.models import AnthropicAdapter

        model_id = _env(MODEL_ID_ENV) or AnthropicAdapter.DEFAULT_MODEL
        return AnthropicAdapter(model_id=model_id)

    if provider in _OPENAI_COMPAT:
        key_env, base_default, model_default = _OPENAI_COMPAT[provider]
        api_key = _env(key_env)
        if not api_key:
            return None
        from .openai_compat import OpenAICompatAdapter

        base_url = _env(BASE_URL_ENV) or base_default
        model_id = _env(MODEL_ID_ENV) or model_default
        return OpenAICompatAdapter(base_url=base_url, api_key=api_key, model_id=model_id)

    return None


def _wrap_live(live: ModelClient, deterministic_fallback: ModelClient) -> ModelClient:
    """Innermost-first safety chain: secure egress, then schema-validate+degrade."""
    secure = SecureModelClient(live)
    return ValidatingModelClient(
        secure, fallback=deterministic_fallback, schema_retries=SCHEMA_RETRIES
    )


class _RoutedClient:
    """Thin ``ModelClient`` over a :class:`ModelRouter` for a fixed task.

    Priority 0 is the wrapped live client; priority 1 is the deterministic fallback.
    Any live exception (incl. ``RouterExhausted`` when the chain is empty) falls
    through to the deterministic fallback so behavior degrades to byte-identical
    keyless output rather than raising.
    """

    __slots__ = ("_router", "_task", "_fallback", "active")

    def __init__(
        self,
        router: ModelRouter,
        task: str,
        fallback: ModelClient,
        active: ActiveModel,
    ) -> None:
        self._router = router
        self._task = task
        self._fallback = fallback
        self.active = active

    def propose(self, req: ModelRequest) -> ModelResponse:
        try:
            return self._router.complete(
                self._task,
                req.prompt,
                schema=req.schema,
                temperature=req.temperature,
                max_tokens=req.max_tokens,
            )
        except Exception:  # noqa: BLE001 — exhausted/unknown task degrades to keyless
            return self._fallback.propose(req)


def resolve_client(task: str, *, fallback: ModelClient) -> ModelClient:
    """Resolve the ``ModelClient`` for ``task`` from env.

    With NO provider env (or a missing key) returns ``fallback`` UNCHANGED — the
    SAME object the call-site built — so the keyless path is byte-identical and no
    decorator/router ever runs. With a provider + key, returns a router-backed client
    whose priority-0 spec is ``ValidatingModelClient(SecureModelClient(live),
    fallback=fallback)`` and whose tail is ``fallback``.
    """
    provider = _env(PROVIDER_ENV).lower()
    if not provider:
        return fallback  # IDENTITY: keyless byte-identical path
    if provider not in PROVIDERS:
        return fallback  # unrecognized provider -> fail-safe to keyless

    live = _build_live_adapter(provider)
    if live is None:
        return fallback  # key missing -> never raise, fall back to keyless

    wrapped = _wrap_live(live, fallback)
    model_id = getattr(live, "model_id", provider)
    active = ActiveModel(
        provider=provider,
        model_id=model_id,
        live=True,
        reason=f"provider={provider!r} with key resolved to a live adapter",
    )

    router = ModelRouter()
    router.register_model(
        task,
        ModelSpec(factory=lambda c=wrapped: c, model_id=model_id, tier="frontier", priority=0),
    )
    router.register_model(
        task,
        ModelSpec(factory=lambda c=fallback: c, model_id="heuristic", tier="deterministic", priority=1),
    )
    return _RoutedClient(router, task, fallback, active)


def model_status(client: Optional[ModelClient] = None) -> ActiveModel:
    """Observability summary of the active model.

    With ``client`` given, reports what backs THAT resolved client (live provider +
    model id, or deterministic when it is a bare fallback). With no argument, reports
    what the CURRENT env WOULD resolve to (without constructing a live adapter): the
    active provider if a provider + key are present, else ``deterministic/keyless``.
    """
    if client is not None:
        active = getattr(client, "active", None)
        if isinstance(active, ActiveModel):
            return active
        return ActiveModel(
            provider="",
            model_id=getattr(client, "model_id", "heuristic"),
            live=False,
            reason="client is a bare deterministic fallback (keyless)",
        )

    provider = _env(PROVIDER_ENV).lower()
    if not provider or provider not in PROVIDERS:
        return ActiveModel(
            provider="",
            model_id="heuristic",
            live=False,
            reason="no provider set; keyless deterministic path",
        )
    # report intent without building a live adapter (and without a network call).
    if provider == "anthropic":
        has_key = bool(_env("ANTHROPIC_API_KEY"))
        model_id = _env(MODEL_ID_ENV) or "claude"
    else:
        key_env, _base, model_default = _OPENAI_COMPAT.get(provider, ("", "", provider))
        has_key = bool(_env(key_env))
        model_id = _env(MODEL_ID_ENV) or model_default
    if not has_key:
        return ActiveModel(
            provider="",
            model_id="heuristic",
            live=False,
            reason=f"provider={provider!r} set but key missing; fell back to keyless",
        )
    return ActiveModel(
        provider=provider,
        model_id=model_id,
        live=True,
        reason=f"provider={provider!r} with key would resolve to a live adapter",
    )
