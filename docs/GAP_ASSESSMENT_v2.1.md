# Gap Assessment — OntoForge build vs. Consolidated Build Instructions v2.1

Honest mapping of the v2.1 handoff doc (the autonomous-data-engineering mandate) against the current
codebase (Engine Wave 1 landed; 1400 tests green). Status: **HAVE** / **PARTIAL** / **GAP** / ✅ **DONE**.

## Phase 0 (§16) — the harness-first mandate: ✅ effectively SATISFIED
- Property tests for the math substrate (semiring/lattice/bitemporal/DBSP incrementality) — **HAVE** (tests/m0,m4,m6 + hypothesis).
- LLM cassettes, zero-token CI — **HAVE** (`CassetteAdapter`; all tests keyless/deterministic).
- Cost instrumentation — **HAVE** (`CostMeter`, ledger cost rows).
- Live-data estates — **HAVE** (Aviation FAA/ASRS/NTSB + the 450-dataset Wild corpus + Meridian). Scholarly/OpenAlex 477M "crazy-large" + OSM live-stream — **GAP** (deferred scale estates).
→ We are well past Phase 0; the work now is **engine depth**, per the doc's "then assess gaps."

## PART I — Engineering (the engine)

### §1.1 Heuristics-first confidence proxy — ✅ **DONE (Wave 1)**
Shipped `src/ontoforge/relationships/` — a dedicated, evidence-bearing **confidence-proxy scoring engine** that fuses value-OVERLAP (containment / MinHash Jaccard) + value-**DISTRIBUTION** alignment (Jensen-Shannon for categoricals, quantile divergence for numerics) + entropy + cardinality + key-uniqueness + sampled-row evidence into a `[0,1]` proxy, and emits an `EvidenceArtifact` trail (which signals *fired*, which *conflicted*). The distribution-divergence conflict is the discriminator that kills "looks-similar-isn't-related." `tests/relationships/` (20+) green.

### §1.2 Typed semantic relationships (not binary joins) — ✅ **DONE (Wave 1)**
Shipped `relationships/classify.py` — a deterministic ordered rule cascade over the evidence → the full taxonomy: **FK-join · lookup/dimension · many-to-many bridge · denormalization · derived field · unrelated-despite-similarity · unknown**, with the false-positive killer firing even at full vocabulary overlap when distributions diverge and neither side is a key. `roadspy.py` packages the evidence (sterilized, capped samples — never bulk data) as a `ScoutPayload`. **Wired into the atlas**: every `/api/atlas` arc now carries an additive `rel_type` + evidence summary.

### §1.3 Reasoning-path voting (not temperature noise) — ✅ **DONE (Wave 1)**
Shipped `ensemble/paths.py` + `ensemble/relgate.py` — three **distinct reasoning PATHS** (schema-centric, value-centric, business-logic) voting on the relationship **TYPE** by plurality, with **median-of-path** confidence and the SQL validation as booster/veto; commit only on consensus, else route to human. `should_vote` is the cost scalpel (confident FKs skip the vote). The fire/hold `Gate` stays unchanged alongside it. `tests/ensemble/test_relgate.py` (23) green.

### §1.4 SQL-synthesis-and-execute backward validation — ✅ **DONE (Wave 1)**
Shipped `src/ontoforge/validation/` — actually **synthesizes the join in DuckDB, EXECUTES it (in-process `:memory:`), and validates against real data**: match / orphan / fan-out / null-key, derived typed verdict + `ok`. Documented deterministic stratified sampling for big tables. **Wired into the engineer**: `/api/engineer/apply` runs it before committing a link and records the `JoinValidation` as provenance; a contradicting executed verdict routes to review (never weakens the coverage floor). `tests/validation/` (18) green.

### §1.5 Per-tenant pattern learning (isolated) — ✅ **DONE (Wave 1)**
Shipped `src/ontoforge/tenant/priors.py` — an **isolated** per-tenant store (key space `(tenant_id, kind, key)`; no global/cross-tenant rollup even on a shared SQLite file) learning name conventions, semtype habits, and accepted/rejected join shapes. `adjust_candidate` applies a **bounded** nudge (±0.08) that tunes ranking only and is suppressed by hard contrary evidence — it can never lift a sub-floor candidate over the join floor. `tests/tenant/test_priors.py` (18) green, isolation asserted.

### §2 Tiered compute / model-agnosticism / stratified sampling — **PARTIAL**
HAVE: `aimodels/router.py` (tiers, model-agnostic, OpenAI-compatible-ready), `secure.sample_rows` (stratified). GAP: **schema-informed stratified sampling around candidate keys / cardinality boundaries / distribution edges**, bootstrapped off an initial ontology hypothesis, for billion-row join inference. Current sampling is generic, not join-inference-targeted.

