# OntoForge — Competitive Battlecard

> **For founder-led sales conversations and win-loss prep.** Grounded in the verified
> competitive scan (`docs/MARKET_EDGE.md`, June 2026) and the positioning calls in
> `docs/STRATEGY_MEMO.md`. Tags: **(verified)** = cited to a primary/research source in
> MARKET_EDGE; **[assumption]** = founder judgment to falsify, not a finding. **[UNVERIFIED]**
> carries through where the source brief could not confirm against a primary.
>
> **The discipline that wins:** never fight on the incumbent's turf (scale, latency, raw
> text-to-SQL accuracy, ingestion breadth, BI dashboards — we lose those, MARKET_EDGE §c).
> Win only on the three unclosed axes: **granularity** (value-level lineage), **calibrated
> autonomy** (never confidently wrong), and **exit-as-a-feature** (full-estate $0-exit +
> customer-holds-the-key). And always be ready with the honest "when NOT us" — it builds the
> trust that closes the regulated buyer.

---

## The 30-second frame (say this in any competitive deal)

> *"Every platform you're comparing us to starts from data that's **already modeled** and
> gives you **column-level or chunk-level** audit. We start from the **raw mess**, induce and
> validate the model autonomously, and audit down to the **individual value**. We're
> **never confidently wrong** — we abstain instead of bluffing — and you can **export the
> entire estate for free**, with a published test that proves it replays elsewhere. We don't
> beat them on scale or latency. We beat them on **trust you can inspect.**"*

---

## vs. Palantir Foundry / AIP — *"Foundry outcomes without Foundry lock-in"*

**Where they win (verified):** Hand-built ontology + operational write-back + forward-deployed
engineer (FDE) delivery; real, large revenue (Q1 FY2026 $1.63B, 206 deals ≥$1M). When the
buyer genuinely needs **40 FDEs sitting on-site** to deliver an operational program, they win
on motion, not product.

**Where we win (judgment, grounded in verified facts):**
- **Induced, not hand-authored.** Their ontology is *"fully hand-authored by forward-deployed
  engineers"* (verified). Ours is induced and validated from messy sources autonomously — no
  FDE army, no 12–18-month modeling project.
- **Granularity.** Foundry audits at the object/column level; we drill to the **per-value,
  per-temporal-version source atom** (verified gap: MARKET_EDGE §b3/§b4).
- **Pricing as a weapon.** They bill **three opaque meters including ontology-indexed GB-month,
  no public rates** — a customer cost complaint went **60 days unanswered** (verified). We pass
  **compute through at cost, itemized**, on a flat estate-band subscription: *"your ontology is
  not a meter."*
- **Exit.** Their exports are *"not readily usable by any other equivalent system"* (verified,
  adversarial source, directionally corroborated); ontology is *"the deepest and dangerous
  moat,"* no formal migration pathways. Our **AMBER $0-exit with a published restore-elsewhere
  test** is the direct antidote — and the EU Data Act (egress bans 2027-01-12) is on our side.

**When NOT us (honest):** If the buyer needs **operational write-back at scale** (actions
flowing back into production systems), **petabyte-scale operations today**, or wants a vendor
who will **staff the program with bodies**, Palantir is the safer call right now. We are the
*induce-validate-audit-and-leave-freely* system, not (yet) an operational action platform.
**[assumption]** the buyer who picks us bought the *product*, not a *team* — falsify with the
first design partner (STRATEGY A1).

---

## vs. Snowflake (Semantic View Autopilot + Cortex Analyst)

**Where they win (verified):** Auto-generates/maintains semantic views from query history +
table metadata; Cortex Analyst NL-to-SQL claims ~85–90% on *"well-defined"* models;
**petabyte scale, sub-2s in-warehouse latency.** If the data already lives in Snowflake and is
already modeled, they're fast and co-located. **Do not fight them on scale or latency.**

**Where we win (verified gap):**
- **Cold start.** Autopilot *"cold-start fails without query history"* and works only from
  **already-modeled warehouse tables**, with a ~10-table / 50–100-column ceiling per semantic
  view. We start from **raw, multi-source, never-queried data** — the exact case they're weak
  in (MARKET_EDGE §a).
- **Granularity + abstention.** Cortex doesn't cite the source values behind an answer at all
  (verified); it has no calibrated abstention. We cite at atom granularity and abstain instead
  of returning a plausible-wrong number (MARKET_EDGE §b2/§b3).
- **Cost transparency.** Cortex is **~$0.20/question + dual warehouse billing** (verified). Our
  flat band + at-cost compute removes the per-question meter that bites agent traffic.

**When NOT us:** If the estate is **already clean and modeled inside Snowflake** and the need is
high-volume, low-latency NL analytics at scale — that's their home turf. **Position Snowflake
as an export target, not a rival:** we emit OSI v1.0 so our estate rides into their warehouse
(MARKET_EDGE §c). *"We make your messy data Snowflake-ready; we don't replace Snowflake."*

---

## vs. Databricks (Unity Catalog Business Semantics + Genie)

**Where they win (verified):** Metric views as governed catalog assets (core open-sourced into
Apache Spark, SPARK-54119); Genie grounds NL on those metric views; mature governed catalog,
lakehouse scale.

**Where we win (verified):**
- **Still hand-authored.** Definitions are *"SQL/UI-authored"* — *"AI-assisted human
  authoring,"* not autonomous induction. We induce + validate + resolve entities across messy
  sources, which they don't do (MARKET_EDGE §a).
