# OntoForge — the data platform that is never confidently wrong, and that you can leave for free

> **One-pager for design-partner and investor conversations.** Built on the verified
> base in `docs/MARKET_EDGE.md` and the founder judgment in `docs/STRATEGY_MEMO.md`.
> Claims tagged: **(measured)** = in this repo, fixture scale, deterministic, zero-network;
> **[assumption]** = founder bet to falsify with design partners, not a finding.

---

## The problem — 18 months of data engineering gates every AI initiative

Every regulated enterprise has the same stall. Before a single AI answer is
trustworthy, someone has to model the data: hand-author an ontology, wire the joins,
clean the values, prove where each number came from. That work runs **12–18 months**
and never finishes — and the market has already paid the bill. **42% of companies
abandoned most of their AI initiatives in 2025; ~95% of gen-AI pilots returned zero
ROI; 43% of CDOs name data quality and readiness as their single biggest obstacle**
(MARKET_EDGE §d3). The pilots don't die on the model. They die on the data underneath it
— messy sources, provenance gaps, and answers nobody can audit or defend to a regulator.

The 2026 "auto-generation" wave does not fix this. Snowflake Autopilot, Databricks
Genie, Microsoft Fabric IQ, the catalogs — **every one of them starts from
already-modeled data** (warehouse tables + query history, a Power BI model, a curated
glossary) and emits flat, platform-bound metric definitions. Point them at raw,
multi-source, messy data with no query history and they cold-start fail (MARKET_EDGE §a).
**Nobody starts from the mess. Nobody resolves entities, validates the model against the
real data, and exports the whole estate as an open bundle.** That loop is unshipped —
three of four independent research briefs concluded it (MARKET_EDGE §b1).

---

## The mandate — autonomous data engineering, with the trust artifacts nobody else stores

OntoForge points at messy CSV/Parquet sources and runs the whole loop autonomously:
**induce a validated ontology → resolve entities → materialize them in a bitemporal
store with per-value provenance → answer in natural language with atom-level citations
or a calibrated abstention → export the entire estate as a portable open bundle.**
No hand-authored semantic model. No forward-deployed engineers. The closed-core engine
is real and CI-guarded; the existence proof is in the repo — **1400+ tests green,
deterministic, keyless, zero-network** (CLAUDE.md gates).

The regulatory clock makes this urgent, not optional. **EU AI Act Article 10 is
enforceable 2026-08-02** and maps one-to-one to our ledger: documented data provenance,
dataset specifications, version-control and traceability — auto-generatable from the
data itself. **BCBS 239 attribute-level lineage** is an ECB supervisory priority through
2027 with capital-add-on teeth. **The EU Data Act bans switching/egress charges
2027-01-12** — which turns our free-exit guarantee from a nicety into a legal-requirement
match (MARKET_EDGE §d2).

---

## The wedge — regulated mid-market, entered through compliance-forced budget

We sell to the **CDO / Head of Data at an EU-exposed, regulated firm of ~200–2,000
employees that has already burned one or two AI POCs** (MARKET_EDGE §d). This segment is
structurally unserved: Palantir's floor is $1M+ forward-deployed deals, Fabric demands a
Microsoft-stack commitment, and the catalogs run six-figure ACVs without storing the data
at all. The opening line writes itself against a deadline:

> *"Article 10 lands August 2nd. Your ledger has to prove data provenance, dataset
> specs, and as-of reconstruction. Ours generates that artifact from the data itself —
> and you can audit it down to the individual value."*

That maps a hard date to a deliverable. It does not sell "ontology."

---

## The three differentiators — and why they are NOT table stakes

Citations, lineage, semantic layers, and ontologies are **table stakes in 2026** — every
incumbent now has the checkbox (MARKET_EDGE §c). So we **do not lead with them.** We lead
with the three axes the competitive scan found genuinely unclosed, ranked by the strength
of the negative evidence that nobody else ships them.

### 1. GRANULARITY — value-level lineage, not column-level or chunk-level
Every citation vendor on the market (Graphwise, Stardog, WRITER, Fluree, cognee) cites at
**document / chunk / entity** granularity. Every catalog (Collibra, Alation, Atlan) does
**column-level pipeline lineage, not value-level fact provenance** (MARKET_EDGE §b3, §b4).
OntoForge resolves **every cell in an answer through a provenance semiring to the exact
content-addressed source atom, its bitemporal version, and its transform lineage** —
inspectable, in one drill-down. *(measured: 100% citation coverage; four-timestamp value
cells; TEMPER snapshot-queryability 100% over 300 random op sequences.)* This is the
headline, because the *word* "citations" no longer carries it — only the drill-down does.

