# OntoForge — Live Demo Runbook (5–7 minutes)

> **For founder-led design-partner and investor demos.** This maps to the **real app**:
> `ontoforge demo meridian` builds the world, `ontoforge serve` opens the three-mode shell
> (**Ask · Build · Studio**). Every question below is a real gold question from
> `fixtures/meridian/gold/questions.yaml`; every behavior is test-enforced, so it does the
> same thing every run (deterministic, keyless, zero-network — see CLAUDE.md gates).
>
> **The one rule:** lead with **granularity, calibration, and exit** — never with the words
> "citations / lineage / ontology." Those are table stakes (MARKET_EDGE §c). The demo exists
> because the *words* no longer differentiate; the **inspectable drill-down does.** Let them
> watch, don't tell them.

---

## 0. Setup (before they're in the room — 90 seconds, done cold)

```bash
export PATH="$HOME/.local/bin:$PATH"      # uv lives here; system python is too old
uv run ontoforge demo meridian /tmp/mcm_demo
uv run ontoforge serve -p /tmp/mcm_demo --port 8765
```

`demo meridian` regenerates a **10-table enterprise corpus** (support tickets, contracts,
purchase orders, shipments, leases, products, quality notifications, suppliers…) from code
— byte-reproducible, no fixture files — then runs the **generic engine** over it:
ingest → profile → **induce** → resolve → materialize → atlas. There is **no hand-authored
ontology and no gold model fed in** — the model on screen was induced from the mess. Open
`http://localhost:8765`. You land in **Ask**. Have these gold questions ready to paste:

| # | Question (verbatim) | What it proves |
|---|---|---|
| MQ-03 | *What is the total net value USD of purchase order lines with supplier name 'Hailong Precision Industry Co., Ltd.'?* | cross-system join + value-level drill-down |
| MQ-08 | *What is the average csat score of support tickets with issue category 'BATTERY'?* | a second clean cited answer (pacing) |
| MQ-10 | *What is our customer churn rate by region this year?* | **calibrated abstention** (answerable: false) |
| MQ-12 | *What is the total net price in dollars across purchase order lines for material CMP-DSP-0451?* | **unit-coherence rejection** |

> **Talk-track framing (say this once, up front):** *"I pointed this at ten raw enterprise
> tables ninety seconds ago. Nothing was modeled by hand. Everything you're about to see —
> the joins, the answers, the audit trail — the engine built itself. Watch what happens when
> I ask it something it can't answer."*

---

## 1. ASK → the value-level drill-down (the headline — ~90 sec)

**Click path:** You're already in **Ask** (the default landing — one centered question box).
Paste **MQ-03** and hit Enter.

- A **cited answer card** comes back with inline **"Where this came from"** dots (the
  de-jargoned name for atom-level citations — UI_DESIGN §0.1). The confidence shows on the
  **270° arc gauge**: teal band = confirmed.
- **THE AHA — drill down.** Click a "Where this came from" dot. The **Where-this-came-from
  tray** opens *beside* the answer and resolves the number to **the exact source records it
  was computed from** — the specific purchase-order-line atoms, their **bitemporal version**,
  and the **transform lineage** that produced the total.

> **Talk-track:** *"This answer crossed purchase-order-lines and the supplier table — a join
> the engine induced, not one I wired. Now watch this."* (click the dot) *"Every catalog on
> the market gives you column-level lineage: 'this number came from the PO table.' Every
> citation tool gives you chunk-level: 'see document 14.' Mine drills to the **individual
> source values** behind this exact figure, the version of each as of the answer date, and the
> transform that combined them. **Nobody else can finish this drill-down** — and for an
> auditor, that's the difference between a defensible number and a guess."*

