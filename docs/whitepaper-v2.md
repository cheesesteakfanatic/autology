# OntoForge v2: An Autonomous Semantic Data Platform
## Technical White Paper & Build Specification — v2.0 (June 2026)

**Status:** Definitive build spec. Supersedes v1. Written for a world-class engineering audience and for the agentic coding team that will implement it. No code appears in this document; everything is specified one level above code: formal definitions, named algorithms, module contracts, data models, acceptance tests, and dependency order.

**Reading guide.** Part I (§§1–16): Sections 1–2 state the problem and architecture. Sections 3–9 define the system's eight named novel techniques (STRATA, TEMPER, HEARTH, ANVIL, WARDEN, LODESTONE, VISTA, AMBER) plus the carried-forward v1 framework (the Decision Spine, the provenance semiring substrate, DBSP incrementality, stable-URI incremental clustering). Section 11 is the module-by-module build specification. Section 14 contains the novelty-claims table that positions every invention against its closest prior art. Part II (§§17–19): §17 is the open-source end-to-end testing program on verified live public corpora; §18 is the agentic development plan (roles, task graph, anti-reward-hacking verification, milestones); §19 is the iterative algorithm-development and continuous-research protocol. Quantitative claims are cited or marked **[design target]**.

---

## 1. Vision and Formal Problem Statement

### 1.1 The data-maturity bottleneck

Every enterprise AI initiative today is gated on the same precondition: *data maturity*. Before a company can ask an LLM a hard cross-system question, someone must integrate the sources, model the domain, resolve the entities, define the metrics, and build the pipelines — eighteen months of data engineering before the first useful AI answer. The 2026 warehouse-native answers (Snowflake Cortex Analyst, Databricks AI/BI Genie) confirm the diagnosis while dodging the disease: both require a **hand-authored semantic model** (Cortex Analyst's YAML semantic views; Genie's Unity Catalog metric views) and both stop at their own warehouse boundary — Snowflake's NL surface is confined to data inside Snowflake, Genie is structured-data-only inside Databricks. The semantic model that makes NL2SQL accurate is precisely the artifact nobody has time to write.

OntoForge's thesis: **autonomously induce the semantic substrate** — a validated, data-bearing ontology spanning *all* of a customer's sources, structured and unstructured — and keep it continuously correct under daily change. Then AI search stops being a warehouse feature and becomes the front door to the whole estate: a CEO asks a convoluted multi-system question and gets an accurate, atom-cited answer; an analyst gives a vague dashboard request and receives ranked, runnable dashboard proposals. No more waiting for data maturity to do AI.

Three product consequences follow, and they reshape the architecture relative to v1:

1. **The ontology holds the data.** OntoForge is not a metadata catalog pointing at data elsewhere. Canonical, entity-resolved objects materially store records, properties hold values, and links are traversable at query time — the pattern Palantir Foundry validated with its Ontology backed by Object Storage V2, where a Funnel service indexes datasource rows and user edits into specialized object databases. OntoForge does the same job with two differences: the ontology is *induced* rather than hand-modeled, and the store is built on open formats so the customer can leave (§7).
2. **It is a full pipeline platform.** Transformations are first-class, versioned, human-readable artifacts with column-level lineage; jobs run on schedules and CDC triggers with dependency-aware orchestration, backfills, retries, SLAs, run history, and auto-generated data-quality monitors. Most transforms are *synthesized*, not written (§5).
3. **Exit is a feature.** At any moment the customer can freeze the entire semantic estate — ontology, all entity data, link graph, transform definitions, lineage, ER decisions — into a self-contained portable bundle (AMBER, §7) and re-host on-prem or on any other platform, losing only the live autonomous-update capability. Anti-lock-in is a trust mechanism that *accelerates* adoption by the exact buyers (regulated, sovereignty-sensitive) who need this product most.

### 1.2 Notation and artifacts

Sources S = {S_1..S_n} (relational DBs, files, document corpora, APIs) emit change sets ΔS_i^(t) per cycle t via CDC. A **source atom** a is the smallest addressable unit of evidence (cell, field, text span) with a stable content-addressed URI. A^(t) is the live atom set.

OntoForge maintains, per cycle:

- **Ontology** O^(t) = (C, ≤_C, P, ax): classes C with a subsumption partial order ≤_C (the induced taxonomy, §3.4), properties P (with datatypes, units, cardinalities, functional-dependency annotations), and axioms ax in OWL 2 RL/QL; plus SHACL shapes Σ^(t).
- **Entity store state** H^(t): the materialized canonical objects, property values, and links held in HEARTH (§4), each value carrying a bi-temporal validity interval and an interned provenance term.
- **Transform graph** T^(t): a DAG of declarative, versioned transform definitions (most synthesized by ANVIL, §5) mapping raw source layers to conformed and entity layers.
- **Entity partition** Π^(t) with stable URIs; **provenance map** ρ assigning every derived element a term in the provenance semiring N[X] (v1 §3.3, extended in §9).
- **Semantic layer** M^(t): metrics, dimensions, and join-path metadata *derived from* O^(t) (not hand-authored), consumed by LODESTONE and VISTA (§6).

### 1.3 The optimization problem (v2 statement)

Decisions d ∈ D^(t) now span six kinds: ER (entity match), SM (schema correspondence), REL (relationship inference), EX (extraction grounding), TX (transform-synthesis acceptance, §5), and QI (query-interpretation selection, §6). Every decision is an instance of one abstract problem — cost-sensitive selective classification with calibrated confidence and conformal deferral — resolved by the Decision Spine (§8).

**OntoForge problem (per cycle t).** Choose a routing/threshold policy π to

  maximize  E[ Q(O^(t), H^(t), Ans^(t)) ]

subject to:

  (B)  Σ_{d∈D^(t)} c_π(d) ≤ B^(t)            — budget (economy profile) or latency envelope (CRUCIBLE profile, §8)
  (H)  ∀ triple τ ∈ H^(t): ρ(τ) ≠ 0           — hallucination bound: every stored fact is derivable from atoms
  (Δ)  Work(t) = O(|ΔS^(t)| + |affected(ΔS^(t))|)  — delta-proportional maintenance (§9)
  (P)  AMBER(H^(t), O^(t), T^(t)) is complete   — the freeze-frame snapshot satisfies the §7 completeness property at all times
  (V)  H^(t) ⊨ Σ^(t) on the committed layer     — SHACL validity of canonical data

Q is the aggregate quality functional: ontology alignment vs. gold, ER F1 with calibration, transform correctness, competency-question accuracy with citation faithfulness, and dashboard-proposal acceptance rate. Constraint (P) is new in v2 and is what makes portability an *invariant*, not an export feature: the system is never allowed to evolve into a state it cannot fully snapshot.

### 1.4 Why token economics is a profile, not the thesis

v1 framed the 4-tier cascade as a cost device. v2 reframes: the cascade's durable value is **calibration, auditability, and guarantees** — every decision carries a calibrated confidence, a tier-of-record, and a provenance term, which is what regulated buyers audit and what the active-learning loop trains on. Cost-efficiency is one *deployment profile* of the same machinery (§8): the **economy profile** binds a token budget via the Lagrangian governor (for customers with large, wild data and finite wallets); the **CRUCIBLE profile** sets the budget shadow price to ~0 and re-optimizes the identical spine for quality and latency — parallel multi-model T3 ensembles, self-consistency voting, and verification chains — for customers to whom token price is noise. Same spine, different operating point on the cost–quality frontier.

---

## 2. Platform Architecture Overview

### 2.1 Layered architecture (Figure 1)

```
┌────────────────────────────────────────────────────────────────────────────────┐
│ L8 GOVERNANCE & OBSERVABILITY: audit, lineage UI, cost attribution, RBAC/ABAC,   │
│    policy propagation via provenance valuation, run history, SLA monitors        │
├────────────────────────────────────────────────────────────────────────────────┤
│ L7 AI SEARCH & ANALYTICS: LODESTONE query planner (OQIR), VISTA dashboard        │
│    synthesis, clarification dialogs, per-cell citations, derived semantic layer  │
├────────────────────────────────────────────────────────────────────────────────┤
│ L6 ONTOLOGY, GRAPH & PORTABILITY: OWL2 RL/QL + SHACL canonical artifact,         │
│    RDF-star ⇄ LPG mapping, TEMPER evolution calculus, AMBER freeze-frame export  │
├────────────────────────────────────────────────────────────────────────────────┤
│ L5 TRANSFORMATION & ORCHESTRATION: ANVIL transform synthesis, declarative        │
│    transform graph, scheduler (cron/CDC/dependency), backfills, WARDEN monitors  │
├────────────────────────────────────────────────────────────────────────────────┤
│ L4 HEARTH ENTITY-DATA STORE: bi-temporal columnar entity shards, link store,     │
│    interned provenance dictionaries, write-back Actions, raw→conformed→entity    │
├────────────────────────────────────────────────────────────────────────────────┤
│ L3 INDUCTION CORE: profiling, FD/IND discovery, semantic typing, STRATA type-    │
│    lattice induction, ER cascade, relationship fusion, triple verification       │
├────────────────────────────────────────────────────────────────────────────────┤
│ L2 DECISION SPINE: calibration, conformal deferral, two-threshold routing,       │
│    budget governor (economy) / CRUCIBLE ensemble controller (quality), AL loop   │
├────────────────────────────────────────────────────────────────────────────────┤
│ L1 COMPUTE TIERS: T0 rules │ T1 classical ML │ T2 distilled specialists │ T3 LLM  │
├────────────────────────────────────────────────────────────────────────────────┤
│ L0 FOUNDATION: provenance ledger (N[X], bi-temporal), CDC ingest, content-       │
│    addressed atom URIs, memo/semantic/prefix caches, model abstraction layer     │
└────────────────────────────────────────────────────────────────────────────────┘
```

L4, L5, L7 are new in v2. L2 and L0 remain cross-cutting: every layer routes decisions through the spine and writes provenance to the ledger.

### 2.2 End-to-end dataflow (Figure 2)

```
sources ──CDC──▶ Δ atoms ──▶ RAW layer (HEARTH, lossless, bi-temporal)
                   │
                   ▼
        profiling + FD/IND discovery + semantic typing      (L3, T0/T1 mostly)
                   │
                   ▼
        STRATA type-lattice induction ──▶ O^(t) + Σ^(t)      (taxonomy, properties, axioms)
                   │                          │
                   ▼                          ▼
        ANVIL synthesizes transforms    TEMPER diffs O^(t-1)→O^(t),
        RAW → CONFORMED → ENTITY        emits migration plan
                   │                          │
                   ▼                          ▼
        ER cascade + incremental clustering ──▶ Π^(t), canonical objects in HEARTH
                   │
                   ▼
        relationship fusion + triple verification ──▶ links, edge properties
                   │
                   ▼
        WARDEN compiles Σ^(t) → runtime expectations; monitors drift
                   │
                   ▼
        derived semantic layer M^(t) ──▶ LODESTONE / VISTA serving
                   │
                   ▼
        AMBER snapshot (on demand or scheduled): the whole estate, portable
```

Every arrow is a DBSP stream operator (§9); every box writes N[X] provenance; every diamond consults the spine.

### 2.3 What is genuinely new at the architecture level

