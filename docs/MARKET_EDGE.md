# OntoForge — Market Edge (competitive positioning, June 2026)

Synthesized 2026-06-12 from four research briefs (warehouse-native NL analytics, data-platform
incumbents + agentic data engineering, KG/GraphRAG/ontology tooling, buyer pain + GTM). Every claim
cites its brief source and date. Claims the researchers could not verify against primary sources are
marked **[UNVERIFIED]**. What OntoForge "ships" below means: implemented and measured in this repo
at fixture scale (882 tests green, M0–M14; see README "Measured results"), not deployed at customers.

---

## (a) The 2026 landscape in one table

| Vendor / product | What it actually does | Pricing signal | Hand-authored vs automated |
|---|---|---|---|
| **Snowflake** Semantic View Autopilot (GA 2026-02-03) + Cortex Analyst | Auto-generates/maintains semantic views from query history, table metadata, uploaded SQL/Tableau context; Cortex Analyst NL-to-SQL over them (~85–90% claimed on "well-defined" models) | ~$0.20/question (6.7 credits/100 msgs) + warehouse compute dual-billing; Autopilot pricing not public **[UNVERIFIED]** | **Automated generation, mandatory human review**; inputs are already-modeled warehouse tables; cold-start fails without query history; ~10-table / 50–100-column ceiling per semantic view (Snowflake docs, fetched 2026-06-12) |
| **Databricks** Unity Catalog Business Semantics (GA 2026-04-02) + Genie / Genie Code | Metric views as governed catalog assets (core open-sourced into Apache Spark, SPARK-54119); Genie grounds NL on metric views; Genie Code *suggests* measures/dims/synonyms | Genie billing enforced 2026-07-06: 150 free DBUs/user/mo, ~$0.07/DBU overage; service principals (agent traffic) billed from first request; serverless SQL ~$0.75/DBU separate | **AI-assisted human authoring** — definitions still SQL/UI-authored |
| **Microsoft** Fabric IQ Ontology (preview, Build 2026) + Graph in Fabric (GA 2026-06-03) + Osmos acquisition (2026-01-05) | Explicit ontology item (entity types/instances, typed relationships with `confidence`/`effectiveAt`, cardinality rules), GQL graph engine on OneLake, MCP endpoints; Osmos = "autonomous data engineering" for raw→AI-ready in OneLake | Bundled into Fabric capacity (CU) pricing — "good-enough" feels free to E5/Fabric shops | Ontology **generated only from an existing Power BI semantic model** (human modeling upstream); entity-to-data binding manual; no ER across messy sources |
| **Google** Looker / Conversational Analytics | LookML-grounded NL analytics; BI Agents (Next '26); claims semantic layer cuts gen-AI errors ~2/3 **[UNVERIFIED — Google internal tests only]** | Bundled w/ Gemini for GCloud; ~$9/user/mo Looker Studio AI **[UNVERIFIED, third-party figure]** | **Hand-authored LookML** with Gemini authoring assistant |
| **Palantir** Foundry / AIP | Hand-built ontology + operational write-back + AIP agents; forward-deployed delivery; Q1 FY2026 $1.63B rev, 206 deals ≥$1M, 615 US commercial customers total (2026-05-04, partially verified vs SEC) | Three meters: compute-seconds, storage GB-mo, **ontology indexed GB-mo** (uncompressed, auto-reindex compute); no public rates; verified customer cost complaint went unanswered 60 days (community.palantir.com, 2025-02-13) | **Fully hand-authored** by forward-deployed engineers; exports "not readily usable by any other equivalent system" (HASH 2025-04-03 — competitor-authored, directionally corroborated) |
| **Fivetran + dbt Labs** (merger closed 2026-06-01) | Managed EL + governed transforms; MetricFlow re-licensed Apache under OSI; "Agents Schema" open standard (semantics/metrics/lineage as SQL tables for agents); ~$600M combined rev, 100k+ teams | Consumption (Fivetran) + seat/usage (dbt Cloud); Agents Schema free/open | **Hand-authored dbt semantic models**; conceded "a closed-source semantic layer doesn't work" |
| **Qlik (Talend)** Agentic Swarm (2026-04-14) | Catalog/Glossary/Data Product/Data Quality agents — find, describe, bundle assets | Sold into existing enterprise contracts; no public agentic pricing | Autonomous remediation **explicitly future-tense roadmap**, not shipped |
| **Informatica (Salesforce**, closed 2025-11-18) | CLAIRE agent skills + MCP servers in AWS Bedrock AgentCore; "headless data management" (2026-05-20, partially verified) | Enterprise MDM contracts | Agent-callable APIs over **existing human-configured MDM/quality** functions |
| **Ascend.io / Ardent AI** (agentic pipelines) | DataOps agents: incident reports, code review, commit messages, tuning suggestions (Ascend GA 2025-07-29); Ardent: pipeline repair over Databricks/Snowflake/Airflow ($2.15M pre-seed, ~$100K ARR self-reported) | Ascend: opaque "credits"; "50–70% of operational tasks automated" **[UNVERIFIED marketing, no methodology]** | **Assistive**, pipeline-scoped; humans author pipelines and all semantics |
| **Zep/Graphiti** (27.3k stars) | Bi-temporal KG memory for agents: event-vs-ingestion time, fact invalidation, point-in-time queries, ER, episode-level provenance; prescribed or learned ontologies | Free 1k credits/mo; Flex $1,250/yr; metered per 350 bytes ingested | Ontology prescribed (Pydantic) or **learned from chat/text** — agent-memory use case, not enterprise records |
| **Neo4j** Aura Agent (GA ~2026-02) + LLM KG Builder | Ontology-driven agent auto-construction, hosted MCP; doc→graph extraction | $0.35/agent-hour (external agents, from 2026-03) + AuraDB consumption | Automates agents **from an ontology you already have**; extraction ≠ validated induction |
| **Stardog Voicebox** (relaunch 2025-09-03) | "Hallucination-free" NL→SPARQL, per-answer Knowledge Panel lineage, **binary** refusal ("cannot find an answer") | Enterprise quote-based | **Hand-modeled** KG/semantics required; refusal is structural, not calibrated |
| **Graphwise** (Ontotext+SWC) GraphRAG v1.0 "Trust Layer" (2025-12-18) | GA per-answer document/entity citations, explainability panel; Taxonomy Advisor (LLM-assisted) | Enterprise quote-based | **Requires existing taxonomy/ontology**; no abstention, no confidence scores |
| **cognee** ($7.5M seed 2026-02) | "Cited memory" graph for agents; auto entity/relationship/rule extraction; 70+ deployments | OSS free; $35–$200/mo cloud, document-count metered | Semi-automatic extraction; agent-memory framing |
| **Collibra Semantic Agents / Timbr** | Collibra: auto-generate semantic models from business glossaries + metadata (late 2025); Timbr: virtual ontology / GraphRAG layer hand-mapped over relational DBs | Enterprise quote-based; Collibra base ~$170–197K/yr **[vendor-adversarial teardowns — use as ranges]** | Generation **from existing glossaries/metadata** (human curation upstream); Timbr mappings hand-authored — niche competitor to watch on the entity-graph angle |
| **Microsoft GraphRAG / LazyGraphRAG** (v3.1.0, 2026-05-28) | OSS text→graph; LazyGraphRAG indexing at ~0.1% of full-GraphRAG cost | Free (MIT); cost = LLM tokens | Graph-from-documents is **commoditized and near-free** |
| **OSI v1.0** (spec finalized 2026-01-27, ~30+ vendors) | Vendor-neutral interchange for datasets/metrics/dimensions/relationships/context, multi-dialect expressions; Phase 2 targets 50+ platforms by end-2026 | Free, Apache-2 | A format, not a product — semantics still authored upstream by humans or vendor AI |

**Structural read:** the entire 2026 auto-generation wave starts from *already-modeled* data
(warehouse tables + query history, Power BI models, business glossaries) and produces *flat
metric/dimension definitions*, platform-bound. Independent critique (Timbr, 2026): every vendor
solution "locks semantics to single platforms and fails to enable multi-hop entity reasoning."
Nobody starts from messy raw sources; nobody resolves entities; nobody validates; nobody exports the
whole estate.

---

## (b) OntoForge's defensible differentiators — ranked by evidence nobody ships them

Ranked by the *strength of negative evidence* (how thoroughly the researchers searched and found no
shipping equivalent), not by how loudly we can market them.

### 1. Auto-induced, validated ontology from messy raw sources (STRATA + ER + ANVIL closed loop)
**Evidence nobody ships it: strongest — three of four briefs independently concluded this.**
- "No shipping product found (June 2026) that induces a validated ontology from messy sources. This
  is the single clearest unclaimed capability." (incumbents brief)
- "No commercial product shipping end-to-end validated ontology induction was found" — induction is
  research-stage (AutoSchemaKG, ATOM/iText2KG EACL 2026, LLMs4OL @ ISWC 2025, LLM4KGOE @ ESWC
  2026-05-11). (KG brief)
- Every 2026 auto-generator (Autopilot, Genie Code, Fabric IQ generation) starts from curated
  tables/models + usage signals, with an explicit cold-start failure when no query history exists.
  (warehouse brief)
- The adjacent unsolved frontier confirms it: Spider 2.0-DBT (end-to-end multi-step transformation
  projects) is stuck at **65.6%** while raw SQL generation self-reports 90%+ — exactly ANVIL's
  transform-synthesis + holdout-verification territory. "Synthesized, auditable cleaning transforms
  [are] absent across the entire KG/GraphRAG landscape… no one packages this today." (warehouse, KG,
  GTM briefs)