*(Optional pacing beat: paste MQ-08 for a second clean cited answer if the room wants to see
it's not a one-off. Keep it short — the drill-down already landed the point.)*

---

## 2. ASK → calibrated abstention (the closer — ~60 sec)

**Click path:** In the same **Ask** box, paste **MQ-10** (*"customer churn rate by region
this year"*) and hit Enter.

- Instead of a confident-looking wrong answer, you get the **dignified abstention card** —
  *"No grounded answer — won't guess"* (UI_DESIGN §0.1), rendered in calm taupe, **never
  red**. There's no churn data in this estate; the engine knows it, and says so.

> **Talk-track:** *"Here's the failure mode that kills enterprise AI. There is no churn data
> in here. A typical NL-to-SQL tool will hallucinate a plausible-looking number — and a
> plausible-looking wrong number on a board deck or in front of a regulator is a catastrophe.
> Mine **abstains.** It is calibrated to be **never confidently wrong** — measured at ECE
> under 0.05 with zero confidently-wrong answers on the whole competency suite. Abstention is
> an unsolved problem in the literature; the only thing competitors ship is a binary 'query
> failed.' This is the difference between a tool you can trust with an auditor and one you
> can't."*

**Bonus beat (if they push on "but does it just give up?"):** paste **MQ-12** (the
*"net price in dollars… for material CMP-DSP-0451"*). The engine **rejects it on unit
incoherence** — the question mixes incompatible units and the OQIR type checker refuses the
traversal *before* it ever runs. *"It doesn't just abstain when data is missing — it
statically refuses a question that doesn't typecheck. It won't add apples to euros."*

---

## 3. STUDIO → watch the model build live + the false-positive killer (~2 min)

**Click path:** Top-bar segmented control → **Studio** (or ⌘3). The signature pairing tiles
automatically: **Data Map** across the top, **Console** docked along the bottom.

- **The live build.** In **Data Catalog** (left rail), the datasets are already modeled, so
  show the build motion by adding/selecting and hitting **Build map** — or narrate the Data
  Map that's already there. On a real build, **nodes pop the moment a type is induced and
  join-arcs draw the moment a join is classified**, batched ≤4 per frame so a burst never
  strobes (UI_DESIGN §5). The join tiers read as map roads: **solid teal = confirmed join,
  dashed marigold = likely join, standalone = nothing joined (honest, not an error).**

> **Talk-track:** *"This is the part no competitor in the market can draw — because their
> platforms don't induce the model in the first place. You're watching the engine resolve
> entities and form joins from raw data, live. The honest silos at the bottom — types it
> couldn't join — aren't failures. They're the engine telling you the truth."*

- **THE UNRELATED FALSE-POSITIVE KILLER.** Hover a **likely** (dashed marigold) arc to open
  its **evidence card** — tier, score, coverage %, overlap count, and up to five sample shared
  values. The point to make: two columns can share a *huge* vocabulary and still **not be
  related**. The engine fuses value-overlap **with distribution alignment** (Jensen-Shannon
  divergence), so when two columns look identical but their value *distributions* diverge and
  neither is a key, it classifies them **"unrelated-despite-similarity"** instead of asserting
  a bogus join. Then, in the **Console**, type a plain-English instruction to add a join —
  the engine runs it through a **Preview card first** ("nothing has changed yet"), and if you
  try to force a join below the coverage floor, it **refuses** (`ok=false, blocked=true`) and
  routes it to **Confirm suggestions** rather than asserting a confidently-wrong link.

> **Talk-track:** *"Everyone's auto-joiner over-connects — it sees two columns full of the
> same-looking IDs and draws a line. Mine checks the **distributions**, not just the overlap.
> These two look identical and the engine still says 'unrelated.' And when I try to force a
> weak join in plain English"* (type it in the Console) *"it previews first, then **refuses**
> rather than tell you something confidently wrong. The guardrail is on the server — even a
> hand-crafted API call can't push a sub-floor join through."*

---

## 4. BUILD / EXPORT → "leave with everything" (the de-risker — ~60 sec)

**Click path:** Top-bar → **Build** (or ⌘2). Pick a measure and a break-it-down-by dimension
in plain terms; the **Dashboard proposals** render as warm Vega charts. Then move to the two
clearly-separated outputs: **Extract** (the filtered table → Download CSV — a slice) and
**Export** (Download the whole dataset, portable — the AMBER bundle).

- Click **Export**. This produces the **AMBER full-estate bundle** — data + ontology +
  provenance + transforms — as plain **Parquet + Turtle + JSONL**, with **no OntoForge
  dependency to read it.**

> **Talk-track (the close):** *"Here's the thing that costs nothing and unlocks the deal. The
> single biggest objection a burned, regulation-aware CDO has is lock-in — Palantir's exports
> are 'not readily usable by any other equivalent system,' and the EU Data Act bans exit
> charges from January 2027. So we make exit a **feature.** This button hands you the **entire
> estate** — the model, the data, every provenance link, every transform — in open formats.
> We publish a restore-elsewhere test that replays your competency questions on a different
> stack with **100% answer-and-citation equality.** You can leave for free, the day you want
> to. The reason you won't is that everything you teach it compounds for you and only you —
> but that's your choice, contractually, not our lock."*

---

## The four "aha" beats, in order (what they should remember)

1. **The drill-down to a single source value** (Ask → MQ-03 → cite-dot). *Granularity.*
   If they remember one image, it's this one.
2. **The abstention on the unanswerable** (Ask → MQ-10). *Never confidently wrong.*
3. **The "unrelated-despite-similarity" verdict + the refused sub-floor join** (Studio).
   *Autonomy with calibration — it over-connects for no one.*
4. **The Export button = the whole estate, portable** (Build → Export). *Exit as a feature.*

---

## Failure-mode contingencies (founder cheat-sheet)

- **App won't serve / sandbox blocks it.** The macOS preview/sandbox blocks `serve`'s process
  spawns. Launch the server through a plain background shell, not the sandboxed preview
  (CLAUDE.md gotcha). Pre-warm it before the call; never build live in front of the prospect
  if the room is read-only.
- **`/api/atlas` 404s mid-build.** The UI degrades gracefully to the plain ontology sky with a
  quiet *"atlas not built — induced ontology shown"* note (UI_DESIGN §5a) — never an error
  screen. If it happens, narrate it as honesty, then re-run `demo meridian`.
- **A question doesn't answer as expected.** Use only the four gold questions in §0 — they are
  test-pinned. Don't improvise a question live; an unrehearsed query risks a (correct, but
  off-script) abstention that breaks pacing.
- **They ask "is this just GPT?"** No — it's **keyless, deterministic, pure-Python, no
  network.** Same answer every run. That's *why* it can be calibrated and audited; a
  temperature-sampled LLM can't make the never-confidently-wrong guarantee.
- **They ask about scale.** Be honest: *"v0 runs at fixture scale; we induce on a
  representative stratified subset by design (Plan mode), not your petabyte, and we ride the
  warehouses for scale. We don't pick a performance fight — we pick the accuracy-with-audit
  fight."* (MARKET_EDGE §c; STRATEGY §7 Risk 3.)
