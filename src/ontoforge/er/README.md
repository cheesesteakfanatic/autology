# M5 — ER Cascade & Incremental Clustering

Spec: whitepaper §11.2 M5 ("blocking, FS, T2 matcher, incremental correlation
clustering, anchor-stable URIs"; v1 G1 targets), §2.2 dataflow ("ER cascade +
incremental clustering → Π^(t)"), §17.2.1 (the aviation estate's
N-number-reuse temporal-identity trap), MVP plan §4.5. Amendments honored:
AMD-0004 (no neural blocking — MinHash-LSH + sorted-neighborhood hybrid),
AMD-0002 (T2 served by a deterministic `HeuristicAdapter`, zero network).

Owns `src/ontoforge/er/` + `tests/m5/`. Imports only frozen Wave-1 modules
(`contracts`, `spine`, `ledger`, `estates`).

## Pipeline

```
estate DataFrames
  └─ records.py      per-table field maps → EntityMention (aircraft, operator)
  └─ blocking.py     (a) exact-key block  (b) MinHash-LSH k=64, b=10×r=6
                     (c) sorted-neighborhood w=4  (d) shared-tail relational
  └─ fs.py           field comparators → agreement levels + continuous vector;
                     Fellegi-Sunter mixture fit by EM (unsupervised, seed=17)
  └─ cascade.py      two-threshold weight band → ambiguous → DecisionSpine
                     (T1 calibrated logistic ← gold TRAIN bootstrap;
                      T2 'spine.adjudicate.er' heuristic with reuse guard)
  └─ clustering.py   KwikCluster (deterministic hash pivots) → anchor URIs
  └─ incremental.py  add_mentions(Δ): delta-scoped blocking/adjudication,
                     local re-clustering, URI hysteresis + churn log
```

## Design decisions

**Mention extraction (records.py).** One `EntityMention` per source row per
kind. Tails are canonicalized to the registry's N-less form (`N3484Z` →
`3484Z`) so leading-'N' variants block together, but normalization never
merges identity: the mention keeps its serial and its date evidence, so the
reused-tail trap (§17.2.1) stays separable for the matcher. ASRS tails are
extracted from the NARRATIVE text with `\bN[0-9]{1,5}[A-Z]{0,2}\b` (210/350
fixture narratives carry one). FAA rows get a true validity window
[CERT ISSUE DATE, EXPIRATION DATE]; events get their event date; ERP work
orders their open/close span.

**Blocking (blocking.py).** Aircraft block on the exact tail (a candidate
generator, never a merge rule). Operators: exact-normalized-name equality IS
the T0 dedup rule, so one blocking node per distinct name (144 nodes from
3 601 mentions) — pairwise scoring then runs at name level, the standard
canopy collapse. MinHash is implemented from scratch (grams → xxh3_64 ints →
k=64 multiplicative-shift hashes, vectorized minimum; banding b=10 × r=6
gives the s-curve midpoint (1/b)^(1/r) = 0.681 ≈ the required 0.7 Jaccard
operating point; P(candidate) = 1−(1−s^r)^b). The shared-tail relational pass
is what surfaces zero-string-overlap operator aliases (FedEx Express ~
Federal Express Corp co-occurring on the same airframes). Two reduction
ratios are reported: `reduction_ratio` counts pairs actually scored (the
comparison-cost view that the ≥0.95 gate reads on) and
`implied_reduction_ratio` expands exact-name groups back to mention pairs
(coverage view; operators 0.8999 because the fixture's registrant names are
heavily repeated — those pairs are resolved by the T0 exact-key rule, not by
scoring).

**Fellegi-Sunter + EM (fs.py).** Per-field agreement levels
(agree/partial/disagree, missing = field skipped): aircraft = tail, serial,
model, window, name; operator = name(JW), tokens(fuzzy Jaccard/containment),
alias(acronym/fused-prefix, e.g. UPS ~ United Parcel Service, FEDEX ~
FEDERAL EXPRESS — generic string algorithms, no lookup tables), shared_tail.
The `window` field is observed only when a registry side carries a real
validity window — event↔event date gaps say nothing about airframe identity.
EM on the two-component conditionally-independent multinomial mixture,
randomly initialized from seed 17, Laplace-smoothed M-step (0.5), convergence
when |Δll| < 1e-8·(1+|ll|) (≤300 iters; trace kept, monotonicity tested).
Components are exchangeable under random init, so the match class is
identified post hoc as the one with larger Σ_f P(agree) — a deterministic
rule. Match weight w = Σ_observed log2(m_f[l]/u_f[l]); banding is done in
weight space via the exact map w* = log2(P/(1−P)) − log2(p/(1−p)) at
posterior cuts 0.95/0.05.

