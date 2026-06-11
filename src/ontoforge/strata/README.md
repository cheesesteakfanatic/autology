# M4 — STRATA: Stratified Type-Lattice Induction

Whitepaper §3.3–§3.5, §11.2 M4. Induces a class taxonomy `(C, ≤_C)` from the
M3 evidence substrate (profile sketches, FDs, INDs), grounded in *extensional*
evidence via Formal Concept Analysis, with spine-gated admission, deterministic
T2 naming, and AddIntent-style incremental maintenance.

```
candidates (§3.3)        formal context K=(G,M,I)      iceberg lattice B_σ(K)
G-table / G-decomp  ──►  objects = candidates     ──►  CbO + σ pruning     ──►
G-join (hubs)            attrs  = sketch features      stability + covers

spine-gated admission (§3.4.2-3)     ontology emission (§3.4 Output, §3.5)
DecisionKind.ADMIT per concept  ──►  contracts.Ontology: classes, parents,
T0 rule → T1 features → T2 judger    PropertyDefs, ShapeConstraints, events
```

## §11.2 interface mapping

| Whitepaper                       | Here                                      |
|----------------------------------|-------------------------------------------|
| `induce(candidates, K) → lattice`| `Strata.induce(profiles, inds, fds=None, candidates=None)` → `StrataResult` (`.lattice` on it) |
| `admit(concept) → spine decision`| `Strata.admit(concept)` → `DecisionResult` |
| `insert_delta(Δcandidates)`      | `Strata.insert_delta([...])` → `(proposals, affected hashes)` |
| `emit_ontology() → (C, ≤, P, ax, Σ)` | `Strata.emit_ontology()` → `contracts.Ontology` |

## Module map

| File            | Role |
|-----------------|------|
| `_norm.py`      | name-token normalization: abbreviation expansion (`ACFT_REGIST_NMBR` → `aircraft_registration_number`), token Jaccard, camel-casing |
| `candidates.py` | §3.3 generators: G-table (keyed tables), G-decomp (FD-cluster latent types), G-join (IND hub reference domains, `bypass_sigma=True`) |
| `context.py`    | formal context: evidence-driven property synonym clusters, discretized sketch attributes, Galois operators, clarification/reduction |
| `lattice.py`    | Close-by-One iceberg enumeration, stability index, covering relation |
| `admission.py`  | spine routing (T0 rule, T2 deterministic judger), hub pre-review, `strata.name_concept` handler, ledger-backed `NameMemo` |
| `emit.py`       | admitted concepts → `contracts.Ontology` (URIs, transitively-reduced parents, PropertyDefs, shapes, §3.5 event rule) |
| `incremental.py`| object-wise AddIntent insertion + `ChangeProposal` diffs |
| `strata.py`     | the orchestrator holding lattice/admission state across deltas |

## Design decisions