### §3 Prompt router + living library + observation — **PARTIAL → build (Wave 2)**
HAVE: `aimodels/prompts.py` versioned templates. GAP: the **prompt ROUTER** (classifier → prompt by relationship-type/domain/complexity, logged), **RAG over prompts**, novel-case capture→sterilize→test→fold-in, the **observation layer** (flag out-of-library prompts + confidence divergence), live prompt update, bloat guardrails.

### §4 Ask flywheel + dynamic ontology growth — **PARTIAL → build (Wave 2)**
HAVE: LODESTONE Ask + the live playground build. GAP: the **flywheel** — a novel cross-source Ask with no pre-existing dataset triggers **live data engineering**, answers, and **writes the result back as a referenceable ontology object** so the next ask is faster; plus async/emailed result + ETA for heavy asks (latency SLAs §4).

### §5 Semantic search over cached DE work (humans + models) — ✅ **FOUNDATION DONE (Wave 1)**
Shipped `src/ontoforge/discovery/cached_work.py` — a `CachedWorkStore` of **versioned** DE objects (executed joins / transforms / results) with provenance + an **auto-generated description** + **keyless semantic retrieval** (pure-python hashing TF-IDF + IDF-weighted cosine). `search(query)` for humans and `retrieve_for_model(context)` for the model RAG bootstrap ("what we already know about these joins"). Tenant-scoped retrieval. Real embeddings later route through `aimodels` behind the same interface. `tests/discovery/` (7) green. (Not yet surfaced on a `/api` route — that wiring is Wave 2.)

### §6 Lazy usage/criticality recomputation — **PARTIAL → build (Wave 2)**
HAVE: HEARTH bitemporal + WARDEN drift + DBSP incrementality. GAP: a **usage/criticality-driven lazy recompute** policy (system-determined ∪ user-set ∪ blend dial); never nightly-everything.

### §7 Client-side anonymization toolkit — **GAP (open-shell, separate track)**
None yet. One-click anonymize/decipher, customer-held traceable-ID key, cloud computes on anonymized input. This is an **open-shell** deliverable + the headline trust/marketing wedge.

### §8 Per-customer compute ledger (zero-margin pass-through) — **PARTIAL**
HAVE: token CostMeter. GAP: a per-customer **compute ledger** surfaced as the pass-through/transparency artifact.

## PART III — IP architecture (§18-20) — ✅ **DOCUMENTED + GUARDED (Wave 1); human checkpoint remains**
Shipped [docs/IP_ARCHITECTURE.md](IP_ARCHITECTURE.md) documenting the closed-core ring (relationships, validation, ensemble, tenant, discovery, strata, temper, hearth, anvil, warden, lodestone, vista, amber, spine, aimodels) vs the open shell (cdc, estates connectors, server UI, pipeline/engineer scaffolding, the future anonymizer), plus `tests/test_ip_boundary.py` — a pragmatic AST import-guard asserting the open shell (`server`, `cdc`) does not reach into the NEW closed-core engine internals (only via package entrypoints / `contracts`), with a documented carve-out list for pre-existing established-module internals. **Human checkpoint (still open):** the actual private-repo split / open-sourcing decision.

## Engine Wave 1 — ✅ SHIPPED (the §1 "core problem")
The doc opens by naming §1 the central technical risk. Wave 1 targeted it head-on, all keyless-deterministic with a full test suite (LLM adjudication layered in later when keys arrive) — **all five items landed and are WIRED into the product**:
1. ✅ `relationships/` — distribution-aware **confidence-proxy scoring engine** + **typed relationship taxonomy/classifier** + **RoadSpy scout payload** (evidence artifacts throughout).
2. ✅ `validation/` — **SQL-synthesis-and-execute** backward join validation in DuckDB (match/orphan/fan-out/null-key).
3. ✅ `ensemble/` (extended) — **reasoning-path voting** (schema/value/business-logic) on relationship TYPE, consensus-or-route-to-human.
4. ✅ `tenant/` — **per-tenant isolated pattern learning** (priors that re-weight candidates; never cross-tenant).
5. ✅ wired into **atlas** (`rel_type` + evidence on every arc) and **engineer** (proxy + execution-validated confidence + typed provenance before commit); shipped the **semantic-search-over-cached-DE-work** foundation (`discovery/`) and the closed-core/open-shell **IP-boundary doc + import guard**.

Wave 2 (next): living prompt library/router/observation, the Ask flywheel write-back, lazy recompute, anonymization toolkit, and surfacing `discovery` on a `/api` route.
