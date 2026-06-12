# OntoForge — Autonomous Ontology Builder
## Unified End-to-End MVP Engineering Plan (Build Spec for Claude Fable) — v2

> **Working name:** OntoForge
> **One-line:** Point it at messy, disparate, daily-changing data sources; it autonomously proposes a validated, *portable* ontology + knowledge graph you can query in natural language — built by a tiered compute stack where the frontier LLM is a rare, expensive Subject-Matter Expert, not a per-row workhorse.
> **Audience:** Claude Fable (the builder). This is a spec to design algorithms against, research, implement, and test — *not yet code*. Where an algorithm is non-trivial, the spec states the **contract** (inputs/outputs/quality bar) and hands the **design** to Fable.
> **Two non-negotiable spines:** (1) **Portability** — the canonical artifact is RDF/OWL+SHACL and must round-trip into ≥3 graph systems. (2) **Token economics** — every record flows through a 4-tier cascade; the frontier model touches only the hard residual. These two ideas are woven through every section below.

---

## 0. How to use this document (Fable instructions)

You own the hard parts. This spec gives you **contracts**, a **gap analysis** (§10) of the unsolved problems you must design and benchmark, and a **testing program** (§8) on named open-source datasets so every claim is measured, not asserted.

**The seven operating rules (all binding):**
1. **Build the skeleton first, vertically.** One source type → ontology → query, end-to-end, before widening.
2. **Every algorithm module ships with a benchmark harness and a number.** No "it works" without a metric vs a gold set.
3. **Cheapest sufficient tier wins.** Every decision flows deterministic (T0) → classical-ML/statistical (T1) → small specialist model (T2) → frontier-LLM-as-SME (T3). **Escalate only on calibrated uncertainty.** A record reaching T3 is a *design failure to justify*, not a default.
4. **LLM proposes, deterministic code validates, human approves.** Nothing reaches the canonical graph without passing SHACL + a confidence gate.
5. **Never call the model when you can avoid it.** Blocking, caching, memoization, and delta-only processing come *before* any inference. The biggest token savings are the calls you never make.
6. **Model-agnostic from line one.** All model calls (T2 and T3) go through one interface (§5.2). Default to a frontier API model for T3 and a local/open small model for T2; both must run against an OpenAI-compatible endpoint.
7. **Portability is the product.** Canonical = RDF/OWL+SHACL. If it can't round-trip into 3 graph systems with equivalent query results, it isn't done.

---

## 1. Product definition & MVP boundary

### 1.1 What the MVP does
Ingests a small number of heterogeneous sources (relational DB, CSV/Parquet, PDFs/text) that **update daily**, and autonomously:
1. Profiles every source; extracts structural + semantic metadata.
2. Proposes an **ontology** (entity types, properties, relationships) with provenance + confidence.
3. Resolves entities *across* sources — through the tiered cascade, with the frontier model adjudicating only the ambiguous residual.
4. Assembles a **knowledge graph** conforming to that ontology.
5. Validates it (SHACL + reasoner); surfaces low-confidence items for **human review**.
6. Processes each subsequent day as a **delta** — only changed records incur compute.
7. Exports a **portable artifact** (OWL/RDF+SHACL+JSON-LD) and loads it into ≥3 graph backends.
8. Answers natural-language questions over the graph (GraphRAG) with citations back to source atoms.

### 1.2 What the MVP explicitly does NOT do (cut to stay solo-buildable)
- No live/streaming ingestion (batch + daily delta only).
- No 50-connector marketplace (3 source types).
- No multi-tenant SaaS, billing, RBAC (single-user, local/desktop or single-VM).
- No "whole enterprise." One domain, ≤10 sources, ≤1M entities for the MVP.
- No claim of full autonomy. Human approves schema deltas and low-confidence merges.

### 1.3 Definition of done (MVP)
On the **hero corpus (§8.4)**:
- Schema aligns **≥85%** with a hand-built gold ontology (precision & recall reported separately).
- Cross-source entity resolution **≥0.85 pairwise F1**, with calibrated confidence (ECE reported).
- **≥70%** of a fixed competency-question set answered correctly **with citations**; beats a vector-only RAG baseline on multi-hop questions.
- Exported ontology loads and returns **equivalent query results** in Oxigraph/Jena (RDF), Neo4j (LPG), and one more (Kùzu or GraphDB).
- **Token-budget targets met (§7):** frontier (T3) calls reach **≤20–25%** of *adjudication-needing candidates* (and a far smaller fraction of raw records); the daily delta run costs in proportion to what changed, not the full dataset; cache-hit rate on daily reruns **≥40%**.

