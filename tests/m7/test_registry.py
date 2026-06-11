"""Registry: fingerprint registration, ledger persistence (kind 'transform'),
provenance discipline (ONE-leaf synthetic atom for human-authored; the
synthesizer's term for synthesized defs)."""

from __future__ import annotations

import pytest

from ontoforge.contracts import Leaf, leaf, make_cell_atom
from ontoforge.transforms import DslError, deserialize_def
from m7_helpers import make_stack, tdef


def test_register_returns_contract_fingerprint_and_persists() -> None:
    ledger, registry, _ = make_stack()
    t = tdef("clean", ("raw.t",), "conf.t", "SELECT upper(a) AS a FROM raw.t")
    fp = registry.register(t)
    assert fp == t.fingerprint
    row = ledger.connection.execute(
        "SELECT kind, payload, prov_ref FROM artifact WHERE artifact_id = ?",
        (f"transform:{fp}",),
    ).fetchone()
    assert row is not None and row[0] == "transform"
    assert deserialize_def(row[1]) == t
    # provenance is a ONE-leaf term over a real, registered atom
    term = ledger.resolve(row[2])
    assert isinstance(term, Leaf)
    assert ledger.get_atom(term.atom_id) is not None
    assert ledger.valuate_ref(row[2], "derivable") is True


def test_register_is_idempotent_on_identical_content() -> None:
    ledger, registry, _ = make_stack()
    t = tdef("clean", ("raw.t",), "conf.t", "SELECT a FROM raw.t")
    fp1 = registry.register(t)
    fp2 = registry.register(t)
    assert fp1 == fp2
    n = ledger.connection.execute(
        "SELECT count(*) FROM artifact WHERE kind = 'transform'"
    ).fetchone()[0]
    assert n == 1


def test_changed_body_is_a_new_fingerprint_and_new_artifact() -> None:
    ledger, registry, _ = make_stack()
    fp1 = registry.register(tdef("clean", ("raw.t",), "conf.t", "SELECT a FROM raw.t"))
    fp2 = registry.register(
        tdef("clean", ("raw.t",), "conf.t", "SELECT upper(a) AS a FROM raw.t", version=2)
    )
    assert fp1 != fp2
    assert registry.by_name("clean").fingerprint == fp2  # latest wins
    n = ledger.connection.execute(
        "SELECT count(*) FROM artifact WHERE kind = 'transform'"
    ).fetchone()[0]
    assert n == 2


def test_invalid_dsl_is_rejected_before_any_persistence() -> None:
    ledger, registry, _ = make_stack()
    with pytest.raises(DslError):
        registry.register(tdef("bad", ("raw.t",), "conf.t", "DELETE FROM raw.t"))
    n = ledger.connection.execute("SELECT count(*) FROM artifact").fetchone()[0]
    assert n == 0


def test_synthesized_transform_requires_and_uses_synthesizer_term() -> None:
    ledger, registry, _ = make_stack()
    t = tdef(
        "synth", ("raw.t",), "conf.t", "SELECT a FROM raw.t", synthesized_by="anvil:T1"
    )
    with pytest.raises(ValueError, match="synthesizer"):
        registry.register(t)
    atom = make_cell_atom("anvil", "search", "candidate-7", "program", t.sql)
    ledger.register_atoms([atom])
    ref = ledger.intern(leaf(atom.atom_id))
    fp = registry.register(t, prov_ref=ref)
    stored = ledger.connection.execute(
        "SELECT prov_ref FROM artifact WHERE artifact_id = ?", (f"transform:{fp}",)
    ).fetchone()[0]
    assert stored == ref


def test_active_returns_latest_per_name() -> None:
    _, registry, _ = make_stack()
    registry.register(tdef("a", ("raw.t",), "conf.a", "SELECT a FROM raw.t"))
    registry.register(tdef("b", ("conf.a",), "conf.b", "SELECT a FROM conf.a"))
    registry.register(
        tdef("a", ("raw.t",), "conf.a", "SELECT b AS a FROM raw.t", version=2)
    )
    active = {r.tdef.name: r.tdef.version for r in registry.active()}
    assert active == {"a": 2, "b": 1}
