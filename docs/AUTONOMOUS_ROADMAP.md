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
| W5-TRUST | Client-side **anonymization toolkit** (one-click anonymize/decipher, customer-held traceable-ID key) — the trust/marketing wedge + open-shell flagship | §7 compliance moat | queued |
| MEMO | **Strategy memo** (billion-dollar positioning, GTM wedges, pricing credibility, anonymization-as-trust) — synthesis/judgment, built on verifiable MARKET_EDGE facts, clearly labeled not "verified research" | after W1.5, per Glenn | queued |

## Honest guardrails
- **Testing:** build the deterministic suite as we go (per Glenn, don't gate on LLM-live tests until keys arrive). Never weaken an existing gate.
- **C++/UI:** a full C++ rewrite of a *web* UI is wrong (browsers run JS/WASM). The right read of "lower-level for speed" = WASM for heavy client compute + compiled/vectorized hot paths in the Python engine. Documented, not silently reinterpreted.
- **IP repo split / open-sourcing** = Glenn's human checkpoint #2 (we prep the boundary + guard; he makes the call).
- **Strategy memo** is judgment, not fact-checked research (the deep-research harness correctly killed unfalsifiable strategy claims).
- **Auth / multi-tenancy = DEFERRED, human checkpoint #3.** The engine already carries the *substrate* for isolation — `discovery/cached_work.py` and the Ask-flywheel namespace every cached object by `tenant_id` (no cross-tenant rollup, §1.5 isolation asserted in tests) — but the **server has no authentication and the tenant id is not yet enforced from a verified principal**. Real multi-tenancy needs a real identity provider (OIDC/SSO) wired into the FastAPI surface and a per-request tenant binding, plus a human decision on the hosting/identity stack. Building it autonomously would mean inventing a security boundary on a guess; left as a flagged decision rather than a silent stub.