---

## 2. The core engineering idea: the 4-tier cascade

Everything in OntoForge is organized around routing each decision to the cheapest tier that can make it correctly. This is the documented pattern behind FrugalGPT (matches GPT-4 quality at 50–98% lower cost) and RouteLLM (95% of GPT-4 quality at 14–26% of GPT-4 calls). Applied to data engineering, the spread is even larger: fine-tuned small matchers (e.g., AnyMatch, a 124M model) land within ~4–5% F1 of GPT-4 on entity matching at ~3,900× lower cost.

| Tier | What it is | Handles | Cost | Example tasks |
|---|---|---|---|---|
| **T0 — Deterministic** | Rules, exact keys, regex, type checks, declared FKs | The trivially-decidable majority | ~free | Exact-key joins, format validation, declared-FK links, dedup of identical rows |
| **T1 — Classical ML / statistical** | Embeddings + kNN, blocking/LSH, gradient-boosting/logistic classifiers, **Fellegi-Sunter probabilistic linkage (Splink)** | The high-confidence "easy" matches & non-matches | cents/M rows | Candidate generation, auto-match/auto-reject bands, column-type detection |
| **T2 — Small specialist LM** | Fine-tuned encoder/SLM (Ditto-class, AnyMatch, Jellyfish 7–13B), distilled from the frontier model offline | The ambiguous middle band | <1% of frontier | Hard entity-match pairs, relation/triple extraction, NER, schema-attribute matching |
| **T3 — Frontier LLM (SME)** | Frontier model, constrained decoding, batched, prompt-cached | Only novel/ambiguous/high-stakes residual | expensive — minimize | Novel schema/ontology reconciliation, cross-source conflict resolution, the hardest match adjudications |

**The escalation contract (uniform across modules):** each tier emits a decision **and a calibrated confidence**. Policy: `confidence ≥ τ_high → auto-accept`; `confidence < τ_low → auto-reject`; `τ_low ≤ confidence < τ_high → escalate to next tier`. Thresholds are learned on a labeled calibration sample to hit a target quality (e.g., "95% of frontier-equivalent F1") at minimum cost, then monitored and re-calibrated under drift. The T3 gate adds three more conditions — escalate to the frontier model **only if** the decision is also (a) not in cache/memo, (b) high-impact (affects a canonical entity or many downstream edges), and (c) genuinely novel (not a re-run of a memoized schema decision).

**Fable's design task for the cascade itself:** design the router (the per-module confidence representation, the calibration method, and how thresholds adapt from human/active-learning feedback), and the cost-aware deferral rule (conformal prediction or Fellegi-Sunter two-threshold). This is gap **G5** in §10.

---

## 3. System architecture (reference pipeline with tiers shown)

```
        ┌────────────────────────────────────────────────────────────────────────────┐
        │  ORCHESTRATOR (DAG): state machine • PROVENANCE LEDGER • CONFIDENCE GATING   │
        │  + COST GOVERNOR (token budget, per-tier escalation quota, fail-closed)      │
        └────────────────────────────────────────────────────────────────────────────┘
                                          │
        ┌──────────┐   CDC / delta   ┌────────────┐
        │ Postgres │────────────────▶│  Ingestion │  (only changed records flow downstream)
        │ CSV/Parq │                 │ + Provenance│
        │ PDF/Text │                 │  Addressing │
        └──────────┘                 └────────────┘
                                          │
                 ┌────────────────────────┼─────────────────────────────────────┐
                 ▼                        ▼                                       ▼
        T0 DETERMINISTIC          T1 CLASSICAL ML / STAT                  CACHES & MEMOS
        • profiling/stats         • blocking / LSH / kNN  ◀── never let    • semantic cache
        • key & FK discovery      • column-type (Sherlock/Sato)   T2/T3    • schema/ontology memo
        • exact-key joins         • Fellegi-Sunter (Splink)   see a pair   • prompt prefix cache
        • format validation       • GBT/LR pair classifier    before here  • decision dedup
                 │                        │ (ambiguous band only)                  ▲
                 │                        ▼                                        │
                 │                 T2 SMALL SPECIALIST LM ───────────────────────►─┤
                 │                 • Ditto/AnyMatch/Jellyfish matcher  (cache hits)│
                 │                 • relation/triple extraction, NER               │
                 │                 • calibrated confidence (conformal)             │
                 │                        │ (residual hard cases only)             │
                 │                        ▼                                        │
                 │                 T3 FRONTIER LLM as SME ──────────────────────►──┘
                 │                 • novel schema/ontology reconciliation
                 │                 • cross-source conflict resolution
                 │                 • hardest match adjudications
                 │                 • constrained decoding • batched • prompt-cached
                 ▼                        ▼
        ┌──────────────────────────────────────────────┐     ┌──────────────────────┐
        │  COLLECTIVE RESOLUTION                        │     │   HUMAN REVIEW UI     │
        │  transitive closure → correlation clustering  │────▶│  schema deltas +      │
        │  (optional GNN for contextual matching)       │     │  low-conf merges +    │
        └──────────────────────────────────────────────┘     │  validation warnings  │
                 │                                            └──────────────────────┘
                 ▼  approved                                            │ feedback (active learning →
        ┌──────────────────────────────────────────────┐               │  recalibrates thresholds,
        │  ONTOLOGY ASSEMBLER → RDF/OWL + SHACL         │◀──────────────┘  trains T2 specialists)
        │  SHACL validation + OWL reasoner              │
        └──────────────────────────────────────────────┘
                 │
                 ▼
        ┌──────────────────────────────────────────────────────────────────────────┐
        │  CANONICAL RDF STORE ── exporters ──▶ OWL / JSON-LD / LPG(Neo4j,Kùzu)      │
        │                       ── GraphRAG query engine ──▶ NL answers + citations  │
        └──────────────────────────────────────────────────────────────────────────┘
```

