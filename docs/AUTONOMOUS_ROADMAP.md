# Autonomous build roadmap (round-the-clock run, started 2026-06-15)

Mandate (Glenn, going away, "amazing things when I return"): round-the-clock iterative dev, hard
algorithm + prompt work, mature the UI (warm is right, currently "childish" → natural/intuitive/
premium), speed everywhere (WASM/compiled where it pays), build toward an actual company, proactively
build what's missing, deliver the strategy memo after engine Wave 1.5.

**Operating rule:** committing build waves run **sequentially** (one pushes before the next starts —
no git races); non-committing research runs in parallel. Quality bar stays high (adversarial review,
the personas). Each wave: research-informed → build crews → integrate → commit+push → demo/verify.

## Wave sequence (chained; each launches when the prior lands)

| # | Wave | Why | Status |
|---|------|-----|--------|
| W1 | Typed-relationship engine (§1 core: confidence proxy, typed taxonomy, RoadSpy, SQL-execute validation, reasoning-path voting, per-tenant priors) | the doc's central technical risk | **landing** |
| R0 | Research: UI maturity + performance strategy + missing-features/company-agents | informs W1.5+ | **running (this turn)** |
| W1.5 | Engine hardening per Tursio research: PK band 0.95/±5%, IND prune 0.4, infrequent-token Jaccard, top-3-within-0.9 commit/abstain, per-estate weighting profile + **algorithm speed** (vectorize/Polars/numba/Cython on TANE-FD/FCA/IND/value-metrics hot paths; profile first) | accuracy + speed | queued |
| W2-UI | UI maturation: warm-but-grown-up — saturation discipline, neutral-dominant + sparse accent, typographic refinement, calmer motion, density; **WASM for hot client compute** (constellation force-sim, big-table virtualization) | "feels childish" → premium/natural | queued |
| W3-COMPANY | Company-in-a-box: the v2.1 §11 dev-agent roster as **real reusable agents** (Orchestrator/Implementer/Adversarial-Tester/Reviewer/Integrator/Research/IP-Warden) + business artifacts (pitch one-pager, landing page, demo script, pricing/compute-ledger calculator) | turn it into an actual company | **done** — 12 reusable subagents in `.claude/agents/` + `docs/AGENTIC_BUILD_RUNBOOK.md`; landing site (`site/`: index + canned offline demo + compute-ledger pricing calculator); `docs/PITCH_ONEPAGER.md` + `docs/DEMO_SCRIPT.md` + `docs/COMPETITIVE_BATTLECARD.md`. No src/ touched. |
| W4-FEATURES | Missing features: **Plan mode** (data-subset pull), real **connectors** (Postgres/CSV-at-scale/S3), the Wave-2 engine items (living prompt library+router+observation, Ask flywheel write-back, lazy usage/criticality recompute), per-customer compute ledger | what a real DE company needs | **done** — shipped SQL/object-store/large-CSV **connectors** (open shell, lazy optional drivers, keyless preserved) wired into `ontoforge init` (`--db-url`/`--db-table`, `--object-uri`) + `ingest`; **Plan mode** (`ontoforge plan -p X --budget N` → governed, joinability-preserving subset, `plan_subset` in `pipeline/plan.py`); **Observability** surfaces over the existing ledger/HEARTH/CostMeter substrate (`GET /api/lineage` value-level, `/api/audit`, `/api/runs`, `/api/compute-ledger` + the Observatory Studio app); **Ask-flywheel** validity-gated write-back (`lodestone/flywheel.py`, never serves a stale/confidently-wrong cached answer). Full suite green (1720). **DEFERRED:** real auth / multi-tenancy enforcement — needs a real identity provider plus a human decision (see below). |
| W5-TRUST | Client-side **anonymization toolkit** (one-click anonymize/decipher, customer-held key) — the trust/marketing wedge + open-shell flagship | §7 compliance moat | **done** — shipped `src/ontoforge/anonymizer/` (OPEN-SHELL, keyless, zero-network): **join/structure-preserving** tokenization (string HMAC tokens, injective integer/monotone-float numeric maps, valid non-overflowing date maps), **encrypted customer-held keymap** (`keymap.ofx`, demo-grade stdlib cipher honestly labeled NOT a KMS), `Policy` (PII auto-detect + per-column allow/deny). **THE PROOF** (`tests/anonymizer/test_proof.py`): `discover_relationships` returns the IDENTICAL typed-relationship set on RAW vs ANONYMIZED — joinability + the false-positive killer survive. Wired into the CLI (`ontoforge anonymize` / `ontoforge decipher`). Trust surface: `docs/ANONYMIZATION.md` + `site/trust.html`. Full suite green (1761). Integration found+fixed a real date-tokenization bug (ISO string dates routed to format-preserving string tokenization produced impossible dates that flipped the engine's date-aware distribution signal and created a spurious join; the 9999 ERP sentinel overflowed `date.max` — both fixed with a valid, order-preserving, non-overflowing date map + regression tests). |
| MEMO | **Strategy memo** (billion-dollar positioning, GTM wedges, pricing credibility, anonymization-as-trust) — synthesis/judgment, built on verifiable MARKET_EDGE facts, clearly labeled not "verified research" | after W1.5, per Glenn | **done** — `docs/STRATEGY_MEMO.md`. |

## Roadmap complete (W1..W5 + MEMO) — what is next

The committed wave sequence (W1 typed-relationship engine → W1.5 hardening/speed →
W2-UI maturation → W3-COMPANY → W4-FEATURES → **W5-TRUST**) and the MEMO are all
landed. The platform is keyless, deterministic, offline, full suite green (1761).

**What is next (all human checkpoints / key-gated, by design — not autonomous):**

1. **Open-source the toolkit (human checkpoint #2).** The anonymizer is the
   flagship open-shell deliverable and already complies with the IP boundary; the
   actual repo split / public release is Glenn's call (`docs/IP_ARCHITECTURE.md`).
2. **Production cipher (flagged in `docs/ANONYMIZATION.md` §5).** Swap the
   demo-grade stdlib keymap cipher for a real KMS/AEAD (libsodium / cloud KMS,
   per-field key derivation, rotation) — an integration decision plus a key stack.
3. **Auth / multi-tenancy enforcement (human checkpoint #3).** The cache/flywheel
   tenant-namespacing substrate exists; binding a verified principal needs a real
   identity provider (OIDC/SSO) and a hosting decision.
4. **LLM-live layer (key-gated).** The deterministic suite is the foundation per
   Glenn; the model router/ensemble adjudication and live competency gates light up
   when API keys arrive — built behind the existing keyless interfaces.
5. **Engine §3/§6 remainders — CLOSED.** Both shipped, keyless/offline/deterministic:
   - **§3 living prompt library + observation loop** — `src/ontoforge/aimodels/`
     gained `library.py` (`PromptLibrary`: per-task versions + a champion,
     seeded from the static prompts for zero regression, `select_by_observations`
     promotes the best version by mean confidence with deterministic tie-break)
     and `observation.py` (frozen `Observation` + append-only `ObservationLog`
     with integer-seq ordering and stable prompt fingerprints). The
     `ModelRouter` gained an optional `observer` (default `None` = byte-identical
     legacy path) that records exactly one observation per successful propose.
   - **§6 lazy usage/criticality recompute** — `src/ontoforge/criticality/`
     (append-only `UsageLog`, dirty-set/watermark `CriticalityModel`, byte-stable
     snapshot store) exposed through the backend: `GET /api/criticality?top=N`,
     additive `query`/`join` usage emission from the existing `/api/ask` and
     `/api/engineer/apply` handlers (no contract change), and the
     `ontoforge criticality -p PROJECT [--top N]` CLI command. See
     `docs/CRITICALITY.md`.

## Honest guardrails
- **Testing:** build the deterministic suite as we go (per Glenn, don't gate on LLM-live tests until keys arrive). Never weaken an existing gate.
- **C++/UI:** a full C++ rewrite of a *web* UI is wrong (browsers run JS/WASM). The right read of "lower-level for speed" = WASM for heavy client compute + compiled/vectorized hot paths in the Python engine. Documented, not silently reinterpreted.
- **IP repo split / open-sourcing** = Glenn's human checkpoint #2 (we prep the boundary + guard; he makes the call).
- **Strategy memo** is judgment, not fact-checked research (the deep-research harness correctly killed unfalsifiable strategy claims).
- **Auth / multi-tenancy = DEFERRED, human checkpoint #3.** The engine already carries the *substrate* for isolation — `discovery/cached_work.py` and the Ask-flywheel namespace every cached object by `tenant_id` (no cross-tenant rollup, §1.5 isolation asserted in tests) — but the **server has no authentication and the tenant id is not yet enforced from a verified principal**. Real multi-tenancy needs a real identity provider (OIDC/SSO) wired into the FastAPI surface and a per-request tenant binding, plus a human decision on the hosting/identity stack. Building it autonomously would mean inventing a security boundary on a guess; left as a flagged decision rather than a silent stub.