- What we ship: STRATA class P/R 0.938/0.647 vs gold, ER F1 0.997, ANVIL fix rate 1.00 — induce →
  resolve → materialize → transform → validate, end-to-end, no hand-authored semantic model.
- **Caveat:** the research frameworks are open source and converging; "window is open but closing —
  move before a research framework gets productized." (KG brief)

### 2. Calibrated abstention (spine-governed selective answering)
**Evidence: academic literature says unsolved; only shipped competitor behavior is binary refusal.**
- AbstentionBench (arXiv 2506.09038): 20 frontier LLMs, "abstention is an unsolved problem" that
  scaling doesn't fix. No commercial KG/GraphRAG vendor ships confidence-calibrated selective
  answering; Stardog's refusal is binary query-failure, not calibrated. (KG brief, searched 2026-06-12)
- dbt's own benchmark (2026-04-07): the decisive enterprise criterion is failure mode — "plausible
  but incorrect answer" vs "an error message... for a board deck or an auditor, that difference is
  everything." Practitioners (2026-03-31): "you cannot build a production analytics system on a
  component whose failure modes are unpredictable."
- Benchmark-credibility crisis (CIDR 2026; arXiv 2601.08778: pervasive annotation errors in
  BIRD/Spider) means buyers increasingly distrust raw accuracy percentages — selling *verifiable*
  answers with measured abstention is the structurally correct response.