**Principle:** deterministic where correctness is checkable; classical ML/statistics for the high-volume confident cases; small specialist models for the ambiguous band; the frontier model only for the novel/high-stakes residual; humans for the lowest-confidence, highest-impact calls. Caches, memos, and delta processing wrap the whole thing so daily reruns are cheap.

---

## 4. Module-by-module contracts (tier-annotated)

Each module: **Input → Output**, **which tiers it uses**, **quality bar**, **Fable's design task**.

### 4.1 Ingestion, Provenance & Change-Data-Capture — *T0*
- **Input:** Postgres connection, file paths (CSV/Parquet), document folder (PDF/TXT/MD).
- **Output:** raw tables (Arrow) + normalized doc text with spans; **a stable `source_id` URI for every atom**; and a **delta set** (changed/new/deleted records since last run).
- **Quality bar:** lossless ingestion; every cell/span addressable; daily run sees only the delta.
- **Fable task:** (a) design the **provenance addressing scheme** — a URI grammar that uniquely identifies any source atom (`db:schema.table.row#col`, `file:path#row`, `doc:path#page:charStart-charEnd`) and survives all transforms; everything downstream (citations, lineage, incremental recompute) depends on it. (b) Design the **CDC/delta mechanism** (timestamp/version columns, hash-diff for files, snapshot diff for docs) so the pipeline processes changes, not the whole corpus. This is gap **G8**.

### 4.2 Profiler & Metadata Extractor — *T0 + T1*
- **Input:** raw tables + doc text (delta-scoped).
- **Output:** per-column stats (type, cardinality, null %, distributions, format signatures, candidate keys, declared + **inferred** FKs, semantic column type), per-doc structural metadata.
- **Tiers:** T0 deterministic profiling; **T1** semantic column-type classification (Sherlock/Sato-class small model — milliseconds/column, no LLM) and a **scored join-candidate (undeclared-FK) detector** (inclusion-dependency + name/type/cardinality).
- **Quality bar:** ≥90% true-PK and declared-FK detection on the relational test set; calibrated join-candidate scores.
- **Fable task:** design the **undeclared-FK / join-candidate algorithm** and the semantic-type classifier; both feed relationship inference. Memoize results per source schema; re-run only on schema drift. This is gap **G3**.

### 4.3 Schema / Entity-Type Inference — *T1 + memoized T3*
- **Input:** profiles + sampled values + optional seed glossary.
- **Output:** proposed **entity types** + **properties** (datatypes), each with confidence + provenance + rationale; a canonical vocabulary mapping synonyms to one term.
- **Tiers:** **T1** embeddings cluster obviously-synonymous columns; **T3 (frontier, memoized)** reconciles genuinely novel/ambiguous schema structures via the **Extract-Define-Canonicalize** pattern — *called once per (source schema × target ontology) pair, then cached*. Re-triggered only on schema drift.
- **Quality bar:** ≥85% alignment with the gold ontology's type/property set.
- **Fable task:** design the **canonicalization step** (embeddings + frontier-adjudication on the hard cases, deterministic tie-break) and the **memoization key** so the frontier model never re-reconciles a schema it has already seen. This is gap **G2**.

