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

from .context import LinkedSchema, SchemaElement, link_schema, render_grounding
from .library import PromptLibrary
from .observation import Observation, ObservationLog
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

__all__ = [
    "INJECTION_RISK_THRESHOLD",
    "FewShot",
    "LinkedSchema",
    "ModelRouter",
    "ModelSpec",
    "Observation",
    "ObservationLog",
    "PROMPTS",
    "PromptLibrary",
    "PromptTemplate",
    "RouterExhausted",
    "SchemaElement",
    "default_router",
    "get_prompt",
    "link_schema",
    "make_observer",
    "redact_pii",
    "render",
    "render_grounding",
    "sample_rows",
    "scan_injection",
    "wrap_untrusted",
]