- What we ship: spine ECE ≤ 0.05 (5 seeds), conformal coverage within 1.45% of nominal, 0
  confidently-wrong on the competency suite, abstention on unanswerable questions, static rejection
  of unit-incoherent questions.

### 3. Atom-level (per-value, per-temporal-version) citations
**Evidence: citations are now table stakes — but only at document/chunk/entity granularity.**
- Per-answer citations are marketed by Graphwise (GA 2025-12-18), Stardog (page-level), WRITER,
  Fluree, cognee — "all cite at document/chunk/entity or episode granularity, none at
  per-value/atom granularity tied to bi-temporal versions." (KG brief)
- No warehouse-native NL tool cites the source values behind an answer at all. (warehouse brief)
- What we ship: 100% citation coverage; every cell in an answer resolves through the provenance
  semiring to content-addressed source atoms with transform lineage.
- **Caveat:** "citations" as a word is no longer differentiating — the demo must show the
  granularity difference (exact value + temporal version + transform lineage, inspectable).

### 4. Bi-temporal, per-value provenance store for structured enterprise data (HEARTH)
**Evidence: mechanism has one OSS neighbor; the use case is unclaimed.**
- Zep/Graphiti ships bi-temporality + episode-level provenance *for chat/text-derived agent memory*
  (27.3k stars) — "position against Zep by use case, not mechanism." (KG brief)
