# LLM readiness — the operator guide

**One sentence:** setting an API key is the **only** change needed to put a live
frontier model (Kimi/Moonshot, Qwen, or Anthropic) behind the engine. Everything
the live path needs — safety redaction, prompt-injection refusal, structured-output
validation, deterministic retry, fallback, and observability — is already built,
wired, and **proven deterministically with zero network calls**.

The platform is keyless, deterministic, and offline by default. This document
describes the seam that lights up when a key is present, and how to verify it.

---

## 1. The one change: which env var activates which provider

The single seam that reads provider env at runtime is
`src/ontoforge/aimodels/activation.py` (`resolve_client`). Set
`ONTOFORGE_MODEL_PROVIDER` plus the matching API key and the engine routes T2/T3
escalations and `lodestone.generate` through a live model. Nothing else changes —
no code edit, no rebuild.

| Provider | `ONTOFORGE_MODEL_PROVIDER` | API key env | Adapter | Default model | Default base URL |
|----------|---------------------------|-------------|---------|---------------|------------------|
| **Kimi / Moonshot** | `moonshot` | `MOONSHOT_API_KEY` | `OpenAICompatAdapter` | `kimi-k2-0905-preview` | `https://api.moonshot.cn/v1` |
| **Qwen** | `qwen` | `QWEN_API_KEY` | `OpenAICompatAdapter` | `qwen-max` | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| **OpenAI** | `openai` | `OPENAI_API_KEY` | `OpenAICompatAdapter` | `gpt-4o-mini` | `https://api.openai.com/v1` |
| **Anthropic** | `anthropic` | `ANTHROPIC_API_KEY` | `AnthropicAdapter` | `claude-sonnet-4-6` | `https://api.anthropic.com/v1/messages` |

Optional provider-agnostic overrides:

| Env var | Effect |
|---------|--------|
| `ONTOFORGE_MODEL_ID` | override the model id for any provider |
| `OPENAI_BASE_URL` | override the base URL for the OpenAI-compatible adapter (any OpenAI-API-shaped endpoint: vLLM, Together, Fireworks, a self-hosted gateway, …) |

Example — put Kimi behind the whole engine:

```bash
export ONTOFORGE_MODEL_PROVIDER=moonshot
export MOONSHOT_API_KEY=sk-...                 # the ONLY new thing
uv run ontoforge demo meridian /tmp/mcm_demo   # induction, ER, and ask now go live
uv run ontoforge ask -p /tmp/mcm_demo "free-text question"
```

**Lazy-user fail-safe.** If `ONTOFORGE_MODEL_PROVIDER` is set but the matching key
is *missing*, the seam does **not** raise — it silently returns the deterministic
keyless path. An unrecognized provider does the same. You can never half-activate
into a crash.

---

## 2. What the live path is wrapped in (built, not TODO)

`resolve_client(task, fallback=<deterministic>)` returns:

- **keyless** (no provider env / missing key): the `fallback` object **unchanged**
  — the same deterministic `HeuristicAdapter` the call-site built. No decorator,
  no router, byte-identical behavior.
- **live** (provider + key): a router-backed client whose **priority-0** spec is

  ```
  ValidatingModelClient( SecureModelClient( <live adapter> ), fallback=<deterministic> )
  ```

  and whose **priority-1** spec is the deterministic fallback itself.

Innermost-first, on every live `propose()`:

1. **`SecureModelClient`** — the enforced egress boundary
   (`aimodels/secure_client.py`). Before any byte leaves the process it:
   - **redacts PII** — emails / phones / SSNs / cards / gazetteer names become typed
     placeholders (`[EMAIL]`, `[PHONE]`, `[NAME]`, …);
   - **scans for prompt-injection** and, at or above the risk threshold (0.5),
     **refuses** — returns an abstaining response, the live model never sees the
     hijacked prompt (fail-closed);
   - **spotlights** the redacted prompt with `wrap_untrusted` so the body sits in a
     data fence, never the instruction channel.
2. **`ValidatingModelClient`** — structured-output guard (`aimodels/validate.py`).
   Validates `resp.parsed` (or salvaged JSON) against `req.schema`; on failure it
   **retries once deterministically (temperature 0)**, then **degrades to the
   deterministic fallback**. A malformed or hallucinated live response can never
   crash or corrupt a decision.
3. **Router fallback** — any live exception (429/5xx/timeout, `RouterExhausted`,
   anything) falls through `_RoutedClient.propose` to the deterministic fallback.
   The worst a broken live model can do is make the engine behave exactly as it does
   keyless.

The live adapters themselves (`AnthropicAdapter`, `OpenAICompatAdapter`) POST via
stdlib `urllib` with **bounded deterministic retry** (`max_retries`/`backoff`/
injectable `sleep`, no jitter) over transient HTTP/timeout failures, and request
JSON-object output when a schema is set. They construct **only** when the key is
present and **never** at import — no module-level env reads.

### Where the live model reaches the engine

`resolve_client` is threaded **inside the engine module constructors**, so every
existing call-site (CLI, `pipeline/induce.py`, `pipeline/playground.py`,
`pipeline/er_generic.py`, `server/world.py`) activates with one env change and **no
edit**, because they all rely on the module defaults:

