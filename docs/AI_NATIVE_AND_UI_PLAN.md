# OntoForge — Award-grade UI + AI-native architecture (research → build plan)

Source: deep-research run (106 agents, adversarial 3-vote verification), 2026-06-15. Guiding
principle: **the user is "ultimately lazy" — minimize what they must learn or do.**

---

## THREAD 1 — Award-grade, fast-spreading UI (all CSS-token / vanilla-JS achievable)

Verified findings (Linear's 2026 design refresh is the strongest primary evidence, 3-0 on each):

1. **Attention hierarchy — dim the chrome so the work surface wins.** "Not every element should
   carry equal visual weight; ones that support orientation/navigation should recede." → In our
   3-mode shell, the *active mode's work surface* must visually dominate; the mode switcher, dock,
   rails, and header recede (lower contrast/opacity, smaller).
2. **Reduce visual noise.** Fewer + smaller icons, remove decorative treatments → a calmer interface,
   less to parse per screen (directly serves the lazy-user principle).
3. **Warm, less-saturated-but-crisp palette.** Validates our warm midcentury direction; the risk is
   "too warm/muddy" — govern hue/chroma/lightness via tokens, keep text crisp.

**Build (UI crew, static/ only):**
- Recede chrome: dim menubar/switcher/dock/rails (color + opacity tokens); active mode surface gets
  full contrast + subtle elevation. Add a `--chrome-ink`/`--chrome-dim` token tier distinct from
  `--ink`.
- Noise cut: audit icon count/size; remove non-functional decoration; ensure every glyph earns its
  place.
- Palette governance: ensure the warm tokens stay crisp (raise lightness contrast on text vs paper);
  document the HCL ranges so it never drifts muddy.
- Award-criteria hygiene (also adoption levers): motion restraint (respect `prefers-reduced-motion`),
  keyboard-first everywhere, AA contrast, sub-16ms interactions, instant perceived load.

---

## THREAD 2 — AI-native architecture (keyless-deterministic now, LLM-layerable later)

All routes through the existing `contracts.models.ModelClient` seam. Build the scaffolding now;
the live model drops in by swapping the adapter — **zero rework**.

### A. Provider router (`ontoforge/aimodels/router.py`)
LiteLLM-style: one OpenAI-compatible `complete()` over many models; per-task selection by
cost/latency tier; priority-ordered fallback (NOTE: the "automatic fallback" claim was *refuted
0-3* in research — implement fallback explicitly + test it, don't assume the library gives it free).
A `ModelSpec` registry maps task → {model, temperature, max_tokens, tier}. Default tier = the
deterministic `HeuristicAdapter` (keyless). Kimi K2 / Qwen / Opus register as additional specs,
activated only when a key is present.

### B. Prompt + context engineering (`ontoforge/aimodels/prompts.py`, `context.py`)
- Prompt registry: versioned, task-scoped templates with constrained/structured output (JSON schema),
  few-shot slots, and ontology grounding (inject the relevant class/property subset, not the whole
  model).
- **Schema linking for context fit** (`context.py`): bidirectional (forward+backward) pruning —
  research shows **94% recall at 83% fewer columns**. Extractive linking (not generative) for
  decoder-only/open models (Qwen) — >20× faster + more accurate there. This is what lets a huge
  induced ontology fit a small context window.

### C. Secure data handling (`ontoforge/aimodels/secure.py`)
- **Prompt injection is inherent & unsolved** — defend *architecturally*, never trust fine-tuning
  (StruQ/SecAlign degrade utility AND stay vulnerable). Treat all ingested text (narratives, docs)
  as untrusted; never let it occupy the instruction channel; structurally separate data from
  instructions; spotlight/delimit.
- **Sample, don't bulk-send.** Send schema + a small stratified value sample, not enterprise rows.
- **PII redaction** pass before any external call; on-prem/local model path for sensitive estates.
- Evaluate every gate/detector on TWO axes (effectiveness vs existing AND adaptive attacks;
  utility preservation) and report FPR/FNR at the chosen threshold.

### D. THE DECISION GATE — temperature-leveraged weighted voting (`ontoforge/ensemble/`)
The headline mechanism: decide whether a data-engineering action (join / merge / retype) should
*fire*, by ensembling model calls and weighting agreement. Research corrects the naive design:

- **Weight PER-EXPERT (per-model), not per-temperature.** Weighted-Majority (WMA): each expert
  weight starts 1.0; pick the candidate with the highest summed weight; penalize wrong experts
  multiplicatively at rate **ε = √(ln N / T)** — provable regret bound (Littlestone–Warmuth).
- **Cross-model ensembling at temperature ≈ 0.0** beats high-temperature single-model
  self-consistency (high temp adds hallucination, not useful diversity).
- **Temperature is still a calibrated lever, two ways:** (1) **TURN** — pick a near-optimal
  aggregation temperature *without labels* via an entropy turning-point (fits our keyless
  constraint); (2) **Soft Self-Consistency** — for sparse/open-ended actions (a specific join), score
  candidates by continuous per-token likelihood (min/mean/product) and gate on a calibrated
  probability-sum threshold, instead of exact-match majority.
- **Hedge via RSL-SQL binary selection:** generate from full schema AND from pruned+grounded schema,
  (execute both where verifiable), judge picks — hedges over-pruning.

**Keyless-deterministic realization (ships now):** the ensemble runs over N deterministic
`HeuristicAdapter` "experts" with varied decision rules; the WMA weighting, the calibrated
threshold, the execution-verification (a proposed join previews coverage — already in `engineer/`),
and the conformal gate all work *today* with zero model calls. Swapping in Kimi/Qwen/Opus experts is
adding `ModelSpec`s — the gate math is unchanged. This is the same philosophy as the spine's
CRUCIBLE profile, specialized to DE-action gating.

### E. Other creative AI-native mechanisms to design/build
- **Execution-grounded verification first, model second:** never assert a join the data refuses
  (coverage floor) — the model only *proposes*; the deterministic verifier + vote *gates*.
- **Provenance-carried confidence:** every gated action records its vote tally + per-expert weights
  into the ledger (auditable "why did this join fire?").
- **Self-improving weights:** human Confirm/Reject verdicts (the review queue) update expert weights
  over time (the WMA penalty loop) — the system gets more autonomous as the lazy user clicks ✓/✗.

---

## Build sequencing
1. (in flight) Build-mode dashboard executor fix.
2. UI crew (static/): attention hierarchy + noise cut + palette governance + award hygiene.
3. AI-native crew (new `aimodels/` + `ensemble/`, wire into `engineer/`): router, prompts, context
   (schema-linking), secure, and the weighted-voting DE-gate — all keyless-deterministic, fully
   tested, with the live-model path stubbed and ready.
4. Integration: full suite, browser verification, commit + push.
5. Then: Cloudflare deployment plan (Workers AI as the cheap-LLM path for the gate's live experts).