- Fabric IQ is the only platform competitor even gesturing at time-varying relationships
  (`effectiveAt`, preview). Nobody applies bi-temporality with per-value provenance to
  entity-resolved records from enterprise systems/CDC; no competitor can answer "what did we believe
  on date X" or replay an answer against past world-state. (warehouse brief)
- Regulatory pull: ECB RDARR flags **attribute-level lineage** as urgent (supervisory priority
  2025–2027, capital add-on threats); ~14% of banks fully BCBS 239 compliant **[vendor-cited from
  regulator reviews — verify against ECB primary before external use]**. Catalogs (Collibra/Alation/
  Atlan) do column-level pipeline lineage, not value-level fact provenance.
- What we ship: four-timestamp value cells, survivorship ranking, temporal stances
  (current / as-of / as-known-at / audit), TEMPER snapshot-queryability 100% over 300 random op
  sequences.

### 5. AMBER exit-guarantee (full-estate portable bundle with executable completeness)
**Evidence: paper-portability is becoming universal; *verified full-estate* portability is unclaimed.**
- OSI v1.0 (2026-01-27) means every consortium member can claim "semantic portability" — but only
  for metric/dimension definitions. "No vendor markets full-estate portability (data + ontology +
  provenance + transforms as an open bundle)." (KG brief) "If you swap warehouses, you rebuild."
  (agami comparison, 2026-05-07)
- Regulatory tailwind: EU Data Act applicable 2025-09-12, all switching/egress charges banned
  2027-01-12, 30-day transition mandated. Palantir's record (no formal migration pathways; ontology
  as "deepest and dangerous moat" — HASH 2025-04-03, adversarial source) is the foil.
- What we ship: AMBER bundle-only replay with 100% answer+citation equality (the executable
  completeness property) — a *testable* exit guarantee, beyond anything OSI requires.
- **Caveat:** to stay ahead of OSI-washing we must emit OSI v1.0 and dbt's Agents Schema, and
  publish a restore-elsewhere test. Otherwise this collapses into everyone's checkbox.

### 6. Spine-calibrated autonomy (every autonomous decision through a calibrated, budgeted gate)
**Evidence: nobody ships auditable autonomy; the market is drowning in "agent washing."**
- Gartner (2025-06-25): >40% of agentic AI projects canceled by end-2027; only ~130 of thousands of
  self-described agentic vendors are real. Ascend's "50–70% automated" has no methodology
  **[UNVERIFIED]**; Qlik's autonomous remediation is explicitly roadmap.
- No incumbent ships abstention or per-decision calibration artifacts; the winning posture is
  "auditable autonomy: calibrated abstention curves, per-answer citations, provenance receipts
  instead of automation percentages." (incumbents brief)
- What we ship: ADMIT/SM/ER/synthesis decisions all routed through one spine with per-kind
  calibration, conformal guarantees, and per-operation cost counters in the ledger.
