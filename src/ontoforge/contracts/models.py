"""Model abstraction layer (MVP plan §5.2, whitepaper §11.1 T3 access).

ALL T2/T3 calls go through ModelClient. Three adapters (AMD-0002):

- HeuristicAdapter  — deterministic rule-based proposer; always available; zero tokens.
- CassetteAdapter   — record/replay (§18.4 item 4): deterministic CI, zero live calls.
- AnthropicAdapter  — live frontier calls; constructed only when ANTHROPIC_API_KEY set.

Implementations live in `ontoforge.ledger.models` (M0 wave); this contract is what
every escalating module imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol


@dataclass(frozen=True, slots=True)
class ModelRequest:
    task: str                      # stable task name, e.g. "strata.name_concept"
    prompt: str
    schema: Optional[str] = None   # JSON schema (serialized) for constrained output
    temperature: float = 0.0
    max_tokens: int = 1024


@dataclass(slots=True)
class ModelResponse:
    text: str
    parsed: Any = None             # schema-validated object when schema was given
    input_tokens: int = 0
    output_tokens: int = 0
    model_id: str = "heuristic"
    cached: bool = False

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class ModelClient(Protocol):
    def propose(self, req: ModelRequest) -> ModelResponse: ...


@dataclass(slots=True)
class CostMeter:
    """Per-operation cost counters wired to the ledger (§18.4 item 5)."""

    tokens_by_task: dict[str, int] = field(default_factory=dict)
    calls_by_task: dict[str, int] = field(default_factory=dict)

    def record(self, task: str, tokens: int) -> None:
        self.tokens_by_task[task] = self.tokens_by_task.get(task, 0) + tokens
        self.calls_by_task[task] = self.calls_by_task.get(task, 0) + 1

    @property
    def total_tokens(self) -> int:
        return sum(self.tokens_by_task.values())