### 4.4 Relationship Inference — *T1 + T2 + selective T3*
- **Input:** entity types + properties + join candidates + doc-extracted triples.
- **Output:** typed relationships (domain, range, cardinality, direction) with confidence + provenance.
- **Tiers:** **T1** structural/FK evidence; **T2** small relation/triple-extraction model on text (distilled from frontier); **T3** only when structural and semantic signals conflict or world knowledge is needed.
- **Quality bar:** relationship F1 ≥0.75 vs gold.
- **Fable task:** design the **signal-fusion logic** reconciling structural, semantic, and textual relationship evidence into one ranked, de-duplicated set with calibrated confidence and explicit conflict rules. This is gap **G4**.

### 4.5 Entity Resolution — *the full cascade: T0 → T1 → T2 → T3* (the hardest module)
- **Input:** instance rows/mentions typed to compatible entity types, across sources (delta-scoped).
- **Output:** clusters of mentions referring to one real-world entity, each merge with calibrated confidence + the evidence used.
- **Tiers (this is the canonical example of the whole architecture):**
  1. **T0/T1 Blocking** — set-similarity join / MinHash-LSH / embedding-kNN (DeepBlocker-class) reduces the O(n²) pair space by **≥99%** at ≥98% pairs-recall. *Nothing downstream ever sees a pair that blocking didn't surface.* This single step is what prevents the "infinite LLM loop."
  2. **T1 Statistical linkage** — **Splink/Fellegi-Sunter** scores survivors into **auto-match / ambiguous / auto-reject** bands via two thresholds. Target: ≥80–90% of candidate pairs resolved here without any model.
  3. **T2 Specialist matcher** — a Ditto/AnyMatch/Jellyfish-class fine-tuned model adjudicates the ambiguous band, distilled offline from the frontier model. Within ~4–5% F1 of frontier at >100× lower cost.
  4. **T3 Frontier adjudication** — only the residual pairs T2 is still unsure about, batched + cached. Used-as-judge, frontier adjudication on narrow "same entity?" calls is high-agreement.
  5. **Collective resolution** — transitive closure → **correlation clustering** (KwikCluster/Pivot or a parallel approximation) resolves A=B, B=C, A≠C conflicts into coherent clusters without pre-specifying cluster count. Optional **GNN (HierGAT-class)** if pairwise F1 plateaus.
  6. **Incremental ER** — new daily records link against maintained clusters (FAMER-style) without full recompute; re-adjudicate only edges the delta touches.
- **Quality bar:** ≥0.85 pairwise F1 on §8.1 benchmarks; **zero** auto-merges below the human-review threshold; calibrated probabilities (ECE reported); **≤20–25% of ambiguous-band pairs reach T3**.
- **Fable task (largest):** design every tier above — blocking keys + recall proof; the T1/T2/T3 escalation thresholds; confidence calibration (temperature scaling + conformal prediction); the correlation-clustering step; and the incremental path. Benchmark each sub-step independently and end-to-end. This is gaps **G1** (resolution) and partly **G5/G8**.

### 4.6 Ontology Assembler — *T0*
- **Input:** approved types, properties, relationships, resolved entities.
- **Output:** canonical **RDF + OWL + SHACL**, fully provenance-annotated (PROV-O); every triple traces to ≥1 source atom.
- **Fable task:** design the **mapping templates** (entity type → OWL class; property → datatype/object property; constraint → SHACL), the OWL-vs-SHACL split (OWL open-world for inference, SHACL closed-world for validation), and the URI-minting strategy for resolved entities. This is gap **G7** (portability foundation).

### 4.7 Validation — *T0*
- **Input:** canonical graph + SHACL shapes + OWL ontology.
- **Output:** severity-ranked validation report (violations, inferred triples, inconsistencies).
- **Fable task:** design the **gating policy** (hard-block vs warning) and mark reasoner-inferred triples distinctly from source-asserted ones so they're never confused.

### 4.8 Human Review UI — *human tier*
- **Input:** schema deltas, low-confidence merges, validation warnings.
- **Output:** approve/reject/edit decisions, logged as ground truth.
- **Critical loop:** every human decision is **reused** — to recalibrate cascade thresholds and as **active-learning labels to train the T2 specialists** (DTAL shows ~100–300 labels can close most of the SLM-vs-frontier gap). This is how the frontier-call rate *drops over time*.
- **Fable task:** design the **review-queue prioritization** (surface highest-impact, lowest-confidence first) and the feedback loop into calibration + specialist training.