- **Caveat:** ranked last not because it's weak but because it's the least legible to buyers as a
  standalone feature — it is the *mechanism* that makes #1–#5 trustworthy, and should be marketed
  through them.

---

## (c) Where we are NOT differentiated (honest)

- **Raw text-to-SQL accuracy.** Commercial agents self-report 90–97% on Spider 2.0-Snow
  **[self-reported, unverifiable]**; NL-to-SQL on a known schema is commoditizing. Never lead with
  accuracy percentages — the benchmarks themselves are discredited (CIDR 2026).
- **Graph-from-documents extraction.** LazyGraphRAG indexes at ~0.1% of full-GraphRAG cost inside
  free OSS; Neo4j KG Builder is a top-4 AuraDB feature. Extraction is free; we add nothing here.
- **Citations as a word.** Document/entity-level citations are GA across Graphwise, Stardog, WRITER,
  Fluree, cognee. Only the atom granularity is ours.
- **The semantic format itself.** OSI v1.0 and Agents Schema are open; metric views are in Apache
  Spark. Value capture cannot sit in the format — only in induction, validation, provenance, answer
  quality. (Both pricing briefs converge on this.)
- **Bi-temporality as a mechanism.** Graphiti ships it (Apache-2.0, huge mindshare) for agent
  memory; ATOM claims 93.8% latency reduction over Graphiti. Our claim is the *enterprise-records +
  per-value-provenance + validation* ensemble, not bi-temporality per se.
- **Scale and performance.** v0 is Python at fixture scale (AMD-0001: 10^5–10^6 atoms, rescaled
  perf gates; AMD-0008: plain Parquet, no Iceberg). Warehouse-native rivals run at petabyte scale
  with sub-2s latency co-located with compute. Do not pick a performance fight yet.
- **Connectors and ingestion breadth.** File/CSV/doc connectors vs Fivetran's 600+. Ride the
  EL incumbents (ingest from staged warehouse schemas); do not compete with them.
- **Dashboards/BI surface.** VISTA is deliberately minimal; Looker/Power BI/Sigma/Omni are mature.
  Export to them via OSI rather than building a BI tool.
- **Distribution, trust, attestations.** No SOC 2, no customers, no marketplace presence, solo-built.
  Microsoft/Snowflake/Databricks bundle rival features into platforms buyers already pay for.
  SOC 2 Type II is a pass/fail procurement gate we currently fail.
- **Agent-memory framing.** Zep and cognee own it with funding and stars. Avoid the category;
  position as the autonomous semantic data platform / system of record.

---

## (d) Target wedge and buyer profile

**Wedge: regulated mid-market (≈200–2,000 employees) in EU-exposed financial services and
safety-regulated industries (banking/insurance first; aviation MRO/safety second, where our hero
estate already speaks the domain), entered through compliance-forced budget.**

Reasoning from the briefs:

1. **The mid-market is structurally unserved for this capability.** Palantir's effective floor is
   $1M+ forward-deployed deals (206 deals ≥$1M in Q1 FY2026 against 615 total US commercial
   customers); Fabric requires Microsoft-stack commitment; Fivetran+dbt serve the mid-market but
   only at the pipeline/transform layer; catalogs run six-figure ACVs and don't store data. "A
   self-serve, predictably priced semantic-estate product for 200–2,000-employee regulated firms has
   no direct competitor." (incumbents brief)
2. **Regulator-forced budget with pass/fail requirements that map one-to-one to our architecture.**
   EU AI Act Article 10 enforceable **2026-08-02** (~7 weeks out): documented data provenance,
   dataset specifications, version-control and traceability — auto-generatable from our ledger.
   BCBS 239 attribute-level lineage is an ECB supervisory priority 2025–2027 with capital-add-on
   teeth. DORA (in force 2025-01) pushes sovereign/on-prem deployment; sovereignty inquiries +305%
   H1 2025 **[Gartner via secondary, unverified primary]**. EU Data Act makes exit a legal right.