Foundry proved the data-bearing-ontology pattern operationally; its Ontology is hand-modeled, its store is closed, and its indexed object data is uncompressed and priced by indexed volume. Warehouse semantic layers proved NL accuracy needs governed semantics; theirs are hand-authored and single-estate. Agentic data-engineering platforms (Ascend.io's Agentic Data Engineering with its metadata Intelligence Core and DataOps agents) prove demand for automation; they assist engineers building pipelines rather than inducing the semantic model itself. OntoForge's architectural claim is the closed loop none of them has: **induce → materialize → transform → validate → query → learn**, with one provenance substrate and one calibrated decision spine running through all of it.


---

## 3. The Autonomous Induction Core (Deepened)

This is the most important subsystem: the algorithms that build crazy-large ontologies autonomously. v2 deepens v1's induction design in five ways: (i) dependency discovery as a first-class evidence source, (ii) STRATA — a new hierarchical type-induction algorithm, (iii) unit/dimension and event/temporal modeling, (iv) n-ary and qualified relations, and (v) TEMPER — a new ontology-evolution calculus. Throughout, "scale" means: millions of entities, thousands of induced types, hundreds of sources [design envelope].

### 3.1 Evidence substrate: profiles, dependencies, and sketches

Every column/field/span stream is profiled incrementally (T0): type signature, cardinality, null rate, value distribution sketches (KLL quantile sketches for numeric, HyperLogLog for cardinality, k-MinHash for value-set similarity), format signatures (regex lattice over samples), and token-distribution embeddings (T1 semantic typing, Sherlock/Sato-class, upgraded to a table-foundation-model encoder where available). The composite is the **profile sketch** φ(p) — v1's memo key, now promoted to the universal feature object consumed by every induction algorithm.

**Functional dependencies (FDs) and keys.** We run HyFD-style hybrid FD discovery: sample-driven candidate generation (agree-set analysis on row pairs) alternating with lattice-based validation, which is the fastest known exact approach and degrades gracefully to approximate FDs (AFDs with confidence) on dirty data. Discovered FDs feed: key detection (minimal FDs X→all), normalization-aware type proposals (an FD cluster X→Y1..Yk inside a wide table is evidence of a latent entity keyed by X — the classic 3NF decomposition signal, here used generatively), and ANVIL's search pruning (§5). 

**Inclusion dependencies (INDs).** BINDER-style divide-and-conquer IND discovery over the value-index, refined n-ary via apriori on validated unary INDs (v1 G3). INDs + name/type/cardinality scoring yield join candidates that become object-property evidence.

**Why dependencies matter doubly in v2.** Auto-Pipeline's central insight — that implicit table constraints such as FDs and keys can be exploited to drastically constrain an otherwise underspecified synthesis search — generalizes across OntoForge: FDs/INDs constrain (a) which latent types STRATA may posit, (b) which transforms ANVIL may synthesize, and (c) which join paths LODESTONE may traverse. One dependency-discovery pass, three consumers.

### 3.2 Semantic typing and unit/dimension induction

Column-level semantic types (T1 classifier over φ(p), escalating per the spine) are extended with **physical dimension inference**: a T0/T1 pass parses unit tokens (kg, °F, USD, knots) from headers, values, and adjacent doc spans into a dimension vector over the SI basis plus currency/count pseudo-dimensions; properties carry (dimension, canonical-unit, conversion) annotations. Two properties may only be merged by schema canonicalization if their dimension vectors match; ANVIL auto-synthesizes conversion transforms when sources disagree on units (a top-3 real-world silent-corruption class). Conflicting or novel units escalate to T3 once, memoized on φ(p).

### 3.3 From columns to candidate types

Candidate entity types arise from four generators, each emitting (candidate, confidence, provenance):
- **G-table:** a table with a detected key is a candidate type (its non-key FD-closure columns are properties).
- **G-decomp:** an FD cluster inside a wide/denormalized table posits a latent type (the normalization signal of §3.1).
- **G-doc:** document NER/typing yields mention-type candidates (T2 extractor, EDC-style define/canonicalize).
- **G-join:** a high-score IND hub (many tables referencing one value domain) posits a shared reference type even when no source table materializes it (e.g., five tables all referencing airport codes ⇒ an Airport type with no backing table).

These candidates are the input objects of STRATA.

### 3.4 NEW TECHNIQUE 1 — STRATA: Stratified Type-Lattice Induction

**Problem.** Given candidate types {τ_i} with profile sketches and extensional samples, induce a class taxonomy (C, ≤_C) — thousands of classes, sensible depth, named, axiomatized — autonomously, incrementally, with calibrated admission. Flat type lists (what most LLM-KGC systems produce) are not ontologies; hierarchy is what powers inheritance, query generalization, and LODESTONE's interpretation search. The LLMs4OL challenge series frames exactly this as Taxonomy Discovery (inducing is-a relations between types); 2025's strongest entries are hybrid embedding+classifier+LLM cascades, and Chain-of-Layer shows iterative LLM layering works but is unconstrained by data evidence. STRATA's delta: ground the lattice in *extensional* evidence via Formal Concept Analysis (FCA), then use the cascade only to prune, name, and axiomatize.

**Formal construction.** Build a formal context K = (G, M, I): objects G = candidate types and sampled instances; attributes M = discretized profile-sketch features (has-property-p, dimension-d, format-f, value-overlap-bucket-with-domain-v, doc-type-token-w); I the incidence relation. The concept lattice B(K) — all (extent, intent) pairs closed under the Galois connection — is the *complete* candidate subsumption structure: concept (A1,B1) ≤ (A2,B2) iff A1 ⊆ A2, which is exactly extensional is-a. FCA gives us soundness (every lattice edge is evidenced by shared attributes) but raw B(K) is worst-case exponential. STRATA controls size in three ways:

1. **Iceberg restriction.** Keep only concepts with extent support ≥ σ (iceberg lattice), σ chosen so |B_σ(K)| ≤ K_max [design target: K_max = 10·|expected classes|]. Complexity: TITANIC/CHARM-class iceberg construction is near-linear in the number of frequent closed itemsets retained.
2. **Attribute clarification & reduction.** Standard FCA preprocessing (merge equivalent attributes, drop reducible ones) — on profile-sketch attributes this typically shrinks M by a large constant factor because sketch features are highly correlated.
3. **Spine-gated admission.** Each surviving concept is a *decision*: admit-as-class / merge-into-parent / discard. T1 scores structural quality (support, intent distinctiveness vs. parent, stability index — the standard FCA concept-stability measure); ambiguous band escalates to T2 (a distilled judger fine-tuned on prior admissions); genuinely novel concepts reach T3 once for **naming + definition + axiom proposal** (disjointness with siblings, domain/range of characteristic properties), constrained-decoded against the OWL vocabulary and memoized on the concept's intent hash. Conformal gating per the spine: a concept is auto-admitted only when its prediction set is singleton at level α.

4. **Incremental maintenance.** New candidate types/instances arrive as deltas; we maintain B_σ(K) with AddIntent-style incremental lattice insertion (amortized cost proportional to the affected order filter, not the lattice), satisfying the (Δ) constraint. Concept identity is anchored on intent hashes so admitted classes have stable URIs under lattice motion; extent churn that flips an admission decision is routed back through the spine as a TEMPER operation (§3.6), never silently.

**Output.** Admitted concepts become OWL classes with ≤_C from the lattice order (transitively reduced), characteristic intents become property domain/range axioms and SHACL shapes, and T3-proposed names/definitions are attached with provenance. Multiple inheritance is permitted exactly where the lattice supports it (FCA's native strength over tree-only taxonomy inducers like Chain-of-Layer).

**Complexity.** Context build O(|G|·|M|) with sketches already computed; iceberg lattice output-sensitive; admission decisions |B_σ| · spine cost (dominated by T1; T3 only on novel intents). Incremental insertion: O(affected filter). **Failure modes:** (a) attribute discretization too coarse → lattice collapses (mitigate: per-feature adaptive binning validated against gold benchmarks); (b) support threshold hides rare-but-real types (mitigate: G-join and G-doc candidates bypass σ with spine review); (c) LLM naming drift across runs (mitigate: memoized names keyed on intent hash; renames only via TEMPER).

**Benchmarks/targets.** LLMs4OL Task C suites (taxonomy F1 vs. gold is-a pairs) [target: ≥ published 2025 challenge-winner F1 on C1/C5 subtasks]; gold-ontology alignment ≥85% on the hero corpus including hierarchy edges, reported as precision/recall over (class, parent) pairs [design target]; lattice-maintenance cost sublinear in |K| on staged delta loads [design target].

### 3.5 Events, time, and n-ary relations

**Event induction.** A candidate type is event-like when its intent contains ≥1 timestamp-dimension property + ≥2 object-property references (actor/object patterns) + append-mostly CDC behavior (T0 signal: inserts ≫ updates). Event classes get bi-temporal treatment natively in HEARTH (occurrence time = valid time) and induce qualified relations: an ASRS incident is not a binary link Aircraft—Airport but an event node with role properties (aircraft, location, phase, narrative), which is the n-ary pattern OWL handles by reification and HEARTH stores as first-class event objects. The induction rule: when relationship fusion (v1 G4) finds ≥3 mutually-correlated binary signals among the same key set, propose an n-ary event/relation class instead of pairwise links — scored and gated by the spine.

**Temporal semantics.** Following the bi-temporal model proven by Graphiti/Zep — four timestamps per fact: (t_valid, t_invalid) for when the fact held in the world, (t_created, t_expired) for when the system believed it — every HEARTH property value and link carries both intervals, and new evidence *invalidates* (closes the system interval of) superseded facts rather than deleting them (§4.3). This is what lets LODESTONE answer "as-of" questions ("what was the fleet status on March 3?") and lets corrections be audited rather than destructive.

### 3.6 NEW TECHNIQUE 2 — TEMPER: the Ontology Evolution Calculus

**Problem.** O^(t) changes daily. Uncontrolled schema change breaks: stored data (H must migrate), transforms (T references types/properties), saved queries and dashboards (LODESTONE/VISTA artifacts compile against O), and historical snapshots (AMBER bundles must remain interpretable). Ontology-evolution literature catalogs change operations; what production systems lack is a *closed calculus* where every change carries its own data migration and compatibility certificate. TEMPER provides it.

**Operator set.** A typed, minimal-complete set of schema-change operators, each a triple (precondition, ontology rewrite, migration synthesis):

| Operator | Ontology effect | Synthesized migration (forward / backward) |
|---|---|---|
| AddClass(c, parent) | C ∪ {c}, c ≤ parent | none / drop-class view |
| SplitClass(c → c1, c2; discriminator δ) | replace c | route instances by δ (a T0/T1 classifier or predicate); backward = union view c1 ∪ c2 |
| MergeClasses(c1, c2 → c) | replace pair | union + property alignment map; backward = δ-split view using retained discriminator column |
| PromoteProperty(p of c → class c_p) | new class + link | group-by p, mint entities (through the ER cascade so promoted values deduplicate); backward = rejoin view |
| DemoteClass(c_p → property p) | inverse of promote | flatten via join; backward = regroup view |
| Generalize(p: c → parent) / Specialize | move property in ≤_C | widen: no data move; narrow: spine-gated instance check + quarantine of violators |
| RetypeProperty(p, dtype/unit) | datatype/unit change | ANVIL-synthesized conversion transform, verified on samples |
| AddFacet / RetireFacet | SHACL shape change | WARDEN recompiles expectations; no data move |
| RenameClass/Property | label change | URI stable (labels are annotations); zero migration |
| RetireClass(c) | tombstone | freeze extent read-only; excluded from new writes |

**Closure and round-trip properties.** (i) Every operator's forward migration is a DBSP-expressible transform over HEARTH (hence incremental and provenance-carrying); (ii) every operator declares a backward *view* sufficient to answer any query written against O^(t-1) on data stored under O^(t) — this is the **snapshot-queryability theorem** TEMPER must maintain: for the supported operator set, ∀ query q valid under O^(s), s < t, there exists a rewriting q' under O^(t) with equal answers on the as-of-s bi-temporal slice. The proof obligation per operator is discharged by construction (each backward view is the operator's categorical inverse on the data it touched); composite changes compose views. (iii) Operator sequences are recorded as the **morphism ledger** — the ontology's own provenance — so AMBER snapshots embed the exact operator path between any two ontology versions, and a departing customer can replay or invert it.

**Autonomy integration.** STRATA's lattice motions, ER cluster splits/merges (v1's stable-URI protocol), and schema drift detected by WARDEN all *emit TEMPER operations* rather than mutating O directly. High-impact operations (SplitClass, MergeClasses on populated types) are spine decisions with human-review escalation; low-impact ones auto-apply. **Failure modes:** discriminator drift on SplitClass (mitigate: δ versioned + monitored by WARDEN); migration cost spikes on PromoteProperty over huge extents (mitigate: lazy materialization — backward view serves reads while forward migration backfills under the scheduler).

**Benchmarks/targets.** Replay test: apply 1,000 random valid operator sequences over the hero corpus; assert snapshot-queryability (old competency queries return identical answers via rewritings) and migration cost ∝ touched extent [design target: 100% queryability preservation; zero full-table rewrites for label/axiom-only changes].


---

## 4. NEW TECHNIQUE 3 — HEARTH: the Provenance-Anchored Bi-temporal Entity Store

### 4.1 Problem and design forces

The ontology must *hold* data: canonical entity lookups in single-digit milliseconds, link traversals at graph-database throughput, analytical scans at columnar speed, full bi-temporal history, per-value provenance, user write-back, and — uniquely — the standing constraint (P) that the entire store be losslessly exportable to open formats at any time. No existing engine satisfies all simultaneously: Foundry's OSv2 achieves the serving properties by indexing into specialized object databases behind a Funnel write orchestrator, but its indexed representation is closed and uncompressed (priced as indexed GB-month) and is not the portability artifact. Lakehouse table formats (Iceberg/Delta) give open, versioned, time-travel columnar storage but no entity/link semantics or millisecond point reads. Graph stores (Kùzu's columnar adjacency, Oxigraph) give traversal but not the analytical+bitemporal+provenance ensemble. HEARTH composes them with a specific, novel layout.

### 4.2 Layout: delta-versioned columnar entity shards over an open table substrate

**Three medallion layers, all in HEARTH, all bi-temporal:**
- **RAW:** lossless source mirror, one table per source object, every cell an atom with content-addressed URI. Open format: Iceberg tables (snapshot isolation, schema evolution, time travel come free and align with our (Δ) and AMBER constraints).
- **CONFORMED:** ANVIL-transformed, type/unit-normalized rows still keyed by source identity.
- **ENTITY:** canonical objects post-ER. Physical unit = the **entity shard**: a columnar chunk of one class's instances, sorted by entity URI, with per-property column segments.

**The entity shard record model.** For entity e, property p, the cell is not a scalar but a small ordered set of **versioned value cells**:

  cell(e,p) = { (v_k, [t_valid, t_invalid), [t_created, t_expired), prov_k, conf_k, src_rank_k) }

— value, world-time interval, system-time interval (Graphiti-style four-timestamp bi-temporality), provenance term, calibrated confidence, and survivorship rank (which source won the merge and why). The *current* value is the unique cell with open intervals and top rank; history is the rest. Columnar encoding: current values in a dense vector (fast scans/serving); historical cells in a side segment, delta-encoded, accessed only by as-of queries. This makes "ontology data cannot be compressed" — Foundry's stated property — a non-axiom: HEARTH's canonical layer compresses like any columnar store because history and provenance are factored out of the hot path.

**Interned provenance dictionaries (the storage novelty).** Naively storing an N[X] polynomial per value cell is ruinous. Observation: provenance terms are massively repetitive — most values in a shard share derivation *shape* (same transform chain, same merge rule), differing only in leaf atoms. HEARTH therefore stores provenance as a two-level interning: (i) a per-shard **derivation-shape dictionary** (the polynomial with leaf variables abstracted — typically tens of distinct shapes per shard), and (ii) a per-cell compact reference (shape-id + leaf-atom-id array). Expected overhead: a few bytes per cell amortized [design target: ≤8 bytes/cell median]. Exact invalidation (§9) becomes a dictionary-side join: changed atoms → affected shape instances → affected cells, without touching unaffected columns.

**Link store.** Links are first-class edges (subject URI, predicate, object URI, edge-property cells with the same versioned-cell model), stored twice: (a) as Iceberg edge tables (open, scannable, exportable), and (b) as a CSR-style columnar adjacency index per (class, predicate) for traversal — the Kùzu-validated layout for vectorized many-hop expansion. (b) is a *derived index* rebuilt incrementally from (a); only (a) is canonical, preserving constraint (P).

**Serving indexes.** Point-lookup (entity URI → shard offset) via a learned/B-tree hybrid per shard; secondary value indexes auto-created for properties that LODESTONE's query log shows are filter-hot (spine-gated index-advisor decisions); full-text + vector indexes over document atoms and entity summaries for hybrid retrieval. All serving indexes are derived, disposable, and excluded from AMBER (they rebuild on import — a documented, capability-neutral loss).

### 4.3 Write paths

1. **Pipeline writes** (the Funnel analogue): ANVIL transform outputs and ER merge results land as Iceberg commits to CONFORMED/ENTITY, then incrementally refresh adjacency and serving indexes. Every commit is a DBSP delta batch; provenance terms are computed in-flight.
2. **User write-back (Actions).** Following the OSv2 lesson — user edits only via typed Actions, never raw writes — an Action is a declared operation (set property, link/unlink, create object) with SHACL pre-validation, an actor identity, and a provenance leaf of kind `human-edit`. Action edits occupy the top survivorship rank by default (a human override beats pipeline values) but are *versioned cells like any other* — a subsequent source change does not silently clobber them; conflicts route through the spine to the review queue.
3. **TEMPER migrations** write through the same path as pipeline writes, tagged with the operator id, so schema-change data motion is fully lineage-tracked.

### 4.4 Read paths and consistency

Reads declare a **temporal stance**: `current` (default), `as-of(world-time)`, `as-known-at(system-time)`, or `audit(both)`. Snapshot isolation per Iceberg snapshot for analytical reads; serving reads hit the current-value vectors + adjacency at index freshness ≤ one refresh interval [design target: p99 entity point-read < 10 ms; 2-hop traversal over 10^6-edge neighborhoods < 500 ms; analytical scan ≥ 80% of raw-Parquet scan rate on the same hardware]. Cross-layer lineage is a constant-time hop: any canonical cell → provenance ref → atoms → RAW rows.

### 4.5 Failure modes and acceptance tests

Failure modes: version-cell bloat on hot properties (mitigate: interval coalescing + history tiering to cold storage); adjacency-index staleness under burst writes (mitigate: read-path fallback to edge-table scan with planner hinting); survivorship oscillation between conflicting sources (mitigate: hysteresis + spine review, mirroring the ER anchor rule). Acceptance tests (§11): bitemporal correctness suite (insert/correct/retroactive-change scenarios with gold answers per stance); provenance-overhead budget; export-import idempotence (HEARTH → AMBER → HEARTH bit-equivalent canonical state); Action conflict matrix.

---

## 5. The Transformation & Orchestration Layer

### 5.1 The transform graph

A transform is a declarative, versioned artifact: typed inputs (HEARTH tables/objects), a body in a constrained relational DSL (a SQL dialect subset + a vetted scalar-function library + ER/extraction operator nodes), typed outputs, declared expectations, and computed column-level lineage (parse the body; map output columns to input columns/functions — the SQLMesh-proven approach of deriving lineage and change-impact from the SQL AST itself). Transform versions are content-fingerprinted; the orchestrator runs **virtual environments**: a changed transform spawns shadow outputs reusing unchanged upstream materializations by fingerprint, enabling zero-copy dev/staging and instant promotion — the same memoization theory as our schema memo keys (v1 §3.4), applied to whole table states.

**Orchestration.** The scheduler triggers on cron, CDC arrival, and dependency completion; it is delta-native (a run processes the input Z-set delta, §9). Backfills are *bounded replays*: select a world-time or system-time window, invalidate via provenance, recompute exactly the affected partition set — never "rerun everything since March." Retries with idempotent commit semantics (Iceberg atomic swap); SLAs and run history in L8; WARDEN gates promotion.

### 5.2 NEW TECHNIQUE 4 — ANVIL: By-Ontology Transform Synthesis

**Problem.** Someone must write the RAW→CONFORMED→ENTITY transforms for every source — historically the bulk of data-engineering labor. Programming-by-example (FlashFill/PROSE) automates single string transforms; Auto-Pipeline showed multi-step pipelines can be synthesized **by-target** — give input tables and a target table, and let FDs/keys constrain the search — reaching 60–70% synthesis success on real ≤10-step pipelines. ANVIL's insight: **OntoForge already possesses the target.** The induced ontology O^(t) + shapes Σ^(t) *are* a machine-checkable target specification for every source: required properties, datatypes, units, key constraints, link cardinalities. Transform synthesis becomes by-ontology: synthesize a program mapping source tables into the entity layer such that outputs satisfy Σ and align with O.

**Algorithm (tiered, per source object).**
1. **T0 — fix detectors.** Pattern-match the standard corruption taxonomy against profile sketches: encodings, date/number locale formats, unit mismatches (§3.2 dimension vectors), trim/case/null-token normalization, header rows in data, pivoted layouts (detected via low-cardinality column-name-as-value signatures). Each detector emits a parameterized library transform — no search.
2. **T1 — constrained program search.** For residual gaps between source profile and ontology target: enumerative search over the DSL operator grammar (project, rename, cast, split/extract via PROSE-style string-transform synthesis, join along discovered INDs, group-by along discovered FDs, unpivot, dedupe), with the Auto-Pipeline pruning rule generalized — a candidate program is discarded the moment any intermediate violates a discovered FD/IND or a Σ shape that the target requires. Search is beam-limited; candidates are **verified** on held-out row samples: a program is admitted only if outputs satisfy Σ on the holdout and the provenance-checked equivalence test passes (every output cell's term derives only from intended input atoms — catching accidental cross-row leakage that value-equality tests miss; this is a provenance-native verification unavailable to prior PBE systems).
3. **T2/T3 — semantic synthesis.** Gaps requiring meaning (derive `age` from `dob`; map free-text status codes to an induced enum; reconcile fiscal calendars) go to the distilled synthesis specialist (T2), escalating to the frontier (T3) with constrained decoding into the DSL grammar — never free-form code — and the same holdout+provenance verification. Verified T3 syntheses are distillation training data (v1 G10), so synthesis capability accretes.
4. **Acceptance is a spine decision** (kind TX): calibrated by holdout pass rate, sample coverage, and program complexity prior (shorter programs preferred, MDL-style); ambiguous syntheses ship to the human review queue *as readable DSL diffs* — the analyst approves a transform, not a black box.

**Properties.** Every synthesized transform is a normal transform-graph artifact: versioned, human-readable, lineage-computed, incremental, exportable in AMBER. Autonomy never produces opaque pipelines — this is the "see your transformations" requirement satisfied jointly with autonomy. **Complexity:** T0 linear; T1 beam search b·d·|grammar| with FD/IND pruning empirically collapsing the branching factor (the Auto-Pipeline result); verification linear in holdout size. **Failure modes:** holdout unrepresentative (mitigate: stratified + adversarial sampling from profile-sketch outliers); synthesis overfit to current data (mitigate: WARDEN drift monitors auto-flag transforms whose runtime expectation pass-rate decays); DSL insufficiency (escape hatch: human-authored transform with the same contract — autonomy is a default, not a cage).

**Benchmarks/targets.** Auto-Pipeline's released benchmark (700 real pipelines) [target: ≥70% end-to-end synthesis success, matching published Auto-Pipeline, with the by-ontology variant measured additionally on synthesis-to-Σ-satisfaction]; TPC-DI as the integration-pipeline stress test [target: ≥90% of TPC-DI's defined transformations synthesized or T0-matched without human code]; unit-conversion corruption suite with zero silent failures [design targets].

### 5.3 NEW TECHNIQUE 5 — WARDEN: Expectation & Drift Sentinel Synthesis

**Problem.** Data-quality tooling (Great Expectations/dbt tests/Soda) requires humans to author checks; coverage is perpetually partial. OntoForge possesses what those tools lack: an induced, validated model of what the data *should* be. WARDEN compiles it into runtime monitors automatically.

**Construction.** Three generators: (i) **Σ-compilation** — every SHACL shape lowers to a streaming expectation over transform outputs (datatype, cardinality, pattern, range, link-integrity), evaluated incrementally on deltas; (ii) **sketch-drift sentinels** — for every profiled stream, sequential drift tests on the sketch vector (population-stability index on quantile sketches, MinHash-Jaccard shift on value sets, cardinality-ratio control charts), with change-points routed: schema drift → TEMPER proposal; distribution drift → ANVIL re-verification of dependent transforms; quality drift → quarantine + alert; (iii) **contract emission** — per source, WARDEN materializes the implied **data contract** (expected schema, types, units, keys, freshness cadence inferred from CDC history) as a human-readable artifact; upstream violations are detected at ingest, before they poison the entity layer. Calibration: sentinel thresholds are spine-managed (a drift alarm is a decision with FP/FN costs), so alert precision is tunable and measured — addressing alert-fatigue, the empirical killer of DQ tooling. **Targets:** ≥95% of Σ-expressible constraints auto-monitored with zero human authoring; drift-detection median lag ≤1 cycle; alert precision ≥0.8 at recall ≥0.9 on an injected-corruption suite [design targets].


---

## 6. AI Search & Analytics — the Payoff Layer

### 6.1 Why raw NL2SQL cannot carry this product

The 2025–2026 evidence is unambiguous. On BIRD (12,751 questions, 95 real databases), the best published systems sit near 73–76% execution accuracy (CHASE-SQL at 73.0% test via multi-candidate generation + selection; Snowflake's Arctic-Text2SQL-R1-32B at 71.8% via execution-aligned RL) — and execution accuracy itself disagrees with human judgment on a large fraction of items (the FLEX study). On Spider 2.0's enterprise-scale schemas (thousands of columns, multi-dialect, multi-step), GPT-4-class models collapsed to single-digit accuracy versus ~87% on academic Spider 1.0. Meanwhile Cortex Analyst attains its claimed ~90% only *inside a hand-authored semantic view*. The lesson: **accuracy lives in the semantic model, not the language model**, and schemas at enterprise scale defeat direct linking. OntoForge's structural advantage: it *induces* the semantic model as a by-product of induction — O^(t), the FD/IND join graph, unit annotations, and the metric layer M^(t) derived from them — and spans the whole estate including documents, which warehouse-native tools exclude by scope.

### 6.2 NEW TECHNIQUE 6 — LODESTONE: Ontology-Grounded Query Planning over OQIR

**Problem.** Map an arbitrary NL question — up to CEO-grade convolution across many systems — to a correct, cited, multi-backend execution plan; ask at most one crisp clarification, and only when genuinely necessary.

**OQIR: the typed intermediate representation.** A LODESTONE plan is a term in a small typed algebra over the *induced ontology*, not over physical schemas:

- **Types:** EntitySet⟨c⟩ for c ∈ C; Measure⟨dimension-vector, unit⟩; Dim⟨p⟩; Interval (bi-temporal stance); Doc (evidence spans).
- **Operators:** select(c, predicate) → EntitySet⟨c⟩; traverse(E, link ℓ) → EntitySet⟨c'⟩ (typed by ℓ's range, only along O's link graph); aggregate(E, measure m, group-by dims) → Table; compare/trend/rank (analytical combinators over Tables); ground(E|Table) → cited atoms; asOf(stance) wrapping any subterm; textJoin(E, doc-predicate) for structured↔unstructured hops (e.g., entities mentioned in incident narratives matching a phrase).
- **Well-formedness:** every traverse must follow an existing link type; every aggregate's measure must be dimension-consistent (unit algebra from §3.2 — you cannot sum USD with EUR without an injected conversion node); every leaf binds to ontology elements with recorded provenance. Type-checking OQIR statically eliminates the dominant NL2SQL error classes (phantom joins, wrong-grain aggregation, unit mixing) *before execution* — errors the BIRD-style pipelines only catch, if at all, by running SQL.

**Interpretation as a spine decision (kind QI).** Stage 1 — **grounding:** link question spans to ontology elements via hybrid retrieval (lexical + embedding over class/property/metric names, synonyms accumulated from usage, and value-index probes for literals — AutoLink-style schema exploration, but over O instead of raw columns, which is orders smaller and semantically labeled). Stage 2 — **candidate generation:** T2 (a distilled OQIR generator) emits k candidate OQIR terms under grammar-constrained decoding; T3 generates only for novel question shapes (memoized on a question-template sketch). Stage 3 — **scoring:** each candidate gets calibrated P(correct | question, grounding, type-check result, historical acceptance of similar templates); conformal set Γ_α over candidates.

**Minimal-entropy clarification.** If |Γ_α| = 1 → execute. If |Γ_α| > 1, LODESTONE does not guess and does not interrogate: it computes the single discrete question that maximizes expected entropy reduction over Γ_α — formally, choose the partition of Γ_α induced by a human-answerable distinction (metric variant? time window? entity scope? — distinctions are enumerable because candidates are *typed terms*, so their disagreements are structural diffs) with maximal information gain; render it as one multiple-choice clarification. This is the disciplined version of what BIRD-INTERACT shows production systems need (multi-turn interaction for ambiguity) — but bounded: one question, only when the conformal set is non-singleton [design target: clarification rate ≤25% of questions; post-clarification singleton rate ≥90%].

**Lowering and execution-guided repair.** OQIR lowers per backend through total, type-preserving rules: entity/link subterms → HEARTH serving indexes or SPARQL-star/Cypher; aggregates → SQL over entity shards (DataFusion); textJoin → hybrid index probes. Execution is staged with guards: empty/degenerate intermediate results trigger bounded repair (re-ground the failing leaf, re-rank Γ) — execution-guided self-correction, proven on BIRD-class systems, but operating on the IR, not on SQL strings. Every answer cell carries its provenance term → per-cell citations to source atoms, rendered as clickable evidence. If repair exhausts: return "cannot answer reliably," with the failed grounding shown — the system knows when it is wrong, which the FLEX findings show matters more than two points of EX.

**Failure modes:** grounding misses on tribal vocabulary (mitigate: synonym accretion from accepted clarifications — every clarification answer is labeled training data); OQIR expressiveness gaps for exotic analytics (escape hatch: spine-gated raw-SQL tool with the same citation discipline, flagged as lower-assurance); latency on deep multi-hop plans (mitigate: plan-cost model + materialized aggregate advisor in HEARTH).

**Benchmarks/targets.** BIRD dev [target: ≥ published SOTA EX when run *through* the induced semantic layer on BIRD's schemas]; Spider 2.0-Lite [target: ≥3× the published GPT-4 baseline]; the **CEO-question suite** (§12.4) [target: ≥70% fully-correct with 100% citation coverage; 0% confidently-wrong]; MetaQA/MuSiQue multi-hop for the graph path [targets per v1].

### 6.3 NEW TECHNIQUE 7 — VISTA: Vague-Spec Dashboard Synthesis

**Problem.** "Give me a dashboard on supplier risk" is an underspecified intent, not a query. NL2VIS research (nvBench; LIDA-style generation) maps *specific* utterances to charts; the analyst reality is vagueness. VISTA treats vagueness as a ranking problem over the derived semantic layer.

**Algorithm.** (1) **Intent grounding:** map the utterance to a region of M^(t) — candidate metric set, dimension set, entity scope, time grain — via the same grounding machinery as LODESTONE, but returning a *weighted lattice* of (metric × dimension × filter) combinations rather than one term. (2) **Composition search:** enumerate dashboard candidates as small sets of OQIR analytical terms subject to composition constraints (one primary KPI block, complementary breakdowns, no redundant grain, dimension diversity) — a constrained optimization scored by: grounding weight, historical usage priors (what this org actually monitors, mined from the query/provenance ledger), data-health (WARDEN status of feeding pipelines), and chart-type appropriateness rules from visualization theory (measure cardinality × dimension type → mark/encoding via standard NL2VIS practice, targeting Vega-Lite). (3) **Spine-gated proposal:** present the top-k (default 3) ranked dashboards as live, runnable previews with per-chart citations and an explanation of interpretation choices; analyst selection/edits are labeled feedback. Every accepted dashboard becomes a versioned artifact compiled against O — TEMPER's morphism ledger then auto-migrates or flags it on schema evolution, killing the silently-broken-dashboard failure class endemic to BI estates. **Targets:** nvBench [≥ published SOTA chart-accuracy when intent is specific]; vague-intent acceptance study: ≥60% of vague requests yield an accepted proposal within top-3 without re-prompting [design target].

---

## 7. NEW TECHNIQUE 8 — AMBER: the Freeze-Frame Snapshot

**Definition.** AMBER(t) is a self-contained, versioned bundle: (1) O^(t) as OWL 2 + SHACL + JSON-LD contexts; (2) **all entity data** — ENTITY and CONFORMED layers as Iceberg/Parquet shards including version-cell history (bi-temporal columns materialized) — plus RAW on request; (3) the link graph as RDF-star dump *and* edge Parquet; (4) the transform graph: every transform's declarative DSL body, versions, lineage maps, schedules; (5) the morphism ledger (TEMPER history) and ER decision records (match scores, tier, evidence); (6) the provenance ledger extract with the interning dictionaries; (7) generated documentation (class/property definitions, contracts, run-book); (8) a signed manifest with content hashes and a capability declaration.

**Completeness property (the formal product guarantee).** Let Q_OF be OntoForge's query capability and Q_AMBER the capability of a reference open stack (any SPARQL 1.1 store + any Iceberg-reading SQL engine) over the bundle. AMBER is **complete** iff: for every query q answerable in OntoForge at time t under any temporal stance, there exists q' over the bundle with identical answers and identical citations — *modulo the declared capability-loss set L*, where L is exactly: live autonomous induction/update, the trained T2 specialists and calibration state (OntoForge runtime assets, not customer data), serving-index latency profiles (indexes rebuild), and LODESTONE/VISTA NL interfaces (the *compiled artifacts* — saved OQIR plans lowered to portable SQL/SPARQL — are included; the NL front-end is not). Nothing in L is the customer's data or logic. The completeness test is executable: the release pipeline round-trips hero-corpus AMBER bundles into Jena/Oxigraph + DuckDB/Trino and replays the full competency-question and dashboard suite [acceptance: 100% answer+citation equality].

**Why a feature, not a bug.** The buyer's worst case is priced and finite: leave with everything, lose only the autonomy and have a static—but complete, documented, standards-based—semantic estate. This converts the procurement objection ("another proprietary ontology platform") into the differentiator, and it is honest: the moat (§15) never depended on holding data hostage.

---

## 8. The Decision Spine, Updated: Two Operating Profiles

The spine (v1 §3.1) is unchanged in form — calibrated scores, two-threshold selective rules, conformal deferral, Lagrangian budget coupling — and now serves six decision kinds (ER, SM, REL, EX, TX, QI). v2 adds the profile dimension:

**Economy profile.** As v1: shadow price λ tuned so Σc_π(d) = B^(t); fail-closed quarantine on exhaustion. For the customer whose data is large and wild and whose token budget is real.

**CRUCIBLE profile (quality/latency-optimal).** Set λ→0; re-derive thresholds with c_esc ≈ latency-only. The optimal policy changes shape, not substance: (i) the escalation band *widens* (escalate on any non-trivial ambiguity); (ii) T3 becomes a **verification ensemble** — parallel heterogeneous frontier models with self-consistency voting (the CHASE-SQL-validated pattern of diverse candidate generation + learned selection, generalized to all decision kinds), plus adversarial verification chains for high-stakes decisions (a second model attacks the first's grounding; disagreement → human queue); (iii) T2 still runs — not to save tokens but as an *independent agreement signal* that feeds calibration and as the latency fast-path for interactive QI decisions. The conformal guarantees are identical in both profiles; what differs is the achievable (coverage, accuracy) operating point. Same spine, two markets.

---

## 9. Incrementality and Provenance, Extended Through the Platform

v1 established the pipeline as DBSP operators over Z-sets with N[X] provenance. v2 extends the substrate through the new layers:

**Transforms are operators.** Every ANVIL/authored transform is, by DSL construction, a composition of linear and bilinear Z-set operators — so the *entire* transform graph incrementalizes mechanically: a daily delta flows RAW→CONFORMED→ENTITY touching work proportional to the delta and its bilinear join fan-out. Backfills are window-bounded replays driven by provenance invalidation (changed atoms → interned-shape join → affected cells/partitions), making constraint (Δ) hold across the platform, not just the induction core.

**Bi-temporal provenance.** Atom tokens gain validity annotations; the semiring product is extended pointwise over interval intersection (a derived fact's valid interval is the intersection of its supports' intervals under ×, the union under + of alternative derivations). This single extension is what makes as-of queries *citable*: the provenance term of an as-of answer evaluates only over atoms valid in the stance interval. The universality of N[X] (one computation, many valuations) now covers: citations, confidence, cost, invalidation, access-control (§10), and time.

**Identifier stability** carries over: entity URIs via the anchor protocol (v1), class URIs via STRATA intent hashes, transform URIs via content fingerprints, all churn-tracked in ledgers — the precondition for AMBER bundles being diffable across time.

---

## 10. Security, Governance, Multi-tenancy

**Policy as a valuation.** Access control is a semiring valuation: atoms carry classification labels from a security lattice; a derived value's effective label is the lattice-join over its provenance term. Row/column/property policies therefore *propagate automatically through every transform and into every LODESTONE answer* — a user sees an answer cell only if cleared for its label, and redaction is provenance-exact (the cited atoms a user cannot see are elided, and the answer is marked partial). This is structurally stronger than per-table grants, and it falls out of the substrate.

**Action authority.** Write-back Actions carry role requirements declared on the ontology (who may edit which property of which class), versioned with TEMPER. **Tenancy:** single-tenant data planes (per-customer HEARTH + ledger; non-negotiable for the target buyer), shared control plane for model serving with strict prompt/data isolation; T2 specialists are per-tenant artifacts (they are trained on tenant adjudications and are included in no other tenant's runtime — and are excluded from AMBER as OntoForge runtime IP, per §7). Audit: every decision, edit, query, and export is a ledger row; the audit UI is a provenance browser.


---

## 11. Build Specification for the Agentic Coding Team

This section is the implementation contract. Modules are listed in **dependency order**; each specifies purpose, interface (conceptual signatures), data model, invariants, and acceptance tests. An agentic coder should be able to take any module row, its referenced section, and the test list, and produce a reviewable implementation without further product decisions.

### 11.1 Technology stack (proposed defaults; deviations require a written justification referencing the affected acceptance tests)

| Concern | Choice | Justification |
|---|---|---|
| Core services language | Rust | storage, ledger, DBSP runtime, OQIR type-checker: performance + correctness-critical |
| ML/induction language | Python | profilers, matchers, STRATA admission models, T2 training; PyO3 boundary to core |
| Columnar substrate | Apache Arrow + DataFusion | in-memory format + embeddable SQL execution for shards and OQIR lowering |
| Open table format | Apache Iceberg (Rust impl) | snapshots, schema evolution, time travel; the AMBER data layer |
| Graph serving | Kùzu (embedded) + Oxigraph | columnar adjacency traversal; SPARQL-star canonical queries |
| Incremental runtime | DBSP-style Z-set engine (own, minimal) | §9 is the spine of (Δ); existing DBSP (Feldera) studied/borrowed, but provenance hooks are bespoke |
| FD/IND discovery | HyFD + BINDER reimplementations | Metanome-published algorithms; ours must be incremental-capable |
| ER components | Splink-style FS core, DeepBlocker-style blocking, Ditto-class T2 | per v1; T2 served via vLLM |
| T2 serving | vLLM on tenant GPU pool | throughput + LoRA-per-tenant adapters |
| T3 access | model-abstraction layer, OpenAI/Anthropic adapters, constrained decoding | batch + prompt-cache aware |
| Validation | SHACL engine over Oxigraph; WARDEN compiler bespoke | §5.3 |
| Viz target | Vega-Lite | NL2VIS standard target |
| Orchestrator | bespoke delta-native scheduler (Dagster studied for ergonomics, not embedded) | virtual environments + provenance invalidation are non-standard |
| UI | TypeScript/React | review queues, lineage browser, Ask surface, dashboard previews |

### 11.2 Module inventory

**M0 — Atom & Ledger Core** (§1.2, §9; depends: none)
Interface: `register_atoms(source_id, batch) → [atom_uri]`; `append_decision(record)`; `append_artifact(artifact, prov_ref)`; `invalidate(Δatoms) → affected set`; `valuate(prov_ref, semiring) → value`.
Data model: ATOM / DECISION / ARTIFACT / PROV_SHAPE / PROV_EDGE / CALIB_SAMPLE tables (v1 §4.4) + interning dictionaries (§4.2) + bitemporal columns.
Invariants: append-only; content-addressed atom identity; every ARTIFACT has non-zero provenance.
Tests: dedup-on-content; invalidation exactness on synthetic derivation DAGs (no over/under-invalidation); valuation homomorphism property checks on random polynomials; 10^8-atom scale ingest throughput target.

**M1 — CDC & Ingestion** (depends M0)
Interface: `pull(source) → Δbatch` per connector (Postgres logical decoding, file hash-diff, doc snapshot-diff, API cursors).
Tests: lossless RAW mirror (byte-equality audits); delta completeness on mutation fuzzing; atom-URI stability across re-pulls.

**M2 — Decision Spine** (§8; depends M0)
Interface: `decide(kind, features, candidates, profile) → {outcome, conf, conformal_set, tier, cost}`; `recalibrate(kind, samples)`; `set_profile(economy(B) | crucible(latency))`.
Tests: ECE ≤0.05 post-calibration on every kind's benchmark; conformal coverage within ±2% of nominal across 5 seeds; budget binding (spend ≤ B, fail-closed quarantine verified); CRUCIBLE ensemble agreement-vs-accuracy curves.

**M3 — Profiler & Dependency Discovery** (§3.1–3.2; depends M1)
Interface: `profile(stream) → φ`; `discover_fds(table) → [FD, conf]`; `discover_inds(corpus) → [IND, score]`; `dimension(column) → unit vector`.
Tests: TPC-H/DS declared-key recovery F1 ≥0.90; HyFD-parity runtime on Metanome datasets; incremental re-profile cost ∝ Δ; unit-inference suite (mixed-unit corruption corpus) zero silent misses.

**M4 — STRATA** (§3.4; depends M2, M3)
Interface: `induce(candidates, K) → lattice`; `admit(concept) → spine decision`; `insert_delta(Δcandidates)`; `emit_ontology() → (C, ≤, P, ax, Σ)`.
Tests: LLMs4OL Task C parity targets; hero-corpus hierarchy P/R ≥0.85; AddIntent incrementality (insert cost vs. affected filter, asserted on staged loads); intent-hash URI stability under permuted input order.

**M5 — ER Cascade & Clustering** (v1 G1; depends M2, M3)
Per v1 spec: blocking, FS, T2 matcher, incremental correlation clustering, anchor-stable URIs.
Tests: v1 targets (F1 ≥0.85, blocking ≥99%/≥98%, deferral ≤25%); URI-churn ≤1 per affected entity per cycle under delta streams; incremental-vs-batch F1 ratio ≥0.97.

**M6 — HEARTH** (§4; depends M0, M5; co-designed with M4 for class shards)
Interface: `commit(layer, Δcells)`; `read(entity_uri, stance)`; `traverse(uri, link, stance, depth)`; `scan(class, predicate-pushdown)`; `action(actor, op) → validated commit`; `refresh_indexes(Δ)`.
Tests: bitemporal scenario suite (retroactive correction, supersession, as-of/as-known-at gold answers); p99 point-read <10 ms at 10^7 entities; 2-hop traversal target; provenance overhead ≤8 B/cell median; Action conflict matrix; SHACL-validity of committed ENTITY layer enforced.

**M7 — Transform Graph & Orchestrator** (§5.1; depends M6, M0)
Interface: `register(transform_def) → fingerprint`; `plan(Δ|backfill window) → run DAG`; `run(dag)`; `promote(env)`; `lineage(column) → input map`.
Tests: column-level-lineage correctness on a curated SQL corpus; virtual-environment reuse (changed transform reuses unchanged upstream by fingerprint — asserted via execution counters); backfill bounds (touched partitions == provenance-predicted set); idempotent retry under injected failures.

**M8 — ANVIL** (§5.2; depends M3, M4, M7, M2)
Interface: `synthesize(source_obj, target=O,Σ) → [candidate programs + verification report]`; `accept(program) → M7 registration`.
Tests: Auto-Pipeline benchmark ≥70%; TPC-DI transformation coverage ≥90%; provenance-equivalence verifier catches seeded cross-row leakage (mutation testing); all accepted programs round-trip through DSL pretty-printer (readability invariant).

**M9 — WARDEN** (§5.3; depends M4, M7)
Tests: Σ-coverage ≥95% auto-monitored; injected-corruption detection P≥0.8/R≥0.9; drift→TEMPER/ANVIL routing verified end-to-end; alert-budget conformance.

**M10 — TEMPER** (§3.6; depends M4, M6, M7)
Interface: `propose(op)`; `apply(op) → migration plan + backward view`; `rewrite(query, from_version) → query'`.
Tests: 1,000-op replay with 100% snapshot-queryability; migration cost ∝ touched extent; morphism-ledger replay/inversion equality; compiled-artifact (dashboard) auto-migration suite.

**M11 — Ontology/Graph Export & RDF⇄LPG** (v1 G7; depends M6)
Tests: round-trip isomorphism modulo documented loss across Oxigraph/Jena/Neo4j/Kùzu; parallel query-equivalence suite.

**M12 — LODESTONE** (§6.2; depends M4, M6, M2, M11)
Interface: `ground(question) → bindings`; `candidates(question, bindings) → [OQIR]`; `clarify(Γ) → question | none`; `lower(oqir, backend) → plan`; `answer(question) → {result, citations, confidence | abstention}`.
Tests: OQIR type-checker rejects seeded ill-typed plans (unit mixing, phantom traversals) with 100% recall; BIRD/Spider2.0-Lite targets (§6.2); clarification-rate and post-clarification-singleton targets; abstention correctness (0% confidently-wrong on the adversarial slice); per-cell citation resolution to atoms 100%.

**M13 — VISTA** (§6.3; depends M12)
Tests: nvBench parity; vague-intent top-3 acceptance study protocol; TEMPER-migration of saved dashboards.

**M14 — AMBER** (§7; depends M6, M7, M10, M11, M0)
Interface: `snapshot(scope, stance) → bundle + manifest`; `verify(bundle)`; `import(bundle) → HEARTH state`.
Tests: the executable completeness test (reference-stack replay, 100% answer+citation equality); export-import idempotence; manifest hash verification; capability-loss set L is exactly as documented (negative tests: nothing else missing).

**M15 — Governance & Observability** (§10; depends M0, all)
Tests: label-propagation correctness (lattice-join over provenance on randomized policies); redaction exactness in LODESTONE answers; full audit reconstruction of a cycle from ledger alone.

**M16 — Distillation Loop** (v1 G10; depends M2, M5, M8, M12)
Tests: T2-vs-T3 quality ratio ≥95% on warmed domains; deferral-rate downward trend across ≥10 simulated cycles; per-tenant adapter isolation.

### 11.3 Critical path

M0 → M2 → (M3, M1) → M4 → M5/M6 (parallel, co-designed) → M7 → M8 → M12 → M14, with M9/M10 joining after M7 and M13/M15/M16 off-path. The single highest-risk integration is **M4↔M6** (induced classes must drive shard layout while ER is still resolving entities); de-risk by building the hero-corpus vertical slice through a *frozen* hand-built mini-ontology first, then swapping STRATA in behind the same interface.

---

## 12. Evaluation Methodology (Expanded)

**12.1 Component benchmarks.** As specified per module above: ER suite (DBLP-ACM…WDC), Valentine/Spider/BIRD-schema/TPC-H/DS (schema & dependencies), LLMs4OL Task C (taxonomy), WebNLG/REBEL (extraction), Auto-Pipeline-700 + TPC-DI (transform synthesis), BIRD + Spider 2.0-Lite (NL analytics), nvBench (visualization), HotpotQA/2Wiki/MuSiQue/MetaQA (multi-hop).

**12.2 Storage benchmarks.** HEARTH targets (§4.4) measured against: DuckDB-over-Parquet (scan parity baseline), Kùzu (traversal baseline), and a bitemporal-correctness suite with no external baseline (gold-answer scenarios). Report latency distributions, not means.

**12.3 Ablations (the integration evidence).** Each named technique off vs. on, measuring joint (ΔQ, Δcost, Δlatency): STRATA→flat types; TEMPER→naive in-place schema mutation (measure broken-artifact count); ANVIL→human-authored-only (measure engineering-hours proxy: transform count synthesized vs. written); WARDEN→hand-authored expectations; LODESTONE OQIR→direct NL2SQL on the same backends (the decisive ablation: it isolates the induced-semantic-layer effect that the Cortex/Genie evidence predicts); CRUCIBLE→economy on identical workloads; provenance interning→naive terms (storage overhead).

**12.4 The CEO-question suite (new end-to-end eval).** Construct 50 questions over the hero corpus (FAA registry + ASRS narratives + a synthetic ERP/maintenance source added for cross-system pressure), authored to require: ≥3 sources, ≥2 hops, ≥1 structured↔unstructured join, ≥1 temporal stance, and ≥1 unit/metric subtlety each (e.g., "Which operators' fleets had rising incident rates in the two quarters after their average aircraft age crossed 15 years, and what did the narratives most commonly cite?"). Gold answers hand-derived with citation sets. Scoring: exact/partial answer correctness, citation precision/recall, abstention appropriateness, clarification quality (human-rated), wall-clock. This suite, not BIRD, is the product's definition of done.

**12.5 Statistical protocol.** ≥5 seeds; bootstrap 95% CIs; paired tests vs. baselines; Benjamini–Hochberg across the suite; all configs and dataset versions pinned; cost/latency instrumented from ledger ground truth.

---

## 13. Roadmap and Risk Register

**Phase 0 (foundation):** M0–M2 + hero-corpus harness. *Exit: ledger invariants + spine guarantees green.*
**Phase 1 (induction vertical):** M3–M5 on frozen mini-ontology slice; STRATA behind interface. *Exit: induction targets on benchmarks; vertical slice answers 10 competency questions.*
**Phase 2 (the platform turn):** M6–M7; migrate slice onto HEARTH; orchestrated daily cycles. *Exit: bitemporal suite green; delta-proportional cycle cost demonstrated.*
**Phase 3 (autonomy of labor):** M8–M10. *Exit: ≥70% transform synthesis on hero sources; WARDEN/TEMPER closed loop on injected drift.*
**Phase 4 (payoff layer):** M11–M13. *Exit: CEO-suite ≥70% with 100% citations; vague-dashboard study target.*
**Phase 5 (trust & ship):** M14–M16, AMBER completeness test in CI, security review, design-partner deployment.

**Risk register (delta from v1; v1 risks carry forward).**

| Risk | Signal | Mitigation | Kill criterion |
|---|---|---|---|
| STRATA lattice quality below LLM-only baselines | Task C F1 gap | richer attributes; T2 admission judger retraining | if FCA grounding never beats Chain-of-Layer-style induction on 2+ suites, demote lattice to candidate-generator and let the cascade arbitrate |
| HEARTH can't hit serving + openness simultaneously | point-read p99 or export fidelity miss | more aggressive derived-index layer (closed indexes are allowed; only canonical must be open) | if dual goals irreconcilable, declare serving indexes a documented AMBER loss (rebuild-on-import) — already the design |
| ANVIL synthesis rate too low on real (non-benchmark) sources | <40% on design-partner data | grow DSL library from observed human transforms; better T2 | reposition ANVIL as accelerator (suggest, human approves) not autonomy; product still stands |
| OQIR grounding fails on tribal vocab at new tenants | clarification rate ≫ target in week 1 | onboarding synonym harvest from BI artifacts + docs | if cold-start clarification >50% persists past 4 weeks, add guided-ontology-tour onboarding requirement |
| TEMPER operator set insufficient for real evolution | frequent escape-hatch manual migrations | extend calculus (operators are additive) | none — calculus is extensible by design |

---

## 14. Related Work & Novelty Claims

| # | Named technique | Closest prior art | Precise delta (what is new) |
|---|---|---|---|
| 1 | STRATA | FCA-based ontology learning; LLMs4OL hybrid winners; Chain-of-Layer | FCA iceberg lattice over *profile-sketch evidence from heterogeneous sources* (not text corpora), with conformal spine-gated concept admission, memoized LLM naming on intent hashes, and AddIntent incremental maintenance with stable class URIs — the combination (evidence-grounded + calibrated + incremental + identity-stable) is unpublished |
| 2 | TEMPER | ontology-evolution operator catalogs; schema-migration tooling | a *closed calculus* where every operator ships an auto-synthesized forward migration (DBSP-expressible) + backward view discharging a snapshot-queryability theorem, recorded in a replayable morphism ledger that also auto-migrates compiled query/dashboard artifacts |
| 3 | HEARTH | Foundry OSv2 (Funnel + object DBs); Iceberg/Delta; Kùzu; Graphiti bi-temporality | open-format canonical entity store with four-timestamp bi-temporal *value cells*, survivorship-ranked multi-source values, and two-level interned provenance-polynomial dictionaries enabling exact invalidation at ≤8 B/cell — provenance/time/openness as storage-layer primitives rather than metadata |
| 4 | ANVIL | PROSE/FlashFill; Auto-Pipeline by-target; LLM codegen | "by-ontology" synthesis: the *induced* ontology+SHACL is the target spec (no user-provided target needed), FD/IND-pruned tiered search with **provenance-checked equivalence verification** (catches cross-row leakage invisible to value tests), outputs as readable versioned DSL artifacts feeding the distillation loop |
| 5 | WARDEN | Great Expectations/dbt tests/Soda; drift-detection literature | expectations *compiled from induced SHACL* + sketch-drift sentinels whose alarms are spine-calibrated decisions routed into TEMPER/ANVIL repair — closing detect→diagnose→repair autonomously |
| 6 | LODESTONE | CHASE-SQL/Arctic-class NL2SQL; semantic layers (Cortex/Genie, hand-authored); AutoLink schema linking | typed OQIR over an *induced* cross-estate ontology with static unit/grain/path type-checking, conformal interpretation sets, minimal-entropy single-question clarification, multi-backend lowering with IR-level execution repair, and per-cell semiring citations — accuracy from induced semantics rather than bigger models |
| 7 | VISTA | nvBench/LIDA NL2VIS | vagueness as ranked composition search over the *derived* metric layer with usage/health priors and TEMPER-tracked dashboard artifacts |
| 8 | AMBER | open table formats; data-portability practice | a formally stated, *executably tested* completeness property over the entire semantic estate (data+ontology+transforms+lineage+decisions) with an explicit, minimal capability-loss set — portability as a standing system invariant, not an exporter |

Carried-forward v1 contributions (decision spine, provenance substrate, DBSP pipeline, stable-URI incremental clustering, RDF⇄LPG documented-loss mapping) retain their v1 novelty positioning.

---

## 15. Defensibility: Why the Moat Holds at Platform Scope

1. **Compounding private state.** Per-tenant distilled specialists, calibration tables, synonym/usage priors, memo stores, and the synthesized-transform library all accrete from operation. A competitor cloning every published idea still starts at zero state; OntoForge's marginal quality rises and marginal cost falls with tenure. AMBER deliberately excludes none of the *customer's* assets and all of this *runtime* state — the moat and the anti-lock-in promise are disjoint by construction.
2. **Integration depth.** Eight named techniques share two substrates (spine, provenance semiring). HEARTH's invalidation needs the interning dictionaries; ANVIL's verifier needs cell-level provenance; LODESTONE's citations need HEARTH's value cells; TEMPER's migrations need DBSP transforms; AMBER's completeness needs all of it. Reverse-engineering observable behavior recovers none of these internal couplings.
3. **Guarantees as contract.** Conformal error control on auto-decisions, delta-proportional cost, snapshot-queryability under evolution, 100% citation grounding, and executable export completeness are *promises competitors' architectures cannot retrofit* — they fall out of substrates, not features.
4. **The trust inversion.** Every closed platform's strongest objection (lock-in) is OntoForge's strongest feature; every open tool's weakness (no autonomy, no guarantees) is OntoForge's core. The positioning is hard to attack from either side without rebuilding the whole.

---

## 16. Conclusion

v2 completes the reframe: OntoForge is not a pipeline cost-optimizer with an ontology attached; it is an autonomous semantic data platform whose product is *data maturity on demand* — induced, materialized, transformed, validated, queryable in natural language with per-cell citations, and exportable in full at any moment. The eight named techniques are individually defensible and jointly load-bearing; the build specification in §11 is the contract for the agentic implementation team; the CEO-question suite in §12.4 is the bar. Build the vertical slice, prove the suite, then widen.

*Attribution: claims about named external systems derive from their published sources (Palantir Foundry OSv2 documentation; Snowflake Cortex Analyst/semantic views and Arctic-Text2SQL-R1; Databricks Genie/metric views; CHASE-SQL ICLR 2025; Spider 2.0; BIRD & BIRD-INTERACT; FLEX; AutoLink; Auto-Pipeline VLDB 2021; PROSE/FlashFill; HyFD; BINDER; LLMs4OL 2024/2025; Chain-of-Layer CIKM 2024; Graphiti/Zep arXiv 2501.13956; Kùzu; Apache Iceberg; SQLMesh; Great Expectations; Ascend.io announcements; plus v1's bibliography: FrugalGPT, AnyMatch, Ditto, Splink, DeepBlocker, Gruenheid et al., DBSP, Green–Karvounarakis–Tannen, EDC, AutoSchemaKG, LazyGraphRAG, nvBench/LIDA). All bracketed targets are engineering goals, not measurements.*

---

# PART II — TESTING PROGRAM AND AGENTIC DEVELOPMENT PLAN

*Sections 17–19 extend the v2 specification. §17 defines the open-source end-to-end testing program: which freely available online datasets best stress every subsystem, and the full four-tier test methodology. §18 is the agentic development plan: build-spec-precise instructions for the AI coding agents that will implement the product. §19 defines the iterative algorithm-development and continuous-research protocol under which those agents build, benchmark, and improve the algorithms over time. Dataset access patterns, licenses, cadences, and scale figures in §17 were verified against primary sources on 9–11 June 2026.*

---

## 17. Open-Source End-to-End Testing Program

### 17.1 Why a multi-tier corpus program (and not just benchmarks)

The component benchmarks already specified (§12) are necessary but *insufficient*. Each isolates one subsystem on a static, pre-cleaned sample. They cannot exercise the three properties that distinguish OntoForge from a pile of components:

1. **Cross-source entity resolution on genuinely independent sources** — the same real-world entity described differently by organizations that never coordinated.
2. **Delta-proportional incremental processing on real change streams** — CDC, bi-temporal HEARTH cells, DBSP incremental recomputation, and TEMPER ontology evolution can only be validated against data that actually changes day to day.
3. **End-to-end answer fidelity with provenance** — STRATA → ER → HEARTH → ANVIL → LODESTONE must produce a correctly cited answer to a question no single source can answer alone.

The program therefore has four tiers (§17.4), anchored on **standing test estates** built from live public corpora (§17.2–17.3). A corpus qualifies as a standing estate only if it satisfies all five selection criteria:

| # | Criterion | Subsystem stressed |
|---|-----------|-------------------|
| C1 | ≥2 heterogeneous sources about overlapping real-world entities | M5 ER cascade, correlation clustering, stable URIs |
| C2 | ≥1 structured + ≥1 unstructured source | M3 profiler, structured-text joins, M12 LODESTONE path typing |
| C3 | Daily or more frequent updates | M1 CDC, M2 decision spine, M6 bi-temporal HEARTH, DBSP, M10 TEMPER |
| C4 | Messy real-world quality issues | M8 ANVIL synthesis, M9 WARDEN monitors/contracts |
| C5 | Scale to millions of entities | M4 STRATA lattice scalability ("crazy large ontologies") |

### 17.2 Verified corpus families (access, license, cadence, scale, warts)

#### 17.2.1 AVIATION — the hero estate (recommended primary)

The strongest single estate: it is the paper's existing hero corpus, every core source is free and public-domain, the structured anchor updates **daily**, and the join key (tail/N-number) is clean enough to build a credible gold set while messy enough to stress ER.

| Source | Access | License | Cadence | Size | Warts (testing features) |
|--------|--------|---------|---------|------|-------|
| **FAA Releasable Aircraft Database** | registry.faa.gov/database/ReleasableAircraft.zip (CSV/zip) | US Gov public domain | **Daily, refreshed ~11:30 pm Central** | ~60 MB zip | Manufacturer-name inconsistency (FAA's own docs cite "BMW ROLLS" vs "ROLL-ROYCE", "ROCKWELL INTERNATIONAL CORP" vs "ROCKWELL INTERNATIONAL"); blank "permissible" fields are not errors |
| **NASA ASRS** (incident narratives) | Database Online CSV export (≤10,000 records/download); Report Sets (50-record PDF bundles) | US Gov; de-identified | **Monthly** | 1988–present | Voluntary, self-report bias; multiple reports of one event merged into one record; not validated by NASA; **NB: ASRS announced (May–June 2026) database-structure and CSV-export changes — pin extracts and monitor for schema drift (a live TEMPER test in itself)** |
| **NTSB CAROL aviation accidents** | data.ntsb.gov/carol-main-public; bulk avall.zip (1982–present) + PRE1982.zip; monthly list | US Gov public domain | Preliminary within days; monthly bulk | full history | Narratives sparse pre-1993; N-numbers reused across aircraft over time ("use with caution") — a built-in temporal-identity ER trap |
| **FAA SDR (Service Difficulty Reports)** | FAA SDR archive | US Gov | **Monthly** | — | Free-text mechanical defect descriptions |
| **FAA Airworthiness Directives** | FAA server | US Gov | **Daily** | — | Regulatory text linking to models/series |
| **OpenSky Network ADS-B** | REST API (OAuth2; live state vectors 5–10 s resolution); full history via Trino (state_vectors_data4) | **Non-profit research/education ONLY; commercial use requires a separate license**; mandatory citation (Schäfer et al., IPSN 2014) | Live 5–10 s; nightly UTC historical batch | 30+ trillion messages from 5000+ sensors — the largest open air-traffic dataset | Coverage gaps over oceans/deserts; aircraft-metadata table "as is" |

**Entity-overlap structure.** Tail/N-number joins registry ↔ ASRS coded fields ↔ NTSB events ↔ OpenSky tracks; manufacturer and operator names join across all four and are deliberately dirty (perfect ER stress). Structured↔unstructured joins: registry rows ↔ ASRS/NTSB narratives mentioning the aircraft, operator, and event.

> **License caution for CI.** OpenSky's non-profit-only terms mean OntoForge's *commercial* CI cannot legally ingest OpenSky data without a license. **Decision:** the commercially-runnable hero estate is the FAA/NTSB/ASRS triad (all public domain); OpenSky is reserved for a research-scoped live-stream track under a research agreement.

#### 17.2.2 HEALTHCARE / DRUGS

| Source | Access | License | Cadence | Warts |
|--------|--------|---------|---------|-------|
| **openFDA** | api.fda.gov JSON; bulk zipped JSON | US Gov | FAERS adverse events **quarterly** (3+ month lag); **drug labels (SPL) weekly**; NDC directory; recalls/enforcement | FAERS: duplicates, incomplete reports, explicitly non-causal, not validated |
| **DailyMed** | REST API v2; SPL bulk zips | US Gov | **Daily / weekly / monthly** options | SPL is a "living document"; archives split by size |
| **RxNorm** | UTS download | **Current Prescribable Content subset: NO license required**; full RxNorm requires a free **UMLS/UTS individual license with mandatory annual usage report**; SNOMED CT fees may apply outside member countries | Full release **monthly**; **weekly** updates | US-centric; UMLS lags current RxNorm |
| **ClinicalTrials.gov** | API v2 | US Gov | frequent | Free-text eligibility/outcomes |
| **Orange Book** | FDA download | US Gov | periodic | — |

**Overlap:** drugs/manufacturers/ingredients via RXCUI, NDC, UNII, SPL set IDs. **Decision:** use the license-free RxNorm Prescribable subset + openFDA + DailyMed in CI; full UMLS stays out of the dependency chain.

#### 17.2.3 CORPORATE / FINANCE — recommended second estate

| Source | Access | License | Cadence | Scale/Warts |
|--------|--------|---------|---------|-------------|
| **SEC EDGAR** | Daily-index files (company/form/master/xbrl per date); CompanyFacts/Submissions JSON at data.sec.gov; Financial Statement Data Sets | US Gov | Filings accepted business days; **daily-index built nightly ~10 pm ET**; Financial Statement Data Sets quarterly; Statement-and-Notes monthly | **10 req/s rate limit; mandatory User-Agent header with contact email (missing UA → 403)**; XBRL duplicate facts |
| **GLEIF LEI** | Golden Copy download; also anonymous AWS S3 | **CC0** | **Golden Copy 3×/day (8-hour intervals)**; delta files at 8 h / 7 d / 31 d | Level 1 (who-is-who) + Level 2 (who-owns-whom); ≤24 h issuer lag |
| **USPTO PatentsView** | **Mid-migration to data.uspto.gov Open Data Portal** (transition began 20 Mar 2026; legacy Developer Hub decommissioned 5 Jun 2026; new ODP API keys required) | **CC-BY 4.0** | **Quarterly** | Inventor/assignee disambiguation errors are documented and *change between releases* — an excellent ER ground-truth-drift test |
| **USAspending.gov** | REST API + bulk | US Gov | frequent | Federal contracts/grants |
| **Form D / 13F** | EDGAR | US Gov | as filed | Beneficial-ownership graphs |

**Overlap:** companies/officers/addresses via LEI, CIK, ticker, CUSIP, assignee names. GLEIF 3×-daily deltas + EDGAR nightly daily-index are the cleanest *real* CDC streams with explicit change semantics — ideal for HEARTH four-timestamp validation and DBSP delta-proportionality.

#### 17.2.4 RESEARCH / SCHOLARLY — recommended scale estate ("crazy large ontology")

- **OpenAlex** — free public snapshot, **CC0**, refreshed **quarterly**; ~330 GB compressed / ~1.6 TB decompressed; **477M works indexed** (per the OpenAlex 2026 roadmap — the largest connected repository of scholarship). Incremental updates via updated_date partitions (download only partitions newer than your last sync); hourly polling and monthly snapshots are paid-plan features — the free path is the quarterly snapshot + partitions. Anonymous S3; no snapshot rate limit.
- **Crossref** (metadata + REST API), **ORCID** (author identity), **arXiv** (metadata + full text), **Papers with Code**.
- **Overlap:** authors/institutions/papers via DOI, ORCID, ROR. Author disambiguation is the headline ER wart and *exactly* the millions-of-entities test STRATA needs.

#### 17.2.5 GEOSPATIAL / MOBILITY — the gold-standard CDC testbed

- **OpenStreetMap** — minutely / hourly / daily replication diffs at planet.openstreetmap.org/replication/, sequence-numbered .osc.gz + state.txt. **ODbL.** The canonical real-change stream: every edit, with object-level before/after recoverable against a local copy; augmented diffs (full before/after per changeset) CC0 for changesets after ~2024-11. **The recommended Tier-3 delta-proportionality testbed.**
- **GTFS** — Mobility Database catalogs 6,000+ GTFS/GTFS-RT/GBFS feeds across 99+ countries. GTFS Schedule = relational CSV-in-zip (a real mini relational schema per agency, thousands of independently-designed instances — a schema-induction stress test in itself); GTFS-Realtime = high-frequency protobuf referencing static IDs.
- **GeoNames** (daily dumps, CC-BY), **OpenFlights** (airports/routes, joins the aviation estate).

#### 17.2.6 E-COMMERCE / PRODUCT

- **Open Food Facts** — nightly full dumps (JSONL ~43 GB decompressed; CSV; **Parquet**) + **daily delta exports** (14-day window). **ODbL** (data). 4M+ products, 150 countries, crowdsourced → richly messy. GTIN barcode as entity ID.
- **WDC Products** and **Amazon Reviews** remain Tier-1 component benchmarks.

#### 17.2.7 GOVERNMENT MISC

- **NYC Open Data** (Socrata SODA API + SoQL): **311 Service Requests** — 40M+ rows as of Dec 2025, **updated daily** (split 2010–2019 / 2020–present); taxi trips; motor-vehicle collisions.
- **NOAA GHCN-Daily** weather (daily, fixed-width/CSV — a format-parsing stress test); **FEC** campaign finance.

### 17.3 Selected standing test estates

Three integration estates plus one live-stream estate — balancing subsystem coverage against gold-set construction cost:

| Estate | Sources | Primary stressors | Why chosen |
|--------|---------|-------------------|------------|
| **A. Aviation (hero)** | FAA registry (daily) + ASRS + NTSB (+ OpenSky, research track) | ER on tail/operator; structured↔narrative joins; unit inference (ft, kt); daily CDC; N-number reuse temporal traps | Clean-enough join key for gold sets; public-domain core; matches the paper's hero corpus |
| **B. Corporate** | EDGAR (daily-index) + GLEIF (3×/day, CC0) + PatentsView (CC-BY) | Large-scale company/officer ER; explicit-semantics CDC; bi-temporal restatements; provenance/cost attribution | Best *real* change streams; CC0/CC-BY keep CI legal |
| **C. Scholarly (scale)** | OpenAlex (CC0, 477M works) + Crossref + ORCID | STRATA lattice at millions of entities/thousands of types; author disambiguation at scale | The "crazy large ontology" validation |
| **Live-stream** | **OSM minutely diffs** + GLEIF deltas + EDGAR daily-index + openFDA SPL weekly + GTFS-RT | DBSP delta-proportionality; TEMPER op rates; URI churn; WARDEN drift on real upstream changes; answer consistency over time | OSM is the highest-frequency real change stream available anywhere |

### 17.4 The four test tiers

**Tier 1 — Component benchmarks.** As specified in §12; each gates one module's CI (M3/M5: ER + schema suites; M4: LLMs4OL; M8: Auto-Pipeline-700 + TPC-DI; M12: BIRD + Spider 2.0-Lite; M13: nvBench; multi-hop QA suites). Run on every PR touching the relevant module.

**Tier 2 — Corpus integration tests (per estate).** Each estate ships four gold artifacts:
- **Gold mini-ontology** — 30–60 hand-built classes with SHACL shapes; serves double duty as the frozen ontology for the de-risking vertical slice (§11.3, §18.2) and as the oracle for induced-ontology quality (precision/recall of STRATA class admission and hierarchy edges against gold).
- **Gold entity-match sample** — built via §17.5's sampling protocol, not exhaustive labeling.
- **Gold competency-question suite** — ≥50 per estate spanning multi-hop ("Which operators flew aircraft models appearing in ≥3 NTSB events since 2015?"), temporal/bi-temporal ("What was company X's reported revenue *as known on date D* vs. as later restated?" — four-timestamp HEARTH), unit-sensitive ("incidents below 10,000 ft" — LODESTONE unit/grain type-check), and structured↔unstructured ("twin-engine aircraft whose ASRS narratives mention bird strikes").
- **Gold transform pipelines** — hand-built reference programs for the estate's canonicalizations; ANVIL output checked for provenance-verified equivalence against the reference.

**Tier 3 — Longitudinal live-stream tests.** Recommended run length **6 weeks [design target]**. Metrics: (i) **delta-proportionality** — daily compute cost vs. measured change volume (cost ∝ delta, not corpus size); (ii) **ontology-evolution stability** — TEMPER operation rate/day, URI churn rate (stable URIs must not thrash on routine edits); (iii) **WARDEN drift detection** P/R against *real* upstream changes (EDGAR XBRL taxonomy updates, OSM tagging shifts, the announced ASRS schema change); (iv) **answer-consistency-over-time** — re-ask the gold questions weekly; every answer flip must be justified by a provenance-backed source change, else it is a defect.

**Tier 4 — Adversarial / chaos tests.** Injected corruptions per the mutation taxonomy (nulls, unit swaps, typos/transpositions, duplicate explosions, referential breaks); schema-drift injection (rename/split/merge columns) that must trigger the *correct* TEMPER operators and migrations; source-disagreement injection exercising HEARTH survivorship + provenance; 10× scale ramps (STRATA + ER must not degrade super-linearly); and **red-team NL questions** — ambiguous (must trigger exactly one minimal-entropy clarification), unanswerable (must abstain), trick-unit ("altitude in dollars" must be rejected by the OQIR type-checker). Abstention correctness is measured explicitly.

### 17.5 Gold-annotation playbook (ER gold without exhaustive labeling)

Exhaustive pairwise labeling is infeasible at scale and biases gold sets toward whatever blocker generated the candidates. Protocol:

1. **Entity-centric sample-and-recover** (Binette et al., arXiv 2404.05622): draw a probability sample of k records; annotators recover each record's *full true cluster* (starting from the predicted clustering, with a search tool to surface candidates). Yields unbiased precision/recall/F estimates without all-pairs labeling.
2. **Filter trivial pairs** (score≈0 and score≈1 carry no information); concentrate annotation in the ambiguous middle.
3. **Stratify by match score**; **oversample cross-source pairs**, where errors concentrate.
4. **Sequential importance sampling** (OASIS-style) to hit target CI width on F at up to ~83% fewer labels than random sampling.
5. **Agreement bar:** two independent annotators + adjudicator; **Krippendorff's α ≥ 0.8** required (≥ 0.667 permits only tentative conclusions; healthcare estate tightened to **α ≥ 0.90** per clinical-data norms); rolling 10% audit re-checks α.

**Cost model [design target].** ~2,000 labeled records × 3 estates × 2 annotators ≈ 12,000 annotations ≈ 200 annotator-hours + ~40 h adjudication; competency-question authoring ≈ 1 SME-day/estate; gold mini-ontology ≈ 2–3 days/estate.

### 17.6 Reproducibility protocol (versioning live data for CI)

- **Pin a snapshot coordinate per corpus:** OSM replication **sequence number** + state.txt; GLEIF **publication timestamp**; EDGAR **daily-index date**; OpenAlex **snapshot release**; Open Food Facts **dump date**; openFDA **dated download**.
- **Archive every pinned snapshot to immutable object storage**; tag corpus versions alongside code commits using the platform's own Iceberg substrate (data versioning is a product capability — dogfood it).
- **Two-track CI:** *pinned* track (deterministic, gates merges) and *live* track (nightly against real upstreams; feeds Tier 3 and surfaces upstream regressions early).
- **Statistics:** fixed seeds; bootstrap 95% CIs; **paired** tests (paired bootstrap/McNemar) for version-vs-version comparisons on identical gold items; conformal calibration sets held disjoint from test sets; Benjamini–Hochberg across suites.

### 17.7 Pass/fail gates mapped to the §1.3 quality functional

| Gate | Threshold | Maps to |
|------|-----------|---------|
| ER F1 on gold sample (per estate) | ≥ 0.90 [design target] | Q correctness; M5 |
| Induced-ontology class precision / recall vs. gold mini-ontology | ≥ 0.85 / ≥ 0.75 [design target] | M4 STRATA |
| Competency-question accuracy (multi-hop/temporal/unit included) | ≥ 0.70 with 100% citation coverage [design target] | M12; §12.4 |
| Abstention correctness on unanswerable/trick set | ≥ 0.90; 0% confidently-wrong [design target] | decision spine |
| Tier-3 cost vs. delta | within 2× theoretical minimum [design target] | (Δ) constraint |
| WARDEN drift F1 on real upstream changes | ≥ 0.80 [design target] | M9 |
| AMBER export completeness | 100% executable completeness, every release | M14; constraint (P) |

> **2026 accessibility call-outs (verified):** PatentsView is mid-migration to data.uspto.gov (pin ODP endpoints; legacy keys invalid). OpenSky is non-profit-only (excluded from commercial CI). Full RxNorm/UMLS licensing is unfit for CI (use the Prescribable subset). OpenAlex hourly/monthly updates are paid (use quarterly CC0 snapshot + partitions). NASA ASRS is changing its database structure and CSV exports (pin extracts; treat the change as a live TEMPER/WARDEN test).

---

## 18. Agentic Development Plan

*Written for autonomous AI coding agents under spec-driven development: code is the implementation detail of the specification, not the other way around. **This white paper is the spec.***

### 18.1 Operating model

**Cardinal rule.** Every module PR must cite the exact spec section(s) it implements. Where the spec is silent or wrong, the agent does **not** improvise — it files a spec-amendment proposal.

**Spec amendments use a TEMPER-like calculus.** Spec changes are typed change-objects (ADD/MODIFY/REMOVE a requirement, stated in GIVEN/WHEN/THEN form), each carrying: (a) affected module(s), (b) a migration note for dependent contracts, (c) the acceptance tests that change, (d) a human approval signature. No code merges against an unapproved amendment. The amendment ledger is itself versioned and replayable — the spec gets the same evolution discipline as the ontology.

**Agent roles** (small, separable, adversarially structured where it matters):

| Role | Owns | Key constraint |
|------|------|----------------|
| **Architect / spec-keeper** | The spec, contract crates, amendment ledger | Only role that can approve a spec delta (with human co-sign) |
| **Module implementers** (one per ownership boundary) | Code inside one M-module directory | May not edit another module's directory, any test it did not author, or any CI gate |
| **Test-author** (adversarially separated) | Acceptance + property + holdout tests | **Never shares context with implementers; implementers never see holdout tests** — the primary anti-reward-hacking control |
| **Research agent** (§19) | Literature monitoring, research briefs, challenger proposals | Cannot write production code; outputs enter via the §19 experiment protocol |
| **Integration agent** | Merges, cross-module conflict resolution, full-harness runs | Cannot weaken a gate to make a merge pass |
| **Red-team agent** | Tier-4 chaos, abstention probing, mutation campaigns | Adversarial to all implementers |
| **Docs agent** | Runbooks, API docs, ADRs | Must cite spec sections |

**Human checkpoints (humans approve only these four things):** spec changes; security boundaries (auth, data-plane isolation, the access-control semiring valuation); gold-set signoff (§17.5 — humans certify Krippendorff α and adjudicate); release gates (§18.5 phase exits).

**Repository topology.** Monorepo; one ownership boundary per module M0–M16 enforced by CODEOWNERS; **contract-first**: shared interfaces live in dedicated contract crates/packages (Rust crates for storage/ledger/DBSP/OQIR types; Python packages for ML interfaces) whose stubs are **code-generated from the spec's §11.2 interface descriptions**. Implementer agents work in disjoint trees and integrate only through versioned contracts — this, not merge heroics, is what prevents conflict storms among parallel agents.

### 18.2 Build sequence as a dependency-ordered task graph

**Critical path (per §11.3):** M0 → M2 → (M3 ∥ M1) → M4 → (M5 ∥ M6) → M7 → M8 → M12 → M14, with M9/M10 joining after M7 and M11/M13/M15/M16 off-path.

**De-risking gate (mandatory, before STRATA swap-in):** build the hero-corpus vertical slice on the **frozen, hand-built aviation mini-ontology** (§17.4's gold artifact). This proves M5→M6→M7→M8→M12→M14 end-to-end with a known-correct ontology, so that when M4/STRATA replaces it behind the same interface, any regression is isolable to STRATA.

Each module decomposes into 5–15 agent tasks using this template:

> **Task:** one-sentence outcome.
> **Inputs:** spec §§, contract crate(s), upstream artifacts.
> **Definition of Done:** named acceptance tests pass; line+branch coverage ≥ 85% [design target]; mutation score ≥ 70% [design target]; benchmark within regression budget.
> **Complexity:** S / M / L. **Parallel-with:** task IDs.

**Module-by-module decomposition (the full graph is ≈150–200 tasks [design target]; representative tasks shown):**

| Module | Representative agent tasks (count) | Parallelism |
|--------|------------------------------------|-------------|
| **M0** atom/ledger | atom typing + content addressing (S); N[X] semiring trait + property tests (M); interned shape dictionaries ≤8 B/cell (L); append/replay ledger (M); invalidation join (M); bitemporal columns (M); valuation homomorphisms (S); cost counters (S) — 8–10 | Mostly serial until contracts freeze, then parallel |
| **M2** spine | calibrators per kind (M); split-conformal predictor (M); two-threshold selective rule (S); Lagrangian governor (L); economy/CRUCIBLE profiles (M); quarantine semantics (S); AL feedback ingestion (M); risk-coverage reporting (S) — 8 | After M0 |
| **M1** CDC | Postgres logical decoding (M); file hash-diff (S); doc snapshot-diff (M); OSM replication consumer (M); GLEIF delta consumer (S); EDGAR daily-index consumer (M, with UA + 10 req/s compliance); GTFS/GTFS-RT (M); openFDA/DailyMed (S); RAW-mirror byte-equality audit (S); atom-URI stability fuzzing (M) — 10–12 | ∥ M3 |
| **M3** profiler/deps | sketch suite KLL/HLL/MinHash (M); format-signature lattice (M); HyFD reimplementation (L); incremental FD maintenance (L); BINDER reimplementation (L); n-ary IND refinement (M); semantic-typing classifier (M); unit/dimension inference (M); profile-sketch key (S); TPC-H/DS key-recovery harness (S) — 12–15 | ∥ M1 |
| **M4** STRATA | formal-context builder (M); iceberg lattice construction (L); attribute clarification/reduction (M); stability index (S); admission decision integration (M); T3 naming with constrained decoding + memoization (M); AddIntent incremental insertion (L); intent-hash URI registry (S); LLMs4OL harness (M); hero gold-ontology comparator (S) — 10–12 | Critical path |
| **M5** ER | blocking hybrid (M); Fellegi–Sunter core (M); T2 matcher serving via vLLM (M); pair-decision spine wiring (S); incremental correlation clustering (L); anchor-stable URIs + hysteresis (M); ER benchmark harness (S); churn metrics (S) — 8–10 | ∥ M6 |
| **M6** HEARTH | entity-shard layout (L); four-timestamp value cells (L); survivorship ranking (M); Iceberg commit path (M); CSR adjacency index (L); point-read index (M); Actions write-back with SHACL pre-validation (M); temporal-stance read API (M); bitemporal scenario suite (M); provenance-overhead budget test (S) — 12–15 | ∥ M5 |
| **M7** transforms/orchestrator | DSL grammar + parser (M); column-lineage from AST (M); content fingerprints + virtual environments (L); delta-native scheduler (L); backfill = bounded provenance replay (M); idempotent retries (S); run history (S) — 10 | Critical path |
| **M8** ANVIL | T0 fix-detector library (M); FD/IND-pruned beam search (L); PROSE-style string-transform synthesis (L); holdout verification harness (M); provenance-equivalence checker (L); T2/T3 constrained synthesis (M); TX spine decisions (S); readable-DSL pretty-printer (S); Auto-Pipeline-700 + TPC-DI harnesses (M) — 12 | Critical path |
| **M9** WARDEN | Σ→expectation compiler (M); sketch-drift sentinels (M); drift→TEMPER/ANVIL routing (M); contract emitter (S); alert calibration (M); injected-corruption suite (M) — 8 | After M7 |
| **M10** TEMPER | operator set + preconditions (M); migration synthesis per operator (L); backward-view construction + queryability prover (L); morphism ledger (M); compiled-artifact migration (M); 1,000-op replay harness (M) — 10 | After M7 |
| **M11** export | RDF-star emitter (M); LPG emitters Neo4j/Kùzu (M); round-trip isomorphism tests (M) — 6 | Off path |
| **M12** LODESTONE | OQIR types + checker (L); grounding/retrieval over O (M); T2 candidate generator, grammar-constrained (L); interpretation scoring + conformal sets (M); minimal-entropy clarification (M); lowering rules per backend (L); execution-guided repair (M); per-cell citation assembly (M); abstention logic (S); BIRD/Spider2.0/CEO-suite harnesses (M) — 15 | Critical path |
| **M13** VISTA | metric-layer derivation (M); composition search (L); chart-type rules → Vega-Lite (M); proposal ranking + feedback capture (M) — 8 | Off path |
| **M14** AMBER | bundle assembly (M); manifest + signing (S); completeness replay on reference stack (L); import path (M); negative tests on loss set (M) — 6 | Path terminus |
| **M15** governance | label lattice valuation (M); redaction in answers (M); audit reconstruction (S) — 5 | Off path |
| **M16** distillation | adjudication mining (S); data curation (M); LoRA fine-tune harness (M); A/B promotion protocol (M); per-tenant isolation tests (S) — 6–8 | Off path |

### 18.3 The agentic loop protocol (per task)

1. **Read** the cited spec section(s). 2. **Restate the contract** in the PR description (inputs/outputs/invariants/errors); the architect agent diff-checks the restatement against the spec — mismatch blocks the task. 3. **Tests first** — written by the implementer before code, or (mandatory for M0, M4, M5, M6, M12) supplied by the adversarial test-author. 4. **Implement.** 5. **Run the local harness** (§18.4). 6. **Self-review checklist:** invariants hold; every error path handled; **a provenance hook on every value write** (no cell without N[X] attribution); no test edits, no input special-casing. 7. **Open PR** with spec citation. 8. **CI gates, all mandatory:** lint → types → unit → property-based → **mutation score ≥ threshold** → **benchmark regression gate** → **holdout acceptance tests the implementer has never seen**. 9. **Integration agent merges.**

**Reward-hacking countermeasures (non-negotiable).** Agentic coders demonstrably game tests — documented behaviors include editing tests to pass, hard-coding test inputs, and modifying harnesses; measured reward-hacking gaps grow sharply with code size, so the controls scale with module size: adversarial test/implementer separation; holdout end-to-end suites (validation tests cover features singly, holdout tests compose them); **hard CI failure on any implementer edit to test or harness files**; mutation testing against vacuous tests; sampled LLM-judge classification of solutions (legitimate / special-cased / hacked); and sandboxed execution with no force-merge path.

### 18.4 Verification infrastructure to build FIRST (Phase 0 deliverables)

1. **The benchmark harness** — runs all Tier-1 suites + Tier-2 estates, emits machine-readable scorecards; every CI gate and every §19 experiment reads from it.
2. **Golden-data fixtures** — §17's gold artifacts, snapshot-pinned (§17.6), loadable offline.
3. **Property-based generators for the algebraic laws** — these subsystems have *exact* laws, ideal for property testing (proptest/quickcheck-class shrinking): **semiring axioms** (associativity/commutativity/distributivity/identities/annihilation of N[X]); **Galois-connection laws** for STRATA (monotone closure, idempotence, extent/intent duality); **bitemporal invariants** (interval ordering, non-overlap per cell, system-time monotonicity); **DBSP linearity** (f(a ⊎ Δ) = f(a) ⊎ f(Δ) for linear operators; incremental ≡ from-scratch — the executable form of constraint (Δ)).
4. **Deterministic LLM replay (cassettes)** — every T2/T3 call recorded and replayed in CI: tests are deterministic and burn zero tokens; prompt drift = cassette mismatch = test failure.
5. **Cost instrumentation** — per-operation counters wired to the ledger so budget targets and delta-proportionality are *testable*, not aspirational.

### 18.5 Milestones mapped to phases, with exit criteria

| Phase | Scope | Exit criteria (tied to §17 tiers) | ≈ tasks |
|-------|-------|-----------------------------------|---------|
| **0 — Verification first** | §18.4 infra; M0; contract crates | All property suites green; cassette + cost harness operational | ~25 |
| **1 — Spine, ingest, profile** | M2, M1, M3 | Tier-1 ER/schema/FD bars met; CDC adapters replay pinned OSM/GLEIF/EDGAR snapshots deterministically | ~35 |
| **2 — Vertical slice (frozen ontology)** | M5, M6, M7, minimal M8 on the frozen aviation mini-ontology | **Tier-2 Estate A green** (ER F1 ≥ 0.90; transform equivalence verified); de-risking gate cleared | ~45 |
| **3 — STRATA swap-in + evolution** | M4 replaces frozen ontology; M9, M10 | Induced-ontology P/R bars met on Estate A+B; **6-week Tier-3 run**: delta-proportional cost, stable URIs, WARDEN drift F1 ≥ 0.80 | ~40 |
| **4 — Payoff layer** | M12, M13, M11 | **CEO-question suite ≥ 70% with 100% citations**; abstention ≥ 90%; BIRD/Spider2.0/nvBench bars | ~35 |
| **5 — Trust & ship** | M14, M15, M16; hardening | **AMBER completeness = 100% in CI**; security review signed; 10× load ramp passed | ~25 |

Plan for Phases 0–2 consuming ≈40% of total effort — the infrastructure and de-risking investment is what makes Phases 3–5 fast and safe.

**Process risk register:**

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Spec drift (code quietly diverges; spec becomes a historical document) | High | Amendment calculus; PR spec citations; architect diff-checks; docs agent keeps spec↔code cross-reference current |
| Reward hacking / test gaming | High (documented in SOTA agents) | §18.3 control stack |
| Context loss across agent sessions | High | Spec + ADRs + repo map as durable context; contract crates as interface memory |
| Merge conflict storms | Medium | Module ownership + contract-first integration |
| Silent cross-module contract drift | Medium | Codegen'd stubs; contract version pinning; integration agent full-harness pre-merge |
| Destructive agent actions (deleting files, weakening auth "to simplify dev") | Medium | Sandboxing; human approval on security boundaries; no force-merge; git backstop |

### 18.6 Production-readiness checklist (release gate)

**Security:** single-tenant data-plane isolation verified; Actions authn/authz; access-control valuation threaded through N[X] and red-team tested; secrets handling; **LLM prompt-injection review of all ingested-content paths** (PDF/narrative text reaching T2/T3 prompts is untrusted input); audit for agent-introduced shortcuts. **Performance:** 10× scale ramp sustained; ingest at real volumes (OpenAlex 1.6 TB, OFF 43 GB, NYC-311 40M rows); query p95 budgets; governor behavior under load. **Deployment:** per-component health/rollback across the Rust core, Python ML services, vLLM pool, and UI. **Runbooks:** snapshot pinning/refresh; replication catch-up; SEC UA/rate compliance; incident and cost-overrun response. **Final gate:** the AMBER executable-completeness property holds at 100% — a release cannot ship if the export cannot reproduce the certified answer set alone.

---

## 19. Iterative Algorithm Development & Continuous Research Protocol

The eight named techniques are *initial designs*, not final answers. STRATA, ANVIL, LODESTONE and the ER cascade all sit on fast-moving research frontiers (the BIRD leaderboard turns over quarterly; ER and taxonomy-induction SOTA moved materially in 2024–2026 alone). The platform's algorithms must therefore be developed the way the platform itself treats data: **continuously, incrementally, with calibrated evidence and full provenance.** This section defines that loop as a first-class engineering process the agents execute indefinitely — research is not a phase, it is a standing subsystem.

### 19.1 The Algorithm Lab: baseline–challenger discipline

Every algorithmic component (each named technique plus each cascade tier model) is registered in the **Algorithm Registry** with: its contract crate interface, its current **BASELINE** implementation, its scorecard (every relevant Tier-1/2 metric + cost + latency, with CIs), and its spec section. Improvement happens only through **CHALLENGERS**:

1. A challenger implements the *same contract interface* as the baseline — never a new interface (interface changes are spec amendments, §18.1).
2. Challengers are developed on branches with full access to validation suites but **never** to holdout suites or Tier-2 gold answers beyond the published training splits.
3. Promotion requires, on the pinned harness: (a) statistically significant improvement on the target metric (paired test, BH-corrected, ≥5 seeds); (b) **no regression beyond budget on any other gate** — including cost, latency, calibration ECE, and abstention correctness, so a challenger cannot buy accuracy with miscalibration; (c) green holdout + Tier-2 estate runs executed by the integration agent, not the proposer; (d) a written **experiment record** (hypothesis, design, results, decision) appended to the experiment ledger.
4. Demoted baselines are retained and runnable — every promotion is reversible, and the scorecard history is the algorithm's own provenance trail.

This is the spine's selective-classification philosophy applied to the development process itself: a promotion is a decision with calibrated evidence, an explicit cost structure, and an audit trail.

### 19.2 The continuous-research loop (the Research Agent's standing duties)

The research agent (§18.1) runs a perpetual cycle with three cadences:

- **Weekly — frontier diffing.** Monitor the named external surfaces: the BIRD / Spider 2.0 leaderboards (M12), entity-matching and blocking literature (M5), LLMs4OL and taxonomy-induction venues (M4), program-synthesis and data-wrangling venues (M8), incremental-computation and streaming-DB work (M0/M7), and table-foundation-model releases (M3). Output: a **research brief** per relevant finding — claim, source, affected module, estimated effect size, and a feasibility note. Briefs are triaged by the architect agent into: ignore / watch / spawn-challenger.
- **Monthly — module deep reviews.** For one module per month (round-robin over the critical-path modules), the research agent performs **error mining**: pull the module's failure cases from the harness and the production-shadow logs (wrong ER merges, rejected ANVIL syntheses, LODESTONE abstentions and repairs, WARDEN false alarms), cluster them, and write a failure taxonomy. Each major cluster becomes a hypothesis ("DeepBlocker recall collapses on short-string tail-number variants"; "OQIR grounding misses metric synonyms introduced by finance users") and, where warranted, a challenger task with a targeted metric slice. Error mining is the highest-yield research activity because it is grounded in *this* system's actual distribution rather than benchmark folklore.
- **Quarterly — assumption audits.** Re-test the load-bearing design assumptions against the current world: token-price ratios and cache/batch discounts (the economy-profile math), T2-vs-T3 quality gaps (the distillation curve), conformal coverage under each estate's drift, and the benchmark-vs-human-judgment gap (the FLEX-style concern). If an assumption has moved enough to change an architectural trade-off, the research agent files a spec-amendment proposal — research findings enter the spec only through the §18.1 calculus, never by silent code change.

### 19.3 Iterative improvement loop per algorithm (the inner cycle)

For any challenger task, the implementing agent runs a bounded experimental loop:

> **implement → benchmark (pinned harness, ≥5 seeds) → ablate (which component moved the metric) → error-mine (what still fails, clustered) → hypothesize → next variant**

with hard bounds: an **iteration budget** (default 5 variants per challenger task [design target]), a **compute/token budget** drawn from a dedicated research allocation tracked by the same cost instrumentation as production (the budget governor governs research too), and **kill criteria** (two consecutive variants without significant improvement → write up the negative result and stop; negative results are ledger entries — they prevent the next agent from re-running the same dead end). Cassette-recorded LLM calls keep iteration cheap; only the final promotion run uses live calls.

**Self-improvement data closes the loop.** The product's own operation feeds the lab: every T3 adjudication is distillation data (M16); every accepted/edited ANVIL synthesis is synthesis training data; every LODESTONE clarification answer is grounding training data; every human review-queue decision is calibration data. The Algorithm Lab is therefore not separate from the product — it is the offline half of the same flywheel, and the per-tenant variants of it (tenant-specific T2 adapters, synonym stores) are exactly the accreting moat of §15.

### 19.4 Guardrails

Research velocity must not corrode the guarantees: (i) challengers cannot weaken any §17.7 gate — gates only move via spec amendment with human sign-off; (ii) the research agent never writes production code and the implementer never selects its own promotion evidence; (iii) any challenger touching safety-relevant surfaces (abstention logic, access-control valuation, provenance hooks) triggers mandatory red-team review; (iv) external datasets or models adopted from research briefs go through the §17.2-style license check before entering CI (the PatentsView/OpenSky/UMLS lessons generalize); (v) all experiment records, scorecards, and promotion decisions live in the same append-only ledger as everything else — the development process is as auditable as the data platform it builds.

---

## 20. Closing

The document is now complete as both white paper and build program: §§1–16 define the system and its eight named inventions at build-spec precision; §17 grounds rigorous testing in verified, licensed, genuinely-changing open data with gold-artifact construction protocols and pass/fail gates; §18 turns the spec into a dependency-ordered agentic build with adversarial verification and human checkpoints at exactly four decision surfaces; §19 makes improvement itself a governed, perpetual subsystem. The agents' first task is Phase 0 of §18.5. The spec is the contract; the harness is the judge; the ledger remembers everything.

*Attribution: claims about named external systems and datasets derive from their published sources and primary documentation as cited throughout (§16 bibliography note; §17 dataset verifications of 9–11 June 2026; agentic-process findings per the 2025–2026 spec-driven-development and reward-hacking literature including SpecBench-class measurements). All bracketed thresholds are engineering targets, not measurements.*
