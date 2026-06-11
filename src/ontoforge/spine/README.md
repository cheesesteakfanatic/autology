# M2 — Decision Spine

Implements `ontoforge.contracts.decisions.Spine` (whitepaper §8, §11.2 M2; MVP plan
§2 escalation contract, §5.3 confidence & gating, §5.4 cost governor). Every
consequential judgment in the platform is routed through `DecisionSpine.decide()`
as cost-sensitive selective classification with calibrated confidence and
conformal deferral.

## Files

| file | contents |
|---|---|
| `spine.py` | `DecisionSpine`: tier router, selective rule, budget governor, economy/CRUCIBLE chains, ledger write-through |
| `calibration.py` | `KindCalibrator`: per-kind base logistic model, Platt + isotonic recalibrators with ECE referee, split-conformal predictor; `expected_calibration_error`; pre-fit heuristic |
| `adjudicator.py` | T2/T3 prompt contract (`spine.adjudicate.<kind>`), fail-closed `{choice, confidence}` parsing |

## Tier chain (MVP plan §2; whitepaper §8)

- **T0** — registered deterministic rule functions per `DecisionKind`
  (`register_rule(kind, fn)`, `fn(req) -> TierScore | None`). A firing rule's
  normalized scores go through the same two-threshold rule; abstentions fall
  through. Zero tokens.
- **T1** — per-kind calibrated scorer over `req.features`. Base model: sklearn
  `LogisticRegression` fit on `CalibrationSample`s via `recalibrate()`. Before
  any fit (or when a binary calibrator's classes don't match the request's
  candidates) a transparent feature-average heuristic is used and the result
  rationale is tagged `t1:uncalibrated-heuristic` — never silently presented as
  calibrated.
- **T2 / T3** — `ModelClient.propose()` with task `spine.adjudicate.<kind>`;
  the prompt carries a `tier: T2|T3` header plus the candidates, features, and
  opaque context serialized as sorted-key JSON (deterministic; cassette-safe).
  The model must answer `{"choice": ..., "confidence": ...}`; malformed or
  off-candidate answers degrade to abstention (never raise). T3 is consulted
  only when T2's confidence lands in the ambiguous band (economy).
- **HUMAN** — tiers exhausted ⇒ `deferred_to_human=True`.

## Selective rule (MVP plan §2)

