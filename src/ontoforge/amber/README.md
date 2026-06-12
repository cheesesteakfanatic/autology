# M14 — AMBER: the Freeze-Frame Snapshot

Implements whitepaper §7 / §11.2 M14 at the AMD-0008 substrate (plain Parquet,
no Iceberg). Owner files: `src/ontoforge/amber/`, tests in `tests/m14/`
(25 tests, including the executable completeness gate).

## Bundle layout (§7 items 1–8)

```
manifest.json                  per-file sha256 + sizes, counts, constants
                               (open-interval sentinel, survivorship order),
                               capability-loss declaration L
ontology/ontology.ttl          O^(t) as OWL 2 + SHACL (M11, sorted Turtle)
ontology/ontology.json         native exact serialization (order-preserving)
data/                          HEARTH export_canonical: every value/link shard
                               as plain Parquet WITH full bi-temporal history,
                               plus HEARTH's own content-hash manifest
rdf/data_current.ttl           current-stance entity graph + per-cell
                               provenance annotations (the SPARQL leg)
transforms/<fp>.sql,.meta.json every ledger 'transform' artifact: readable SQL
                               with a comment header + verbatim payload
decisions/decisions.jsonl      DECISION ledger extract (ER match records etc.)
morphisms/morphisms.jsonl      TEMPER morphism ledger ('temper-op' artifacts;
                               replayable AND invertible via M10)
provenance/prov_terms.jsonl    interned term table for every prov_ref reachable
provenance/prov_shapes.jsonl   from the bundle, + the §4.2 shape dictionary,
provenance/atoms.jsonl         + every leaf atom (id, source URI, value)
docs/README.md                 generated documentation: classes, properties,
                               counts, how-to-open, the loss set
```

## Surface

* `snapshot(out_dir, hearth, ontology, ledger, scope='full') -> manifest path`
* `verify(bundle_dir) -> {"ok", "errors", "checks"}` — manifest hash + size on
  every file, stray-file detection, provenance completeness (every cell
  `prov_ref` resolves to a non-ZERO term whose leaves are all present),
  transform readability + fingerprint recomputation, ontology/TTL parse,
  extract well-formedness.
* `import_bundle(bundle_dir, new_root) -> (Hearth, Ontology, SqliteLedger)` —
  a WORKING store: atoms re-registered with preserved ids, terms re-interned
  (content addressing must reproduce every `prov_ref` byte-for-byte, asserted),
  artifacts/decisions re-appended, HEARTH rebuilt through its own hash-verified
  canonical import, monotone clock restored, post-import commits work.
* `reader.BundleReader` — the §7 reference answerer. **Imports no OntoForge
  module** (only duckdb / rdflib / pyoxigraph / pyarrow / stdlib): DuckDB with
  the manifest-declared stance predicates + survivorship window over the
  Parquet; SPARQL over the two TTL files in either engine; citations resolved
  from the provenance extract (a term's citation set = its leaf set, since the
  citations semiring unions leaves across both ⊕ and ⊗).

## The completeness gate (tests/m14/test_completeness.py)

Five representative queries answered LIVE (Hearth + ledger) and from the
BUNDLE ALONE, with a **hard 100% equality gate on answers AND citation
atom-id sets**:

1. entity lookup with per-property citation atoms (current stance);
2. 1-hop link with citations — via Parquet *and* via SPARQL in both stores;
3. aggregates (entity count, AVG seats, exact year histogram) — DuckDB vs scan;
4. **as-of temporal slice** with citations (the historical registrant, not the
   current successor — bi-temporal history really ships);
5. subsumption-aware count via `rdf:type/rdfs:subClassOf*`, identical in
   rdflib and pyoxigraph and equal to the live extent.

**Loss-set negative test:** an AST audit of `reader.py` (allowlist =
duckdb/rdflib/pyoxigraph/pyarrow/stdlib, no `ontoforge`) plus a subprocess run
proving zero `ontoforge.*` modules load at answer time. Nothing outside the
declared L is needed.

## Design decisions

1. **No timestamps in the bundle, ever** (§7 "modulo timestamps", resolved by
   construction): ledger `created_at` columns are dropped from every extract;
   the generated docs carry none. Consequence: export-import idempotence is
   FULL manifest equality — `snapshot(import_bundle(snapshot(X)))` reproduces
   every sha256. (Morphism `timestamp` fields are data — deliberate Instants
   recorded by M10 — and ship verbatim.)
2. **Verbatim payloads for re-append fidelity.** Transform/morphism artifacts
   keep their exact ledger payload strings in the bundle, so a rebuilt ledger
   re-serializes identically. The artifact-table `seq` is NOT serialized (it
   interleaves artifact kinds in arrival order, which a rebuilt ledger cannot
   reproduce); morphism order is the version chain itself.
3. **Self-describing constants.** The open-interval sentinel (2^62) and the
   survivorship ORDER BY ship in the manifest, so a bundle-only consumer needs
   no OntoForge source to reproduce stanced reads — `BundleReader` reads them
   from the manifest rather than hardcoding.
4. **Two ontology serializations** (§7 item 1): Turtle for the open stack,
   native JSON for exact order-preserving reconstruction (the RDF round trip
   normalizes tuple order — see M11 README).
5. **Known limitation:** atoms whose values are not JSON scalars round-trip
   through `value_repr` (string form). No such atom exists in v0 corpora (CDC
   registers scalar cell values); a non-scalar atom would still verify and
   resolve, but its re-registered `value_json` would differ on a second
   export-import cycle.
6. **Scope:** v0 ships `scope='full'`; RAW-on-request and class-scoped bundles
   ride with the AMD-0007 deferrals.