### 4.9 Exporters & Portability — *T0*
- **Output:** (a) RDF/Turtle+OWL+SHACL bundle, (b) JSON-LD, (c) LPG load (Neo4j Cypher/CSV; Kùzu loader).
- **Quality bar:** **round-trip test passes** — export → load into Oxigraph/Jena, Neo4j, and Kùzu/GraphDB → run the same competency queries → equivalent results (documented lossiness only at known RDF↔LPG boundaries: edge properties via RDF-star, reification).
- **Fable task:** design the **RDF↔LPG mapping** and a reproducible round-trip diff harness. This is gap **G7**.

### 4.10 GraphRAG Query Engine — *T1 retrieval + T2/T3 generation*
- **Input:** NL question + canonical graph.
- **Output:** answer + citations (source atoms) + the subgraph/path used.
- **Tiers:** **T1** retrieval (text-to-SPARQL/Cypher for precise questions; community-summary/subgraph retrieval for thematic ones); **T2** small model for routine query generation; **T3** only for complex multi-hop synthesis. A query router (RouteLLM-style) picks the cheapest sufficient generator.
- **Quality bar:** ≥70% correct on the competency set; 100% of answers cite source atoms; beats vector-only RAG on multi-hop.
- **Fable task:** design the **router** (structured query vs subgraph retrieval vs hybrid; cheap vs frontier generator), the **text-to-query validator/repairer** (reject/repair invalid queries), and the **citation assembler** tying every claim to source atoms via the provenance ledger. This is gap **G9**.

---

## 5. Cross-cutting infrastructure

### 5.1 Orchestrator + Provenance Ledger
A resumable DAG (Dagster or lean asyncio) that runs the pipeline, persists intermediate artifacts, supports resume-from-step and **delta-only reruns**, and writes everything to an append-only **provenance ledger** (every derived artifact → its inputs → module/version → confidence → tier-that-decided-it). The ledger powers citations, lineage, debugging, incremental recompute, *and* cost attribution. **Fable: design the ledger schema and the resume/incremental-rerun semantics.**

### 5.2 Model abstraction layer (T2 + T3)
One `ModelClient` interface: `propose(task, context, json_schema) → structured output + token usage + logprobs/confidence`. Adapters for (a) a frontier API model (T3) and (b) a local/open small model (T2), both OpenAI-compatible. Built in: **constrained/structured decoding** (XGrammar/Outlines-style `guided_json` so the model emits only decision fields, not prose), retry/repair on malformed output, **semantic caching** (embedding-keyed; serve cached decisions on similarity hits), **prompt-prefix caching** (keep system prompt + ontology schema + few-shot examples as a stable cached prefix), and a **token/cost meter per call**. **Fable: design the prompt-contract registry, the structured-output validation/repair loop, and the cache key/eviction policy.**

### 5.3 Confidence & gating (the cascade control plane)
A uniform confidence representation across all tiers and the global escalation policy from §2. Calibration via temperature scaling + **conformal prediction** (prediction-set size drives deferral: singleton → automate; large set → escalate), with thresholds learned from the human/active-learning feedback. **Fable: design the calibration methodology, the cost-aware deferral rule, and the threshold-adaptation loop.** This is gap **G5**.

