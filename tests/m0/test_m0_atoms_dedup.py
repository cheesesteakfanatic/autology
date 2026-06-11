"""M0 acceptance: dedup-on-content for the atom registry (§1.2, §11.2 M0)."""

from ontoforge.contracts.atoms import make_cell_atom, make_span_atom
from ontoforge.ledger import SqliteLedger


def _atom_rows(led):
    return led.connection.execute("SELECT COUNT(*) FROM atom").fetchone()[0]


def test_same_cell_twice_one_row_same_id():
    led = SqliteLedger()
    a1 = make_cell_atom("crm", "orders", "r17", "amount", 12.5)
    a2 = make_cell_atom("crm", "orders", "r17", "amount", 12.5)
    ids1 = led.register_atoms([a1])
    ids2 = led.register_atoms([a2])
    assert ids1 == ids2 == [a1.atom_id]
    assert a1.atom_id == a2.atom_id
    assert _atom_rows(led) == 1


def test_changed_value_yields_new_atom_id():
    led = SqliteLedger()
    a1 = make_cell_atom("crm", "orders", "r17", "amount", 12.5)
    a2 = make_cell_atom("crm", "orders", "r17", "amount", 13.0)  # same coordinates, new value
    assert a1.atom_id != a2.atom_id
    led.register_atoms([a1, a2])
    assert _atom_rows(led) == 2
    # both atoms retrievable: the old one is superseded, not mutated
    assert led.get_atom(a1.atom_id).value == 12.5
    assert led.get_atom(a2.atom_id).value == 13.0


def test_none_and_empty_string_are_distinct_atoms():
    led = SqliteLedger()
    a_none = make_cell_atom("s", "t", "r1", "c", None)
    a_empty = make_cell_atom("s", "t", "r1", "c", "")
    assert a_none.atom_id != a_empty.atom_id
    led.register_atoms([a_none, a_empty])
    assert _atom_rows(led) == 2
    assert led.get_atom(a_none.atom_id).value is None
    assert led.get_atom(a_empty.atom_id).value == ""


def test_batch_with_internal_duplicates_dedups():
    led = SqliteLedger()
    atoms = [make_cell_atom("s", "t", f"r{i % 3}", "c", i % 3) for i in range(9)]
    ids = led.register_atoms(atoms)
    assert len(ids) == 9
    assert len(set(ids)) == 3
    assert _atom_rows(led) == 3


def test_get_atom_roundtrip_and_missing():
    led = SqliteLedger()
    span = make_span_atom("docs", "contracts/msa.txt", 120, 145, "net 30 payment terms")
    cell_int = make_cell_atom("erp", "lines", "r9", "qty", 7)
    cell_bool = make_cell_atom("erp", "lines", "r9", "shipped", True)
    led.register_atoms([span, cell_int, cell_bool])
    got = led.get_atom(span.atom_id)
    assert got.uri == span.uri and got.value == "net 30 payment terms"
    assert got.atom_id == span.atom_id
    assert led.get_atom(cell_int.atom_id).value == 7
    assert led.get_atom(cell_bool.atom_id).value is True
    assert led.get_atom("0000000000000000") is None


def test_reingest_unchanged_then_changed_cycle():
    """CDC-style: cycle 1 and 2 identical (no new rows), cycle 3 changes one cell."""
    led = SqliteLedger()
    cycle = [make_cell_atom("api", "tickets", f"r{i}", "status", "open") for i in range(10)]
    led.register_atoms(cycle)
    led.register_atoms(cycle)  # re-pull, unchanged
    assert _atom_rows(led) == 10
    changed = make_cell_atom("api", "tickets", "r4", "status", "closed")
    led.register_atoms([changed])
    assert _atom_rows(led) == 11
