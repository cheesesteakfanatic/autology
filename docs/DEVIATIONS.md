# Deviations Ledger (spec amendments per whitepaper §18.1)

Typed change-objects against `ontoforge-whitepaper-v2-complete.md`. Each entry: requirement delta,
affected modules, justification referencing acceptance tests, and migration note.

## AMD-0001 — MODIFY §11.1 stack: Python-first monorepo (no Rust core in v0)

- **GIVEN** the spec proposes Rust for storage/ledger/DBSP/OQIR, **WHEN** building the first complete
  vertical slice on a single 8-core/16GB dev machine with no Rust toolchain, **THEN** all modules are
  Python 3.12 with columnar engines (DuckDB, Arrow/Parquet) carrying the performance-critical paths.
- **Affected:** all modules; specifically the M0 10^8-atom ingest target and M6 p99 <10ms point-read
  target are **rescaled** to fixture scale (10^5–10^6 atoms) for this pass.
- **Justification:** the risk being retired in Phases 0–4 is *algorithmic correctness and integration*,
  which is language-independent; §11.1 itself permits deviations with written justification. Contracts
  are typed and language-neutral so a Rust port can replace hot modules behind the same interfaces.
- **Migration note:** performance gates re-enter at original scale when/if the Rust core lands.

## AMD-0002 — MODIFY §11.1 T2/T3 serving: deterministic keyless tiers in v0

- **GIVEN** no vLLM GPU pool and no API key in the build environment, **WHEN** any module escalates to
  T2/T3, **THEN** calls route through `ModelClient` adapters: `HeuristicAdapter` (deterministic
  rule-based proposer) and `CassetteAdapter` (record/replay per §18.4 item 4); a live `AnthropicAdapter`
  exists behind the same interface, used only when `ANTHROPIC_API_KEY` is present.
- **Affected:** M2 (tier costs), M4 (naming), M5 (T2 matcher), M8 (semantic synthesis), M12 (candidate
  generation). Tests must pass with zero network access (this *strengthens* §18.4 determinism).
- **Migration note:** none — the spec already mandates cassette determinism in CI; live tiers are
  additive.

## AMD-0003 — MODIFY §3.1: FD discovery baseline is partition-refinement (TANE-class), HyFD is a challenger

- **GIVEN** §3.1 specifies HyFD-style hybrid discovery, **WHEN** implementing the v0 BASELINE,
  **THEN** exact FD discovery uses stripped-partition refinement (TANE-class), which is exact,
  simpler to verify, and adequate at fixture scale; HyFD enters via the §19.1 baseline–challenger
  protocol when scale demands it.
- **Affected:** M3 tests (HyFD-parity runtime target deferred; exactness tests retained).

## AMD-0004 — MODIFY §11.2 M5: no neural blocking/embeddings in v0

- **GIVEN** DeepBlocker-class neural blocking requires embedding models, **WHEN** building v0 blocking,
  **THEN** blocking is MinHash-LSH + token/sorted-neighborhood hybrid; pairs-recall gate (≥98% on gold)
  retained. Neural blocking is a registered challenger.
- **Affected:** M5 blocking tests.

## AMD-0005 — ADD decision kind ADMIT

- **GIVEN** §1.3 enumerates six decision kinds and §3.4 routes STRATA concept admission through the
  spine, **THEN** concept admission is modeled as a first-class decision kind `ADMIT` rather than
  overloading SM.
- **Affected:** M2 (kind registry), M4. No test deltas; calibration per kind already generic.

## AMD-0006 — MODIFY §17.2: hero estate uses pinned schema-faithful fixtures where live downloads are blocked

- **GIVEN** registry.faa.gov returns 403 to non-browser clients from this environment, **WHEN** building
  the Tier-2 estate, **THEN** the estate uses real downloads when reachable and otherwise generates
  fixtures faithful to the documented real schemas (FAA ReleasableAircraft MASTER/ACFTREF layout, ASRS
  export columns, NTSB event tables) with §17.2's documented warts injected deliberately and labeled.
- **Affected:** Tier-2/Tier-3 gates run at fixture scale; live-corpus Tier-3 longitudinal runs deferred.

## AMD-0007 — DEFER M15 (governance valuation), M16 (distillation), full M13

- **GIVEN** §11.3 marks M13/M15/M16 off-path, **THEN** they are deferred from this pass except a
  minimal VISTA (metric layer → ranked Vega-Lite specs). The label-lattice valuation hooks exist in the
  provenance API (valuations are pluggable) but no policy engine ships in v0.
