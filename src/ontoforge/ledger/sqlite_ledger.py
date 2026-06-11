"""M0 — Atom & Ledger Core: SQLite-backed reference implementation of the
``ontoforge.contracts.ledger.Ledger`` protocol (whitepaper §1.2, §4.2, §9, §11.2 M0).

Design highlights
-----------------
* **Append-only** (§11.2 M0 invariants): every table is INSERT-only; SQL triggers
  abort UPDATE/DELETE as defense-in-depth. Corrections supersede, never mutate.
* **Content-addressed atoms** (§1.2): atom_id = xxh3(uri, value_repr); re-registering
  identical content is a no-op (``INSERT OR IGNORE`` on the primary key).
* **Two-level provenance interning** (§4.2): a derivation-SHAPE dictionary (the
  polynomial with leaf atoms abstracted to positional slots, keyed by the shape's
  term_hash) plus a per-term compact leaf-atom-id array. Most terms in a workload
  share a handful of shapes, so shape rows stay tiny while terms stay exact.
* **Constraint H** (§1.3): ``append_artifact`` refuses any prov_ref that resolves
  to the ZERO polynomial — no stored fact without a derivation.
* **Exact invalidation** (§9): changed atoms → PROV_LEAF (atom→term edges from the
  interning pass) → ARTIFACT rows referencing those terms. An artifact is affected
  iff a changed atom appears as a leaf of its provenance term — no more, no less.
* **One term, many valuations** (§9): named semiring valuations 'citations',
  'confidence', 'derivable' run over resolved terms via ``contracts.provenance.valuate``.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator, Mapping, Optional, Sequence

from ontoforge.contracts.atoms import Atom, value_repr
from ontoforge.contracts.decisions import DecisionResult
from ontoforge.contracts.models import CostMeter
from ontoforge.contracts.provenance import (
    ONE,
    ZERO,
    Leaf,
    Prod,
    ProvTerm,
    Sum,
    leaf,
    map_leaves,
    term_hash,
    valuate,
)

# Slot ids live in a reserved namespace so they can never collide with real
# atom_ids (which are hex digests or caller-chosen printable strings).
_SLOT_PREFIX = "\x00slot:"

# Value types cheap & lossless to mirror as JSON next to the canonical string.
_JSON_SCALARS = (type(None), bool, int, float, str)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS atom (
    atom_id    TEXT PRIMARY KEY,
    uri        TEXT NOT NULL,
    value_repr TEXT NOT NULL,
    value_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prov_shape (
    shape_hash TEXT PRIMARY KEY,
    shape_json TEXT NOT NULL,
    n_slots    INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prov_term (
    prov_ref   TEXT PRIMARY KEY,
    shape_hash TEXT NOT NULL REFERENCES prov_shape(shape_hash),
    leaf_ids   TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- PROV_EDGE of the whitepaper data model: atom -> term containing it as a leaf.
CREATE TABLE IF NOT EXISTS prov_leaf (
    atom_id  TEXT NOT NULL,
    prov_ref TEXT NOT NULL REFERENCES prov_term(prov_ref),
    PRIMARY KEY (atom_id, prov_ref)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_prov_leaf_atom ON prov_leaf(atom_id);

CREATE TABLE IF NOT EXISTS artifact (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    artifact_id TEXT NOT NULL,
    kind        TEXT NOT NULL,
    payload     TEXT NOT NULL,
    prov_ref    TEXT NOT NULL REFERENCES prov_term(prov_ref),
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_artifact_prov ON artifact(prov_ref);
CREATE INDEX IF NOT EXISTS idx_artifact_id ON artifact(artifact_id);

CREATE TABLE IF NOT EXISTS decision (
    seq               INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id       TEXT NOT NULL,
    outcome           TEXT NOT NULL,
    confidence        REAL NOT NULL,
    conformal_set     TEXT NOT NULL,
    tier              INTEGER NOT NULL,
    cost_tokens       INTEGER NOT NULL,
    deferred_to_human INTEGER NOT NULL,
    quarantined       INTEGER NOT NULL,
    rationale         TEXT NOT NULL,
    prov_atoms        TEXT NOT NULL,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cost (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    task       TEXT NOT NULL,
    tokens     INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
"""

