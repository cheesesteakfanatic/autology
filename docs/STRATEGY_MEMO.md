# OntoForge — Strategy Memo (Founder / CEO Judgment)

> **READ THIS FIRST — what this document is, and is not.**
> This is a **reasoned founder/CEO strategy memo**, written after Engine Wave 1.5.
> It is **explicitly NOT fact-checked research.** The deep-research harness that
> produced `docs/MARKET_EDGE.md` and `docs/RESEARCH_ENGINE_SOTA.md` ran 111 agents
> with 3-vote adversarial verification on the strategy thread and **correctly
> refused to certify a single strategic claim** — strategy is opinion about an
> uncertain future, not a falsifiable proposition. So this memo does the honest
> thing: it builds **only on the verifiable facts already established** in
> `docs/MARKET_EDGE.md`, `docs/GAP_ASSESSMENT_v2.1.md`, the v2.1 mandate, and the
> measured state of this repo (1400 tests green, M0–M14, Engine Wave 1 shipped and
> wired). Everything *built on top of* those facts — the thesis, the sequencing,
> the pricing posture, the lock-in model — is **my judgment as the founder**, and
> every leap beyond the verified base is **marked `[ASSUMPTION]`**. Treat the
> assumptions as hypotheses to falsify with design partners, not as findings.
>
> The single most important honesty discipline in here: I separate **"this is
> true and measured"** (cited to the source doc) from **"I believe this will be
> true"** (mine, falsifiable, often wrong). If you only remember one thing: the
> moat is **granularity + calibration + exit-guarantee**, the clock is the
> **"open but closing" induction window**, and the thing that kills us is **not a
> competitor feature — it's a trust incident or running out of founder-hours.**

---

## 0. The one-paragraph version