`confidence ≥ tau_high` → auto-accept; binary `P(candidates[1]) ≤ tau_low` →
auto-reject (the contract's `("no","yes")` convention); in between → escalate.
Multiclass has no reject side: argmax must clear `tau_high`. `req.impact > 1`
widens the band by `0.02·(impact−1)` per side — high-impact decisions escalate
more readily (the contract's `impact` field; the MVP plan's T3 "high-impact"
gate condition).

## Calibration (whitepaper §11.2 M2: "ECE ≤ 0.05 post-calibration")

`recalibrate(kind, samples)` does a fixed, per-kind-seeded 45/20/10/25 split:

1. **train** — fit the base logistic model (coefficients extracted once; all
   decide-time math is closed-form numpy, so scoring is deterministic).
2. **cal-fit** — fit BOTH recalibrators of the raw score: Platt scaling
   (a logistic regression on the logit of the raw score — the standard Platt
   parameterization of "LogisticRegression on the raw score") and sklearn
   `IsotonicRegression`.
3. **select** — referee on a held-out split: the method with lower ECE wins
   (ties → Platt, the more stable parametric map). Exposed via `ece(kind)` and
   `calibration_report()` (both candidate ECEs, chosen method, split sizes).
4. **conformal** — bank nonconformity scores on a final untouched split.

Below 50 samples (or a single observed class) the kind stays uncalibrated and
T1 uses the marked heuristic.

## Split conformal prediction (whitepaper §3.4 admission gating)

`conformal_set = {c : 1 − raw_P(c) ≤ q̂}` with the finite-sample-adjusted
quantile `q̂ = ceil((n+1)(1−alpha))/n`-th smallest banked score. A **singleton
set at level alpha auto-decides** even inside the threshold band (economy),
implementing §3.4's "auto-admitted only when its prediction set is singleton".

**Design decision (deviation from the obvious implementation):** the
nonconformity score is `1 − RAW base-model probability` of the outcome, not
the recalibrated probability. Split conformal is valid for *any* fixed score
function; the raw logistic score is continuous, whereas isotonic recalibration
produces heavily tied scores whose empirical quantile over-covers badly
(measured +7% above nominal at alpha=0.2 before the change). With the raw
score, empirical coverage on the two-Gaussian benchmark is within ±2% of
nominal per seed for alpha ∈ {0.05, 0.1, 0.2} (tests/m2/test_conformal_coverage.py).

## Budget governor (whitepaper §8 economy; MVP plan §5.4)

Every T2/T3 call is admitted against the remaining budget using a conservative
reservation (`len(prompt)/4 + max_output_tokens`). If the reservation would
overrun `profile.budget_tokens`, the call is **not made** and the decision
returns `quarantined=True` with the last-consulted tier as tier-of-record —
fail-closed, never a silent auto-decision (`auto_decided` is False). Actual
spend (from `ModelResponse` token counts) is charged after each call and
exposed via `spent_tokens()`. Decisions T0/T1 can settle are unaffected by
exhaustion.

## CRUCIBLE profile (whitepaper §8)

- budget shadow price ~0: admission checks are skipped; spend is still metered;
- escalation band widened to (0.02, 0.98) — "escalate on any non-trivial
  ambiguity" — and a fitted conformal predictor must ALSO produce a singleton
  before a T1 threshold pass auto-decides (in economy the singleton gate is a
  sufficient bypass; in crucible it is a necessary extra check — our reading of
  "any non-trivial ambiguity");
- T2 and T3 are BOTH consulted (T2 as the independent agreement signal per §8).
  Agreement boosts confidence by independent-error OR-combination
  `1 − (1−c₂)(1−c₃)`; disagreement (or parse failure) routes to the human
  queue (§8 adversarial verification). Crucible therefore never quarantines and
  escalates a strict superset of economy's escalations on the same workload.

## Ledger (M0 integration)

When constructed with a ledger, every `decide()` calls
`ledger.append_decision(result, req.prov_atoms)` and
`ledger.record_cost("spine.decide.<kind>", result.cost_tokens)` — quarantined
and deferred results included (every decision is an auditable ledger row, §10).

## Determinism

Fixed per-kind split seeds (crc32 of the kind name), deterministic sklearn
fits, closed-form numpy scoring, sorted-key JSON prompts. Same samples + same
workload + deterministic client ⇒ identical `DecisionResult` sequences
(tests/m2/test_determinism.py).

## Test-side notes (tests/m2/)

- The synthetic benchmark draws features from two overlapping Gaussians (one
  per outcome) so the Bayes posterior is analytic; calibration is asserted
  against ground truth (ECE ≤ 0.05 on 10k independent samples × 5 fixed seeds,
  plus mean |p̂ − p*| ≤ 0.05 vs. the closed-form posterior). A misspecified
  variant (cubed feature) checks the Platt/isotonic referee repairs a badly
  calibrated base model.
- Conformal coverage is asserted **per seed** within ±2% of (1−alpha) for
  alpha ∈ {0.05, 0.1, 0.2} on 10k test points (seeds 1–5 chosen a priori).
- The token-charging fake `ModelClient` charges `len(prompt)//4` input tokens
  plus a few output tokens — below the spine's reservation, as any real adapter
  honoring `max_tokens` is.

## Contract observations (no contract edits made)

- `ModelRequest` has no tier field, so the T2/T3 distinction is carried in the
  prompt's first line (`tier: T2|T3`) and the response `model_id`; cassette and
  fake adapters key on it deterministically. Workable; a `tier` field on
  `ModelRequest` would be cleaner.
- `SpineProfile` has no explicit CRUCIBLE tau/alpha overrides; the widened
  (0.02, 0.98) crucible band is a module constant per §8's qualitative spec.
