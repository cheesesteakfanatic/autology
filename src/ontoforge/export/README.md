# M11 — Ontology/Graph Export & RDF round-trip

Implements whitepaper v1 G7 / §11.2 M11. Owner files: `src/ontoforge/export/`,
tests in `tests/m11/` (23 tests). Consumed by M14 (AMBER ships the Turtle this
module produces) and M12 (SPARQL lowering target).

## Surface

| function | role |
|---|---|
| `ontology_to_rdf(onto)` | O^(t) as OWL 2: `owl:Class` + `rdfs:subClassOf`, labels/definitions, `owl:DatatypeProperty`/`owl:ObjectProperty` with `rdfs:domain`/`rdfs:range`, `owl:disjointWith` |
| `shapes_to_rdf(onto)` | Σ as SHACL: one `sh:NodeShape` per class, `sh:property` shapes with `sh:datatype/pattern/minCount/maxCount/in/minInclusive/maxInclusive` |
| `ontology_graph(onto)` | OWL + SHACL merged (the AMBER `ontology/ontology.ttl` payload) |
| `data_to_rdf(hearth, onto, stance)` | stance-visible entities as typed resources; XSD-typed literals; links as object triples; provenance annotations |
| `rdf_to_ontology(graph)` | exact inverse of `ontology_graph` (the round-trip gate) |
| `sorted_turtle(graph)` | byte-deterministic serialization |
| `assert_store_equivalence(ttl, queries)` | the parallel query-equivalence harness (rdflib + pyoxigraph) |
| `shacl_validate(data, shapes, ont_graph)` | pySHACL conformance with structured violation report |

## Design decisions

1. **No blank nodes, anywhere.** SHACL property shapes and `sh:in` list spines
   get minted, deterministic IRIs (`<class>#shape-NN-prop`, `...-in-J`).
   That makes `sorted_turtle` trivial and exact: serialize as N-Triples lines
   (a syntactic subset of Turtle), sort, join. Same graph ⇒ same bytes, across
   runs and processes — pinned by tests.
2. **Lossless `of:` annotation layer.** OWL cannot carry unit/dimension/
   cardinality/functional/synonyms/intent-hash/confidence/prov-ref, so they
   ride as annotation properties under `https://ontoforge.dev/ns#`. The
   round-trip isomorphism gate (`rdf_to_ontology` ∘ parse ∘ `ontology_graph` =
   id) holds **modulo one documented loss: tuple ORDER** of parents/
   properties/shapes/synonyms (sets exact; SHACL shape order is preserved via
   zero-padded shape IRIs). AMBER's native `ontology.json` keeps order exactly.
3. **Provenance as annotation resources (RDF-star deferred).** Each asserted
   data triple gets a deterministic `of:CellAnnotation` resource carrying
   `of:provRef` (the interned M0 term hash) + `of:confidence`. RDF-star quoted
   triples are the natural upgrade, but their Turtle round-trip is not yet
   byte-stable across rdflib/pyoxigraph at pinned versions; the annotation
   scheme is store-neutral and SPARQL-queryable today.
4. **Inherited properties keep the declaring class's URI** (`_resolve_prop`
   walks ancestors), so SHACL paths (e.g. `Agent/prop/name`) and SPARQL joins
   line up across the subsumption hierarchy for subclass instances.
5. **Result-multiset equivalence oracle.** Cross-store comparison normalizes
   terms (plain literal ≡ `xsd:string` per RDF 1.1; numeric lexical forms
   canonicalized) and compares `Counter`s — order-free, duplicate-exact.
6. **Store matrix deviation:** v0 runs rdflib + pyoxigraph (the approved deps).
   Jena/Neo4j/Kùzu from the full §11.2 M11 matrix need external runtimes
   (AMD-0001 environment); the harness takes any (load, query) pair, so adding
   stores is additive.

## Gates (tests/m11)

* round-trip isomorphism modulo the documented loss; SHACL shape order exact;
  subsumption (`ancestors`) preserved; byte-determinism of both TTL payloads.
* 7 SPARQL queries (instance counts, property lookup, 1-hop join, an
  `rdfs:subClassOf*` subsumption count, numeric filter, edge count, reverse
  join) with **identical result multisets across rdflib and pyoxigraph AND
  equal to direct-HEARTH answers** (read/scan/traverse).
* pySHACL: the committed estate slice conforms to the gold shapes; seeded
  violations (missing minCount, pattern, minInclusive) are caught with the
  exact expected constraint components and no false positives; subclass
  targeting reaches Operator instances through the Agent shape.
* every data triple is annotated and every `of:provRef` resolves in the real
  ledger to non-empty citations over registered atoms (constraint H in RDF).