3. **The buyer:** CDO / Head of Data (or CRO-sponsored data lead) at a firm that has burned 1–2 AI
   POCs. The market stats they already quote internally: 42% of companies abandoned most AI
   initiatives in 2025 (S&P Global), 46% of POCs scrapped, ~95% of gen-AI pilots zero ROI (MIT
   NANDA, 2025-08-18), 43% of CDOs name data quality/readiness the top obstacle (Informatica 2025).
   Pitch: "your pilots die on messy data, provenance gaps, and untrusted answers."
4. **Displacement narrative for the upper end:** "Foundry outcomes without Foundry lock-in" —
   induced (not forward-deployed-engineered) ontology, transparent flat pricing vs three opaque
   meters, contractual $0-exit backed by AMBER. Third-party Palantir-alternatives content already
   converges on exactly these three objections (cost predictability, portable semantics, auditable
   explainability) **[vendor-biased sources, weak individually, consistent in aggregate]**.
5. **Pricing posture** (from both pricing-notes sections): flat, estate-size-banded subscription —
   **no per-question meter, no ontology-storage meter, no reindexing charges** ("your ontology is
   not a meter," counter-positioned against Palantir's GB-month and the post-2026-07-06 Genie
   per-DBU reality). $100–250K ACV sits inside established catalog-spend expectations
   (Collibra ~$170–197K/yr, Alation ~$198K base **[vendor-adversarial teardowns — use as ranges]**)
   while replacing catalog + quality tooling + semantic layer. Open-core packaging per the Dagster
   playbook: Apache-2.0 core and bundle format (already our license; dbt's ELv2 detour cost trust
   and was reversed within a year), paid tier = audit/governance depth, SSO, deployment options —
   regulated buyers self-select into paid.

---

## (e) Proof points to build next (ordered by what gates or closes deals, per the GTM brief)

1. **Public accuracy-with-abstention benchmark.** Risk-coverage curves, ECE, abstention
   precision/recall, citation verifiability, on a published competency-question methodology
   (AbstentionBench gives the academic frame; dbt's 2026-04-07 benchmark gives the commercial
   frame). Nobody publishes this; define the leaderboard. Counter to the broken-benchmark backlash.
2. **OSI v1.0 export conformance + Agents Schema emission, demonstrated live in a POC.** Makes
   anti-lock-in checkable in an RFP and turns Snowflake/Databricks/Tableau/dbt runtimes into export
   targets rather than rivals.
3. **Published AMBER restore-elsewhere test.** Round-trip a real (non-fixture) corpus bundle into
   Jena/Oxigraph + DuckDB/Trino, replay the competency suite, publish the 100% answer+citation
   equality result. This is the exit-guarantee receipt; pair with a contractual $0-exit clause.
4. **EU AI Act Article 10 artifact demo.** Auto-generate dataset specification documents,
   provenance documentation, and as-of reconstructions from the ledger — timed to the 2026-08-02
   enforcement date for EU-exposed prospects.
5. **Cold-start induction demo on live messy data.** Point OntoForge at raw multi-source data with
   zero query history (the exact case where Autopilot is weak) and show validated ontology + ER +
   cited answers in hours. Requires lifting AMD-0006 (live corpora).
6. **SOC 2 Type II observation window — start now.** Pass/fail gate; Type II needs months of
   evidence; 77% of buyers demand verified proof **[compliance-vendor stat, indicative]**.
7. **Self-hosted / VPC / on-prem deployment matrix.** Pass/fail in EU banking/defense/pharma
   (DORA, sovereignty). Complements the portability story: portable bundle + run-anywhere.
8. **Public RFP answer pack + transparent pricing page.** Pre-write the standard catalog-RFP rubric
   answers (lineage beyond column-level, connectors, security, TCO calculator vs six-figure catalog
   ACVs). Low-cost asset that opaque-priced incumbents structurally cannot match.