### 5.4 Cost Governor (token budget enforcement)
A first-class component, not an afterthought. Holds a **per-cycle token budget** and a **per-record escalation quota**; meters spend through the cascade; and **fails closed to the cheapest sufficient tier** when the budget is hit (FrugalGPT operates explicitly under a budget constraint). Emits the §7 metrics. **Fable: design the budget model, the quota enforcement, and the fail-closed behavior** (what happens to a record that would exceed budget — queue for next cycle, accept T2's best guess and flag, or send to human).

### 5.5 Storage
Canonical graph: RDF store (Oxigraph embedded, or Jena/Fuseki) as source of truth. Working/intermediate: DuckDB + Parquet. Vectors (mention embeddings, doc chunks, semantic cache): FAISS/LanceDB. **Fable: default to embedded (single binary, solo-friendly).**

---

## 6. Technology stack (proposed; Fable may revise with justification)

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.12 | data + LLM + RDF ecosystem |
| Orchestration | Dagster / lean asyncio DAG | resumable, observable, delta-aware |
| Profiling | DuckDB, PyArrow, ydata-style stats | fast, embedded |
| Blocking / candidate gen | set-similarity joins, MinHash-LSH, DeepBlocker | ≥99% pair reduction before any model |
| Statistical linkage (T1) | **Splink** (Fellegi-Sunter) | labels-free, scales 100M+, interpretable thresholds |
| Classical matchers (T1) | gradient boosting / logistic regression on similarity features | handles the easy band cheaply |
| Specialist matcher (T2) | Ditto / AnyMatch / Jellyfish, distilled from frontier | ~4–5% F1 of frontier at >100× lower cost |
| Column type (T1) | Sherlock / Sato | ms/column, no LLM |
| Calibration | temperature scaling, conformal prediction (MAPIE-style) | deferral gating |
| Collective ER | correlation clustering (KwikCluster); optional HierGAT GNN | coherent clusters / contextual matching |
| RDF/OWL | rdflib, pySHACL, Oxigraph | canonical store + validation |
| LPG export | Neo4j, Kùzu | round-trip portability proof |
| Vectors | sentence-transformers, FAISS/LanceDB | local, model-agnostic |
| Constrained decoding | XGrammar / Outlines (`guided_json`) | zero wasted tokens, parseable output |
| Frontier (T3) | frontier API model + Batch API + prompt caching | reserved SME role |
| KG-construction refs | Microsoft GraphRAG, iText2KG, AutoSchemaKG, LangChain LLMGraphTransformer | study/borrow, don't rebuild |
| Cost/observability | Langfuse / Helicone / OpenLLMetry | cost per 1k entities, escalation rate, cache-hit rate |
| UI | FastAPI + light React | review queue + query console |
| Packaging | Docker compose; single-VM/desktop | solo-deployable |

License posture: Apache-2.0 core; avoid copyleft in the core path; track every dependency's license.

---

## 7. Token economics: budgets, targets, and the cost model

**Per-cycle cost model.** `Cost_day ≈ Σ_tiers (records_reaching_tier × calls_per_record × tokens_per_call × price_per_token) − cache/batch discounts`. The dominant lever is **records_reaching_T3**. Drive it down with blocking, statistical linkage, specialist models, caching, and delta processing — in that order.

**Targets the MVP must hit (and instrument):**
- **T3 deferral:** ≤20–25% of *adjudication-needing candidates* reach the frontier (RouteLLM anchors 14–26%); a far smaller fraction of *raw records*.
- **Blocking:** ≥99% candidate-pair reduction at ≥98% pairs-recall.
- **Statistical band:** ≥80–90% of candidate pairs auto-decided at T1 (outside the ambiguous band).
- **Cache-hit rate (daily reruns):** ≥40%; if lower, delta-detection or key-normalization is leaking duplicate work.
- **Discounts on residual T3 calls:** Batch API (~50%) + prompt-cache reads (~90% on cached prefix) stacked.
- **Trend:** frontier-call rate per cycle should **decline over time** as active-learning labels improve the T2 specialists and memos accumulate.

**The durable design ratios** (treat as signals; verify live prices at build time): ~20–23× frontier/cheap-model price spread; ~100× distillation savings; small-matcher within ~4–5% F1 of frontier at orders-of-magnitude lower cost; 50% batch / 90% cache discounts; FrugalGPT 50–98% cost cut at frontier-matching quality; RouteLLM 95% quality at 14–26% frontier calls.

---

## 8. Testing program on open-source data (mandatory)

Every algorithmic claim is measured against public datasets with gold standards. **Build the benchmark-harness shell before the algorithms**, so each module is born measured. (First harness task: verify each dataset below is still downloadable and check its license before wiring it in.)

### 8.1 Entity resolution / record linkage (§4.5)
DBLP–ACM, DBLP–Scholar, Amazon–Google, Abt–Buy, Walmart–Amazon (the Magellan/DeepMatcher benchmark suite) and the WDC Products / LSPM e-commerce matching benchmark. Labeled match/non-match pairs → pairwise P/R/F1 vs published baselines (Magellan, DeepMatcher, Ditto, AnyMatch). **Also measure: blocking recall & reduction ratio, T1-band coverage, T3-deferral rate, and ECE — not just F1.**

### 8.2 Schema matching / data integration (§4.2–4.4)
Valentine schema-matching benchmark; TPC-H/TPC-DS and Spider/BIRD (multi-table schemas with known keys/FKs) for FK discovery and relationship inference, plus NL-question gold for the query engine. Metrics: type/property alignment P/R; FK-discovery recall; relationship F1.

### 8.3 KG construction / GraphRAG (§4.6, §4.10)
WebNLG and REBEL (text↔triples) for triple-extraction P/R/F1; HotpotQA, 2WikiMultiHopQA, MuSiQue for multi-hop QA (prove the graph beats vector-only RAG); MetaQA for KG-grounded QA.

### 8.4 End-to-end vertical "hero" corpus (integration test)
Assemble one realistic multi-source corpus and **hand-build a gold ontology + 30 competency questions**. Recommended for Glenn's wedge: an **aerospace/compliance** set — FAA aircraft registry + NASA ASRS incident reports (structured + unstructured mix) — as the hero demo. Fallbacks for faster iteration: MovieLens (+IMDb/TMDB) for clean cross-source ER; Synthea/MIMIC-demo for multi-table relationships; OpenFlights/GTFS for joins.
**Integration test measures the full §1.3 definition-of-done**, including the token-budget targets in §7.

### 8.5 Harness requirements
One command runs all benchmarks → a scorecard (per-module metric + delta vs last run + delta vs published baseline + **cost-per-1k-entities and T3-deferral-rate per run**). Regression gate fails CI on a metric drop or a cost spike beyond threshold. Every metric tied to a fixed dataset version + seed.

---

## 9. Build phases (sequenced for a solo builder)

**Phase 0 — Skeleton, provenance, CDC, cost governor (wk 1–2).** Orchestrator, provenance ledger (§5.1), model abstraction with caching + constrained decoding (§5.2), cost governor shell (§5.4), ingestion of one Postgres DB + CSV with delta detection. *Exit: an atom is ingested, addressable by URI, and a second run processes only the delta.*

**Phase 1 — Deterministic + statistical floor, then schema→RDF (wk 3–5).** Profiler + semantic-type + join-candidate detector (§4.2), **blocking** (§4.5 step 1), **Splink/Fellegi-Sunter** (§4.5 step 2), schema/type inference with memoized frontier reconciliation (§4.3), assembler (§4.6), SHACL validation (§4.7), RDF+JSON-LD export (§4.9). *Exit: single-source data → validated OWL/RDF scored vs a gold schema on Spider/TPC-H; **blocking hits ≥99% reduction / ≥98% recall**; ≥80% of pairs auto-decided at T1 — all before any T2/T3 call.*

**Phase 2 — Relationships + GraphRAG (wk 6–8).** Relationship inference with signal fusion (§4.4), GraphRAG query engine + router + citations (§4.10), thin "Ask" UI. *Exit: multi-hop questions answered with citations on MovieLens/Spider; beats a vector-RAG baseline; query generation routed cheap-first.*

**Phase 3 — Entity resolution cascade + specialists (wk 9–13) — the hard milestone.** Full T2 specialist matcher distilled from the frontier (§4.5 step 3), T3 frontier adjudication gated + batched + cached (step 4), correlation clustering (step 5), incremental ER (step 6), calibration + conformal deferral (§5.3), Review UI with the active-learning feedback loop (§4.8). *Exit: ≥0.85 ER F1 on §8.1; **T3 deferral ≤20–25%**; calibrated ECE; human decisions retrain T2 and lower the frontier-call rate run-over-run.*

**Phase 4 — Portability hardening + hero demo + budget proof (wk 14–16).** LPG export + 3-backend round-trip (§4.9), incremental daily-update path end-to-end, full integration test on the aerospace/compliance hero corpus (§8.4), and the **token-budget scorecard** (§7) proving the daily delta run is cheap and T3 usage is bounded. *Exit: full §1.3 definition-of-done met, including cost targets; reproducible scorecard; recorded demo.*

**Phase 5 (optional) — OSS launch polish.** README, 2-min demo, one-command quickstart on the hero corpus, Apache-2.0 + CLA, contribution guide.

---

## 10. Gap analysis — the unsolved problems Fable must design, research, and prove

Wiring libraries is not enough here. Each gap is a research+design task with a required benchmark.

| # | Gap | Why it's hard | Fable's deliverable | Proven on |
|---|---|---|---|---|
| **G1** | Cross-source entity resolution as a full cascade | O(n²) blowup; LLM matchers hallucinate; transitivity conflicts; needs calibrated probabilities for gating | The complete T0→T3 ER design (blocking → Fellegi-Sunter → specialist → frontier → correlation clustering → incremental) hitting ≥0.85 F1, low ECE, ≤25% T3 deferral | §8.1 suites |
| **G2** | Schema/property canonicalization across silos, memoized | Synonyms/homonyms/units; open-world naming; must not re-call frontier | EDC-style canonicalizer + memoization key; ≥85% alignment | Valentine, Spider, hero |
| **G3** | Undeclared-FK / join-candidate discovery | Combinatorial; noisy overlaps; false joins poison the ontology | Scored inclusion-dependency + name/type/cardinality algorithm with calibrated scores | TPC-H/DS, Spider |
| **G4** | Relationship signal fusion | Structural vs semantic vs textual signals disagree | Conflict-resolution + ranking producing relationship F1 ≥0.75 | WebNLG/REBEL + Spider |
| **G5** | Cascade calibration, routing & cost-aware deferral | Raw model scores aren't probabilities; thresholds must adapt; budget must bind | Uniform calibration (temp scaling + conformal) + adaptive thresholds from feedback + fail-closed budget rule; report ECE & deferral rate | All modules |
| **G6** | Hallucination control in extraction & matching | Models invent entities/edges not in source | Constrain-then-verify: every asserted triple cites a source atom or is dropped; measure hallucination rate | REBEL/WebNLG, hero |
| **G7** | RDF ↔ LPG portability without semantic loss | Edge properties, reification, OWL semantics don't map cleanly to LPG | Documented bidirectional mapping + round-trip test proving equivalent query results across 3 backends | round-trip harness |
| **G8** | Incremental / delta computation for daily updates | Adding/changing records mustn't force full recompute or break URIs/links | CDC → delta-scoped pipeline + incremental ER + stable URIs + change-set/diff model; cost ∝ delta | hero corpus, staged loads |
| **G9** | GraphRAG routing & faithful citation | When to do structured query vs retrieval; tying every claim to evidence; cheap-first generation | Router + text-to-query validator + citation assembler; ≥70% QA, 100% cited, beats vector RAG | HotpotQA/2Wiki/MuSiQue + hero |
| **G10** | Specialist distillation & token-budget governance | Per-row frontier calls are unaffordable; specialists must approach frontier quality | Offline distillation pipeline (frontier teacher → T2 student) + active-learning loop + measured cost-per-1k-entities under a hard budget | §8.1 + scaling test on hero |

**Highest-risk, do-first:** **G1** (the ER cascade) and **G6/G9** (hallucination + faithful citation), because they gate trustworthiness for regulated buyers; and **G10** (distillation + budget), because it gates whether the economics work at daily-enterprise scale. If G1 or G6 miss their bars, narrow scope (structured-only, single-domain) before widening.

---

## 11. Risks & kill-criteria
- **If ER F1 stays <0.85** even with the full cascade + human-in-the-loop after Phase 3 → restrict to structured-only, single-domain; defer unstructured docs.
- **If T3 deferral exceeds ~30%** of candidates → blocking/T1/T2 are under-performing; invest in better embeddings, distillation, or active-learning labels *before* scaling frontier spend.
- **If cache-hit rate is <40%** on daily reruns → delta detection or key-normalization is leaking duplicate work; fix before adding features.
- **If schema-reconciliation frontier calls recur on the same schemas** → memoization is broken.
- **If GraphRAG can't beat vector RAG** on multi-hop → the graph isn't earning its complexity; revisit relationship/ontology quality.
- **If round-trip portability loses query equivalence** → reposition as RDF-native (drop the "any backend" promise) rather than ship a false claim.

---

## 12. Immediate next actions for Fable
1. Stand up Phase 0: orchestrator + provenance ledger + CDC/delta + model abstraction (with caching + constrained decoding) + cost-governor shell.
2. Build the **benchmark-harness shell** (§8.5) — including cost-per-1k-entities and T3-deferral metrics — *before* the algorithms.
3. Pull the §8.1 ER benchmarks and §8.2 schema/text-to-SQL sets locally (verify availability + license); wire the scorecard.
4. Design **G1 on paper** as a full cascade (blocking → Fellegi-Sunter → specialist → frontier → clustering → incremental), then implement against DBLP–ACM / Amazon–Google first, reporting F1 *and* deferral rate.
5. Design the **distillation pipeline (G10)**: use the frontier model offline as teacher to train the T2 specialist; stand up the active-learning loop from the Review UI.
6. Choose the **hero corpus** (recommend the aerospace/compliance set from §8.4) and hand-build its gold ontology + 30 competency questions.
