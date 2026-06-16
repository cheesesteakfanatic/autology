"""SecureModelClient — the enforced egress boundary for LIVE model calls.

docs/AI_NATIVE_AND_UI_PLAN.md §C: defend ARCHITECTURALLY. This decorator wraps a
*live* ``ModelClient`` so that raw customer values can NEVER leave the process
unredacted, and ingested/untrusted text can never occupy the instruction channel.
Before delegating ``propose()`` to the inner (live) adapter it, in order:

1. **redacts PII** from ``req.prompt`` (:func:`ontoforge.aimodels.secure.redact_pii`)
   — emails / phones / SSNs / cards / gazetteer names become typed placeholders;
2. **scans for prompt-injection** (:func:`scan_injection`) and, when the risk is at
   or above :data:`INJECTION_RISK_THRESHOLD`, **refuses** the live call (fail-closed:
   returns an abstaining :class:`ModelResponse` with empty ``parsed`` rather than
   forwarding a hijacked prompt to the model);
3. otherwise **spotlights** the (now-redacted) prompt with :func:`wrap_untrusted` so
   the model is told the body is DATA, not instructions, before the send.

PARITY: this wrapper is constructed ONLY around a live adapter (see
:mod:`ontoforge.aimodels.activation`). The deterministic ``HeuristicAdapter`` /
``CassetteAdapter`` keyless path is never wrapped, so its ``propose()`` inputs and
outputs stay byte-identical. The decorator itself is deterministic and keyless
(it constructs no client and reads no env).
"""

from __future__ import annotations

from dataclasses import replace

from ontoforge.contracts.models import ModelClient, ModelRequest, ModelResponse

from .secure import (
    INJECTION_RISK_THRESHOLD,
    redact_pii,
    scan_injection,
    wrap_untrusted,
)

__all__ = ["SecureModelClient"]


class SecureModelClient:
    """Decorator enforcing the egress boundary around a LIVE ``ModelClient``.

    Wrap ONLY live adapters — the keyless deterministic path must remain a bare
    pass-through. ``inner`` is the live client the redacted/spotlighted request is
    forwarded to. ``injection_threshold`` is the risk at/above which the live call
    is refused (default :data:`INJECTION_RISK_THRESHOLD`); ``spotlight`` toggles the
    :func:`wrap_untrusted` fence (on by default).
    """

    __slots__ = ("_inner", "_threshold", "_spotlight", "_label")

    def __init__(
        self,
        inner: ModelClient,
        *,
        injection_threshold: float = INJECTION_RISK_THRESHOLD,
        spotlight: bool = True,
        label: str = "prompt",
    ) -> None:
        self._inner = inner
        self._threshold = float(injection_threshold)
        self._spotlight = bool(spotlight)
        self._label = label

    @property
    def inner(self) -> ModelClient:
        return self._inner

    def propose(self, req: ModelRequest) -> ModelResponse:
        """Redact + scan + spotlight ``req.prompt`` BEFORE delegating to the live
        inner adapter. On a high injection-risk prompt the live call is REFUSED
        (fail-closed abstention) and the model never sees the prompt."""
        # 1. redact raw customer values — runs BEFORE anything leaves the process.
        redacted = redact_pii(req.prompt)

        # 2. scan the (original) prompt for injection; refuse above the threshold.
        #    Scan the raw text so redaction placeholders never mask an attack.
        risk = scan_injection(req.prompt)
        if risk >= self._threshold:
            # fail-closed: do NOT forward a hijacked prompt to the live model.
            return ModelResponse(
                text="",
                parsed=None,
                input_tokens=0,
                output_tokens=0,
                model_id="secure-refused",
            )

        # 3. spotlight the redacted prompt out of the instruction channel.
        safe_prompt = wrap_untrusted(redacted, label=self._label) if self._spotlight else redacted

        safe_req = replace(req, prompt=safe_prompt)
        return self._inner.propose(safe_req)
