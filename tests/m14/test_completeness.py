"""M14 — THE EXECUTABLE COMPLETENESS TEST (whitepaper §7, scaled per AMD-0001).

Five representative queries are answered twice:

* LIVE — Hearth + SqliteLedger (the OntoForge runtime), citations via the
  ledger's citations valuation;
* BUNDLE — from the AMBER bundle ALONE through the reference open stack
  (DuckDB over the bundle Parquet; rdflib AND pyoxigraph over the bundle
  Turtle), citations via the bundled provenance extract. The bundle-side
  answerer is ``ontoforge.amber.reader``, which imports no OntoForge module
  (enforced below by an AST import audit — the loss-set negative test).

HARD GATE: 100% answer equality AND 100% citation atom-id-set equality.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

import pytest

from ontoforge.contracts import CURRENT, Layer, Stance
from ontoforge.hearth import survivorship_key

NS = "onto://gold/aviation"
AIRCRAFT = f"{NS}/Aircraft"
MODEL = f"{NS}/AircraftModel"


@pytest.fixture(scope="module")
def reader(bundle):
    from ontoforge.amber.reader import BundleReader

    return BundleReader(bundle)


# ---------------------------------------------------------------------------
# live-side answerer (full OntoForge runtime; the oracle)
# ---------------------------------------------------------------------------


def _live_entity(world, class_uri: str, entity_uri: str, stance) -> dict[str, dict]:
    """prop -> {value, citations} with the cell-level prov_ref resolved through
    the REAL ledger — the live half of the §7 'answers and citations' pair."""
    hearth, ledger = world["hearth"], world["ledger"]
    shard = hearth._shards[(Layer.ENTITY, class_uri)]
    visible: dict[str, list] = {}
    for seq in shard.by_entity.get(entity_uri, ()):
        c = shard.cells[seq]
        if c.visible_under(stance):
            visible.setdefault(c.prop, []).append((seq, c))
    out = {}
    for prop, cells in visible.items():
        _, win = min(cells, key=lambda sc: survivorship_key(*sc))
        out[prop] = {
            "value": win.value,
            "citations": frozenset(ledger.valuate_ref(win.prov_ref, "citations")),
        }
    return out


# ---------------------------------------------------------------------------
# Q1 — entity lookup with citation atoms (current stance)
# ---------------------------------------------------------------------------


def test_q1_entity_lookup_with_citations(world, reader):
    known = world["known_uri"]
    live = _live_entity(world, AIRCRAFT, known, CURRENT)
    bundled = reader.entity(AIRCRAFT, known)
    assert set(live) == set(bundled)
    for prop in live:
        assert bundled[prop]["value"] == live[prop]["value"], prop
        assert bundled[prop]["citations"] == live[prop]["citations"], prop  # HARD GATE
    # citations are real atoms over real source cells, resolvable bundle-side
    for prop in live:
        for atom_id in bundled[prop]["citations"]:
            assert reader.atom(atom_id)["uri"].startswith("atom://")


# ---------------------------------------------------------------------------
# Q2 — 1-hop link with citations, via Parquet AND via SPARQL in both stores
# ---------------------------------------------------------------------------


def test_q2_one_hop_link(world, reader):
    known = world["known_uri"]
    hearth, ledger = world["hearth"], world["ledger"]
    [live_model] = hearth.traverse(known, "model", CURRENT)
    link_shard = hearth.links._shards[(AIRCRAFT, "model")]
    live_link = next(
        c for c in link_shard.cells if c.subject_uri == known and c.valid.open and c.system.open
    )
    live_citations = frozenset(ledger.valuate_ref(live_link.prov_ref, "citations"))

    [bundled] = reader.links("model", subject=known)
    assert bundled["object"] == live_model
    assert bundled["citations"] == live_citations  # HARD GATE

    q = f"SELECT ?m WHERE {{ <{known}> <{AIRCRAFT}/prop/model> ?m }}"
    want = [(("iri", live_model),)]
    assert reader.sparql(q, engine="rdflib") == want
    assert reader.sparql(q, engine="oxigraph") == want


# ---------------------------------------------------------------------------
# Q3 — aggregates (SQL over the bundle Parquet vs live scan)
# ---------------------------------------------------------------------------


def test_q3_aggregates(world, reader):
    hearth = world["hearth"]
    # count of aircraft
    live_n = hearth.scan(AIRCRAFT, CURRENT).num_rows
    [(bundle_n,)] = reader.aggregate(
        AIRCRAFT, "SELECT COUNT(DISTINCT entity_uri) FROM cells"
    )
    assert bundle_n == live_n == world["n_aircraft"]
    # average seats across models
    live_seats = [s for s in hearth.scan(MODEL, CURRENT).column("seats").to_pylist() if s is not None]
    live_avg = sum(live_seats) / len(live_seats)
    [(bundle_avg,)] = reader.aggregate(
        MODEL, "SELECT AVG(CAST(value_json AS DOUBLE)) FROM cells WHERE prop = 'seats'"
    )
    assert bundle_avg == pytest.approx(live_avg, abs=1e-9)
    # year histogram, exact multiset
    live_years = Counter(
        y for y in hearth.scan(AIRCRAFT, CURRENT).column("year_mfr").to_pylist() if y is not None
    )
    rows = reader.aggregate(
        AIRCRAFT,
        "SELECT CAST(value_json AS INTEGER), COUNT(*) FROM cells "
        "WHERE prop = 'year_mfr' GROUP BY 1",
    )
    assert Counter(dict(rows)) == live_years


# ---------------------------------------------------------------------------
# Q4 — as-of temporal slice WITH citations (bi-temporal history in the bundle)
# ---------------------------------------------------------------------------


def test_q4_as_of_slice_with_citations(world, reader):
    known = world["known_uri"]
    t_mid = world["known"]["t_mid"]
    stance = Stance("as_of", valid_at=t_mid)
    live = _live_entity(world, AIRCRAFT, known, stance)
    bundled = reader.entity(AIRCRAFT, known, at=t_mid)
    assert set(live) == set(bundled)
    for prop in live:
        assert bundled[prop]["value"] == live[prop]["value"], prop
        assert bundled[prop]["citations"] == live[prop]["citations"], prop  # HARD GATE
    # and the slice is genuinely historical: registrant differs from current
    assert bundled["registrant_name"]["value"] == world["known"]["registrant"]
    assert reader.entity(AIRCRAFT, known)["registrant_name"]["value"] == world["known"]["successor"]


# ---------------------------------------------------------------------------
# Q5 — subsumption-aware SPARQL, identical across BOTH bundle stores AND live
# ---------------------------------------------------------------------------


def test_q5_subsumption_query_both_stores(world, reader):
    live_n = world["hearth"].scan(f"{NS}/Operator", CURRENT).num_rows
    q = (
        "PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> "
        "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> "
        f"SELECT (COUNT(?x) AS ?n) WHERE {{ ?x rdf:type/rdfs:subClassOf* <{NS}/Agent> }}"
    )
    want = [(("num", str(live_n)),)]
    assert reader.sparql(q, engine="rdflib") == want
    assert reader.sparql(q, engine="oxigraph") == want


# ---------------------------------------------------------------------------
# the loss-set negative test: NOTHING outside L is needed by the bundle side
# ---------------------------------------------------------------------------

_ALLOWED_READER_IMPORTS = {
    # the §7 reference open stack...
    "duckdb",
    "rdflib",
    "pyoxigraph",
    "pyarrow",
    # ...and the standard library
    "json",
    "pathlib",
    "re",
    "typing",
    "collections",
    "dataclasses",
    "functools",
    "itertools",
    "math",
    "hashlib",
    "os",
    "sys",
    "__future__",
}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import => inside ontoforge.amber => forbidden
                mods.add("ontoforge")
            elif node.module:
                mods.add(node.module.split(".")[0])
    return mods


def test_loss_set_negative_reader_needs_no_ontoforge():
    import ontoforge.amber.reader as reader_mod

    mods = _imported_modules(Path(reader_mod.__file__))
    assert "ontoforge" not in mods, "bundle-side answerer leaked an OntoForge dependency"
    assert mods <= _ALLOWED_READER_IMPORTS, mods - _ALLOWED_READER_IMPORTS


def test_loss_set_negative_runtime_modules(bundle):
    """Belt and braces: import the reader in a pristine interpreter and prove
    answering Q1 loads no ontoforge module at runtime either."""
    import json as _json
    import subprocess
    import sys

    code = (
        "import sys, json\n"
        f"sys.path.insert(0, {str(Path('src').resolve())!r})\n"
        "import importlib.util as u\n"
        f"spec = u.spec_from_file_location('amber_reader', {str(Path('src/ontoforge/amber/reader.py').resolve())!r})\n"
        "mod = u.module_from_spec(spec); spec.loader.exec_module(mod)\n"
        f"r = mod.BundleReader({str(bundle)!r})\n"
        "classes = [e['class_uri'] for e in json.load(open(str(r.bundle / 'data' / 'manifest.json')))['shards'] if e['kind'] == 'values']\n"
        "next(iter(r.scan(classes[0])))\n"
        "print(json.dumps(sorted(m for m in sys.modules if m.split('.')[0] == 'ontoforge')))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert _json.loads(out.stdout.strip()) == []
