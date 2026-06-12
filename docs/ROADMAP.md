# OntoForge — Post-v0 Roadmap (prioritized, 2026-06-12)

Merges the whitepaper's deferred items (recorded as amendments AMD-0001…0008 in
`docs/DEVIATIONS.md`: Rust core, Iceberg substrate, live corpora, M15 governance, M16 distillation,
HyFD/neural-blocking challengers, live model tiers) with what the June 2026 market research says
actually gates or closes deals (see `docs/MARKET_EDGE.md` for sources). Ordering principle from the
GTM brief: **compliance artifacts and verifiable trust close deals; raw performance does not** —
so several "engineering-sexy" deferrals (Rust, Iceberg) rank below "boring" proof points.

**Baseline (done):** M0–M14 real, 882 tests green, CLI pipeline, AMBER replay 100%.
**In flight now (v0.2, not roadmap):** FastAPI web app (`ontoforge.server`) and the generic
any-data estate engine (`ontoforge.estates`, YAML-defined estates) — both assumed landed before P0
items start. Several P0 items depend on them.

Effort scale: S ≈ days, M ≈ 1–3 weeks, L ≈ 1–2 months, XL ≈ a quarter+ (solo + agent-assisted).

---

## P0 — Next quarter: deal-gating proof points

### P0.1 OSI v1.0 export + Agents Schema emission (in AMBER and standalone)
- **Effort:** M. Mapping induced ontology/metrics/relationships into the OSI spec (Apache-2,
  GitHub) and emitting dbt-style Agents Schema SQL context tables; conformance tests in CI.
- **Market evidence:** OSI v1.0 finalized 2026-01-27 with ~30+ vendors (Snowflake, Databricks,
  Salesforce, dbt, Collibra…); Phase 2 targets native support in 50+ platforms by end-2026.
  Fivetran+dbt's Agents Schema (2026-06-01) is the agent-context standard to ride. Makes
  "anti-lock-in" checkable in an RFP and turns competitors' semantic runtimes into export targets.
- **Dependency:** M11 export + M14 AMBER (done). Generic estate engine (in flight) so exports
  aren't aviation-shaped.

### P0.2 Public accuracy-with-abstention benchmark + published methodology
- **Effort:** M. Risk-coverage curves, ECE, abstention precision/recall, citation-verifiability
  rate on the competency suite; AbstentionBench-style external framing; publish methodology + raw
  results; wire as a permanent CI artifact.
- **Market evidence:** dbt's 2026-04-07 benchmark made failure mode ("never silently wrong") the
  decisive enterprise criterion; CIDR 2026 / arXiv 2601.08778 discredit raw leaderboard accuracy;
  no commercial vendor publishes calibration numbers (KG brief, searched 2026-06-12). First mover
  defines the leaderboard. Extend the same methodology to transform synthesis: Spider 2.0-DBT SOTA
  is 65.6% (vs 90%+ self-reported raw SQL) — ANVIL's holdout-verified fix results belong in public
  view under the same published harness.
- **Dependency:** M2 spine + M12 LODESTONE (done). Stronger after P0.4 (live corpora) so results
  aren't fixture-only.

### P0.3 EU AI Act Article 10 compliance-artifact generator
- **Effort:** M. Auto-generate dataset specification documents, provenance/traceability reports,
  and as-of reconstructions from the N[X] ledger + HEARTH temporal stances; one CLI/API call,
  auditor-readable output.
- **Market evidence:** Article 10 enforceable **2026-08-02** — documented provenance, dataset
  specs, version traceability become mandatory for high-risk AI in the EU. Also services BCBS 239
  attribute-level lineage (ECB supervisory priority 2025–2027). This is regulator-forced budget
  with a date attached; nothing else on this list has a countdown clock.
- **Dependency:** M0 ledger + M6 HEARTH (done). None blocking — highest urgency-to-effort ratio.

### P0.4 Live corpora Tier-3 runs (lift AMD-0006)
- **Effort:** M–L. Browser-capable fetch for FAA/ASRS/NTSB real downloads, pinned snapshots,
  longitudinal drift runs; then one non-aviation public estate (e.g., NYC-311 or OpenFoodFacts per
  whitepaper §17) through the generic engine.
- **Market evidence:** Snowflake Autopilot's documented cold-start weakness (no query history =
  weak output) is our demo wedge — "point at raw messy data, get a validated ontology" only lands
  if shown on *real* messy data, not labeled fixtures. Also de-risks every P0.2 claim.