| Module | Task | Seam |
|--------|------|------|
| **STRATA** (`strata/strata.py`) | `strata.name_concept` | `self.client = resolve_client("strata.name_concept", fallback=build_strata_client())` — this same client feeds STRATA's `DecisionSpine`, so **admission adjudication** goes live too. |
| **ER** (`er/cascade.py`) | `spine.adjudicate.er` | `ERCascade(model_client=…)` (new optional arg, defaults to the internal deterministic adjudicator) → `resolve_client("spine.adjudicate.er", fallback=that)`; feeds ER's `DecisionSpine`. |
| **LODESTONE** (`lodestone/__init__.py`) | `lodestone.generate` | `self.client = resolve_client(GENERATE_TASK, fallback=HeuristicAdapter({GENERATE_TASK: make_generate_handler(onto)}))`. `candidates.py` now sends a `GENERATE_SCHEMA` so the validating client has a contract to enforce on the live path. |

An **explicitly-passed** `model_client` (tests, custom embeddings) is always honored
as-is and never re-wrapped, so injection stays exact.

**Deferred by design (parity beats coverage):** the bare `DecisionSpine(model_client=
None)` constructed directly in `server/world.py` (for the served Ask path) and in
`lodestone.ask(...)`/`warden`/`anvil` is **not** threaded, because those sites have
no deterministic adjudicator to serve as the router's priority-1 fallback (a
`None`-fallback live client would crash on degrade). The LLM leverage for the served
Ask path is already live via the `Lodestone(...)` generate client; spine
adjudication there stays at T0/T1 exactly as today. Threading those would require
registering a deterministic spine-adjudicate fallback first — a separate, additive
change that does not block readiness.

---

## 3. Cost / deferral story (only escalates per the spine band)

A live model is **not** called on every decision. The `DecisionSpine`
(`spine/spine.py`) escalates to a `ModelClient` (T2/T3) **only** when the calibrated
confidence lands in the ambiguous band between `tau_low` and `tau_high` (widened by
decision impact). Confident decisions auto-resolve at T0/T1 with **zero tokens**.

- The **budget governor** admits each T2/T3 call against the remaining token budget
  with a conservative reservation; an over-budget call is **not made** and the
  decision returns `quarantined=True` (fail-closed, never a silent auto-decision).
- **CRUCIBLE** profile sets the budget shadow price to ~0 and widens the band, for
  high-stakes runs that want maximum escalation.
- Every call is metered through the `CostMeter` / ledger `cost` table, surfaced by
  the Observatory's `/api/compute-ledger`.

So the cost of going live scales with **ambiguity**, not with data volume: a clean
estate spends almost nothing; only the genuinely uncertain merges/links/queries
reach the frontier tier.

---

## 4. The keyless-default guarantee (parity is sacred)

With **no** provider env set:

- `resolve_client(task, fallback=f)` returns `f` **by object identity** (`is f`) —
  proven in `tests/integration/test_llm_dryrun.py::test_keyless_resolve_is_identity`;
- each engine constructor (`Strata`, `ERCascade`, `Lodestone`) holds the bare
  deterministic `HeuristicAdapter`, never a wrapper;
- no `SecureModelClient` / `ValidatingModelClient` / router code runs on the hot
  path;
- the full pipeline's answers (rows, citations, OQIR, confidence, abstention
  reasons) are **byte-identical** to the pre-wave deterministic output — proven in
  `…::test_keyless_pipeline_output_equals_deterministic_baseline`, which compares a
  keyless `Lodestone` against one built with the explicit pre-wave handler over
  every competency question, and **fails the test** if construction ever tries to
  build a live adapter keyless.

CI never sets a key, so the suite stays keyless, offline, and deterministic. The
only clients ever constructed in tests are `HeuristicAdapter` / `CassetteAdapter`.

---

## 5. The dry-run proof (run this)

`tests/integration/test_llm_dryrun.py` is the proof that a real LLM slots in and the
guarantees survive — **with zero network calls**. It drives the *live* branch of the
activation seam with a recorded `CassetteAdapter` standing in for the frontier model
(the live-adapter factory is monkeypatched, so no socket ever opens), and asserts:

- the seam resolves a **live** routed client under a fake `MOONSHOT_API_KEY`;
- the small end-to-end LODESTONE pipeline (aviation gold world) **completes**,
  producing ontology-grounded, **fully cited** answers;
- every **load-bearing gate still holds**: zero confidently-wrong (no wrong answer
  at confidence ≥ `tau_high`), unanswerables abstain, the trick-unit question is
  rejected by the type checker;
- **safety**: `SecureModelClient` redacted PII before the model saw the prompt and
  **refused** a high-injection prompt (model never called);
- **fallback**: `ValidatingModelClient` degraded to the deterministic adapter on a
  malformed cassette entry, and a fully-broken live model degrades the whole
  pipeline to the byte-identical keyless answer;
- **parity**: the keyless pipeline output equals the deterministic baseline.

Run it:

```bash
export PATH="$HOME/.local/bin:$PATH"
uv run pytest tests/integration/test_llm_dryrun.py -q -p no:warnings
```

And the full gate set (must stay green):

```bash
uv run pytest tests/integration tests/m12 tests/meridian -q -p no:warnings
```

---

## 6. Observability — what model is active

`aimodels.model_status()` reports what the current env *would* resolve to **without
constructing a live adapter or making a network call**:

```python
from ontoforge.aimodels import model_status
s = model_status()           # reads env intent
s.provider, s.live, s.label  # ("", False, "deterministic/keyless") when keyless
```

`model_status(client)` reports what backs an already-resolved client (live provider
+ model id, or deterministic when it is a bare fallback) via its `.active`
`ActiveModel` summary.
