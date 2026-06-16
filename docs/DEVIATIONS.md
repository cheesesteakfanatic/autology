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

## AMD-0008 — MODIFY §4.2/§11.1 HEARTH substrate: plain Parquet + DuckDB views in v0 (Iceberg deferred)

- **GIVEN** §4.2 specifies Iceberg tables for the canonical RAW/CONFORMED/ENTITY layers (and §11.1
  lists Apache Iceberg as the open table format), **WHEN** building M6 at AMD-0001 fixture scale with
  no Iceberg runtime in the approved dependency set, **THEN** the canonical layer is plain Parquet
  shards queried via DuckDB views, with atomic whole-shard rewrites (temp file + `os.replace`) standing
  in for Iceberg atomic commits.
- **Affected:** M6 (HEARTH) layout and commit path; M14 (AMBER) data layer ships the same Parquet
  schemas. Snapshot/time-travel capability is preserved *logically* by the bi-temporal columns
  themselves (every historical state reconstructs from the cells); the open-format constraint (P) is
  satisfied directly — `export_canonical` ships the very schema the store runs on
  (`tests/m6/test_portability.py`: cell-set equality, hash idempotence, tamper detection).
- **Justification:** at 10^4–10^5 cells a single atomic rewrite is simpler and safer than
  delta-file + compaction machinery; append-only is enforced as a *logical* invariant
  (property-tested I2) rather than a physical-file one. See `src/ontoforge/hearth/README.md`.
- **Migration note:** Iceberg re-enters with the Rust core (AMD-0001 migration); the cell schema and
  survivorship ordering are substrate-independent, so the swap is a storage-layer replacement behind
  the same `Hearth` API.

## AMD-0009 — ADD connection atlas (pipeline/atlas.py + /api/atlas): tier caps and build hooks

- **GIVEN** the Atlas UI contract (constellation.js; scale-proven at 250 nodes / 600 arcs against
  `tests/server/fixtures/atlas_synthetic_250.json`) tiers cross-dataset joins as
  confirmed / likely / hint, **WHEN** building the atlas engine over the wild corpus (282 real
  datasets), **THEN** three documented disciplines apply beyond the tier rules themselves:
  (1) **LIKELY_CAP = 600 by score** — the raw likely band admits ~32k weak numeric/date co-coverage
  pairs at wild scale; the cap mirrors the contract's own hint cap (400) and the UI's proven arc
  budget; one arc per (src class, src prop, dst class, dst prop), strongest evidence first.
  (2) **'atlas' ledger artifact provenance is ONE Leaf over a synthetic `atom://atlas/build` atom**
  (constraint H satisfied cheaply; per-arc evidence remains inspectable in the payload itself).
  (3) **the frozen CLI does not auto-build the atlas**: `materialize_induced` gained an OPTIONAL
  `atlas_dir` keyword (existing callers unchanged); CLI/demo projects build offline via
  `python -m ontoforge.pipeline.atlas <project_dir>`, which is exactly what `GET /api/atlas`'s
  404 message instructs.
- **Affected:** pipeline (atlas.py; materialize_induced optional kwarg), server
  (/api/atlas, /api/atlas/link, ProjectWorld.read_atlas + reload cache drop).
- **Justification:** tests/pipeline/test_atlas*.py (exact tiers/components/stats on a crafted
  corpus; scaled-IND equivalence property) and tests/server/test_atlas_api.py (the UI fixture
  parses through the API schema — the compatibility gate).
- **Migration note:** none — caps are constants; lifting them is a one-line change when the UI
  renderer's arc budget grows.

## AMD-0010 — ADD SignalKind.INFREQUENT_TOKEN + per-estate weighting + Tursio thresholds (engine accuracy)

- **GIVEN** the M-REL §1.1 evidence-fusion engine fuses a fixed signal menu under one global formula,
  **WHEN** hardening accuracy per the verified `docs/RESEARCH_ENGINE_SOTA.md` (Tursio + multi-signal
  fusion), **THEN** four additive, KEYLESS/DETERMINISTIC refinements land, all behind the same
  contracts and gates:
  (1) **NEW signal `SignalKind.INFREQUENT_TOKEN`** (contracts `relationships.py`) — Jaccard restricted
  to the RARE value tokens, catching format-variant joins ("123 Main St"↔"123 Main Street") where
  verbatim containment/Jaccard collapse to ~0. Fused as one more `EvidenceArtifact` (weight 0.12,
  positive corroborator only — never a veto). The fusion's hard divergence veto and the divergence
  NEGATIVE are damped ONLY when rare-token overlap is high AND verbatim containment is low (a
  format-artifact, not a genuine frequency clash) — gated so a genuine look-alike (no rare-token
  agreement) or an identical-vocabulary frequency-swap (high containment) is UNAFFECTED.
  (2) **Tursio PK band + IND-0.4 prune** (`relationships/classify.py`, `relationships/score.py`) as
  documented, tunable constants: `PK_DISTINCT_RATIO=0.95` / `PK_BAND_TOLERANCE=0.05` (a PK candidate
  has distinct ≥ 0.95·rows AND within ±5% of the table's max distinct); `IND_PRUNE_FLOOR=0.4` over a
  5-component IND/candidate score (containment backbone + key-uniqueness + cardinality + type + weak
  name), with containment as a necessary backbone so a near-key target with zero value overlap cannot
  pass. The distribution-divergence false-positive killer is unchanged.
  (3) **NEW `relationships/weighting.py`** — a per-estate `WeightingProfile` (RELATIONAL / LAKE /
  BALANCED) detected from profiles alone (keyed-table fraction, string-column fraction, avg
  uniqueness); `fuse_confidence` scales each signal's weight by its GROUP (structural / overlap /
  semantic) — structure+overlap dominate clean relational estates, semantic metadata dominates messy
  lakes. The FP-killer negatives are NEVER scaled. `discover_relationships` auto-detects by default.
  (4) **top-3-within-0.9 commit calibration** (`ensemble/relgate.py`) — `RelationshipGate.
  calibrate_commits` (precision-over-recall): a typed relationship COMMITS only when it is top-3 by
  confidence, within `TOP_CONFIDENCE_RATIO=0.9` of the leader, reaches per-candidate consensus, AND no
  competing candidate sits within 0.9 of the leader (a borderline 2nd candidate routes the whole field
  to a human). `should_vote` stays the cost scalpel; `decide` is unchanged.
- **Affected:** contracts (`relationships.py`: one ADD-only `SignalKind` member — the pinned membership
  test updated); relationships (`signals.py`, `score.py`, `classify.py`, `discover.py`, new
  `weighting.py`, `__init__.py`); ensemble (`relgate.py`, `__init__.py`). No change to `profiling/`,
  `static/`, the spine, or any existing gate.
- **Justification:** `tests/relationships/test_signals.py` (St↔Street recovery, silence on disjoint
  ids), `tests/relationships/test_weighting.py` (estate detection, re-weighting moves the proxy, FP
  killer survives every profile, PK band 0.95/±5%, IND prune at 0.4), `tests/ensemble/test_relgate.py`
  (top-3-within-0.9 commit/abstain incl. borderline-2nd routing, validation-veto composition,
  determinism), `tests/contracts/test_relationships_contracts.py` (enum membership). `tests/relationships`
  + `tests/ensemble` green; full suite green.
- **Migration note:** none — all four are additive and tunable via the new module-level constants; the
  default `BALANCED` profile reproduces the prior global fusion when no estate fingerprint is supplied.