**Spine integration (cascade.py).** Ambiguous-band pairs become
`DecisionKind.ER` requests: T1 features = continuous comparators + FS weight
and posterior; T1 is recalibrated once per run from a bootstrap labeled set
drawn from the gold **TRAIN split only** (entity-level 50/50 split, seed
20260611 in `eval.SPLIT_SEED`; the TEST half is untouchable and is the only
thing the F1 gates read). T2 is the ModelClient task the spine actually emits
(`spine.adjudicate.er`, also registered as `er.adjudicate`) served by a
deterministic weighted-field-agreement scorer — deliberately distinct logic
from T1's calibrated logistic — carrying the temporal-reuse guard: serial
mismatch ⇒ no (0.95); serial mismatch + disjoint date ranges ⇒ no (0.98);
dates disjoint by >1y without serial corroboration ⇒ no (0.93). Deferred or
quarantined decisions reject the edge (fail-closed) and are counted against
the v1 ≤25% deferral target.

**Clustering (clustering.py).** KwikCluster with the deterministic pivot
order sorted by (xxh3_64(node_id), node_id). Intransitive evidence (A=B, B=C,
A≠C) resolves to a partition that violates exactly one judgement — which one
is the pivot order's choice (tested both branches). URIs:
`ent://<kind>/<xxh3_64 hex of the lexicographically-min founding mention_id>`.
Hysteresis: a cluster keeps its URI while it retains its anchor mention;
merges keep the lexicographically-smallest anchor's URI; splits keep the
anchor side and mint fresh for the rest.

**Incremental (incremental.py).** `add_mentions(Δ)` registers delta mentions
into the live blocking indexes (LSH add/query, block membership,
sorted-neighborhood windows recomputed around touched keys), scores ONLY
never-before-decided pairs with the frozen FS model + the same spine, and
re-clusters only the subgraph of touched clusters + new nodes. Old accepted
edges persist; nothing outside the affected set is recomputed (tested:
zero URI changes off the affected set).

## Measured numbers (fixtures/aviation, seeds: EM 17, split 20260611, LSH 0x5EEDED)

Held-out (TEST split: 388/775 aircraft entities, 12/24 operator entities;
559 + 8 790 closure pairs — ≥50% of gold pairs never touched by calibration):

| metric | aircraft | operator | combined | gate |
|---|---|---|---|---|
| pairwise precision | 1.000 | 1.000 | 1.000 | — |
| pairwise recall | 1.000 | 0.9941 | 0.9944 | — |
| pairwise F1 | **1.000** | **0.997** | **0.9972** | ≥ 0.85 HARD |
| blocking pairs-recall (closure / listed) | 1.0 / 1.0 | 0.99987 / 1.0 | — | ≥ 0.98 |
| reduction ratio (scored / implied) | 0.99622 / 0.99622 | 0.99987 / 0.89989 | — | ≥ 0.95 (scored) |

Cascade: 25 152 aircraft + 817 operator pairs scored; stages — aircraft
fs_low 22 643, fs_high 1 079, spine:T1 583, spine:T2 847 (escalation 5.7%);
operator escalation 0.5%; deferrals 0 (≤25% target); 1 527 bootstrap
calibration samples; post-calibration ECE(ER) = 0.0063 (§12 target ≤0.05).
FS EM: converged in 75 iters (aircraft, prior 0.082, weight band
[−0.76, +7.74]) and 24 iters (operator, prior 0.088); m>u at the agree level
on every identity field. Temporal trap: all 8 reused tails kept separate
(0 cross-era merges; all 5 gold trap pairs correctly era-matched).
Incremental two-cycle (6 110 → +1 141 mentions): 6 176 delta pairs scored,
F1 ratio incremental/batch = **0.9973** (≥0.97), max URI churn per affected
entity = **1** (gate ≤1), 19 mention-level changes, 0 outside the affected
set. Batch run ≈ 6.6 s; two runs bit-identical (URIs, edges, decisions).

## Failure modes & notes

- 140 ASRS narratives carry no tail; their mentions stay singletons (no gold
  labels exist for them — recall is unaffected at fixture scale, but a model/
  operator/date soft-blocking pass is the obvious extension for live data).
- The operator implied reduction ratio (0.8999) reflects exact-name group
  expansion, not comparisons; at live scale the name-group collapse is what
  keeps the comparison budget sub-quadratic.
- KwikCluster is a 3-approximation; star-shaped event clusters fragment if
  event↔event edges are missed, which is why the comparators score
  event↔event pairs on tail+model+name with window treated as missing.
- The FS candidate population is match-heavy (prior ≈ 0.08 only because SNM/
  LSH contribute clear non-matches); the two-threshold band plus spine
  escalation absorbs the resulting u-estimate noise.