- **Dependency:** Generic estate engine (in flight); M1 CDC hardening.

### P0.5 SOC 2 Type II observation window + security hardening
- **Effort:** M to start (controls, logging, access policies), then calendar time (months of
  evidence accrual) — start now precisely because it cannot be compressed later.
- **Market evidence:** SOC 2 Type II is the de facto pass/fail procurement gate for data/AI
  software; 77% of buyers demand verified proof [compliance-vendor stat, indicative]. Absence loses
  deals silently in security review.
- **Dependency:** FastAPI server + multi-tenant boundaries (in flight); whitepaper Phase-5 security
  items (prompt-injection review of ingested-content paths, Actions authn/authz).

---

## P1 — Two to three quarters: expand the wedge

### P1.1 Self-hosted / VPC / air-gapped deployment packaging
- **Effort:** M–L. Containerized single-tenant deploy, BYO-cloud install docs, offline model-tier
  configuration (the deterministic adapters are an unexpected asset here), deployment matrix doc.
- **Market evidence:** sovereignty inquiries +305% H1 2025 [Gartner via secondary, unverified];
  DORA in force 2025-01 pushing EU banks to repatriate; on-prem is a stated pass/fail RFP line in
  regulated evaluations. Compounds the AMBER story: portable bundle + run-anywhere.
- **Dependency:** P0.5 (security posture), FastAPI server.

### P1.2 MCP server endpoint for the estate
- **Effort:** S–M. Expose LODESTONE ask (with citations + abstention), HEARTH temporal reads, and
  AMBER export as MCP tools on the FastAPI server.