### 2. CALIBRATED ABSTENTION — never confidently wrong
Abstention is **academically unsolved** (AbstentionBench: 20 frontier LLMs, scaling
doesn't fix it), and the only shipping competitor behavior is Stardog's **binary refusal**
— not calibration (MARKET_EDGE §b2). dbt's own benchmark names the decisive enterprise
criterion as the *failure mode*: *"a plausible but incorrect answer vs. an error message…
for a board deck or an auditor, that difference is everything."* OntoForge ships
**ECE ≤ 0.05, conformal coverage within 1.45% of nominal, and zero confidently-wrong
answers on the competency suite** *(measured, 5 seeds)*. Ask it something the data can't
answer and it **abstains or asks one clarifying question — it never bluffs.**

### 3. AMBER EXIT + customer-holds-the-key trust — leave with everything, prove it
Paper-portability is becoming universal (OSI v1.0). **Verified full-estate portability is
not** — *"no vendor markets full-estate portability (data + ontology + provenance +
transforms as an open bundle)"* (MARKET_EDGE §b5). AMBER produces a bundle in plain
**Parquet + Turtle + JSONL** with **100% replay answer+citation equality** — a *testable*
$0-exit guarantee, with no OntoForge dependency to read it. Paired with **client-side
anonymization where the customer holds the traceable-ID key**, the trust question changes
from *"do I trust their SOC 2?"* to *"they literally cannot see my identifiable data"* —
a mathematical answer to a procurement question, and a posture Microsoft structurally
cannot match while Fabric's value is *"it's already bundled in your OneLake"* (STRATEGY §5).

---

## The economics — compute at cost, because the incumbents' billing is the #1 complaint

Our pricing is itself a positioning move. The market's loudest grievance is opaque
metering: Palantir bills **three meters including ontology-indexed GB-month with no public
rates** — a customer cost complaint went **60 days unanswered**; Snowflake Cortex runs
**~$0.20/question plus dual warehouse billing**; Databricks Genie's per-DBU meter bites
agent traffic from the first request (MARKET_EDGE §a). Against that, OntoForge is:

- **Compute at cost (zero margin), fully itemized** — the per-customer compute ledger
  passes raw compute/egress through at cost. *"Here is exactly what you'll pay and why,
  before you sign"* is the literal inverse of the unanswered complaint. **[assumption:
  zero-margin compute reads as trust, not weakness — A/B it in the first 5 pitches.]**
- **Flat, estate-size-banded subscription** — **no per-question meter, no
  ontology-storage meter, no reindexing charges.** *"Your ontology is not a meter."*
- **A service fee** for onboarding and Plan-mode subset design — the work that produces
  first value.

The flat band sits **inside existing catalog spend** (Collibra ~$170–197K/yr, Alation
~$198K base) while replacing catalog + quality + semantic-layer tooling. A **$100–250K
ACV is a consolidation, not a new budget line** (MARKET_EDGE §d5). The margin lives in the
subscription and service fee; the compute pass-through is the trust signal — **gated on a
CostMeter that measures real egress and compute, never estimates** (STRATEGY §4).

---

## The honest read — the moat, the clock, and what kills us

The **moat is granularity + calibration + exit-guarantee**, plus the part a free export
can't carry away: the **isolated per-tenant compounding loops** — every accepted join and
answered question makes the next one faster *for that tenant*. We **export the world for
free and keep the flywheel** (STRATEGY §6). The **clock** is the induction window, *"open
but closing"* as AutoSchemaKG / ATOM / Fabric IQ + Osmos converge — held **~12–18 months
[assumption]**. What kills us is **not a competitor feature — it's a trust/security
incident or running out of founder-hours** (STRATEGY §7). Every speed tradeoff stops at
the gate line; the never-confidently-wrong guards are load-bearing tests, not features.

---

## The ask

We are taking on **3–5 design partners** — EU-exposed, regulated, ~200–2,000 employees,
one or two burned AI POCs, facing the Article 10 deadline — to run a real corpus through
the never-wrong engine and prove the three differentiators on their own data. **What we
need:** a representative subset of your messy sources, a security review (we want the
customer-holds-the-key story tested against it), and a 6-week pilot to first cited answer.
**What you get:** a validated ontology induced from your mess, value-level audit down to
the cell, a calibrated answer engine that abstains instead of bluffing, an Article 10
artifact generated from your ledger, and a signed $0-exit clause backed by a published
AMBER restore-elsewhere test. **Leave with everything, the day you want to.**