**Candidate generators (§3.3).** G-doc is out of scope here (AMD-0007 deferred
document path). G-decomp accepts single-FD clusters (`COMPONENT →
ATA_CHAPTER` *is* a real latent type) but requires an entity-like lhs: it
skips temporal and long-text lhs columns, lhs columns that are foreign keys
into another table's key (the type is that table), lhs columns participating
in cross-table INDs (shared domains are G-join's hub territory), and lhs whose
cardinality is not ≪ the row count. G-join builds connected components of the
high-coverage IND graph; components containing a table's singleton candidate
key are dropped (that domain *is* the keyed table).

**Synonym map is evidence-built, never hardcoded.** Columns merge into one
canonical property via six edge rules over normalized name tokens, IND links,
MinHash value overlap, format-signature shape, and shared identity-like
semantic types (`context._synonym_edge`). Two deliberate restrictions, both
pinned by aviation-estate failures: (a) same-table INDs are NOT synonym
evidence (`NO-ENG`'s 0–8 sits inside `TYPE-ENG`'s 1–11 by numeric coincidence);
(b) the weak-name+format rule requires a shared *non-generic* token — two
`…_ID` columns sharing only "id" are different identifier namespaces
(`MECHANIC_ID` vs `EV_ID`).

**Context attributes** per candidate: `has-prop:<canonical>` (weight 1.0),
`semtype:`, `dim:`, `fmt:`, `key-arity:`, `has-timestamp`,
`has-narrative-text`. Clarification (identical extents merged) and reduction
(extent = intersection of strictly larger extents dropped) shrink M for CbO
only; every reported intent is re-expanded against the ORIGINAL context, so
intent hashes — and therefore class URIs
(`contracts.class_uri_from_intent`) — are independent of the reduction and of
input order.

**Iceberg + bypass.** CbO prunes any branch whose extent falls below σ
(extents only shrink down the search tree, so pruning is sound). G-join hub
candidates bypass σ — their object concepts are force-included and flagged
`bypass=True` — but receive an explicit *pre-lattice* spine review
(`review_hub_candidates`): a posited shared domain is admitted only when its
referring columns agree in meaning (synonym-cluster unity ≥ 0.8) or share a
non-generic semantic type. This keeps rare-but-real reference types (the §3.3
airport example; aviation's shared US-state domain) while discarding numeric
value-range coincidences (`TYPE-ACFT ⊆ NO-SEATS`).

**Admission** processes the lattice top-down; each concept is ONE
`DecisionKind.ADMIT` spine decision over `("merge", "admit", "discard")` with
features: support, stability, weighted intent distinctiveness vs admitted
ancestors, extent distinctiveness, generator prior, protected flag (object
concept of a generated candidate). A T0 rule merges concepts with no
distinguishing full property; the deterministic T2 judger admits when the
structural-quality score clears the bar AND the concept either carries ≥ 2
distinguishing properties or is protected. The structural root (extent = G, no
shared property) is discarded as ⊤, not a class. Merge targets resolve to the
most specific admitted ancestor.

**Naming** is a T2 `ModelClient` task (`strata.name_concept`) executed at
emission time, memoized on intent hash through the ledger artifact table
(kind `strata.name_memo`): re-induction — including by a fresh `Strata`
sharing only the ledger — reuses recorded names; renames happen only via
TEMPER (§3.4 failure-mode (c)). Collision suffixes are assigned in intent-hash
order, so they are permutation-stable too.

**Emission.** Parents = transitively reduced lattice order restricted to
admitted concepts (multiple inheritance allowed). Properties = the concept's
distinguishing `has-prop` intent; datatype/dimension/unit aggregate from
member `ColumnProfile`s; `functional=True` iff an exact FD (candidate key →
column) backs it. Link properties: INDs into admitted candidates' key columns,
G-decomp lhs foreign keys, and admitted hub-domain membership. Shapes compile
from profile stats (null rate → `min_count`, shared code-like signatures →
regex `pattern`, sketch quantiles → value ranges). §3.5 event rule: timestamp
property + ≥ 2 link properties + append-mostly CDC behavior → `is_event`.

**Incremental maintenance** inserts *objects* (the dual of AddIntent): the
extents of exactly the order filter of γ(g) grow — existing intents, hashes,
and URIs never change — and new concepts appear only at intersections of
attrs(g) with existing intents/object rows. **Limitation:** this candidate set
is provably complete for σ ≤ 2 (a concept newly crossing the threshold has
extent S ∪ {g} with |S| ≤ 1); σ > 2 would need (σ−1)-subset intersections.
Admission is re-run with decision memoization (identical (intent, features)
pairs reuse the cached `DecisionResult`), and EVERY outcome flip — including
spine-less structural-root discards — is emitted as a `ChangeProposal`
(ledger artifact kind `strata.change_proposal`), the future TEMPER operation;
flips are never silently applied.

## Measured results (aviation hero estate, tests/m4)

Real pipeline (`load_estate` → M3 `profile_table`/`discover_inds` → STRATA)
vs `load_gold_ontology()`, best-match scoring (property-overlap F1 ·0.40 +
member-table Jaccard ·0.35 + name-token Jaccard ·0.25, match θ = 0.4):

- class precision **0.938** (15/16), recall **0.647** (11/17)
  — gates: ≥ 0.70 / ≥ 0.60
- hierarchy-edge precision **0.375** (6/16), recall **1.0** (2/2) on matched
  classes (ancestor-closure credit). Low edge precision is dominated by FCA
  intersection abstractions (e.g. `TailNumberDateEvent`) sitting above
  matched leaf classes; the §12 0.85 bar is a design target for the full
  T1/T2/T3 cascade, not this keyless heuristic build.
- unmatched gold classes are the purely-intensional upper taxonomy the estate
  has no extensional evidence for (`Agent`, `Person`, `Organization`,
  `Operator`, `Manufacturer`, `Airport`).

## Invariants the test suite enforces (tests/m4)

1. gold harness gates above (`test_gold_harness.py`);
2. identical class-URI and name sets under shuffled profiles/INDs/candidates
   (`test_uri_stability.py`);
3. one-by-one `insert_delta` ≡ all-at-once induction; insertion touches
   exactly the order filter; every admission flip yields a `ChangeProposal`
   (`test_incremental.py`);
4. Galois-connection laws + CbO enumerates exactly the closed extents on
   random contexts (hypothesis, derandomized; `test_galois.py`);
5. iceberg σ respected at lattice AND admission level, bypass only for
   spine-reviewed hubs (`test_iceberg.py`, `test_lattice.py`);
6. hand-computed exact lattice for a 4-object context (`test_lattice.py`);
7. naming memoization through the ledger (`test_naming_memo.py`);
8. §3.5 event rule, link targets, functional flags, shape compilation on a
   synthetic micro estate (`test_emission.py`).