_APPEND_ONLY_TABLES = ("atom", "prov_shape", "prov_term", "prov_leaf", "artifact", "decision", "cost")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------
# Term <-> JSON (used only for the SHAPE dictionary; leaves are slot markers)
# --------------------------------------------------------------------------


def _term_to_obj(t: ProvTerm) -> Any:
    if isinstance(t, Leaf):
        return ["L", t.atom_id]
    if t == ZERO:
        return ["0"]
    if t == ONE:
        return ["1"]
    if isinstance(t, Sum):
        return ["S", [_term_to_obj(s) for s in t.terms]]
    if isinstance(t, Prod):
        return ["P", [_term_to_obj(s) for s in t.terms]]
    raise TypeError(f"not a ProvTerm: {t!r}")


def _term_from_obj(o: Any) -> ProvTerm:
    tag = o[0]
    if tag == "L":
        return Leaf(o[1])
    if tag == "0":
        return ZERO
    if tag == "1":
        return ONE
    subs = tuple(_term_from_obj(s) for s in o[1])
    # Raw constructors: the stored shape is already normalized; preserve it exactly.
    return Sum(subs) if tag == "S" else Prod(subs)


def _abstract(norm: ProvTerm) -> tuple[ProvTerm, list[str]]:
    """Abstract a NORMALIZED term's leaves into positional slots (§4.2 level 1).

    Uses contracts.provenance.map_leaves; traversal is deterministic left-to-right
    DFS, so the slot order is reproducible and instantiation is the exact inverse.
    Each leaf OCCURRENCE gets its own slot (the same atom may fill several slots).
    """
    slots: list[str] = []

    def to_slot(atom_id: str) -> ProvTerm:
        slots.append(atom_id)
        return Leaf(f"{_SLOT_PREFIX}{len(slots) - 1}")

    shape = map_leaves(norm, to_slot)
    return shape, slots


def _instantiate(shape: ProvTerm, leaf_ids: Sequence[str]) -> ProvTerm:
    """Inverse of _abstract: fill positional slots with concrete atom ids (§4.2)."""

    def from_slot(slot_id: str) -> ProvTerm:
        return Leaf(leaf_ids[int(slot_id[len(_SLOT_PREFIX):])])

    return map_leaves(shape, from_slot)


# --------------------------------------------------------------------------
# Named valuations (§9: one term, many valuations)
# --------------------------------------------------------------------------


class _CitationsSemiring:
    """Which atoms support this value: (P(atoms), ∪, ∪, ∅, ∅)."""

    def zero(self) -> frozenset[str]:
        return frozenset()

    def one(self) -> frozenset[str]:
        return frozenset()

    def plus(self, a: frozenset[str], b: frozenset[str]) -> frozenset[str]:
        return a | b

    def times(self, a: frozenset[str], b: frozenset[str]) -> frozenset[str]:
        return a | b

    def leaf(self, atom_id: str) -> frozenset[str]:
        return frozenset((atom_id,))


class _ConfidenceSemiring:
    """Viterbi semiring ([0,1], max, ×, 0, 1); leaf confidence defaults to 1.0
    unless an explicit atom→confidence map is supplied."""

    def __init__(self, atom_confidence: Optional[Mapping[str, float]] = None) -> None:
        self._conf = atom_confidence or {}

    def zero(self) -> float:
        return 0.0

    def one(self) -> float:
        return 1.0

    def plus(self, a: float, b: float) -> float:
        return a if a >= b else b

    def times(self, a: float, b: float) -> float:
        return a * b

    def leaf(self, atom_id: str) -> float:
        return float(self._conf.get(atom_id, 1.0))


class _DerivableSemiring:
    """Boolean semiring ({F,T}, or, and, F, T): is there ANY derivation at all?"""

    def zero(self) -> bool:
        return False

    def one(self) -> bool:
        return True

    def plus(self, a: bool, b: bool) -> bool:
        return a or b

    def times(self, a: bool, b: bool) -> bool:
        return a and b

    def leaf(self, atom_id: str) -> bool:
        return True


_VALUATIONS = ("citations", "confidence", "derivable")


