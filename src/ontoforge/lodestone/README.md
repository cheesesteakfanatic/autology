# M12 — LODESTONE: Ontology-Grounded Query Planning over OQIR

Implements whitepaper §6.1–6.2 and §11.2 M12. Owner files:
`src/ontoforge/lodestone/`, tests in `tests/m12/`.

Public entry point (§11.2 interface):

```python
from ontoforge.lodestone import Lodestone, ask

answer = ask(question, ontology, hearth, ledger, spine)   # -> contracts.Answer
# or, stateful (supports answering the ONE clarification LODESTONE may pose):
eng = Lodestone(ontology, hearth, ledger, spine)
a = eng.ask("How many events are recorded for DELTA AIR LINES INC?")
if a.clarification:                       # exactly one multiple-choice question
    a = eng.answer_clarification("AccidentEvent")
```

## Pipeline (one `ask()` call)

1. **Grounding** (`grounding.py`) — deterministic hybrid-lexical retrieval
   over the *induced ontology*, never physical schemas (AMD-0002: no
   embeddings, zero network): class names/camel-split tokens/definition
   acronyms (ASRS, NTSB, FAA…), property names + synonyms + head tokens,
   literal-value probes against a HEARTH **value index** (normalized string
   values of non-TEXT props), unit words (profiling §3.2 alias table +
   currency words), time expressions (`as of 1987-05-20`, `OPENED AFTER
   2017-04-13`), aggregation/comparison cues, textJoin verbs + object phrase
   (`describe a bird strike` → pattern `bird strike`). Coverage is computed
   over the interrogative sentence only and counts **strong** bindings; weak
   evidence (a token appearing in some class definition) never licenses an
   answer. `coverage < 0.6` → **abstain** with the ungrounded words shown
   (§6.2 "the system knows when it is wrong").

2. **Candidate generation** (`candidates.py`) — ≤8 well-typed OQIR terms via
   compositional templates over the ontology link graph, behind the
   ModelClient task **`lodestone.generate`** (HeuristicAdapter handler; a live
   T2/T3 generator swaps in behind the same task, AMD-0002). Templates:
   entity lookup, filtered select (off-class filters become **dotted
   forward-path conditions** like `model.manufacturer.name`, which keep
   traversal direction unambiguous), 1–3-hop traverse chains for off-class
   projections, aggregate (+ group-by/having for "more than one X per Y"),
   topK, textJoin, asOf wrapping. Ambiguity is represented, not guessed away:
   equal-evidence readings (same mention → several classes; same literal →
   several host properties; equal-cost link paths; unanchored date fields)
   fan out as candidates with **tied priors**; discriminating signals (cue
   adjacency, measure host, mentioned intermediate classes, host-class
   mentions) separate priors decisively. Score = 0.55·coverage + 0.25·prior +
   0.20·type-check-pass.

3. **Type checking** (`typecheck.py`) — `typecheck(term, ontology[,
   expect_unit])` → `OQIRType | TypeError_`. Statically rejects: unknown
   classes/properties, phantom forward/reverse traversals, conditions on link
   properties, ordered comparisons across datatypes, contains on non-text,
   SUM/AVG over TEXT/STRING, unknown measures/group-bys, TopK over a
   non-Table, malformed stances, and **unit mixing**: a condition literal's
   unit must be dimension-convertible to the property's unit (the conversion
   is injected at lowering; *"total altitude in dollars"* is a `TypeError_`,
   never a coercion; cross-currency "conversion" is rejected as FX, §3.2).
   Reverse traversals propagate a **union of owner classes** upward; enclosing
   operators narrow it (conditions, textJoin props, aggregate measures), and
   a union that survives to the root unresolved is itself a type error.
   `infer()` additionally returns per-node resolved classes for lowering.

4. **Selection** — a `DecisionKind.QI` spine decision: candidate scores (plus
   a coverage-derived abstain pseudo-candidate) go through a registered T0
   rule; `Answer.confidence = spine confidence × grounding coverage`
   (uncalibrated but monotone in evidence; wrong answers stay below
   `tau_high` — the 0-confidently-wrong gate).

5. **Conformal set + clarification** (`clarify.py`) — Γ = candidates within
   0.9 of the top score. Γ's members are *executed first*; plans empty after
   repair leave the set (execution-guided re-ranking, §6.2). If >1 viable
   candidates remain **and their answers differ**, their disagreement is a
   structural diff over typed terms — entity scope / metric / filter field
   (time window) / stance — rendered as ONE multiple-choice question;
   `answer_clarification(choice)` re-ranks to a singleton. Agreeing
   candidates never trigger a question (zero information gain).

6. **Lowering + execution** (`lower.py`, `execute.py`) — Select → cell-level
   class scan (class + descendants) + condition filters; Traverse →
   link-adjacency expansion; TextJoin → case-insensitive substring match on
   the TEXT property; Aggregate/TopK → **DuckDB SQL over the materialized
   frame** (COUNT DISTINCT for group-by reuse questions; blank measures are
   *unknown, not zero* — excluded); AsOf → the Stance is pushed into every
   scan/link visibility test. Default stance is **EVER** (any system-open
   cell regardless of valid time): a registry question without a temporal
   qualifier asks about the record; explicit "as of" pushes a real
   bi-temporal stance (that is what resolves N-number reuse). **Repair**: an
   empty intermediate stage re-runs the plan once per relaxation level
   (1 = case-insensitive, 2 = punctuation/legal-suffix-normalized names);
   exhausted → abstain with the failed leaf shown.