OntoForge is the only system that, per the verified competitive scan, **starts
from messy raw sources, induces a *validated* ontology, resolves entities,
stores them bitemporally with per-*value* provenance, answers with *atom-level*
citations or *calibrated* abstention, and exports the *whole estate* as an open
bundle** — and nobody ships that loop (MARKET_EDGE §b1, the strongest negative
evidence in the corpus: three of four research briefs independently concluded it).
Citations, lineage, semantic layers, and ontologies are now **table stakes**
(R0_FINDINGS §3 pitfall; MARKET_EDGE §c) — so we **do not lead with them.** We
lead with the one axis incumbents have not closed: **granularity** (value-level,
not column-level / chunk-level), backed by **never-confidently-wrong** calibration
and a **testable $0-exit guarantee.** The wedge is regulatory: EU AI Act Article 10
is enforceable **2026-08-02** and maps one-to-one to our ledger (MARKET_EDGE §d2).
The business model is **trust as the product**: client-side anonymization where
**the customer holds the key**, compute billed **at cost**, and a contractual exit
backed by AMBER. The bet — and it is a bet — is that a solo founder driving an
AI-agent crew can reach the first design partners **before** AutoSchemaKG /
ATOM / Fabric IQ + Osmos productize induction (MARKET_EDGE §b1 caveat: "open but
closing").

---

## 1. THE THESIS — can a solo founder + AI-agent crew build the first billion-dollar "vibe-coded" company?

**My answer: the *product* can plausibly get there; the *company* is the binding
constraint, and "vibe-coded" is the wrong frame to bet the company on.** Ranked,
what has to be true.

### What is already true (verified — this is the unusual part)
- A solo founder + agent crew **already built** M0–M14, the Engine Wave 1
  confidence/typing/validation/voting/tenant stack, and a non-trivial web product
  — **1400 tests green, deterministic, keyless, zero-network** (GAP_ASSESSMENT
  header; CLAUDE.md gates). That is the existence proof that the *engineering*
  side of "solo + agents" is real at this scope. This is not aspiration; it is in
  the repo.
- The agent crew is **disciplined**: sequential commits, parallel research,
  adversarial agent-to-agent verification, a deep-research harness that *refuses
  to certify* unfalsifiable claims (RESEARCH_ENGINE_SOTA header; R0 two-layer
  labeling). A crew that tells you when it doesn't know is the precondition for
  trusting it with a company.

### What has to become true (ranked by how likely it is to break the thesis)
1. **`[ASSUMPTION — highest risk]` The category must be one where deep IP + trust
   beats headcount.** "Vibe-coded" wins only where the moat is *invention and
   correctness*, not sales motion or services delivery. OntoForge qualifies:
   the moat is the closed-core ring (IP_ARCHITECTURE) and the never-wrong gates,
   not forward-deployed engineers. If the real buying decision turns out to be
   "who has 40 FDEs to sit on-site" (Palantir's actual motion), a solo founder
   loses regardless of product. **Falsify this fast** with the first design
   partner: did they buy the *product*, or did they need a *team*?
2. **`[ASSUMPTION]` The founder must convert from builder to distributor.** The
   verified gap is brutal and honest: *"No SOC 2, no customers, no marketplace
   presence, solo-built"* (MARKET_EDGE §c). Agents can write code and draft RFP
   answers; they cannot (yet) **be trusted in the room** for a regulated
   six-figure procurement. The thesis dies in distribution far more likely than
   in engineering.
3. **`[ASSUMPTION]` Trust must be *architected*, not *attested*.** A solo company
   cannot out-attest Microsoft. It *can* out-architect them: client-side
   anonymization (customer holds the key) and a $0-exit guarantee are **structural
   trust** that doesn't require the buyer to trust *us* — see §5. This is the only
   version of "trust" a solo founder can credibly sell.
4. **`[ASSUMPTION]` The window must hold ~12–18 months.** Induction-from-messy is
   "open but closing" (MARKET_EDGE §b1). The thesis is time-boxed; it is not a
   patient compounding play until *after* the first lighthouse customers.

### The framing I'd refuse
Do **not** sell the company *as* "the first billion-dollar vibe-coded company."
That headline invites exactly the scrutiny that kills a trust product (see §7's
incident risk) and makes the founder the story instead of the never-wrong engine.
**Internally** it's the operating thesis and a recruiting/fundraising hook;
**externally** the story is "the data platform that is never confidently wrong and
that you can leave for free." The "vibe-coded" angle is a *proof of capital
efficiency* you show investors, not a value prop you show a CDO.

**Verdict:** Thesis is *live, not proven.* The product-side existence proof is
real and rare. The company-side is the open question, and it is a distribution-and-
trust question, not an engineering question. Bet accordingly: spend founder-hours
on the things agents *can't* do (the room, the trust architecture decisions, the
first three logos), not on more engine depth.

---

## 2. GTM WEDGES — regulatory fire → data debt → ontology reconstruction (sequenced, with ICP)

The three wedges are not parallel. They are a **sequence**, ordered by *compulsion
to buy NOW*. Lead with the one that has a deadline.

### Wedge 1 (LEAD) — **Regulatory fire.** Compliance-forced budget, hard dates.
- **Why first:** it has a *clock* and a *budget that already exists*. Verified
  catalysts (MARKET_EDGE §d2): **EU AI Act Article 10 enforceable 2026-08-02**
  (documented data provenance, dataset specs, version-control/traceability —
  *auto-generatable from our ledger*); **BCBS 239 attribute-level lineage** an ECB
  supervisory priority 2025–2027 *with capital-add-on teeth*; **EU Data Act**
  bans switching/egress charges 2027-01-12 (makes our $0-exit a *legal-requirement
  match*, not a nicety); **DORA** pushes sovereign/on-prem.
- **ICP:** CDO / Head of Data (or a CRO-sponsored data lead) at an **EU-exposed,
  regulated firm of ~200–2,000 employees** that has **already burned 1–2 AI POCs**
  (MARKET_EDGE §d3 — they internally quote 42% AI-initiative abandonment, ~95%
  gen-AI zero ROI). Banking/insurance first; aviation MRO/safety second *because
  our hero estate already speaks that domain* (Meridian + aviation fixtures,
  CLAUDE.md).
- **The pitch sentence:** *"Article 10 lands August 2nd. Your ledger has to prove
  data provenance, dataset specs, and as-of reconstruction. Ours generates that
  artifact from the data itself — and you can audit it down to the individual
  value."* That maps a deadline to a deliverable; it does not sell "ontology."

### Wedge 2 (FOLLOW) — **Data debt.** "Your pilots die on messy data."
- **Why second:** broader pain, no deadline. Verified buyer language already
  exists (MARKET_EDGE §d3: *"43% of CDOs name data quality/readiness the top
  obstacle"*). This is the wedge after the regulatory door is open — same buyer,
  bigger budget, sold on *outcomes* (cited answers, abstention) once they trust us.
- **ICP:** same CDO, now post-regulatory-win, with a stalled data-quality or
  "AI-readiness" program. The cold-start-induction demo (MARKET_EDGE §e5) is the
  weapon here: point at raw multi-source data with **zero query history** — the
  exact case where Snowflake Autopilot is weak (MARKET_EDGE §a) — and show
  validated ontology + ER + cited answers in hours.

### Wedge 3 (EXPAND) — **Ontology reconstruction.** The Palantir-displacement play.
- **Why last:** highest value, hardest sale, requires reference customers and a
  published AMBER restore-elsewhere test before it's credible against incumbents.
- **ICP:** an upper-mid-market or enterprise data org that is **trapped in a
  hand-authored / forward-deployed ontology** (Foundry, or a bespoke catalog) and
  is feeling the lock-in (the verified foil: Palantir exports *"not readily usable
  by any other equivalent system,"* opaque GB-month billing, a cost complaint that
  went *60 days unanswered* — MARKET_EDGE §a). The narrative: *"Foundry outcomes
  without Foundry lock-in"* — induced not hand-built, flat-priced not
  three-metered, $0-exit not trapped (MARKET_EDGE §d4).
- **`[ASSUMPTION]`** This wedge only opens *after* §1/§2 land 2–3 referenceable
  logos and the restore-elsewhere receipt is public. Selling displacement to an
  incumbent's customer with zero references is a way to lose 9 months.

**Sequencing rule:** Wedge 1 gets the **next 90 days of founder attention** because
it has the only hard external deadline in the market. Wedges 2 and 3 are warmed by
*agents* (Competitive-Monitor, GTM/Growth, Pitch/Demo — R0 §3 roster) but not
founder-led until Wedge 1 produces a logo.

---

## 3. POSITIONING — lead with GRANULARITY, because lineage/ontology are table stakes

The verified instruction is unambiguous and I am following it: **citations,
lineage, semantic layers, and ontology are table stakes in 2026** (R0 §3 pitfall;
MARKET_EDGE §c). Leading with them is a losing move — every incumbent now has a
checkbox. We lead with the **one axis the scan found unclosed: granularity**, and
we *prove* it in a demo because the *words* no longer carry it.

### The positioning ladder (what we say, in order)
1. **Granularity (the lead).** *Value-level* lineage and *atom-level* citations vs.
   everyone's *column-level / chunk-level / entity-level.* Verified: catalogs do
   *column-level pipeline lineage, not value-level fact provenance* (MARKET_EDGE
   §b4); all citation vendors cite *at document/chunk/entity granularity, none at
   per-value/atom granularity tied to bi-temporal versions* (MARKET_EDGE §b3).
   The demo move (R0 §3): **drill from an answer to the exact source value, its
   temporal version, and its transform lineage.** Nobody else can complete that
   drill-down. This is the headline.
2. **Never-confidently-wrong (the closer).** Calibrated abstention is *academically
   unsolved* (AbstentionBench, MARKET_EDGE §b2) and the only shipping competitor
   behavior is **binary refusal** (Stardog). dbt's own benchmark says the decisive
   enterprise criterion is the *failure mode* — *"plausible but incorrect... for a
   board deck or an auditor, that difference is everything."* We ship ECE ≤ 0.05,
   conformal coverage within 1.45%, **0 confidently-wrong** on the competency
   suite. The demo move: ask an unanswerable question and watch it **abstain or
   ask one clarifying question instead of bluffing.**
3. **$0-exit (the de-risker).** AMBER full-estate bundle with **100% replay
   answer+citation equality** (MARKET_EDGE §b5; PORTABILITY §3) — plain
   Parquet+Turtle+JSONL, no OntoForge dependency to read it. Verified foil: *"no
   vendor markets full-estate portability."*

### Head-to-head (positioned, not claimed superior on their turf)

| Incumbent | Their strength (verified) | Our wedge against them (judgment) |
|---|---|---|
| **Palantir Foundry / AIP** | Hand-built ontology + write-back + FDE delivery; real revenue | **Induced not hand-built; flat not three-metered (compute / storage / *ontology-indexed GB-mo*); $0-exit not "exports not readily usable."** Their cost complaint went 60 days unanswered — we make pricing a *calculator* (§4). |
| **Snowflake** Autopilot + Cortex | Auto semantic views, petabyte scale, sub-2s in-warehouse | **They start from already-modeled tables + query history and *cold-start fail* with no history.** We start from raw. **Do NOT fight on scale/latency** (MARKET_EDGE §c) — ride them as an export target (OSI). |
| **Databricks** Unity + Genie | Metric views (now in Apache Spark), governed catalog | Definitions are still **SQL/UI hand-authored**; per-DBU metering bites agent traffic. We induce + abstain + value-level provenance. Export to them. |
| **dbt (+Fivetran)** | Governed transforms, Agents Schema (open) | They **conceded a closed semantic layer "doesn't work"** and re-licensed open. So we **don't sell the format** — we sell induction + validation + provenance + answer quality (MARKET_EDGE §c). **Emit their Agents Schema** to be a friend, not a rival. |
| **Microsoft Fabric IQ + Osmos** | The *only* one gesturing at time-varying relationships; bundled "feels free" | **Generates ontology only from an existing Power BI model; no ER across messy sources.** This is the **most dangerous fast-follower** (§7) — beat them on granularity + the *no-Microsoft-stack-required + customer-holds-the-key* posture they structurally can't match while bundling. |

**The one slide:** a single answer, with the value-level citation drill-down
expanded, an abstention example beside it, and the AMBER "leave for free" stamp.
Three differentiators, one screen, zero jargon. If a prospect remembers one image,
it's the drill-down to a single source value.

---

## 4. PRICING — is "compute-at-cost + service fee + flat subscription" credible? Yes, *if messaged as anti-Palantir.*

### The model, restated
- **Compute at cost (zero margin):** the per-customer compute ledger
  (CostMeter → the §8 pass-through artifact, GAP_ASSESSMENT §8) bills raw
  compute/egress through **at cost**, fully itemized.
- **Service fee:** for the human/agent work of onboarding, connector setup,
  Plan-mode subset design (the work that produces first value).
- **Flat subscription:** estate-size-banded, **no per-question meter, no
  ontology-storage meter, no reindexing charges** (MARKET_EDGE §d5: *"your
  ontology is not a meter"*).

### Is it credible? My judgment: **yes, and it is a *weapon*, not just a price.**
The verified market context makes zero-margin compute *believable precisely
because the incumbents' billing is the #1 complaint*:
- Palantir bills **three opaque meters** including **ontology-indexed GB-month**
  with auto-reindex compute and **no public rates**; a customer cost complaint went
  **60 days unanswered** (MARKET_EDGE §a). Snowflake Cortex is **~$0.20/question +
  dual warehouse billing**; Databricks Genie metering (per-DBU) **bites agent
  traffic from the first request** (MARKET_EDGE §a). Against that, *"we pass compute
  through at cost and itemize every cent"* is not a margin sacrifice — it is the
  **trust message the whole category has primed buyers to want.**
- The flat band sits **inside existing catalog spend** (Collibra ~$170–197K/yr,
  Alation ~$198K base — MARKET_EDGE §d5, vendor-adversarial ranges) while replacing
  catalog + quality + semantic-layer tooling. So $100–250K ACV is *not a new
  budget line*; it's a *consolidation*.

### How to message it (this is the part founders get wrong)
- **Lead with the calculator, not the number.** R0 §3 names the **pricing/compute-
  ledger calculator** as one of the four earliest GTM artifacts — *"turns CostMeter
  into a transparent quote, counters Palantir's opaque-billing complaint."* The
  message is *"here is exactly what you'll pay and why, before you sign"* — the
  literal inverse of the unanswered-complaint anecdote.
- **`[ASSUMPTION]` Zero-margin-compute is a *trust signal*, and the margin lives in
  the subscription + service fee — be explicit internally that this only works if
  the CostMeter captures *real* egress + compute (and later LLM tokens), not
  estimates** (R0 §3 final pitfall: *"cost-dashboard credibility cuts both ways...
  before the pricing calculator is shown to a prospect"*). A calculator that
  under-counts and then surprises a customer is worse than Palantir's opacity,
  because it breaks the one thing we're selling: trust. **Do not show the
  calculator to a prospect until the CostMeter is provably complete.**
- **Risk to flag (`[ASSUMPTION]`):** "compute at cost" invites the question *"what
  happens at petabyte scale when compute *is* the cost?"* — see §7. Message it as
  *"transparent and at-cost"* not *"cheap,"* and let Plan-mode (pull only the
  stratified subset, R0 §3) keep the at-cost number small and honest.

**Verdict:** Credible and differentiating, *conditional on a complete CostMeter*.
The model is itself a positioning move against the verified #1 incumbent objection.
Message it as **transparency**, gate it on **measurement honesty.**

---

## 5. TRUST AS MOAT — client-side anonymization (customer holds the key) as compliance + marketing wedge

This is, in my judgment, the **most defensible and most under-appreciated** asset,
and the only kind of "trust" a solo company can credibly sell against Microsoft.

### Why it's structural, not attestational
The verified gap is that a solo company **fails the attestation gate today**:
*"No SOC 2, no customers... SOC 2 Type II is a pass/fail procurement gate we
currently fail"* (MARKET_EDGE §c). You cannot out-*attest* an incumbent. You can
out-*architect* one: if the customer **holds the traceable-ID key** and the cloud
computes only on **anonymized input** (the §7 toolkit, GAP_ASSESSMENT §7; R0 §3
W5 — *"NO incumbent ships it"*), then the trust question changes from *"do I trust
OntoForge's SOC 2?"* to *"OntoForge literally cannot see my identifiable data."*
That is a **mathematical** answer to a procurement question, and it is exactly the
posture Microsoft **structurally cannot match** while the value of Fabric is *"it's
already in your OneLake, bundled, feels free"* — bundling and customer-held-key
isolation are in tension.

### The compliance wedge (why it accelerates Wedge 1)
Client-side anonymization + customer-held key is the cleanest possible answer to
DORA sovereignty, EU AI Act data-minimization, and the GDPR posture EU-exposed
buyers already need (MARKET_EDGE §d2). It lets a regulated buyer say *"the vendor
never held our PII"* — which collapses a large part of the data-processing-agreement
and DPIA burden. **`[ASSUMPTION]`** I believe this single property shortens the
security-review cycle more than a SOC 2 would, for the *first* design partners —
because it removes the data-exposure question entirely rather than auditing it.

### Sequencing (don't build it too early — verified)
R0 §3 is explicit and I agree: anonymization is **W5, correctly sequenced AFTER
connectors / auth / observability** — *"no value until real data flows through a
multi-tenant product."* The trap is building the headline trust wedge before
there's a product to flow real data through. So: **architect the boundary now**
(the open-shell anonymizer slot already exists in IP_ARCHITECTURE), **build it
after** connectors + auth land. But **start *messaging* it now** — it's a story
that opens doors before the code exists, the way a roadmap commitment does in
regulated sales.

**Verdict:** This is the moat that a solo founder *uniquely can* build, because
it's an architecture decision, not a headcount or a certification. Make
"customer-holds-the-key" the company's one-line brand. It is the trust answer that
scales to zero employees.

---

## 6. THE LOCK-IN → FREE-EXPORT MODEL + closed-core moat

The model — "3–5 year practical lock-in via depth-of-value, *contractually*
backed by free export" — sounds paradoxical. My judgment: it's **the right
paradox**, and it's *more* defensible than ordinary lock-in.

### Why "free to leave" is a stronger moat than "can't leave"
The verified market is moving *against* lock-in: **EU Data Act bans switching/
egress charges 2027-01-12** (MARKET_EDGE §b5/§d2), and the canonical foil is
Palantir's ontology as *"the deepest and dangerous moat"* with *"no formal
migration pathways."* Against that backdrop, **a $0-exit guarantee backed by a
testable AMBER restore-elsewhere receipt is a SALES weapon, not a giveaway** — it
removes the single biggest objection a burned, regulation-aware CDO has (MARKET_EDGE
§d4). The lock-in is then **earned, not imposed**: customers stay because the
*compounding* value (per-tenant priors, the cached-DE-work flywheel, the
write-back-as-ontology-object loop — GAP_ASSESSMENT §1.5/§5/§4) makes leaving
*pointless*, not *impossible*. That is Dagster/open-core dynamics, and the
verified note is that dbt's ELv2 detour *"cost trust and was reversed within a
year"* (MARKET_EDGE §d5) — i.e. the market punishes the *imposed* version.

### Where the actual moat lives (verified architecture)
The closed-core ring is real and CI-guarded (IP_ARCHITECTURE; `test_ip_boundary.py`):
the moat is **not** the open format (OSI/Agents Schema are commodity — MARKET_EDGE
§c) and **not** the connectors (commodity cost-of-entry — R0 §3). It is:
- **Induction + validation + typed relationships** (the false-positive killer, the
  synthesize-and-execute gate) — the part the research scan found *unshipped*.
- **The compounding loops:** per-tenant isolated priors, semantic search over
  cached DE work, and the Ask-flywheel write-back. **`[ASSUMPTION]`** these are the
  *real* 3–5yr lock-in — every accepted join and every answered ask makes the next
  one faster *for that tenant*, and that history is exactly what a competitor's
  free import of your AMBER bundle **cannot reproduce** (the bundle carries the
  *world*, not the *learned engineering judgment*). So: **export the world for
  free, keep the flywheel.** That's the closed-core moat surviving an open exit.

### The discipline this requires (verified pitfall)
Per-tenant learning **must** stay isolated under the same tenant boundary as RLS
(R0 §3 pitfall: *"never cross-tenant, or you leak one customer's naming/join
patterns into another's — fatal trust breach"*). The flywheel moat and the trust
moat are the **same boundary**; breaking it breaks both. This is non-negotiable
and is a load-bearing test, not a preference.

**Verdict:** Adopt it. Make "free to leave, too valuable to want to" the explicit
model. It aligns with the regulatory direction, neutralizes the #1 objection, and
the *real* moat (induction + the isolated compounding loops) is exactly the part a
free export can't carry away.

---

## 7. THE REALISTIC RISKS THAT KILL THIS (ranked) + mitigations

Ranked by my estimate of *probability × lethality*. The first two are the ones I'd
actually lose sleep over.

### Risk 1 (HIGHEST) — **Trust / security incident.** *Lethality: company-ending.*
A single PII leak, a cross-tenant prior bleed, or a confidently-wrong answer that
reaches a regulator's desk **ends a trust company instantly** — and trust is the
*entire* value prop (§5). It is more lethal for us than for an incumbent precisely
*because* we sell "never wrong / never holds your data."
- **Mitigations:** (a) the never-confidently-wrong gates are **load-bearing tests,
  not features** (CLAUDE.md) — never weaken them, ever; (b) **tenant-isolation is
  the same boundary as the flywheel moat** (§6) — audit `tenant/priors.py` under
  RLS before any multi-tenant customer (R0 §3 pitfall); (c) **customer-holds-the-
  key anonymization** (§5) means a breach exposes anonymized data, not PII —
  architecturally cap the blast radius; (d) **deliberately do NOT brand as the
  "vibe-coded company"** (§1) — don't invite the security press to make us the
  story. *This risk is why every other speed tradeoff stops at the gate line.*

### Risk 2 — **Incumbent fast-follow, esp. Microsoft Fabric.** *Lethality: high.*
Verified: Fabric IQ is the *only* incumbent gesturing at time-varying relationships,
it bundles into capacity pricing that *"feels free,"* and Microsoft just acquired
Osmos for *"autonomous data engineering... raw → AI-ready in OneLake"* (MARKET_EDGE
§a). The induction window is *"open but closing"* with AutoSchemaKG / ATOM /
GraphRAG / Fabric IQ / Osmos converging (MARKET_EDGE §b1 caveat).
- **Mitigations:** (a) **speed to a real customer beats engine depth** (R0 §3
  pitfall — explicit) — pull connectors + auth *forward* into W4 (R0 §3
  sequencing) so a customer's data can actually flow *this year*; (b) compete where
  Microsoft *can't*: **no-Microsoft-stack-required + customer-holds-the-key +
  $0-exit** — all three are in tension with Fabric's bundle-and-lock model; (c)
  win on **granularity + calibration** (§3), which require the closed-core
  induction/validation stack a bundle-feature won't replicate quickly; (d) **`[ASSUMPTION]`**
  accept that we will *lose* the "good-enough, already-in-your-stack" buyer to
  Fabric — that buyer was never our ICP. Our ICP is the *regulated* buyer who
  *cannot* accept "good enough" or "we hold your data."

### Risk 3 — **Compute economics at petabyte scale.** *Lethality: medium-high, scale-gated.*
Verified honesty: *"v0 is Python at fixture scale"*; *"do not pick a performance
fight yet"* (MARKET_EDGE §c). IND discovery is **NP-hard / W[3]-complete** —
*"at billion-row scale you must approximate, sample, and parallelize"*
(RESEARCH_ENGINE_SOTA §6). "Compute at cost" (§4) means *we* eat scale-cost
problems as *transparency* problems.
- **Mitigations:** (a) **Plan mode** — the governed stratified-subset puller (R0 §3
  P0) — means we induce on a *representative subset*, not the petabyte, by design;
  (b) the profiled engine hot-path plan (R0 §2: P1 TANE 20–100×, P2 IND prefilter
  5–30×, P3 MinHash 10–50×) with **byte-identical determinism** keeps at-cost
  numbers honest as scale grows; (c) **ride the warehouses for scale** (MARKET_EDGE
  §c) — push relational-shaped metrics to DuckDB (R0 §2 P6), don't rebuild
  Snowflake; (d) message pricing as *"at-cost + Plan-mode keeps it small,"* never
  *"unlimited."*

### Risk 4 — **Solo-founder bandwidth.** *Lethality: medium, but it's the silent killer.*
The agent crew scales *engineering*; it does not scale *the room*, *the security
review*, *the design-partner relationship*, or *the founder's judgment under a
regulator's questions* (§1). The verified backlog is large (connectors, auth,
observability, flywheel, anonymization — GAP_ASSESSMENT). One person cannot do
all of it *and* sell.
- **Mitigations:** (a) **ruthless sequencing** — Wedge 1 only, 90 days (§8); (b)
  **agents own upkeep, founder owns the room** — Competitive-Monitor refreshes
  MARKET_EDGE, GTM/Pitch/Pricing agents keep artifacts current (R0 §3 roster), so
  founder-hours go *only* to the things agents can't do; (c) build the **four GTM
  artifacts first, automate upkeep second** (R0 §3 — *"a full agent swarm is
  premature until design partners exist"*); (d) **`[ASSUMPTION]`** the first hire
  (or fractional) should be a **regulated-sales/solutions person**, not an
  engineer — engineering is the part that's already working.

### Risk 5 — **Window closes before first logo.** *Lethality: high, fully time-correlated with Risk 2.*
Already covered by Risk 2's mitigations; called out separately because it's the
*timing* version of the same threat and is the reason §8 is aggressive on the
90-day plan. **The clock is the enemy, not any single competitor.**

---

## 8. CONCRETE NEXT MOVES — 90-day and 12-month, for a solo founder + AI agents

The organizing principle: **founder-hours go ONLY to what agents can't do (the
room, trust-architecture calls, the first three logos); agents do everything else.**
Sequencing follows R0 §3's explicit correction — *connectors → Plan mode → auth →
observability → flywheel → lazy-recompute → anonymization* — with connectors+auth
pulled forward.

### The next 90 days (ship to first design-partner conversation)
**Agents build (in R0-sequenced order):**
1. **Source connectors** (PG/MySQL via connectorx/SQLAlchemy, S3 via DuckDB httpfs,
   chunked CSV) — *the gating gap; nothing matters until a customer's data flows*
   (R0 §3 P0). Keep in the open-shell ring, cassette/fixture-tested, **zero-network
   in CI** (R0 §3 pitfall + CLAUDE.md).
2. **Plan mode** (governed stratified-subset puller around candidate keys/cardinality/
   distribution edges — R0 §3 P0, GAP_ASSESSMENT §2). This is the *first-value* and
   *don't-ship-us-your-whole-DB* answer simultaneously.
3. **Auth + multi-tenancy + RBAC** (Postgres RLS + FORCE RLS; reuse the BudgetHunter
   Supabase Auth+RLS pattern — R0 §3 P0). **Audit `tenant/priors.py` isolation under
   the same boundary** (Risk 1).
4. **The four GTM artifacts** (R0 §3 P0, build FIRST): (i) one-page pitch (the
   structural read — *"nobody starts from messy raw sources, resolves, validates,
   exports the whole estate"*); (ii) landing page with a **pre-signup interactive
   demo**; (iii) **demo script** showing the *granularity* (value-level citation
   drill-down → calibrated abstention → AMBER exit — §3); (iv) the **pricing/compute-
   ledger calculator** (§4) — *gated on a complete CostMeter* (Risk in §4).

**Founder does (cannot delegate):**
- Pick **one** Article-10-exposed regulated mid-market target list and start
  **5–10 design-partner conversations** before 2026-08-02 — the deadline *is* the
  cold-open (§2 Wedge 1).
- Make the **trust-architecture decisions**: confirm the customer-holds-the-key
  boundary in the design (§5), confirm tenant isolation under RLS (Risk 1).
- **Start the SOC 2 Type II observation window now** (MARKET_EDGE §e6 — *"Type II
  needs months of evidence"*; it's a pass/fail gate we currently fail). This is
  paperwork that has to *start* even though it finishes in the 12-month window.

### The 12-month plan (to 2–3 referenceable logos)
- **Observability suite** (atom-level lineage UI, audit log, run history, cost
  dashboard — R0 §3 P1): *surface the substrate nobody else has*; the differentiator
  is value-level lineage. Table stakes we can win on granularity.
- **Ask-flywheel write-back** + the **prompt router/living library/observation
  layer** (R0 §3 P1; GAP_ASSESSMENT §3/§4) — close the compounding loop that is the
  real moat (§6). Surface `discovery/` on a `/api` route (GAP_ASSESSMENT §5).
- **Three public proof receipts** (MARKET_EDGE §e), in order of deal-impact:
  (1) the **accuracy-with-abstention benchmark** (risk-coverage curves, ECE,
  citation verifiability — *define the leaderboard nobody publishes*); (2) **OSI
  v1.0 + Agents Schema emission** demonstrated in a POC (turns rivals into export
  targets); (3) the **published AMBER restore-elsewhere test** (the $0-exit receipt —
  §6).
- **EU AI Act Article 10 artifact demo** auto-generated from the ledger
  (MARKET_EDGE §e4) — the Wedge-1 closer.
- **Lazy usage/criticality recompute** (R0 §3 P2; GAP_ASSESSMENT §6) — only matters
  at scale, feeds the cost dashboard.
- **Client-side anonymization toolkit** (R0 §3 W5, GAP_ASSESSMENT §7) — built **last**
  in this window, **after** real data flows through a multi-tenant product; messaged
  **first** (§5).
- **Founder:** convert 1–2 design partners to **paying references**; make the **first
  non-engineering hire** (regulated sales/solutions — Risk 4); finish SOC 2 Type II.

### The single decision that matters most
If I had to compress this whole memo to one operating instruction: **stop adding
engine depth and spend the next 90 days making a real customer's data flow through
the never-wrong engine — before the window closes — without ever weakening a gate
or letting one tenant's data touch another's.** Everything else is downstream of
that.

---

## Appendix — assumption register (the falsifiable bets)

Every claim in this memo marked `[ASSUMPTION]` is a hypothesis to test, not a
finding. The load-bearing ones, and how to falsify each:

| # | Assumption | Falsify by |
|---|---|---|
| A1 | The buyer wants a *product*, not a *team* (§1) | First design partner: did they buy the product or need FDEs? |
| A2 | Customer-holds-the-key shortens security review more than SOC 2 (§5) | Run a real security review with/without the anonymization story |
| A3 | The induction window holds ~12–18 months (§1, §7) | Track Fabric IQ + Osmos GA dates; Competitive-Monitor agent |
| A4 | Zero-margin-compute reads as *trust*, not *weakness* (§4) | A/B the calculator framing in the first 5 pitches |
| A5 | The isolated compounding loops are the real 3–5yr lock-in (§6) | Measure per-tenant flywheel speedup over the first year |
| A6 | First hire should be sales/solutions, not engineering (§7) | Honest audit at 6 months: where did deals stall? |
| A7 | We lose the "good-enough/bundled" buyer to Fabric and that's fine (§7) | Win-loss analysis: are losses our ICP or not? |

**Closing honesty:** the verified facts say the *product gap is real and the window
is open*. Everything about whether the *company* wins is judgment, and judgment in a
moving market is wrong often enough that the only safe move is to **falsify the
assumptions above with real customers, fast.** This memo is a starting hypothesis,
not a plan of record.
