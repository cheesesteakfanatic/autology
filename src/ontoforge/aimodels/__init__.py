"""AI-native scaffolding: keyless-deterministic today, LLM-layerable later.

The four pillars (docs/AI_NATIVE_AND_UI_PLAN.md §A–§C), all built ON the frozen
``contracts.models.ModelClient`` seam with zero network and no API key required at
import or run:

* :mod:`router`  — task-scoped :class:`~router.ModelSpec` registry over
  ``ModelClient`` with EXPLICIT priority-ordered fallback. Default tier is the
  deterministic ``HeuristicAdapter``; live models (Kimi/Qwen/Opus) register later.
* :mod:`prompts` — versioned, task-scoped templates with constrained JSON-schema
  output, few-shot slots, and an ontology-grounding slot.
* :mod:`context` — extractive, bidirectional schema linking to fit a large induced
  ontology into a token budget.
* :mod:`secure`  — PII redaction, stratified sampling, untrusted-text spotlighting,
  and injection scanning (defend architecturally; injection is unsolved).
"""

from .activation import ActiveModel, model_status, resolve_client
from .context import LinkedSchema, SchemaElement, link_schema, render_grounding
from .library import PromptLibrary
from .observation import Observation, ObservationLog
from .openai_compat import OpenAICompatAdapter
from .prompts import PROMPTS, FewShot, PromptTemplate, get_prompt, render
from .router import (
    ModelRouter,
    ModelSpec,
    RouterExhausted,
    default_router,
    make_observer,
)
from .secure import (
    INJECTION_RISK_THRESHOLD,
    redact_pii,
    sample_rows,
    scan_injection,
    wrap_untrusted,
)
from .secure_client import SecureModelClient
from .validate import ValidatingModelClient, validate_against_schema

__all__ = [
    "INJECTION_RISK_THRESHOLD",
    "ActiveModel",
    "FewShot",
    "LinkedSchema",
    "ModelRouter",
    "ModelSpec",
    "Observation",
    "ObservationLog",
    "OpenAICompatAdapter",
    "PROMPTS",
    "PromptLibrary",
    "PromptTemplate",
    "RouterExhausted",
    "SchemaElement",
    "SecureModelClient",
    "ValidatingModelClient",
    "default_router",
    "get_prompt",
    "link_schema",
    "make_observer",
    "model_status",
    "redact_pii",
    "render",
    "render_grounding",
    "resolve_client",
    "sample_rows",
    "scan_injection",
    "validate_against_schema",
    "wrap_untrusted",
]