7. **Citations** (`citations.py`) — execution operates on *cells*, so every
   answer cell carries the `prov_ref`s of the cells that produced it **and**
   of every cell/link a filter consulted; `ledger.valuate_ref(ref,
   "citations")` valuates each ref to source-atom ids → `Answer.citations`
   as `CitedCell` rows. 100% of non-abstained answer cells cite ≥1 atom
   (constraint H makes a citation-less cell a lowering bug, not a data
   condition).

## Acceptance status (tests/m12)

* **Type checker**: 28 seeded ill-typed plans — 100% rejection (hard gate);
  12 well-typed accepted; the CQ-18 "altitude in dollars" plan dies in the
  checker.
* **Competency harness** (§12.4 definition of done) over the REAL estate →
  HEARTH world (gold mini-ontology, frozen slice per §11.3): **15/15
  answerable fully correct (100% ≥ 70% gate)**, 100% atom-level citation
  coverage, both unanswerables abstained (insufficient strong grounding),
  trick-unit rejected by the type checker, 0 confidently-wrong, clarification
  rate 0/18 on the competency set.
* **Clarification**: two crafted ambiguous questions (entity-scope: "events"
  = NTSB vs all safety events; time-field: unanchored "after <date>") emit
  exactly one multiple-choice question each; answering yields a singleton,
  correct, cited answer (both branches verified to differ).
* **Repair**: a case-mismatched quoted literal ('Landing Gear' vs stored
  'LANDING GEAR') succeeds via level-1 repair; a genuinely empty population
  abstains with the failed leaf.
* **Determinism**: identical answers, confidences, and citation sets across
  repeated asks and fresh engines.
* **Free-text robustness** (`test_freetext_robustness.py`, the keyless-NL
  gate): 25 deliberate PARAPHRASES of the aviation gold CQs (LODESTONE never
  sees the canonical strings) — 22 answerable, 2 unanswerable, 1 trick-unit.
  **22/22 = 100% answered-correct WITH atom-level citations** (≥ 70% gate),
  0 confidently-wrong (no wrong answer at confidence ≥ `tau_high`), both
  unanswerables abstain, and the trick-unit ("total altitude in dollars") is
  rejected by the OQIR type checker — the abstention/rejection contract
  survives paraphrasing.

## Deterministic NL hardening (keyless free-text path, §6.2)

The KEYLESS (no-LLM) grounding path is a true core, not a fallback: it answers
real **paraphrased** free text — reworded verbs, abbreviations, cue synonyms,
clause reorder, magnitude/written number forms — without ever emitting a
confidently-wrong answer. The techniques below are all deterministic and
network-free (AMD-0002); they only ever add *evidence tiers* and *re-ranking
nudges* — none can fabricate a binding or override the type checker.

* **Index-time schema-link expansion** (`grounding.py` `Lexicon`). Each class /
  property registers, beyond its verbatim name + camel/snake split + head
  token + authored synonyms: a closed-table **abbreviation** map applied BOTH
  directions (`qty↔quantity`, `amt↔amount`, `mfr/maker↔manufacturer`,
  `dt↔date`, `#/no↔number`, `wt↔weight`…) as STRONG keys (curated, high
  precision); plural↔singular variants; and open morphological **stems**
  (drop `ing/ed/es/s`) as WEAK keys. Forward abbreviation expansion fires only
  on a whole single-word name — a fragment (`mfr` inside `mfr_mdl_code`) gets
  reverse/stem variants only, so it can't falsely equate a compound column with
  an expansion. Stopwords and 1-char tokens are never expanded (no `in`=inch,
  `no`=number, `id`=word leakage). The greedy longest-first matcher still hits
  the exact canonical key first, so canonical phrasings are untouched.
* **Trigram-blocked Jaro-Winkler fuzzy linking** (`Lexicon.lookup` /
  `_fuzzy_lookup`). A pure-python prefix-weighted Jaro-Winkler (no new
  dependency) runs only over keys sharing ≥1 char-trigram with the query token
  (BRIDGE/IRNet-style blocking inverted index), never when the token already
  exact-matches a schema key. Calibrated thresholds are the single most
  important confidently-wrong guard: `JW ≥ 0.92` is STRONG-eligible,
  `0.88 ≤ JW < 0.92` is an evidence-only band that is forced `strong=False`, so
  a fuzzy near-miss can **rank/clarify but never alone clear the coverage
  floor**.