def _chunks(items: list[str], size: int) -> Iterator[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


# --------------------------------------------------------------------------
# The ledger
# --------------------------------------------------------------------------


class SqliteLedger:
    """SQLite-backed implementation of ``ontoforge.contracts.ledger.Ledger``.

    Pass a filesystem path for a durable ledger or ``":memory:"`` (default)
    for an ephemeral one (tests, scratch cycles).
    """

    def __init__(self, path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        for table in _APPEND_ONLY_TABLES:
            for verb in ("UPDATE", "DELETE"):
                self._conn.execute(
                    f"CREATE TRIGGER IF NOT EXISTS {table}_no_{verb.lower()} "
                    f"BEFORE {verb} ON {table} BEGIN "
                    f"SELECT RAISE(ABORT, 'ledger is append-only: {verb} on {table} forbidden'); "
                    f"END"
                )
        self._conn.commit()

    # -- lifecycle ---------------------------------------------------------

    @property
    def connection(self) -> sqlite3.Connection:
        """Read access for diagnostics/tests. Writes outside this class are
        blocked by the append-only triggers anyway."""
        return self._conn

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SqliteLedger":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- atoms (§1.2) --------------------------------------------------------

    def register_atoms(self, atoms: Sequence[Atom]) -> list[str]:
        """Register atoms; identical content dedups to the same atom_id (no new row)."""
        now = _utcnow()
        rows = []
        for a in atoms:
            vjson: Optional[str] = None
            if isinstance(a.value, _JSON_SCALARS):
                vjson = json.dumps(a.value)
            rows.append((a.atom_id, a.uri, value_repr(a.value), vjson, now))
        with self._conn:
            self._conn.executemany(
                "INSERT OR IGNORE INTO atom (atom_id, uri, value_repr, value_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                rows,
            )
        return [a.atom_id for a in atoms]

    def get_atom(self, atom_id: str) -> Optional[Atom]:
        row = self._conn.execute(
            "SELECT uri, value_repr, value_json FROM atom WHERE atom_id = ?", (atom_id,)
        ).fetchone()
        if row is None:
            return None
        uri, vrepr, vjson = row
        # Prefer the typed JSON mirror; fall back to the canonical string for
        # exotic values (identity is preserved by passing the stored atom_id).
        value: Any = json.loads(vjson) if vjson is not None else vrepr
        return Atom(uri=uri, value=value, atom_id=atom_id)

    # -- provenance interning (§4.2 two-level) -------------------------------

    def intern(self, term: ProvTerm) -> str:
        """Intern a term; returns prov_ref (= contracts term_hash of the
        normalized term). Idempotent: re-interning is a no-op."""
        norm = map_leaves(term, leaf)  # rebuild through smart constructors => normal form
        prov_ref = term_hash(norm)
        exists = self._conn.execute(
            "SELECT 1 FROM prov_term WHERE prov_ref = ?", (prov_ref,)
        ).fetchone()
        if exists:
            return prov_ref
        shape, slot_atoms = _abstract(norm)
        shape_hash = term_hash(shape)
        now = _utcnow()
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO prov_shape (shape_hash, shape_json, n_slots, created_at) "
                "VALUES (?, ?, ?, ?)",
                (shape_hash, json.dumps(_term_to_obj(shape)), len(slot_atoms), now),
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO prov_term (prov_ref, shape_hash, leaf_ids, created_at) "
                "VALUES (?, ?, ?, ?)",
                (prov_ref, shape_hash, json.dumps(slot_atoms), now),
            )
            self._conn.executemany(
                "INSERT OR IGNORE INTO prov_leaf (atom_id, prov_ref) VALUES (?, ?)",
                [(aid, prov_ref) for aid in sorted(set(slot_atoms))],
            )
        return prov_ref

    def resolve(self, prov_ref: str) -> ProvTerm:
        """Reconstruct the exact (normalized) term: shape dictionary + leaf array."""
        row = self._conn.execute(
            "SELECT t.leaf_ids, s.shape_json FROM prov_term t "
            "JOIN prov_shape s ON s.shape_hash = t.shape_hash WHERE t.prov_ref = ?",
            (prov_ref,),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown prov_ref: {prov_ref!r}")
        leaf_ids = json.loads(row[0])
        shape = _term_from_obj(json.loads(row[1]))
        return _instantiate(shape, leaf_ids)

    def valuate_ref(
        self,
        prov_ref: str,
        valuation: str,
        *,
        atom_confidence: Optional[Mapping[str, float]] = None,
    ) -> Any:
        """Run a named valuation over an interned term (§9).

        'citations'  -> frozenset[str] of supporting atom_ids
        'confidence' -> float in [0,1]; leaves default to 1.0, or are looked up
                        in ``atom_confidence`` when supplied (missing ids -> 1.0)
        'derivable'  -> bool (ZERO -> False)
        """
        term = self.resolve(prov_ref)
        if valuation == "citations":
            return valuate(term, _CitationsSemiring())
        if valuation == "confidence":
            return valuate(term, _ConfidenceSemiring(atom_confidence))
        if valuation == "derivable":
            return valuate(term, _DerivableSemiring())
        raise ValueError(f"unknown valuation {valuation!r}; expected one of {_VALUATIONS}")

    # -- artifacts & decisions (append-only) ---------------------------------

    def append_artifact(self, artifact_id: str, kind: str, payload: str, prov_ref: str) -> None:
        """Record a derived artifact. Constraint H (§1.3): prov_ref must be a
        previously interned, non-ZERO term — otherwise this raises."""
        term = self.resolve(prov_ref)  # KeyError if never interned
        if term == ZERO:
            raise ValueError(
                f"constraint H violated: artifact {artifact_id!r} has ZERO provenance "
                f"(prov_ref={prov_ref!r}); every stored fact must be derivable from atoms"
            )
        with self._conn:
            self._conn.execute(
                "INSERT INTO artifact (artifact_id, kind, payload, prov_ref, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (artifact_id, kind, payload, prov_ref, _utcnow()),
            )

    def append_decision(self, result: DecisionResult, prov_atoms: Sequence[str] = ()) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO decision (decision_id, outcome, confidence, conformal_set, tier, "
                "cost_tokens, deferred_to_human, quarantined, rationale, prov_atoms, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    result.decision_id,
                    result.outcome,
                    float(result.confidence),
                    json.dumps(list(result.conformal_set)),
                    int(result.tier.value),
                    int(result.cost_tokens),
                    int(result.deferred_to_human),
                    int(result.quarantined),
                    result.rationale,
                    json.dumps(list(prov_atoms)),
                    _utcnow(),
                ),
            )

    # -- invalidation (§4.2 dictionary-side join, §9 constraint Δ) -----------

    def invalidate(self, changed_atom_ids: Iterable[str]) -> set[str]:
        """Changed atoms -> the EXACT set of affected artifact_ids.

        Join path per §4.2: changed atom -> PROV_LEAF (terms containing it as a
        leaf) -> ARTIFACT rows referencing those prov_refs. Exact because
        PROV_LEAF holds precisely the leaf set of each interned term.
        """
        ids = list(dict.fromkeys(changed_atom_ids))
        affected: set[str] = set()
        for chunk in _chunks(ids, 400):
            marks = ",".join("?" * len(chunk))
            rows = self._conn.execute(
                f"SELECT DISTINCT ar.artifact_id FROM prov_leaf pl "
                f"JOIN artifact ar ON ar.prov_ref = pl.prov_ref "
                f"WHERE pl.atom_id IN ({marks})",
                chunk,
            ).fetchall()
            affected.update(r[0] for r in rows)
        return affected

    # -- cost (§18.4 item 5) --------------------------------------------------

    def record_cost(self, task: str, tokens: int) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO cost (task, tokens, created_at) VALUES (?, ?, ?)",
                (task, int(tokens), _utcnow()),
            )

    def total_cost_tokens(self) -> int:
        row = self._conn.execute("SELECT COALESCE(SUM(tokens), 0) FROM cost").fetchone()
        return int(row[0])


class LedgerCostMeter(CostMeter):
    """A CostMeter whose record() writes through to the ledger's COST table,
    so in-memory per-task counters and the durable ledger never diverge."""

    def __init__(self, ledger: SqliteLedger) -> None:
        super().__init__()
        self._ledger = ledger

    def record(self, task: str, tokens: int) -> None:
        super().record(task, tokens)
        self._ledger.record_cost(task, tokens)
