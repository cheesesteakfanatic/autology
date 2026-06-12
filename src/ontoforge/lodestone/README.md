# M12 — LODESTONE: Ontology-Grounded Query Planning over OQIR

Implements whitepaper §6.1–6.2 and §11.2 M12. Owner files:
`src/ontoforge/lodestone/`, tests in `tests/m12/` (64 tests).

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
