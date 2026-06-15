# AI-native architecture (realized)

OntoForge ships an AI-native data-engineering layer that runs **keyless and
deterministic today** — no API key, no LLM call, zero network in tests — while the
live-model path is stubbed and ready to drop in with **zero rework**. This document
describes the realized architecture and how to register a live model later.

Everything routes through the FROZEN `contracts.models.ModelClient` seam
(`ModelRequest`/`ModelResponse`). The new code is two packages — `ontoforge.aimodels`
(the scaffolding) and `ontoforge.ensemble` (the decision gate) — plus a thin wiring
into `engineer/operators.py`. The decision spine (`ontoforge.spine`) is untouched;
the gate mirrors its calibration philosophy specialized to DE-action gating.

---

## 1. Provider router — `aimodels/router.py`

`ModelRouter` selects a model per task and falls back across alternatives.

* A **`ModelSpec`** carries a lazy `factory` (a zero-arg callable returning a
  `ModelClient`), `model_id`, `tier ∈ {deterministic, fast, frontier}`,
  `temperature`, `max_tokens`, and a `priority` (ascending = tried first).
* `register_model(task, spec)` appends a spec to a task's chain (kept sorted by
  priority). Multiple specs per task form the **fallback chain**.
* `complete(task, prompt, schema?)` routes to the primary spec and, on **any**
  adapter failure (including a factory that can't construct because a key is
  absent), moves to the next spec in priority order. If all fail it raises
  `RouterExhausted`. **Fallback is implemented and tested explicitly** — the
  research claim that routing libraries give automatic fallback for free was
  refuted, so we never assume it.
* `default_router()` pre-registers the deterministic `HeuristicAdapter`
  (`tier=deterministic`) as the primary spec for every DE task
  (`join`, `merge`, `retype`, `name_concept`, `answer`). **No key at import or run.**

## 2. Prompts — `aimodels/prompts.py`

Versioned, task-scoped `PromptTemplate`s. Each carries a stable `task@version`, a
JSON **schema** for constrained output (handed to `ModelRequest.schema`), few-shot
example slots, and an **ontology-grounding slot** that injects only the relevant
class/property subset. `render(task, input, grounding?, extra?)` returns the prompt
and schema deterministically (equal inputs → byte-identical prompt).

## 3. Context / schema linking — `aimodels/context.py`

`link_schema(ontology, focus_class, focus_columns, budget)` fits a large induced
ontology into a token budget by **extractive** (select, never generate),
**bidirectional** relevance pruning: forward (lexical similarity + the focus
class's own props) and backward (forward link ranges, and classes linking *into*
the focus). Deterministic lexical + structural relevance — no embeddings, no
network. On the synthetic 200-property ontology in `tests/aimodels/test_context.py`
it achieves **100% recall of the known-needed set at ~90% pruning** (target:
≥90% recall at ≥70% pruning). `render_grounding` emits the compact grounding block.

## 4. Secure data handling — `aimodels/secure.py`

Prompt injection is inherent and unsolved, so we defend **architecturally**:

* `redact_pii(text)` — emails / phones / SSN-like / card-like ids + a gazetteer of
  names → typed placeholders, before any external call. Deterministic.
* `sample_rows(frame, k, stratify_by)` — a small **stratified** sample (every
  stratum represented before any is over-represented); never bulk rows.
* `wrap_untrusted(text)` — spotlights ingested text out of the instruction channel
  with explicit fences and neutralizes break-out attempts.
* `scan_injection(text) → risk` — heuristic risk score; `>= INJECTION_RISK_THRESHOLD`
  is treated as positive. The test reports FPR/FNR on a small labeled set.

---

## 5. The DE decision gate — `ensemble/gate.py` + `ensemble/experts.py`

**The headline.** `Gate.decide(action_ctx, verifier?)` decides whether a
data-engineering action (join / merge / retype) should **fire**, via weighted
voting over experts. Order of operations inside `decide`:

1. **Execution-grounded verification FIRST (the veto).** The `verifier` callback —
   in the engineer wiring, the live join-coverage floor — can **VETO** regardless
   of votes. The data refusing a join overrides any unanimous "fire". This is the
   confidently-wrong guard for the gate; the experts only *propose*.
2. **Per-expert Weighted-Majority Aggregation (WMA).** Each expert starts at weight
   1.0; its confidence-shaped weight is summed into the side it voted; the larger
   summed weight wins. **Weighting is per-expert (per-model), not per-temperature** —
   the research-correct axis (Littlestone–Warmuth).
