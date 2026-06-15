# Gap Assessment — OntoForge build vs. Consolidated Build Instructions v2.1

Honest mapping of the v2.1 handoff doc (the autonomous-data-engineering mandate) against the current
codebase (commit 652916d; 25 modules, 1243 tests green). Status: **HAVE** / **PARTIAL** / **GAP**.

## Phase 0 (§16) — the harness-first mandate: ✅ effectively SATISFIED
- Property tests for the math substrate (semiring/lattice/bitemporal/DBSP incrementality) — **HAVE** (tests/m0,m4,m6 + hypothesis).
- LLM cassettes, zero-token CI — **HAVE** (`CassetteAdapter`; all tests keyless/deterministic).
- Cost instrumentation — **HAVE** (`CostMeter`, ledger cost rows).
- Live-data estates — **HAVE** (Aviation FAA/ASRS/NTSB + the 450-dataset Wild corpus + Meridian). Scholarly/OpenAlex 477M "crazy-large" + OSM live-stream — **GAP** (deferred scale estates).
→ We are well past Phase 0; the work now is **engine depth**, per the doc's "then assess gaps."

## PART I — Engineering (the engine)

### §1.1 Heuristics-first confidence proxy — **PARTIAL → strengthen (Wave 1)**
HAVE: M3 FD/IND discovery, MinHash value-overlap, cardinality/uniqueness, name similarity; atlas join scoring (coverage + name + type + rhs-uniqueness). GAP: a dedicated **confidence-proxy scoring engine** that fuses value-OVERLAP + value-**DISTRIBUTION** alignment (JSD/KL, quantile divergence) + entropy + sampled-row evidence and emits **reasoning artifacts** (which signals fired, which conflicted) — the thing that kills "looks-similar-isn't-related." Today's join score is a weighted sum, not an evidence-bearing, distribution-aware proxy.

### §1.2 Typed semantic relationships (not binary joins) — **GAP (Wave 1, core)**
We classify joins into tiers (confirmed/likely/hint), NOT into the **relationship taxonomy** the doc requires: FK-join · lookup/dimension · many-to-many bridge · denormalization artifact · derived field · **unrelated-despite-similarity**. This typed output is what feeds the ontology builder. Also missing: the **RoadSpy scout payload** (the structured "these might match, here's how" evidence packaged for the adjudicator — feed evidence, never raw data).

### §1.3 Reasoning-path voting (not temperature noise) — **PARTIAL → reframe (Wave 1)**
HAVE: `ensemble/` per-expert weighted-majority gate (coverage/value-overlap/name/type experts). GAP: the doc wants **distinct reasoning PATHS** — schema-centric, value-centric, business-logic — voting on the **relationship TYPE** (plurality) with **median-of-path** confidence, commit only above consensus, else route to Build/human. Our experts approximate this but vote fire/hold, not typed-relationship plurality.

### §1.4 SQL-synthesis-and-execute backward validation — **GAP (Wave 1, core)**
HAVE: a coverage floor in `engineer`. GAP: actually **synthesize the join in DuckDB, execute it, and validate against real data** — match rate, orphan/dangling rate, fan-out, null-key rate. This is the doc's "validate the join backwards against real data" — the strongest correctness guarantee and currently absent.

### §1.5 Per-tenant pattern learning (isolated) — **GAP (Wave 2)**
No per-tenant prior-learning mechanism (naming conventions, prefixes, colloquialisms, historical accepted/rejected join patterns). WMA-weight updates from review verdicts are a seed. HARD constraint to honor: **per-tenant only, never cross-tenant.**

### §2 Tiered compute / model-agnosticism / stratified sampling — **PARTIAL**
HAVE: `aimodels/router.py` (tiers, model-agnostic, OpenAI-compatible-ready), `secure.sample_rows` (stratified). GAP: **schema-informed stratified sampling around candidate keys / cardinality boundaries / distribution edges**, bootstrapped off an initial ontology hypothesis, for billion-row join inference. Current sampling is generic, not join-inference-targeted.

### §3 Prompt router + living library + observation — **PARTIAL → build (Wave 2)**
HAVE: `aimodels/prompts.py` versioned templates. GAP: the **prompt ROUTER** (classifier → prompt by relationship-type/domain/complexity, logged), **RAG over prompts**, novel-case capture→sterilize→test→fold-in, the **observation layer** (flag out-of-library prompts + confidence divergence), live prompt update, bloat guardrails.

### §4 Ask flywheel + dynamic ontology growth — **PARTIAL → build (Wave 2)**
HAVE: LODESTONE Ask + the live playground build. GAP: the **flywheel** — a novel cross-source Ask with no pre-existing dataset triggers **live data engineering**, answers, and **writes the result back as a referenceable ontology object** so the next ask is faster; plus async/emailed result + ETA for heavy asks (latency SLAs §4).

### §5 Semantic search over cached DE work (humans + models) — **GAP (Wave 1 foundation)**
HAVE: `server/search.py` over classes/entities/properties. GAP: every executed join/transform/result as a **versioned object** with provenance + **AI-generated description** + **embedding/semantic retrieval** serving humans (NL) AND models (RAG bootstrap: "here's what we know about import↔weather joins"). Foundation can ship keyless (TF-IDF/hash retrieval; real embeddings later).

### §6 Lazy usage/criticality recomputation — **PARTIAL → build (Wave 2)**
HAVE: HEARTH bitemporal + WARDEN drift + DBSP incrementality. GAP: a **usage/criticality-driven lazy recompute** policy (system-determined ∪ user-set ∪ blend dial); never nightly-everything.

### §7 Client-side anonymization toolkit — **GAP (open-shell, separate track)**
None yet. One-click anonymize/decipher, customer-held traceable-ID key, cloud computes on anonymized input. This is an **open-shell** deliverable + the headline trust/marketing wedge.

### §8 Per-customer compute ledger (zero-margin pass-through) — **PARTIAL**
HAVE: token CostMeter. GAP: a per-customer **compute ledger** surfaced as the pass-through/transparency artifact.

## PART III — IP architecture (§18-20) — **GAP (needs human checkpoint #2)**
No closed-core / open-shell module boundary or import-boundary enforcement. The doc specifies the split precisely (closed: the 8 named inventions + heuristic scoring + calibration + voting + cost-spine + prompt-loop + per-tenant-learning + stratified-sampling; open: connectors + anonymizer + UI + scaffolding). **Autonomous step now:** document the boundary + add a CI import-guard. **Human checkpoint:** the actual private-repo split / open-sourcing decision.

## What I'm building now — Engine Wave 1 (the §1 "core problem")
The doc opens by naming §1 the central technical risk. Wave 1 targets it head-on, all keyless-deterministic with a full test suite (LLM adjudication layered in later when keys arrive):
1. `relationships/` — distribution-aware **confidence-proxy scoring engine** + **typed relationship taxonomy/classifier** + **RoadSpy scout payload** (evidence artifacts throughout).
2. `validation/` — **SQL-synthesis-and-execute** backward join validation in DuckDB (match/orphan/fan-out/null-key).
3. `ensemble/` (extend) — **reasoning-path voting** (schema/value/business-logic) on relationship TYPE, consensus-or-route-to-human.
4. `tenant/` — **per-tenant isolated pattern learning** (priors that re-weight candidates; never cross-tenant).
5. wire into atlas/engineer (typed relationships + execution-validated confidence), a **semantic-search-over-cached-DE-work** foundation, and a closed-core/open-shell **IP-boundary doc + import guard**.

Wave 2 (next): living prompt library/router/observation, the Ask flywheel write-back, lazy recompute, anonymization toolkit.