- **Metering bites agents.** Genie billing (enforced 2026-07-06) charges **service principals —
  i.e. agent traffic — from the first request**, per-DBU (verified). At-cost flat pricing is the
  counter.
- **Granularity + calibration.** Same axis as everyone: no per-value provenance, no calibrated
  abstention. We have both (measured: 100% citation coverage, ECE ≤ 0.05).

**When NOT us:** If they're a **Databricks-committed lakehouse shop** that just needs governed
metric definitions over already-curated tables, Genie is the low-friction path. **Don't sell
against the format** — metric views are now in Apache Spark; **emit to them, ride them for
scale** (MARKET_EDGE §c).

---

## vs. dbt Labs (+ Fivetran, merged 2026-06-01)

**Where they win (verified):** Governed transforms; MetricFlow re-licensed Apache under OSI;
the open **"Agents Schema"** standard (semantics/metrics/lineage as SQL tables for agents);
~$600M combined revenue, 100k+ teams; Fivetran's 600+ connectors. Ingestion + transform breadth
is theirs — **do not compete on connectors** (MARKET_EDGE §c).

**Where we win (judgment):**
- They **conceded a closed semantic layer "doesn't work"** and re-licensed open (verified). So
  the format is not the battleground. We sell **induction + validation + per-value provenance +
  answer quality** — the things a SQL-authored semantic model can't give you.
- **Hand-authored dbt semantic models** vs. our autonomous induction; no calibrated abstention,
  no atom-level provenance on their side.

**When NOT us:** If the team is **comfortable hand-authoring dbt models** and the priority is
pipeline + transform governance, dbt is excellent and we're not a replacement for it.
**Be a friend, not a rival: emit their Agents Schema and OSI** so we interoperate. We're the
layer that builds and validates the model they'd otherwise hand-write — and ingest from the
warehouse schemas Fivetran already staged.

---

## vs. Microsoft Fabric IQ + Osmos — *the most dangerous fast-follower*

**Where they win (verified):** Fabric IQ is the **only** incumbent even gesturing at
time-varying relationships (`effectiveAt`, `confidence`, preview); GQL graph engine on OneLake;
MCP endpoints; the Osmos acquisition (2026-01-05) adds *"autonomous data engineering, raw →
AI-ready in OneLake."* Bundled into Fabric capacity pricing, so it **"feels free"** to any
E5/Fabric shop. This is **Risk 2 in the strategy memo** — speed and bundling are the threat.

**Where we win (verified + judgment):**
- **They generate ontology only from an existing Power BI semantic model** — human modeling
  upstream; entity-to-data binding is manual; **no ER across messy sources** (verified). We
  start from the raw mess and resolve entities — the part they don't do.
- **Granularity + calibration** — same unclosed axes; they have neither per-value provenance nor
  calibrated abstention shipping.
- **The structural counter they can't match while bundling:** **no-Microsoft-stack-required +
  customer-holds-the-key anonymization + $0-exit.** Fabric's whole value is *"it's already in
  your OneLake."* Bundle-and-lock and customer-held-key isolation are **in tension** — they
  cannot credibly offer "we literally cannot see your data" while their pitch is "your data is
  already in our cloud" (STRATEGY §5).

**When NOT us (honest, and important):** If the buyer is a **committed Microsoft-stack shop** for
whom Fabric is "good enough and already paid for," **we will lose that deal — and that buyer was
never our ICP.** **[assumption A7]** Our ICP is the **regulated buyer who cannot accept "good
enough" or "we hold your data"** — falsify by win-loss analysis: were the losses our ICP or not?
Track Fabric IQ + Osmos GA dates closely; the induction window is *"open but closing"* (A3).

---

## The cross-cutting "when NOT OntoForge" (say it before they ask — it builds trust)

Be the vendor who names their own limits. For the regulated, burned-once CDO, this candor is
itself a differentiator against the over-promising incumbents.

- **You need petabyte scale / sub-2s latency today.** v0 is Python at fixture scale; we induce
  on a representative stratified subset (Plan mode) and ride the warehouses for scale. Not a
  scale-fight vendor yet (MARKET_EDGE §c; STRATEGY Risk 3).
- **You need 600+ source connectors or operational write-back at scale.** That's Fivetran /
  Palantir territory. We ingest from staged schemas and focus on induce-validate-audit.
- **You need a mature BI / dashboarding surface.** VISTA is deliberately minimal; export to
  Looker / Power BI / Sigma via OSI rather than expecting a BI tool.
- **SOC 2 Type II is a hard, today, pass/fail gate with no exceptions.** We currently fail it —
  the **observation window is open and started**, and our structural answer is
  **customer-holds-the-key** (a breach exposes anonymized data, not PII). **[assumption A2]**
  this shortens the security review *more* than a SOC 2 would for the first partners — falsify
  by running a real review with and without the anonymization story.
- **Your data is already clean and modeled.** Our wedge is the *mess*. If there's nothing to
  induce, our headline differentiator doesn't apply — Snowflake/Databricks/Fabric serve you
  better.

**The honest close:** *"If you're a clean Microsoft-stack shop that needs scale today, buy
Fabric. If your AI program is dying on messy data, unprovable answers, and lock-in fear — and
you have a regulator's deadline — that's exactly the gap nobody else fills, and it's the only
thing we do."*