* **Expanded cue lexicons** (`AGG_CUES`, `CMP_CUES`, `TEXTJOIN_CUES`). Richer
  ordered phrase tuples (longest-match preserved) route synonymous surface
  forms to the same intent: count←{tally, total number, how many, no. of},
  sum←{combined, aggregate, sum total, add up}, avg←{mean, on average,
  typical}, min/max←{lowest/least/bottom, highest/greatest/peak/biggest},
  GT/LT/GE/LE←{in excess of, north/south of, no more than (LE), at least (GE),
  exceeding, beyond}. Cue words stay in `CUE_WORDS` so they count toward
  coverage without being mistaken for content.
* **Numeric phrase parsing** (`MONEY_MAG`, `WRITTEN_NUMBERS`, `MAGNITUDE`,
  `CURRENCY_PREFIX`). Currency prefixes (`$/£/€`), magnitude suffixes
  (`10k`, `$1M`, `£1.5bn`), grouped/decimal digits, and a small written-number
  table all resolve to the same `number_cond` literal — but **only when a
  comparison cue is adjacent**, and every unit-bearing literal is still routed
  through the unit-dimension check in `candidates.py` + the type checker, so
  `$1M` against a length property stays a `TypeError_`, never a coercion (the
  CQ-18 trick-unit gate is unweakened).
* **Honest coverage accounting** (`STOPWORDS`, coverage block). Conversational /
  politeness / imperative filler (`could`, `would`, `please`, `tell`, `know`,
  `wondering`, `show`, `list`, `find`, `want`…) is in `STOPWORDS`, so paraphrase
  chrome does not inflate the coverage denominator. The strong-only numerator is
  unchanged, so the abstention contract is preserved — the denominator is just
  honest.
* **Soft-clarify band** (`__init__.py`, `MIN_COVERAGE_SOFT = 0.45`). In
  `[0.45, 0.6)` with ≥1 strong class/prop anchor, a strong MEASURE anchor
  (a prop/agg the question named *beyond* the entity class), AND a well-typed
  candidate that actually executes non-empty, LODESTONE asks one disambiguating
  question naming the unconsumed tokens instead of abstaining silently. Below
  0.45 the hard-abstain path is kept (CQ-16/CQ-17 land there: their candidates
  execute empty, so they never reach the soft band). A clarification is never a
  wrong answer, so 0-confidently-wrong holds.

These are guarded against the named pitfalls: fuzzy hits below 0.92 can't clear
the floor; abbreviation expansion is context-gated and stopword-excluded;
magnitude/currency literals route through the existing dimension check; relative
temporal windows resolve against the corpus as-of anchor (never wall-clock); the
coverage numerator stays strong-only; value probes stay `value_contains`
alternatives that execution-guided Γ re-ranking settles. The regression guard
for every change is the **aviation 15/15 + meridian gold gates run green**.

## Design decisions & trade-offs

1. **Forward-path conditions over reverse traversals.** "Count work orders
   for operator X" is `Select(WorkOrder, operator.name = X)`, not
   `Traverse(Select(Operator…), reverse)` — the reverse spelling is
   owner-ambiguous exactly where it matters. Reverse traversal stays in the
   algebra (and the checker handles it with union narrowing), but generation
   prefers the unambiguous spelling.
2. **Execution-guided disambiguation before clarification.** Γ members are
   cheap to execute at fixture scale; readings with no data behind them are
   not worth a human's time. Only viable, *disagreeing* readings earn the one
   clarification.
3. **EVER as the default stance.** HEARTH's `current` stance hides facts
   whose valid windows are closed (a 1995-expired registration is still the
   record). Questions are about the record unless they say "as of".
4. **Subclass-polymorphic link hops cost more** (2.6 vs 1.0) in path search:
   a `registrant` that happens to be a Manufacturer is not *the* manufacturer
   path; declared ranges win every tie, and remaining ties order by which
   intermediate classes the question actually mentioned.
5. **The fixture-builder conforms, LODESTONE queries.** Meter-suffixed
   altitudes, `USD 1,234.56` lexical forms, operator-name folding, and
   leading-N normalization are pipeline work (ANVIL/ER at gold fidelity) done
   by the test fixture-builder, which also records the source lexical unit
   (`altitude_agl_unit`) — that recorded unit is what makes "reports recorded
   in meters" answerable as a typed condition rather than string archaeology.
6. **Uncalibrated-but-honest confidence.** No QI calibration data exists in
   v0; confidence = sharpened score ratio × strong-grounding coverage, so it
   only approaches `tau_high` when nearly every content word grounded and one
   reading dominates. The competency suite verifies no wrong answer reaches
   `tau_high`.

## Known limitations (honest report)

* NL parsing is rule-based per AMD-0002: it generalizes across phrasings of
  the competency-question *styles* (entity lookup, filtered counts, unit
  arithmetic, group-by reuse, text joins, as-of), not open-domain English.
* `Condition.value2`/BETWEEN lowers but no grounding template emits it yet;
  TopK has a template but no competency question exercises it end-to-end.
* Cross-document coreference beyond regex tail-number mentions is M5's job;
  the fixture-builder reuses a deterministic fold, not the full ER cascade.
* BIRD / Spider 2.0-Lite harnesses (§6.2 external benchmarks) are out of
  scope for this wave (no network, no external corpora); the CEO-suite gate
  (§12.4) is the shipped acceptance surface.
