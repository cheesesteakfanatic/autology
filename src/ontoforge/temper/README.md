# M10 — TEMPER: the Ontology Evolution Calculus

Implements whitepaper §3.6 (entire section) and §11.2 M10 at AMD fixture scale.
Owner files: `src/ontoforge/temper/`, tests in `tests/m10/` (68 tests, incl.
the 300-sequence replay harness).

## Files

| file | contents |
|---|---|
| `ops.py` | the typed operator set: each operator = (precondition, ontology rewrite, forward migration, inverse); conversion registry; predicate evaluator |
| `apply.py` | `TemperEngine.apply/propose/rewrite/answer`, `DataAdapter` (the counted Hearth commit surface), spine gating, `MigrationReport` |
| `views.py` | backward views: `StructuredQuery` -> `Plan` (branches of accessor trees), per-operator `op_rewriter`, `RewriterChain` composition, deterministic `execute` |
| `morphism.py` | morphism ledger: `MorphismRecord`, persistence as M0-ledger `temper-op` artifacts, `replay`, `invert_record` |

## Operator set (§3.6 table)

| operator | ontology effect | forward migration | backward view | inverse |
|---|---|---|---|---|
| `AddClass(c, parent)` | C ∪ {c} | none | identity (drop-class is implicit: empty extent) | `DropClass` (only while untouched) |
| `RenameClass` / `RenameProperty` | label only — **URI stable** | **zero** (cells key on the property-URI tail, see below) | identity | rename back |
| `RetireClass` | tombstone marker in `definition`; extent stays readable, every further op on it is rejected | zero | identity | `UnretireClass` |
| `AddProperty` / `DropProperty` | shape of the class | zero (`Drop` requires no data) | identity | each other |
| `AddFacet` / `RetireFacet` | SHACL shape change only | zero | identity | each other (with retained index) |
| `RetypeProperty(p, dtype/unit, conversion)` | datatype/unit swap | conversion plan over current cells of the subtree extent | **inverse-conversion view**: accessors compose `inv` so old queries filter/project in their own units | `RetypeProperty` with the inverse spec |
| `Generalize(p: c→parent)` | move p up | **widen: no data move** | identity (extents are polymorphic at execution) | `Specialize` (with retained position) |
| `Specialize(p: parent→c)` | move p down | instance check; violators get a `__temper_quarantine` marker cell (cost ∝ violators) | identity | `Generalize` |
| `SplitClass(c→c1,c2; δ)` | replace c by two copies | route instances by the **total** predicate δ over property values | **union view** (branch duplication) | `MergeClasses` with retained origin key |
| `MergeClasses(c1,c2→c; align)` | union + property alignment (explicit map + implicit same-name fold, type-checked) | copy both extents + a **retained per-merge origin column** `__temper_origin@…` | **discriminator-split view** filtering on the origin column | `SplitClass` on the origin key (when the alignment is total) |
| `PromoteProperty(p of c→c_p)` | new class + link (p keeps its URI ⇒ key stable) | group-by current values, mint **content-addressed entities** (equal values deduplicate), supersede p-cells with link values | **rejoin view**: `Direct(p)` becomes `Deref(p → c_p.value)` | `DemoteClass` |
| `DemoteClass(c_p→p)` | inverse of promote | join back: link cells superseded by the target's value | link reads collapse to `Direct`; queries *against* c_p use the **regroup view** (re-minted URIs) | `PromoteProperty` |

## Snapshot-queryability (closure property (ii))

`TemperEngine` retains a snapshot of every ontology version. A query authored
at version *s* is `lift`-ed against that snapshot (binding property names to
stable storage keys), then every operator applied since *s* folds its backward
view over the plan (`RewriterChain`). The plan IR is closed under all views:
branches (union semantics), `Direct`/`Deref` accessor trees with composed
inverse-conversion functions, per-branch origin filters, and regroup branches.
Execution reads only shards of classes in the **current** ontology — moved
extents' superseded shards become invisible exactly when their class leaves O,
while system-time bitemporality keeps every pre-migration cell reachable via
`as_known_at` on the store itself.

Executable form of the §3.6 theorem, tested: a fixed 10-query battery authored
against the gold base version answers **identically** after 300 random valid
operator sequences (length ≤ 8) over a ~207-entity HEARTH store — including
sequences that split, merge, promote, demote, retype, and quarantine.

## Morphism ledger (closure property (iii))

Every application appends `(op type, params, from_version, to_version,
migration stats incl. commit count, timestamp)`; persisted via
`ledger.append_artifact(kind='temper-op')` with a real interned provenance
leaf (constraint H holds for schema history). `replay(records, base)`
re-applies the pure rewrites and asserts the version chain — harness-tested to
reproduce the final ontology with exact `ClassDef` equality. `invert()` /
`invert_record()` cover Rename, Add/Retire pairs, Retype (inverse conversion),
Generalize↔Specialize (retained positions), Split↔Merge (retained
discriminator; merge is invertible when its alignment is total — e.g. merges
of split products), Promote↔Demote.

## Spine gating (§3.6 autonomy integration)

`SplitClass`/`MergeClasses` over a populated extent are `DecisionKind.SM`
decisions with `impact = extent size`. The request carries deliberately
*neutral* features: an unconfident spine (no T0 rule, no calibration, no
model client) lands in the escalation band and the engine raises
`OperatorDeferred` — nothing applied, nothing recorded. A confident signal
(T0 rule / calibrated T1 / adjudicator) auto-accepts; empty extents and all
other operators auto-apply.

## Design decisions & trade-offs

1. **Storage-key = property-URI tail.** Cells key on the stable URI tail, not
   the display name, so RenameProperty is zero-migration *by construction*
   (the §3.6 zero-migration row is structural, not promised).
2. **Migrated cells reuse the original prov_refs** (instead of interning
   `Prod(original)` wrappers): the migrated value is derivable from exactly
   the same atoms; interning per-cell wrapper terms would add ledger rows with
   no extra information. Documented deviation per the M10 task note.
3. **Moved extents are not deleted or expired** — split/merge/promote leave
   the source shard intact and remove its class from O. Append-only system
   time keeps pre-migration state reconstructable; current-stance execution
   never sees orphaned shards because it resolves classes through the current
   ontology.
4. **RetireClass is a cooperative tombstone** (marker in `definition`):
   M6 is frozen, so write-freezing is enforced at the TEMPER layer (every
   subsequent operator on a retired class is rejected) rather than inside
   Hearth's commit path.
5. **Per-merge origin keys** (`__temper_origin@n`): merge chains stay
   individually reversible/queryable — a second merge cannot clobber the
   first merge's retained discriminator.
6. **Promote dedup is exact-canonical-value identity** (content-addressed
   entity URIs over the canonical JSON encoding) — the "through the ER
   cascade" obligation at fixture scale; the minting function is shared with
   the demote regroup view so re-minted URIs are bit-identical.
7. **Migrations convert only current cells**; historical world-time segments
   keep their original representation (readable via the old version's view).
   Queryability is asserted on the current slice, matching §3.6's "on the
   as-of slice where data moved".
8. **Harness conversions use power-of-two linear factors** so inverse views
   are float-exact; the generic registry supports arbitrary factors (unit
   tests cover 0.25/4.0 and int↔float casts).

## Measured (this machine)

300-sequence replay harness + battery + unit suite: **68 tests in ~45 s**;
100% snapshot-queryability preservation, 100% replay equality, zero Hearth
commits across all label/axiom-only operators (asserted per morphism record).