- **Market evidence:** MCP is the converged agent surface — Fabric IQ ontologies, Informatica
  CLAIRE (Bedrock AgentCore, 2026-05-20), Neo4j Aura Agent, GraphDB 11 all expose MCP. Databricks
  billing service principals from the first request (2026-07-06) signals machine-to-machine NL
  query volume is where vendors expect revenue; our calibrated, cited answers are *more* valuable
  to agents than to humans (agents can't eyeball-check a dashboard).
- **Dependency:** FastAPI server (in flight).

### P1.3 Warehouse/CDC connector breadth (Snowflake, Databricks, Postgres CDC, common SaaS exports)
- **Effort:** L. Ingest from staged warehouse schemas and Postgres logical replication; do NOT
  rebuild Fivetran — ride it (induce from wherever EL lands the data).
- **Market evidence:** the wedge buyer's data sits in warehouses and operational DBs; Fivetran has
  600+ connectors and 100k+ teams — competing there is lost; complementing it makes Fivetran+dbt a
  channel, not a rival. Also unlocks "induce over your existing Snowflake estate, then export OSI
  back into Cortex" — selling *into* the incumbents' install base.
- **Dependency:** M1 CDC abstraction (done); generic estate engine.

### P1.4 Iceberg substrate for HEARTH (lift AMD-0008)
- **Effort:** L. Replace Parquet+atomic-rewrite with Iceberg commits behind the same `Hearth` API
  (the cell schema and survivorship ordering are substrate-independent per AMD-0008's migration
  note); AMBER data layer emits Iceberg natively.
- **Market evidence:** lakehouse buyers expect Iceberg as the open-table lingua franca; AMBER
  bundles in Iceberg make the restore-elsewhere test (MARKET_EDGE proof point 3) read natively by
  Trino/Spark/DuckDB. Indirect deal evidence only — hence P1, not P0.
- **Dependency:** none hard; pairs naturally with P1.3 scale and precedes P2.1.

### P1.5 Live model tiers + cost governor at production settings (extend AMD-0002)
- **Effort:** S–M. Activate `AnthropicAdapter` paths under budget governance; publish per-question
  cost telemetry; cassette-record live runs back into CI.
- **Market evidence:** the per-question economics window opens 2026-07-06 (Genie billing); market
  anchors are ~$0.20/question (Cortex) and ~$0.07/DBU (Genie). Flat-subscription positioning needs
  *measured* internal unit costs to be safe.
- **Dependency:** per-operation cost counters (done, M0/M2); live corpora (P0.4) for realistic mix.

### P1.6 Design-partner program (2–3 regulated mid-market firms)
- **Effort:** M (ongoing). Aviation MRO/safety org (hero-estate affinity) + one EU-exposed
  insurer/regional bank; success = the P0 proof points run on their estate.
- **Market evidence:** the entire wedge argument (MARKET_EDGE §d); also the *only* path to M16
  distillation data and honest Tier-3 evidence. Every incumbent's weakness here (Palantir $1M+
  floor, Fabric stack lock, catalogs don't store data) is the pitch.
- **Dependency:** P0.1–P0.5 substantially complete (these ARE the sales materials).

---

## P2 — Later: scale, moat accretion, completeness

### P2.1 Rust core for hot paths (lift AMD-0001)
- **Effort:** XL. Storage/ledger/DBSP/OQIR hot modules behind existing typed contracts; perf gates
  re-enter at original scale (10^8 atoms, p99 <10ms point reads, per AMD-0001 migration note).
- **Market evidence:** honest reading of the briefs — **no buyer evidence says performance closes
  deals at our stage**; compliance artifacts and verifiable exit do. Rust becomes necessary when a
  design partner's estate exceeds Python+DuckDB headroom; gate on that signal, not on aesthetics.
- **Dependency:** P1.4 (Iceberg lands with/before the Rust storage layer per AMD-0008);
  design-partner scale data (P1.6).

### P2.2 M16 distillation loop (lift AMD-0007)
- **Effort:** L. Adjudication mining, curation, LoRA fine-tune harness, A/B promotion, per-tenant
  isolation (whitepaper §M16); verified T3 syntheses and LODESTONE clarifications as training data.
- **Market evidence:** this is the accreting per-tenant moat (whitepaper §15) and the long-term
  unit-economics lever against per-question-priced rivals — but it *requires live tenant traffic*
  by definition. Strictly post-design-partner.
- **Dependency:** P1.5, P1.6.

### P2.3 M15 governance / label-lattice valuation (lift AMD-0007)
- **Effort:** M. Policy engine over the already-pluggable provenance valuations; access-control
  valuation threaded through N[X]; audit views.
- **Market evidence:** regulated buyers eventually require row/value-level entitlements and
  policy-aware answers; catalogs market governance heavily. Not yet deal-gating for the first
  design partners (single-tenant, on-prem deploys reduce urgency).
- **Dependency:** none hard; sequenced after P0.5 security baseline.

### P2.4 Multi-hop entity-graph query showcase + benchmark
- **Effort:** S–M. Competency-suite extension with graph-shaped questions ("which suppliers of
  delayed aircraft also…"), demo assets, possibly a CSR adjacency index (whitepaper M6 item).
- **Market evidence:** the documented industry gap — auto-generated flat metric/dimension
  definitions "fail multi-hop entity reasoning" (Timbr critique, 2026); Snowflake semantic views
  cap at ~10 tables. Cheap, high-contrast marketing artifact.
- **Dependency:** none; better after P0.4 live corpora.

### P2.5 ER/profiling challengers at scale (lift AMD-0003, AMD-0004)
- **Effort:** M each. HyFD challenger for FD discovery; neural/embedding blocking challenger for
  ER — both through the §19.1 baseline–challenger protocol with existing recall gates.
- **Market evidence:** none direct (internal quality/scale economics only) — that is exactly why
  they are P2 despite being spec items. Trigger: live-corpus scale (P0.4) or design-partner estate
  where TANE-class/MinHash baselines degrade.
- **Dependency:** P0.4.

### P2.6 Full VISTA (lift AMD-0007 remainder) — deliberately last
- **Effort:** M–L.
- **Market evidence:** dashboards are the most commoditized surface in the landscape table; the
  research says export semantics to incumbent BI via OSI (P0.1) instead of competing with it.
  Keep VISTA minimal until a design partner demands otherwise.
- **Dependency:** P0.1.

---

## Explicitly rejected / deferred-indefinitely (with reasons from the research)

- **Competing on text-to-SQL accuracy:** commoditized (90%+ self-reported on Spider 2.0-Snow);
  benchmarks discredited (CIDR 2026). Our metric is calibrated-abstention + citation coverage.
- **Agent-memory positioning or product:** Zep/Graphiti (27.3k stars) and cognee ($7.5M, Feb 2026)
  own the category; mechanism overlap would drag us into their fight on their terms.
- **Building an EL/connector company:** Fivetran+dbt at ~$600M revenue; ride, don't fight.
- **Closed/source-available license detour:** dbt's ELv2 episode (2025-05-28, reversed within a
  year) is the cautionary tale; core and bundle format stay Apache-2.0 (already true), monetize
  enterprise controls per the Dagster playbook.