3. **TURN aggregation temperature (label-free).** `turn_temperature` picks a near-
   optimal aggregation softness from the spread of the experts' confidences (an
   entropy turning-point) — sharpens a confident, agreeing ensemble; softens a split
   one. Temperature is a **calibrated lever**, not the primary axis.
4. **Soft-Self-Consistency for sparse actions.** The single specific action's "fire"
   side is scored continuously by min/mean/product of the agreeing experts'
   confidences and gated on a calibrated probability `threshold` — not exact-match
   majority. **High-temperature single-model self-consistency is deliberately
   avoided**: it adds hallucination, not useful diversity; cross-expert voting at
   low temperature is the default.
5. **Self-improving weights.** `Gate.update_weights(action_ctx, confirmed_fire)`
   applies the multiplicative penalty `(1 - ε)` with `ε = √(ln N / T)` to every
   expert that voted against a later human-confirmed outcome — the provable-regret
   rule, wired to the review queue's Confirm/Reject. The system gets more autonomous
   as the lazy user clicks ✓/✗.

`decide` returns a `GateDecision {fire, confidence, tally, weights, votes, vetoed,
veto_reason, threshold, soft_score, temperature}` and `to_provenance()` for the
ledger. **Keyless today:** the four default experts
(`CoverageExpert`, `ValueOverlapExpert`, `NameSimilarityExpert`, `TypeCompatExpert`)
are deterministic rule-variants over the action context; the WMA math, threshold,
TURN, Soft-SC, and veto all run with zero model calls.

## 6. Engineer integration — `engineer/operators.py`

`EngineerService.apply` for a link op (`AddProperty` with a `range_class`):

1. re-measures coverage from the live HEARTH and **hard-refuses below
   `JOIN_LIKELY_FLOOR`** (unchanged; the existing confidently-wrong guard);
2. then consults `self.gate.decide(ctx, verifier=floor_verifier)` — the gate ADDS a
   weighted-vote decision on top of the floor and **never weakens it**;
3. a **held** action (gate did not fire, or vetoed) is reported as
   `ok=False, deferred=True` (sent to review) — never applied;
4. the vote tally + per-expert weights + every expert's vote are recorded as
   **provenance** (`result["gate"]` and a durable ledger artifact payload) so a
   gated action can answer *"why did this join fire?"*.

Existing preview-then-confirm and exact TEMPER undo are preserved.

---

## Registering a live model (Kimi K2 / Qwen / Claude Opus)

Adding a live model is registering a `ModelSpec` — the routing/fallback and the
gate math are **unchanged**.

```python
from ontoforge.aimodels import default_router, ModelSpec
from ontoforge.ledger.models import AnthropicAdapter  # or an OpenAI-compatible adapter

router = default_router()  # keyless deterministic baseline

# higher-priority frontier spec; the lazy factory only runs when a key is present
router.register_model(
    "join",
    ModelSpec(
        factory=lambda: AnthropicAdapter(model_id="claude-opus-4-8"),  # raises w/o key -> fallback
        model_id="claude-opus-4-8",
        tier="frontier",
        temperature=0.0,
        priority=-1,  # tried before the deterministic default
    ),
)
# If ANTHROPIC_API_KEY is unset, the factory raises and the router falls back to
# the deterministic HeuristicAdapter — still keyless, still deterministic.
```

To make a live model an **expert** in the gate, wrap a `router.complete("join",
prompt, schema)` call in an object exposing `.name` and `.vote(ctx) -> Vote` (parse
the model's constrained JSON `{decision, confidence, rationale}` from `prompts.py`'s
`join` schema). Pass it alongside the deterministic experts to `Gate(...)`. Cross-
model voting at temperature ≈ 0 is the recommended default; the veto, WMA, TURN,
Soft-SC, and weight-update behaviour are identical whether experts are heuristic or
live.

For OpenAI-compatible providers (Kimi K2, Qwen via an inference endpoint), supply an
adapter implementing `ModelClient.propose` (the `AnthropicAdapter` is the reference
pattern — raw stdlib HTTP, constructed only when a key is present) and register it
exactly as above.

> **Invariants** (test-enforced): keyless at import/run; zero network in tests;
> the coverage floor is never weakened — the gate only adds a decision and the
> verifier veto can only make the gate *more* conservative, never less.
