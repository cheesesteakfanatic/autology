"""AMBER reference reader — the §7 completeness-property witness.

THIS MODULE MUST NOT IMPORT ONTOFORGE. It answers queries from a bundle alone
using only the reference open stack: DuckDB over the bundle's Parquet,
rdflib/pyoxigraph over its Turtle, and the standard library. The M14
loss-set negative test enforces this with an AST import audit — if you add an
import here, it had better be on the declared allowlist, because anything
else would silently grow the §7 capability-loss set L.

Everything the reader needs beyond the data files comes from the bundle's own
manifest: the open-interval sentinel ("forever") and the survivorship order.
Citations are resolved from the provenance extract: a prov_ref's citation
atom-id set is exactly the leaf set of its interned term (the citations
semiring unions leaves across both + and ×, so no polynomial evaluation is
required — leaves(t) IS valuate(t, citations) for any non-ZERO term).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import duckdb


def _norm_term_key(kind: str, value: str, datatype: Optional[str], language: Optional[str]) -> tuple:
    """Engine-independent canonical key for one RDF term (mirrors the M11
    harness contract; reimplemented here because this module must stay free of
    ontoforge imports)."""
    xsd_string = "http://www.w3.org/2001/XMLSchema#string"
    numeric = {
        "http://www.w3.org/2001/XMLSchema#integer",
        "http://www.w3.org/2001/XMLSchema#int",
        "http://www.w3.org/2001/XMLSchema#long",
        "http://www.w3.org/2001/XMLSchema#decimal",
        "http://www.w3.org/2001/XMLSchema#double",
        "http://www.w3.org/2001/XMLSchema#float",
    }
    if kind == "iri":
        return ("iri", value)
    if kind == "bnode":
        return ("bnode",)
    if language:
        return ("lit", value, f"@{language}")
    dt = datatype or xsd_string
    if dt in numeric:
        num = float(value)
        return ("num", str(int(num)) if num.is_integer() else repr(num))
    return ("lit", value, dt)


class BundleReader:
    """Answer queries from an AMBER bundle with no OntoForge runtime."""

    def __init__(self, bundle_dir: str | Path) -> None:
        self.bundle = Path(bundle_dir)
        self.manifest = json.loads((self.bundle / "manifest.json").read_text())
        self.forever: int = self.manifest["constants"]["forever"]
        self._survivorship = self.manifest["constants"]["survivorship_order"]
        data_manifest = json.loads((self.bundle / "data" / "manifest.json").read_text())
        self._value_shards: dict[str, list[str]] = {}
        self._link_shards: dict[str, list[str]] = {}
        for entry in data_manifest["shards"]:
            path = str(self.bundle / "data" / entry["path"])
            if entry["kind"] == "values":
                self._value_shards.setdefault(entry["class_uri"], []).append(path)
            else:
                self._link_shards.setdefault(entry["predicate"], []).append(path)
        self._citations: dict[str, frozenset[str]] = {}
        for line in (self.bundle / "provenance" / "prov_terms.jsonl").read_text().splitlines():
            t = json.loads(line)
            self._citations[t["prov_ref"]] = frozenset(t["leaf_ids"])
        self._atoms: dict[str, dict[str, Any]] = {}
        for line in (self.bundle / "provenance" / "atoms.jsonl").read_text().splitlines():
            a = json.loads(line)
            self._atoms[a["atom_id"]] = a
        self.duck = duckdb.connect()
        self._graphs: dict[str, Any] = {}

    # ------------------------------------------------------------- provenance

    def citations(self, prov_ref: str) -> frozenset[str]:
        """Citation atom-id set for an interned provenance ref (see module doc)."""
        return self._citations[prov_ref]

    def atom(self, atom_id: str) -> dict[str, Any]:
        return self._atoms[atom_id]

    # ------------------------------------------------------------ value reads

    def _stance_sql(self, at: Optional[int]) -> str:
        f = self.forever
        if at is None:  # current
            return f"valid_to >= {f} AND expired_at >= {f}"
        return f"expired_at >= {f} AND valid_from <= {at} AND {at} < valid_to"

    def _survivors(self, class_uri: str, at: Optional[int]) -> str:
        paths = self._value_shards.get(class_uri)
        if not paths:
            raise KeyError(f"no value shards for class {class_uri!r} in bundle")
        files = ", ".join("'" + p.replace("'", "''") + "'" for p in paths)
        return f"""
            SELECT entity_uri, prop, value_json, prov_ref FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY entity_uri, prop
                    ORDER BY {self._survivorship}
                ) AS rn
                FROM read_parquet([{files}])
                WHERE {self._stance_sql(at)}
            ) WHERE rn = 1
        """

    def entity(self, class_uri: str, entity_uri: str, *, at: Optional[int] = None) -> dict[str, dict[str, Any]]:
        """prop -> {value, prov_ref, citations} for one entity, current or as-of."""
        rows = self.duck.execute(
            f"SELECT prop, value_json, prov_ref FROM ({self._survivors(class_uri, at)}) "
            "WHERE entity_uri = ? ORDER BY prop",
            [entity_uri],
        ).fetchall()
        return {
            prop: {
                "value": json.loads(value_json),
                "prov_ref": prov_ref,
                "citations": self.citations(prov_ref),
            }
            for prop, value_json, prov_ref in rows
        }

    def scan(self, class_uri: str, *, at: Optional[int] = None) -> list[tuple[str, str, Any]]:
        """All (entity_uri, prop, value) under the stance, sorted."""
        rows = self.duck.execute(
            f"SELECT entity_uri, prop, value_json FROM ({self._survivors(class_uri, at)}) "
            "ORDER BY entity_uri, prop"
        ).fetchall()
        return [(e, p, json.loads(v)) for e, p, v in rows]

    def aggregate(self, class_uri: str, sql_over_cells: str, *, at: Optional[int] = None) -> list[tuple]:
        """Run SQL where `cells` is the survivor relation (entity_uri, prop,
        value_json, prov_ref) — the §7 'any SQL engine over the bundle' leg."""
        return self.duck.execute(
            f"WITH cells AS ({self._survivors(class_uri, at)}) {sql_over_cells}"
        ).fetchall()

    # -------------------------------------------------------------- link reads

    def links(
        self,
        predicate: str,
        *,
        subject: Optional[str] = None,
        at: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        paths = self._link_shards.get(predicate)
        if not paths:
            raise KeyError(f"no link shards for predicate {predicate!r} in bundle")
        files = ", ".join("'" + p.replace("'", "''") + "'" for p in paths)
        where = self._stance_sql(at)
        args: list[Any] = []
        if subject is not None:
            where += " AND subject_uri = ?"
            args.append(subject)
        rows = self.duck.execute(
            f"SELECT subject_uri, object_uri, prov_ref FROM read_parquet([{files}]) "
            f"WHERE {where} ORDER BY subject_uri, object_uri",
            args,
        ).fetchall()
        return [
            {"subject": s, "object": o, "prov_ref": r, "citations": self.citations(r)}
            for s, o, r in rows
        ]

    # ------------------------------------------------------------------ SPARQL

    def _ttl(self) -> str:
        return (
            (self.bundle / "ontology" / "ontology.ttl").read_text()
            + (self.bundle / "rdf" / "data_current.ttl").read_text()
        )

    def sparql(self, query: str, *, engine: str = "rdflib") -> list[tuple]:
        """Run SPARQL over ontology+data Turtle; returns the SORTED list of
        normalized solution tuples (a canonical multiset encoding)."""
        if engine == "rdflib":
            import rdflib

            if "rdflib" not in self._graphs:
                g = rdflib.Graph()
                g.parse(data=self._ttl(), format="turtle")
                self._graphs["rdflib"] = g
            g = self._graphs["rdflib"]
            result = g.query(query)
            out = []
            for row in result:
                key = []
                for term in row:
                    if term is None:
                        key.append(("unbound",))
                    elif isinstance(term, rdflib.URIRef):
                        key.append(_norm_term_key("iri", str(term), None, None))
                    elif isinstance(term, rdflib.BNode):
                        key.append(_norm_term_key("bnode", str(term), None, None))
                    else:
                        key.append(
                            _norm_term_key(
                                "lit",
                                str(term),
                                str(term.datatype) if term.datatype else None,
                                term.language,
                            )
                        )
                out.append(tuple(key))
            return sorted(out)
        if engine == "oxigraph":
            import pyoxigraph

            if "oxigraph" not in self._graphs:
                store = pyoxigraph.Store()
                store.load(self._ttl().encode(), format=pyoxigraph.RdfFormat.TURTLE)
                self._graphs["oxigraph"] = store
            store = self._graphs["oxigraph"]
            solutions = store.query(query)
            names = [v.value for v in solutions.variables]
            out = []
            for sol in solutions:
                key = []
                for name in names:
                    term = sol[name]
                    if term is None:
                        key.append(("unbound",))
                    elif isinstance(term, pyoxigraph.NamedNode):
                        key.append(_norm_term_key("iri", term.value, None, None))
                    elif isinstance(term, pyoxigraph.BlankNode):
                        key.append(_norm_term_key("bnode", term.value, None, None))
                    else:
                        key.append(
                            _norm_term_key(
                                "lit",
                                term.value,
                                term.datatype.value if term.datatype else None,
                                term.language,
                            )
                        )
                out.append(tuple(key))
            return sorted(out)
        raise ValueError(f"unknown engine {engine!r}")
